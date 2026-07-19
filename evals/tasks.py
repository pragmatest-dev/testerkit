"""Eval task set — one task per AI skill, as plain data.

Kept as data (not code) so a future optimizer (DSPy/GEPA) can consume it as a
trainset. Each Task is a natural-language request an AI is asked to satisfy; the
grader (grader.py) then RUNS (or structurally checks) whatever the AI produced.

Skills live at ``src/testerkit/skills/<skill>/SKILL.md`` (11 of them:
testerkit-tests, testerkit-mocks, testerkit-stations, testerkit-parts, testerkit-profiles,
testerkit-sites, testerkit-capture, testerkit-analysis, testerkit-debug, testerkit-interactive,
testerkit-datasheets). The ``testerkit-tests`` set is the original start-simple ladder
(rung 0-2) plus over-engineering traps; every other skill gets one representative
task spanning its trigger.

Grading dimensions per task:
- pytest_args     — how to run it (e.g. ``--mock-instruments`` at rung 2)
- requires_pytest — False for tasks whose artifact isn't a runnable test (a
                     station/part scaffold, or a CLI-answer question)
- env             — extra env vars for the pytest run (e.g. TESTERKIT_AUTO_CONFIRM=1)
- forbid_globs    — files that must NOT exist (right-sizing: no over-scaffold)
- forbid_fixtures — fixtures the test must NOT take (e.g. psu/dmm below rung 2)
- negative_control — (glob, find, replace): a mutated copy must FAIL, proving the
                     test judges. Best-effort (matches a literal value string).
- validate_yaml   — "station" | "part": validate the emitted YAML against the
                     real StationConfig / Part model instead of running pytest.
- expect_cli      — structural check: the candidate's output/files must contain
                     this literal ``testerkit <subcommand>`` invocation.
- manual          — True to skip in the automated harness (needs an external
                     fixture, e.g. a real datasheet PDF); see skip_reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    prompt: str
    rung: int
    skill: str = "testerkit-tests"
    context_refs: tuple = ()
    pytest_args: tuple = ()
    requires_pytest: bool = True
    env: dict = field(default_factory=dict)
    forbid_globs: tuple = ()
    forbid_fixtures: tuple = ()
    negative_control: tuple | None = None
    validate_yaml: str | None = None
    expect_cli: str | None = None
    manual: bool = False
    skip_reason: str | None = None


# no station / part / fixture / profile YAML — the "don't over-scaffold" set
_NO_CONFIG = (
    "stations/**/*.yaml",
    "parts/**/*.yaml",
    "fixtures/**/*.yaml",
    "profiles/**/*.yaml",
)

TASKS = [
    # ── testerkit-tests — Rung 0 — zero config ─────────────────────────────────
    Task(
        "r0_observe",
        "Record the measured 3.3 V rail voltage in a TesterKit test — just capture "
        "the reading, no pass/fail, no config files.",
        rung=0,
        forbid_globs=_NO_CONFIG + ("**/*.yaml",),
        forbid_fixtures=("psu", "dmm", "station"),
    ),
    Task(
        "r0_verify",
        "Write a TesterKit test that checks a 3.3 V rail reads within 3.0-3.6 V. "
        "Keep the limit inline — no separate config files.",
        rung=0,
        forbid_globs=_NO_CONFIG + ("**/*.yaml",),
        forbid_fixtures=("psu", "dmm"),
        negative_control=("**/test_*.py", "3.28", "5.0"),
    ),
    # ── testerkit-tests — Rung 1 — sidecar ──────────────────────────────────────
    Task(
        "r1_sidecar",
        "Write a TesterKit test that checks a rail voltage, with the limit in an "
        "operator-editable sidecar YAML next to the test (not inline).",
        rung=1,
        forbid_globs=_NO_CONFIG,
        forbid_fixtures=("psu", "dmm"),
        negative_control=("**/test_*.py", "3.28", "5.0"),
    ),
    # ── testerkit-tests — Rung 2 — instruments (mock) ───────────────────────────
    Task(
        "r2_instruments",
        "Write a TesterKit test that sets a PSU to 3.3 V and checks the DMM reads "
        "within 3.0-3.6 V, runnable with mock instruments (no real hardware).",
        rung=2,
        pytest_args=("--mock-instruments",),
    ),
    # ── testerkit-tests — over-engineering traps (graded by ABSENCE) ───────────
    Task(
        "m1_overeng_log",
        "I just want to log a temperature reading from the bench.",
        rung=0,
        forbid_globs=_NO_CONFIG + ("**/*.yaml",),
        forbid_fixtures=("psu", "dmm", "station"),
    ),
    Task(
        "m2_overeng_check",
        "Check that the output voltage is under 5 V.",
        rung=0,
        forbid_globs=_NO_CONFIG,  # a sidecar is acceptable; a station/part spec is not
        forbid_fixtures=("psu", "dmm"),
    ),
    # ── testerkit-mocks ─────────────────────────────────────────────────────────
    Task(
        "mocks_dmm_rail",
        "Check a 3.3 V rail reads correctly via a mocked DMM — no real hardware attached, CI-safe.",
        rung=2,
        skill="testerkit-mocks",
        pytest_args=("--mock-instruments",),
    ),
    # ── testerkit-stations ──────────────────────────────────────────────────────
    Task(
        "stations_scaffold_bench",
        "Scaffold a station YAML for a bench with a PSU and a DMM — no real hardware attached yet.",
        rung=2,
        skill="testerkit-stations",
        requires_pytest=False,
        validate_yaml="station",
    ),
    # ── testerkit-parts ─────────────────────────────────────────────────────────
    Task(
        "parts_spec_buck",
        "Write a part spec for a 3.3 V buck converter with a rail_3v3 output "
        "characteristic (nominal 3.3 V on its output pin).",
        rung=3,
        skill="testerkit-parts",
        requires_pytest=False,
        validate_yaml="part",
    ),
    # ── testerkit-profiles ──────────────────────────────────────────────────────
    Task(
        "profiles_prod_validation",
        "Write a TesterKit test that checks a rail voltage, plus a 'production' "
        "profile that tightens the limit versus the default — selectable with "
        "--test-phase=production.",
        rung=4,
        skill="testerkit-profiles",
        pytest_args=("--test-phase=production",),
    ),
    # ── testerkit-sites ─────────────────────────────────────────────────────────
    Task(
        "sites_dual_site",
        "Test two UUTs at once on the same fixture — the same rail check "
        "running on both sites in one pytest invocation.",
        rung=4,
        skill="testerkit-sites",
    ),
    # ── testerkit-capture ───────────────────────────────────────────────────────
    Task(
        "capture_scope_waveform",
        "Capture a scope waveform (an array of samples) in a TesterKit test and "
        "read it back within the same test to confirm it was stored.",
        rung=1,
        skill="testerkit-capture",
    ),
    # ── testerkit-analysis ──────────────────────────────────────────────────────
    Task(
        "analysis_yield_this_week",
        "What's my yield this week?",
        rung=0,
        skill="testerkit-analysis",
        requires_pytest=False,
        expect_cli="testerkit metrics summary",
    ),
    # ── testerkit-debug ─────────────────────────────────────────────────────────
    Task(
        "debug_why_run_failed",
        "Why did run <run_id> fail?",
        rung=0,
        skill="testerkit-debug",
        requires_pytest=False,
        expect_cli="testerkit show",
    ),
    # ── testerkit-interactive ───────────────────────────────────────────────────
    Task(
        "interactive_confirm_dut",
        "Prompt the operator to confirm the DUT is connected before testing a rail voltage.",
        rung=2,
        skill="testerkit-interactive",
        env={"TESTERKIT_AUTO_CONFIRM": "1"},
    ),
    # ── testerkit-datasheets (manual — needs a real PDF fixture) ───────────────
    Task(
        "datasheets_import_part",
        "Import a part datasheet PDF into a part spec and a starter test.",
        rung=3,
        skill="testerkit-datasheets",
        manual=True,
        skip_reason="needs a real datasheet PDF fixture — run by hand, not in "
        "the automated harness",
    ),
]

BY_ID = {t.id: t for t in TASKS}
