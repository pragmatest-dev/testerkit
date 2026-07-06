"""Deterministic grader for Litmus test-writing evals — dev tooling, NOT shipped.

Grades a *candidate project* (the files an AI produced for a task) by RUNNING it:

- collects?  pytest can import + collect the test
- passes?    green under the task's pytest args (e.g. ``--mock-instruments``)
- sidecar?   any ``<test>.yaml`` validates against litmus ``SidecarConfig``
- minimal?   didn't over-scaffold — no forbidden files, no forbidden fixtures
- negative?  (optional) a paired out-of-band variant must FAIL, proving the test
             actually judges instead of rubber-stamping.

Reuses litmus's own ``SidecarConfig`` so the grader can't drift from the real
schema. The platform never calls an LLM; this is offline dev tooling that lives
outside ``src/litmus``.
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
    collects: bool = False
    passes: bool = False
    sidecar_valid: bool | None = None
    minimal: bool = True
    negative_ok: bool | None = None
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(
            self.collects
            and self.passes
            and self.minimal
            and self.sidecar_valid is not False
            and self.negative_ok is not False
        )

    def summary(self) -> str:
        def flag(b):
            return "?" if b is None else ("ok" if b else "FAIL")

        head = (
            f"[{'PASS' if self.ok else 'FAIL'}] {self.task_id}: "
            f"collects={flag(self.collects)} passes={flag(self.passes)} "
            f"sidecar={flag(self.sidecar_valid)} minimal={flag(self.minimal)} "
            f"neg={flag(self.negative_ok)}"
        )
        if self.notes:
            head += "\n    - " + "\n    - ".join(self.notes)
        return head


def _run_pytest(project: Path, args) -> tuple:
    """Run pytest in an isolated project dir; return (collected, passed, failed, errors, output)."""
    (project / "pytest.ini").write_text("[pytest]\naddopts =\n")
    env = {**os.environ, "LITMUS_SKIP_DAEMON_NOTIFY": "1"}
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

    from litmus.models.test_config import SidecarConfig

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
        res = _run_pytest(neg, task.pytest_args)
        return (res[2] + res[3]) > 0  # failed + errors
    finally:
        shutil.rmtree(neg, ignore_errors=True)


def grade(project, task) -> Grade:
    project = Path(project)
    g = Grade(task_id=task.id)
    collected, passed, failed, errors, out = _run_pytest(project, task.pytest_args)
    g.collects = collected > 0 and errors == 0
    g.passes = collected > 0 and passed == collected and failed == 0 and errors == 0
    if not g.collects:
        last = out.strip().splitlines()[-1] if out.strip() else "no output"
        g.notes.append(f"collection failed / no tests: {last}")
    elif not g.passes:
        g.notes.append(f"not green: {passed}/{collected} passed, {failed} failed, {errors} error")

    g.sidecar_valid, sc_notes = _sidecar_valid(project)
    g.notes += sc_notes

    g.minimal, min_notes = _minimal(project, task)
    g.notes += min_notes

    if task.negative_control:
        g.negative_ok = _negative(project, task)
        if g.negative_ok is False:
            g.notes.append("negative control did NOT fail — the test isn't judging (rubber-stamp)")

    return g
