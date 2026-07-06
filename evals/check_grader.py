"""Self-test the grader against golden candidates — no model, no cost.

    uv run python evals/check_grader.py

Proves the deterministic grader (a) accepts known-good candidates across every
task shape (runnable test, station/part scaffold, guardband sidecar, CLI
answer), and (b) rejects broken ones (over-scaffolded / rubber-stamp / invalid
schema / missing CLI invocation). If this passes, the grader is trustworthy
and the only variable left in a real eval run is the model.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from grader import grade  # noqa: E402
from tasks import BY_ID, Task  # noqa: E402

_GOLDEN = Path(__file__).parent / "golden"

# Ad hoc task for a grader-only fixture — not part of the real TASKS list,
# just proof that a guardband-shaped limit ({characteristic, guardband_pct})
# validates against the real MeasurementLimitConfig via the sidecar path.
_GUARDBAND_TASK = Task(
    "self_test_guardband_sidecar",
    "n/a — internal grader self-test fixture",
    rung=1,
    skill="litmus-tests",
    requires_pytest=False,  # schema-only check; resolving the characteristic
    # at runtime needs an active part, which is out of scope for this fixture
)


def _grade_golden(name: str, task):
    tmp = Path(tempfile.mkdtemp(prefix=f"eval_{task.id}_"))
    shutil.copytree(_GOLDEN / name, tmp, dirs_exist_ok=True)
    try:
        return grade(tmp, task)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    fails = 0

    # 1. good candidates must PASS every dimension
    goldens = [
        ("r0_verify", BY_ID["r0_verify"]),
        ("r1_sidecar", BY_ID["r1_sidecar"]),
        ("guardband_sidecar", _GUARDBAND_TASK),
        ("stations_scaffold_bench", BY_ID["stations_scaffold_bench"]),
        ("parts_spec_buck", BY_ID["parts_spec_buck"]),
        ("analysis_yield_this_week", BY_ID["analysis_yield_this_week"]),
    ]
    for golden, task in goldens:
        g = _grade_golden(golden, task)
        print(g.summary())
        if not g.ok:
            fails += 1
            print(f"  !! expected {task.id} golden to PASS")

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

    # 3. a station YAML missing a required field must FAIL station validation
    bad_station = Path(tempfile.mkdtemp(prefix="eval_bad_station_"))
    (bad_station / "stations").mkdir()
    (bad_station / "stations" / "bench_01.yaml").write_text("id: bench_01\n")  # missing `name`
    g = grade(bad_station, BY_ID["stations_scaffold_bench"])
    print(g.summary())
    if g.station_valid is not False:
        fails += 1
        print("  !! expected invalid station YAML to FAIL station_valid")
    shutil.rmtree(bad_station, ignore_errors=True)

    # 4. a part YAML missing function/direction must FAIL part validation
    bad_part = Path(tempfile.mkdtemp(prefix="eval_bad_part_"))
    (bad_part / "parts").mkdir()
    (bad_part / "parts" / "buck_3v3.yaml").write_text(
        "id: buck_3v3\nname: Demo\ncharacteristics:\n  rail_3v3:\n    pin: TP_VOUT\n"
    )
    g = grade(bad_part, BY_ID["parts_spec_buck"])
    print(g.summary())
    if g.part_valid is not False:
        fails += 1
        print("  !! expected invalid part YAML to FAIL part_valid")
    shutil.rmtree(bad_part, ignore_errors=True)

    # 5. a CLI-answer candidate missing the expected invocation must FAIL cli_ok
    bad_cli = Path(tempfile.mkdtemp(prefix="eval_bad_cli_"))
    (bad_cli / ".eval_response.txt").write_text("Your yield looks healthy this week.\n")
    g = grade(bad_cli, BY_ID["analysis_yield_this_week"])
    print(g.summary())
    if g.cli_ok is not False:
        fails += 1
        print("  !! expected prose-only answer to FAIL cli_ok")
    shutil.rmtree(bad_cli, ignore_errors=True)

    print()
    print("SELF-TEST", "PASSED" if fails == 0 else f"FAILED ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
