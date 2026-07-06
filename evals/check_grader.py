"""Self-test the grader against golden candidates — no model, no cost.

    uv run python evals/check_grader.py

Proves the deterministic grader (a) accepts known-good candidates, and
(b) rejects a broken one (over-scaffolded / rubber-stamp). If this passes, the
grader is trustworthy and the only variable left in a real eval run is the model.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from grader import grade  # noqa: E402
from tasks import BY_ID  # noqa: E402

_GOLDEN = Path(__file__).parent / "golden"


def _grade_golden(name, task_id):
    tmp = Path(tempfile.mkdtemp(prefix=f"eval_{task_id}_"))
    shutil.copytree(_GOLDEN / name, tmp, dirs_exist_ok=True)
    try:
        return grade(tmp, BY_ID[task_id])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    fails = 0

    # 1. good candidates must PASS every dimension
    for golden, task_id in [("r0_verify", "r0_verify"), ("r1_sidecar", "r1_sidecar")]:
        g = _grade_golden(golden, task_id)
        print(g.summary())
        if not g.ok:
            fails += 1
            print(f"  !! expected {task_id} golden to PASS")

    # 2. an over-scaffolded candidate must FAIL minimality (grader must discriminate)
    broken = Path(tempfile.mkdtemp(prefix="eval_broken_"))
    (broken / "test_rail.py").write_text(
        "def test_rail_in_spec(verify):\n"
        "    verify('rail_voltage', 3.28, limit={'low':3.0,'high':3.6,'unit':'V'})\n"
    )
    (broken / "stations").mkdir()
    (broken / "stations" / "s.yaml").write_text("name: s\n")
    g = grade(broken, BY_ID["r0_verify"])
    print(g.summary())
    if g.minimal:
        fails += 1
        print("  !! expected over-scaffold to FAIL minimality")
    shutil.rmtree(broken, ignore_errors=True)

    print()
    print("SELF-TEST", "PASSED" if fails == 0 else f"FAILED ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
