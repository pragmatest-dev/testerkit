"""Eval task set — the start-simple ladder + over-engineering traps, as plain data.

Kept as data (not code) so a future optimizer (DSPy/GEPA) can consume it as a
trainset. Each Task is a natural-language request an AI is asked to satisfy; the
grader (grader.py) then RUNS whatever the AI produced.

Grading dimensions per task:
- pytest_args     — how to run it (e.g. ``--mock-instruments`` at rung 2)
- forbid_globs    — files that must NOT exist (right-sizing: no over-scaffold)
- forbid_fixtures — fixtures the test must NOT take (e.g. psu/dmm below rung 2)
- negative_control — (glob, find, replace): a mutated copy must FAIL, proving the
                     test judges. Best-effort (matches a literal value string).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Task:
    id: str
    prompt: str
    rung: int
    context_refs: tuple = ("first-test",)
    pytest_args: tuple = ()
    forbid_globs: tuple = ()
    forbid_fixtures: tuple = ()
    negative_control: tuple | None = None


# no station / part / fixture / profile YAML — the "don't over-scaffold" set
_NO_CONFIG = (
    "stations/**/*.yaml",
    "parts/**/*.yaml",
    "fixtures/**/*.yaml",
    "profiles/**/*.yaml",
)

TASKS = [
    # ── Rung 0 — zero config ────────────────────────────────────────────────
    Task(
        "r0_observe",
        "Record the measured 3.3 V rail voltage in a Litmus test — just capture "
        "the reading, no pass/fail, no config files.",
        rung=0,
        forbid_globs=_NO_CONFIG + ("**/*.yaml",),
        forbid_fixtures=("psu", "dmm", "station"),
    ),
    Task(
        "r0_verify",
        "Write a Litmus test that checks a 3.3 V rail reads within 3.0-3.6 V. "
        "Keep the limit inline — no separate config files.",
        rung=0,
        forbid_globs=_NO_CONFIG + ("**/*.yaml",),
        forbid_fixtures=("psu", "dmm"),
        negative_control=("**/test_*.py", "3.28", "5.0"),
    ),
    # ── Rung 1 — sidecar ────────────────────────────────────────────────────
    Task(
        "r1_sidecar",
        "Write a Litmus test that checks a rail voltage, with the limit in an "
        "operator-editable sidecar YAML next to the test (not inline).",
        rung=1,
        forbid_globs=_NO_CONFIG,
        forbid_fixtures=("psu", "dmm"),
        negative_control=("**/test_*.py", "3.28", "5.0"),
    ),
    # ── Rung 2 — instruments (mock) ─────────────────────────────────────────
    Task(
        "r2_instruments",
        "Write a Litmus test that sets a PSU to 3.3 V and checks the DMM reads "
        "within 3.0-3.6 V, runnable with mock instruments (no real hardware).",
        rung=2,
        pytest_args=("--mock-instruments",),
    ),
    # ── Over-engineering traps — graded by ABSENCE (right-sizing) ───────────
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
]

BY_ID = {t.id: t for t in TASKS}
