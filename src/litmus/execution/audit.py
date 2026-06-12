"""Runner-neutral traceability audit.

Walks the just-completed step's measurements and reports any that
are missing required traceability fields. Works for any runner that
records measurements through :class:`TestRunLogger` — pytest, OpenHTF,
unittest wrappers all share this check.
"""

from __future__ import annotations

from typing import Any


def audit_traceability(logger_inst: Any, *, strict: bool, spec_active: bool) -> None:
    """Check the current step's measurements for traceability completeness.

    Walks measurements recorded during the just-completed test and
    flags any that lack required fields:

    * ``step_path`` — always required (populated by the runner's step
      wrapper).
    * ``spec_ref`` OR ``uut_pin`` — required only when ``spec_active``
      is True (i.e. a part spec is loaded for the session). Runs
      without a spec exercise the graceful-degradation path and are
      not penalized for lacking pin/spec references.

    In ``strict`` mode, raises :class:`AssertionError` if any
    measurement is incomplete so the test fails. Otherwise returns
    silently — the caller decides whether to surface the issues.
    """
    steps = getattr(getattr(logger_inst, "test_run", None), "steps", None)
    if not steps:
        return
    step = steps[-1]

    incomplete: list[str] = []
    for vec in step.vectors:
        for m in vec.measurements:
            missing: list[str] = []
            if not m.step_path:
                missing.append("step_path")
            if spec_active and not m.spec_ref and not m.uut_pin:
                missing.append("spec_ref/uut_pin")
            if missing:
                incomplete.append(f"{m.name}: missing {', '.join(missing)}")

    if incomplete and strict:
        raise AssertionError(
            "--strict-traceability: measurements missing required fields:\n  "
            + "\n  ".join(incomplete)
        )
