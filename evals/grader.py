"""Deterministic grader for TesterKit AI-skill evals — dev tooling, NOT shipped.

Grades a *candidate project* (the files an AI produced for a task). Most tasks
are runnable pytest tests, graded by RUNNING them:

- collects?  pytest can import + collect the test
- passes?    green under the task's pytest args (e.g. ``--mock-instruments``)
- sidecar?   any ``<test>.yaml`` validates against testerkit ``SidecarConfig``
             (this also exercises ``MeasurementLimitConfig`` for any guardband-
             shaped ``{characteristic, guardband_pct}`` limit entries)
- minimal?   didn't over-scaffold — no forbidden files, no forbidden fixtures
- negative?  (optional) a paired out-of-band variant must FAIL, proving the test
             actually judges instead of rubber-stamping.

A few tasks aren't runnable tests at all (``requires_pytest=False``):

- station/part scaffold tasks — the artifact is ``stations/*.yaml`` /
  ``parts/*.yaml``, validated against the real ``StationConfig`` / ``Part``.
- CLI-answer tasks (``expect_cli``) — the candidate should answer with the
  right ``testerkit <subcommand>`` invocation, not prose. Checked structurally by
  grepping the candidate's response/files for the literal command.

Reuses testerkit's own Pydantic models so the grader can't drift from the real
schema. The platform never calls an LLM; this is offline dev tooling that lives
outside ``src/testerkit``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Grade:
    task_id: str
    collects: bool | None = None
    passes: bool | None = None
    sidecar_valid: bool | None = None
    station_valid: bool | None = None
    part_valid: bool | None = None
    cli_ok: bool | None = None
    minimal: bool = True
    negative_ok: bool | None = None
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(
            self.collects is not False
            and self.passes is not False
            and self.minimal
            and self.sidecar_valid is not False
            and self.station_valid is not False
            and self.part_valid is not False
            and self.cli_ok is not False
            and self.negative_ok is not False
        )

    def summary(self) -> str:
        def flag(b):
            return "?" if b is None else ("ok" if b else "FAIL")

        head = (
            f"[{'PASS' if self.ok else 'FAIL'}] {self.task_id}: "
            f"collects={flag(self.collects)} passes={flag(self.passes)} "
            f"sidecar={flag(self.sidecar_valid)} station={flag(self.station_valid)} "
            f"part={flag(self.part_valid)} cli={flag(self.cli_ok)} "
            f"minimal={flag(self.minimal)} neg={flag(self.negative_ok)}"
        )
        if self.notes:
            head += "\n    - " + "\n    - ".join(self.notes)
        return head


def _run_pytest(project: Path, args, extra_env: dict | None = None) -> tuple:
    """Run pytest in an isolated project dir; return (collected, passed, failed, errors, output)."""
    (project / "pytest.ini").write_text("[pytest]\naddopts =\n")
    env = {**os.environ, "TESTERKIT_SKIP_DAEMON_NOTIFY": "1", **(extra_env or {})}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(project), "-p", "no:cacheprovider", *list(args)],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    out = proc.stdout + "\n" + proc.stderr

    def _n(pat):
        m = re.search(pat, out)
        return int(m.group(1)) if m else 0

    return (
        _n(r"collected (\d+) item"),
        _n(r"(\d+) passed"),
        _n(r"(\d+) failed"),
        _n(r"(\d+) error"),
        out,
    )


def _sidecar_valid(project: Path):
    """Validate each ``<test>.yaml`` sidecar against the real SidecarConfig."""
    import yaml

    from testerkit.models.test_config import SidecarConfig

    sidecars = [
        tf.with_suffix(".yaml")
        for tf in project.rglob("test_*.py")
        if tf.with_suffix(".yaml").exists()
    ]
    if not sidecars:
        return None, []
    ok, notes = True, []
    for sc in sidecars:
        try:
            SidecarConfig.model_validate(yaml.safe_load(sc.read_text()) or {})
        except Exception as e:  # noqa: BLE001 — grader reports any validation failure
            ok = False
            notes.append(f"sidecar {sc.name} invalid: {type(e).__name__}: {str(e).splitlines()[0]}")
    return ok, notes


def _minimal(project: Path, task):
    """Enforce right-sizing: no over-scaffolded files, no forbidden fixtures."""
    notes = []
    for pat in task.forbid_globs:
        hits = [str(p.relative_to(project)) for p in project.glob(pat) if p.name != "pytest.ini"]
        if hits:
            notes.append(f"over-scaffold (rung {task.rung}): created {hits}")
    for fx in task.forbid_fixtures:
        for tf in project.rglob("test_*.py"):
            if re.search(rf"def test_\w+\([^)]*\b{re.escape(fx)}\b", tf.read_text()):
                notes.append(f"used forbidden fixture '{fx}' (rung {task.rung})")
                break
    return (not notes), notes


def _negative(project: Path, task):
    """Apply the out-of-band mutation to a copy; it must FAIL. None = inconclusive."""
    glob, find, replace = task.negative_control
    neg = Path(tempfile.mkdtemp(prefix="eval_neg_"))
    shutil.copytree(project, neg, dirs_exist_ok=True)
    mutated = False
    for p in neg.glob(glob):
        txt = p.read_text()
        if find in txt:
            p.write_text(txt.replace(find, replace))
            mutated = True
    try:
        if not mutated:
            return None  # couldn't inject an out-of-band value — inconclusive
        res = _run_pytest(neg, task.pytest_args, extra_env=task.env)
        return (res[2] + res[3]) > 0  # failed + errors
    finally:
        shutil.rmtree(neg, ignore_errors=True)


def _yaml_dir_valid(project: Path, subdir: str, model_cls):
    """Validate every ``<subdir>/*.yaml`` against ``model_cls``.

    Unlike sidecars (optional at every rung), a task that names
    ``validate_yaml="station"``/``"part"`` REQUIRES the artifact to exist —
    no files emitted is a hard failure, not "not applicable".
    """
    import yaml

    files = sorted((project / subdir).glob("*.yaml")) if (project / subdir).is_dir() else []
    if not files:
        return False, [f"no {subdir}/*.yaml emitted"]
    ok, notes = True, []
    for f in files:
        try:
            model_cls.model_validate(yaml.safe_load(f.read_text()) or {})
        except Exception as e:  # noqa: BLE001 — grader reports any validation failure
            ok = False
            notes.append(f"{subdir}/{f.name} invalid: {type(e).__name__}: {str(e).splitlines()[0]}")
    return ok, notes


def _station_yaml_valid(project: Path):
    """Validate emitted ``stations/*.yaml`` against the real ``StationConfig``."""
    from testerkit.models.station import StationConfig

    return _yaml_dir_valid(project, "stations", StationConfig)


def _part_yaml_valid(project: Path):
    """Validate emitted ``parts/*.yaml`` against the real ``Part`` model."""
    from testerkit.models.part import Part

    return _yaml_dir_valid(project, "parts", Part)


def _measurement_limit_valid(entry: dict) -> tuple:
    """Validate one bare limit entry (e.g. a guardband ``{characteristic,
    guardband_pct}`` shape) against the real ``MeasurementLimitConfig`` —
    for checks where the artifact under test is a limit fragment rather
    than a full sidecar file."""
    from testerkit.models.test_config import MeasurementLimitConfig

    try:
        MeasurementLimitConfig.model_validate(entry)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e).splitlines()[0]}"
    return True, None


def _cli_expect_valid(project: Path, expected: str):
    """Structural check for CLI-answer tasks: the candidate's response/files
    must literally contain the expected ``testerkit <subcommand>`` invocation —
    proving it answered with the right ACTION, not prose that only
    describes one. ``runner.py`` persists the model's textual reply to
    ``.eval_response.txt`` inside the project dir before grading."""
    texts = []
    resp = project / ".eval_response.txt"
    if resp.exists():
        texts.append(resp.read_text())
    ok_suffixes = (".py", ".md", ".sh", ".txt")
    for p in project.rglob("*"):
        if p.is_file() and p.name != ".eval_response.txt" and p.suffix in ok_suffixes:
            try:
                texts.append(p.read_text())
            except (UnicodeDecodeError, OSError):
                continue
    found = expected in "\n".join(texts)
    notes = [] if found else [f"expected CLI invocation {expected!r} not found in candidate output"]
    return found, notes


def grade(project, task) -> Grade:
    project = Path(project)
    g = Grade(task_id=task.id)

    if task.requires_pytest:
        collected, passed, failed, errors, out = _run_pytest(
            project, task.pytest_args, extra_env=task.env
        )
        g.collects = collected > 0 and errors == 0
        g.passes = collected > 0 and passed == collected and failed == 0 and errors == 0
        if not g.collects:
            last = out.strip().splitlines()[-1] if out.strip() else "no output"
            g.notes.append(f"collection failed / no tests: {last}")
        elif not g.passes:
            g.notes.append(
                f"not green: {passed}/{collected} passed, {failed} failed, {errors} error"
            )

        if task.negative_control:
            g.negative_ok = _negative(project, task)
            if g.negative_ok is False:
                g.notes.append(
                    "negative control did NOT fail — the test isn't judging (rubber-stamp)"
                )

    # Sidecar schema validation is orthogonal to whether the test actually
    # runs (it also exercises MeasurementLimitConfig for guardband-shaped
    # limits) — always check it, not just for requires_pytest tasks.
    g.sidecar_valid, sc_notes = _sidecar_valid(project)
    g.notes += sc_notes

    g.minimal, min_notes = _minimal(project, task)
    g.notes += min_notes

    if task.validate_yaml == "station":
        g.station_valid, notes = _station_yaml_valid(project)
        g.notes += notes
    elif task.validate_yaml == "part":
        g.part_valid, notes = _part_yaml_valid(project)
        g.notes += notes

    if task.expect_cli:
        g.cli_ok, notes = _cli_expect_valid(project, task.expect_cli)
        g.notes += notes

    return g
