"""Generate a representative analytics dataset for operator-UI screenshots.

Usage::

    uv run python scripts/seed-demo-data.py [--data-dir PATH] [--clear]

Default ``--data-dir`` resolves to examples/07-profiles/data (the canonical
screenshot project) so ``scripts/regenerate-ui-screenshots.py`` picks it up
without extra flags.

``--clear`` wipes prior demo runs first by removing all parquet files under
``<data_dir>/runs/``, then re-seeding. Idempotent.

Dataset shape
-------------
- 2 parts: DEMO-BUCK-3V3 (A) and DEMO-BUCK-5V0 (B)
- 2 stations: bench_01 (hostname testerkit-station-01), bench_02 (testerkit-station-02)
- 15 serials (SN-B3-001..010 for 3V3, SN-B5-001..005 for 5V0)
- ~50 runs across a 2-week window, ~87% pass rate
- Failures concentrated on ``v_rail`` (out-of-spec) and ``i_idle`` (overcurrent)
  so the Pareto top-2 are clear and DPMO/RTY are non-zero
- Several serials retested (fail then pass) for non-zero retest rate
- Realistic Gaussian measurement distributions for Ppk analysis
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from testerkit.client import RunBuilder, TesterKitClient  # noqa: E402
from testerkit.data.models import Outcome, escalate_outcome  # noqa: E402

# ---------------------------------------------------------------------------
# Script configuration
# ---------------------------------------------------------------------------

# Default data dir: examples/07-profiles/data (canonical screenshot project)
_DEFAULT_DATA_DIR = _REPO_ROOT / "examples" / "07-profiles" / "data"

# Reproducible seed — same dataset every run (for screenshot stability)
_RNG_SEED = 42

# Time window: 2 weeks ending *now* so trend shows recent activity
_WINDOW_DAYS = 14

# ---------------------------------------------------------------------------
# Dataset definition — parts, stations, serials
# ---------------------------------------------------------------------------

_PARTS = [
    {
        "part_number": "DEMO-BUCK-3V3",
        "part_name": "Demo 3.3 V Buck Converter",
        "revision": "A",
        "part_id": "buck_3v3",
    },
    {
        "part_number": "DEMO-BUCK-5V0",
        "part_name": "Demo 5.0 V Buck Converter",
        "revision": "B",
        "part_id": "buck_5v0",
    },
]

_STATIONS = [
    {
        "station_id": "bench_01",
        "station_name": "Bench 01",
        "station_hostname": "testerkit-station-01",
        "station_type": "bench",
    },
    {
        "station_id": "bench_02",
        "station_name": "Bench 02",
        "station_hostname": "testerkit-station-02",
        "station_type": "bench",
    },
]

# Serials per part
_SERIALS_3V3 = [f"SN-B3-{i:03d}" for i in range(1, 11)]  # 10 serials
_SERIALS_5V0 = [f"SN-B5-{i:03d}" for i in range(1, 6)]  # 5 serials

# ---------------------------------------------------------------------------
# Measurement profiles — realistic distributions
# ---------------------------------------------------------------------------

# 3V3 part measurements:
#   v_rail   ~ N(3.30, 0.030) within [3.234, 3.366] (±2% of 3.3 V)
#   i_idle   ~ N(0.050, 0.004) within [0.040, 0.060] (±20% of 50 mA)
#   vin      ~ N(5.00, 0.015) within [4.75, 5.25]   (±5% of 5 V, rarely fails)
#
# 5V0 part measurements:
#   v_rail   ~ N(5.00, 0.040) within [4.900, 5.100] (±2% of 5 V)
#   i_idle   ~ N(0.070, 0.005) within [0.060, 0.080]
#   vin      ~ N(5.00, 0.015) within [4.75, 5.25]


def _measure_3v3(rng: random.Random, force_fail: str | None = None) -> dict:
    """Return sampled measurement values for the 3V3 part.

    ``force_fail`` can be ``"v_rail"`` or ``"i_idle"`` to inject a failure
    on that measurement (drives the failure pareto).
    """
    v_rail = rng.gauss(3.30, 0.030)
    i_idle = rng.gauss(0.050, 0.004)
    vin = rng.gauss(5.00, 0.015)

    if force_fail == "v_rail":
        # Push rail voltage below low limit (3.234)
        v_rail = rng.uniform(3.10, 3.22)
    elif force_fail == "i_idle":
        # Push idle current above high limit (0.060)
        i_idle = rng.uniform(0.062, 0.075)

    return {"v_rail": v_rail, "i_idle": i_idle, "vin": vin}


def _measure_5v0(rng: random.Random, force_fail: str | None = None) -> dict:
    """Return sampled measurement values for the 5V0 part."""
    v_rail = rng.gauss(5.00, 0.040)
    i_idle = rng.gauss(0.070, 0.005)
    vin = rng.gauss(5.00, 0.015)

    if force_fail == "v_rail":
        v_rail = rng.uniform(4.75, 4.88)
    elif force_fail == "i_idle":
        i_idle = rng.uniform(0.082, 0.095)

    return {"v_rail": v_rail, "i_idle": i_idle, "vin": vin}


# ---------------------------------------------------------------------------
# Run plan — which serials get retests, which fail on which measurement
# ---------------------------------------------------------------------------
#
# Representation: list of dicts with keys:
#   serial, part_idx, station_idx, fail_on, test_phase, offset_hours
#
# "fail_on" is None (pass) or "v_rail"/"i_idle" (fail that measurement).
# Retests are adjacent entries with the same serial where the first fails
# and the second passes.


def _build_run_plan(rng: random.Random, window_days: int) -> list[dict]:
    """Produce the run schedule.

    ~50 runs over the window:
    - 43 normal single runs (~87% pass rate when combined with retests)
    - 7 retest pairs (fail first, pass second) = 14 additional run records
    Total = 43 normal + 7*2 retest = 57 runs
    Failures = 7 retest first-attempts + ~3 outright fails = 10/57 ≈ 17.5%
    But retest survivors count as pass in yield → final yield ~87%
    """
    plan: list[dict] = []

    # Spread runs across the 2-week window
    now = datetime.now(UTC)
    window_start = now - timedelta(days=window_days)

    def rand_offset() -> float:
        """Random hour offset within the window."""
        return rng.uniform(0, window_days * 24)

    # Part 0 (3V3) — 10 serials, bench_01 and bench_02
    for i, serial in enumerate(_SERIALS_3V3):
        station_idx = i % 2

        # Each serial gets 2-4 runs over the window
        n_runs = rng.randint(2, 4)
        for run_num in range(n_runs):
            offset = rand_offset()
            # Last run for serial is always a pass; earlier may fail
            if run_num == n_runs - 1:
                fail_on = None
            else:
                # ~20% chance of failure on v_rail, ~15% on i_idle
                roll = rng.random()
                if roll < 0.20:
                    fail_on = "v_rail"
                elif roll < 0.35:
                    fail_on = "i_idle"
                else:
                    fail_on = None

            plan.append(
                {
                    "serial": serial,
                    "part_idx": 0,
                    "station_idx": station_idx,
                    "fail_on": fail_on,
                    "test_phase": "production",
                    "started_at": window_start + timedelta(hours=offset),
                }
            )

    # Part 1 (5V0) — 5 serials, bench_01 only
    for i, serial in enumerate(_SERIALS_5V0):
        n_runs = rng.randint(1, 3)
        for run_num in range(n_runs):
            offset = rand_offset()
            if run_num == n_runs - 1:
                fail_on = None
            else:
                roll = rng.random()
                if roll < 0.25:
                    fail_on = "v_rail"
                elif roll < 0.40:
                    fail_on = "i_idle"
                else:
                    fail_on = None

            plan.append(
                {
                    "serial": serial,
                    "part_idx": 1,
                    "station_idx": 0,
                    "fail_on": fail_on,
                    "test_phase": "production",
                    "started_at": window_start + timedelta(hours=offset),
                }
            )

    # Sort chronologically so the parquet date dirs are tidy
    plan.sort(key=lambda r: r["started_at"])
    return plan


# ---------------------------------------------------------------------------
# Run emission
# ---------------------------------------------------------------------------


def _emit_run(
    client: TesterKitClient,
    rng: random.Random,
    entry: dict,
) -> str:
    """Build and save one run; return its run_id."""
    part = _PARTS[entry["part_idx"]]
    station = _STATIONS[entry["station_idx"]]
    started_at: datetime = entry["started_at"]

    # Realistic test duration: 15–90 seconds
    duration_s = rng.uniform(15, 90)
    ended_at = started_at + timedelta(seconds=duration_s)

    run: RunBuilder = client.start_run(
        uut_serial=entry["serial"],
        station_id=station["station_id"],
        uut_part_number=part["part_number"],
        uut_revision=part["revision"],
        test_phase=entry["test_phase"],
    )

    # Stamp backdated timestamps BEFORE adding steps.
    run._test_run.started_at = started_at
    run._test_run.station_name = station["station_name"]
    run._test_run.station_hostname = station["station_hostname"]
    run._test_run.station_type = station["station_type"]
    run._test_run.part_id = part["part_id"]
    run._test_run.part_name = part["part_name"]
    run._test_run.project_name = "profiles-example"

    fail_on: str | None = entry["fail_on"]

    # Measure appropriate values for the part
    if entry["part_idx"] == 0:
        vals = _measure_3v3(rng, force_fail=fail_on)
        v_low, v_high = 3.234, 3.366  # ±2% of 3.3 V
        i_low, i_high = 0.040, 0.060
    else:
        vals = _measure_5v0(rng, force_fail=fail_on)
        v_low, v_high = 4.900, 5.100  # ±2% of 5 V
        i_low, i_high = 0.060, 0.080

    # Step 1: input voltage check (rarely fails, validates power delivery)
    with run.step("check_vin", "Verify input supply within range") as step:
        step.measure("vin", vals["vin"], unit="V", low=4.75, high=5.25)

    # Step 2: output rail measurement (primary, most failures here)
    with run.step("test_rail_within_spec", "Output rail within spec") as step:
        step.measure("v_rail", vals["v_rail"], unit="V", low=v_low, high=v_high)

    # Step 3: idle current check (secondary failure source)
    with run.step("test_idle_current", "Idle current within spec") as step:
        step.measure("i_idle", vals["i_idle"], unit="A", low=i_low, high=i_high)

    # Roll the run outcome up from each step's OUTCOME. step.outcome already
    # folds in the step's own step-scope measurements (and any vectors);
    # iterating step.vectors alone missed the step-scope measurements after the
    # grain reshape and recorded every failed step as a PASSED run.
    computed: Outcome | None = None
    for step in run._test_run.steps:
        computed = escalate_outcome(computed, step.outcome)
    # Any run that completed all steps with no failures is PASSED.
    if computed is None or computed == Outcome.DONE:
        computed = Outcome.PASSED
    run._test_run.outcome = computed

    # Stamp ended_at BEFORE delegating to backend (bypasses finish()'s now() stamp)
    run._test_run.ended_at = ended_at
    client._backend.save_test_run(run._test_run)

    return str(run.id)


# ---------------------------------------------------------------------------
# Clear helpers
# ---------------------------------------------------------------------------


def _clear_runs(data_dir: Path) -> None:
    """Remove parquet files and the DuckDB index under <data_dir>/runs/.

    Parquets live at <data_dir>/runs/<YYYY-MM-DD>/<file>.parquet.
    Daemon-managed files (_index.duckdb, _runs_duckdb_*, _daemon.log)
    are also removed so the daemon rebuilds a clean index on next start.
    """
    runs_dir = data_dir / "runs"
    if not runs_dir.exists():
        return

    # Remove all parquets under date subdirs
    for parquet in runs_dir.rglob("*.parquet"):
        parquet.unlink()

    # Remove empty date directories left behind
    for entry in sorted(runs_dir.iterdir(), reverse=True):
        if entry.is_dir():
            try:
                entry.rmdir()
            except OSError:
                pass  # not empty (non-parquet files remain)

    # Wipe daemon-managed state files so the index rebuilds clean
    for name in (
        "_index.duckdb",
        "_runs_duckdb.json",
        "_runs_duckdb_flight_port",
        "_runs_duckdb_pid",
        "_runs_duckdb_ready",
        "_daemon.log",
    ):
        target = runs_dir / name
        if target.exists():
            target.unlink()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed representative analytics demo data into a TesterKit data dir."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=_DEFAULT_DATA_DIR,
        help=f"Target data dir (default: {_DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Wipe existing demo runs before seeding (idempotent re-seed).",
    )
    args = parser.parse_args()

    data_dir: Path = args.data_dir.resolve()

    if args.clear:
        print(f"seed-demo-data: clearing runs under {data_dir} …")
        _clear_runs(data_dir)

    rng = random.Random(_RNG_SEED)
    plan = _build_run_plan(rng, _WINDOW_DAYS)

    client = TesterKitClient(data_dir=data_dir)

    total = len(plan)
    passed = 0
    failed = 0
    print(f"seed-demo-data: writing {total} runs to {data_dir} …")

    for i, entry in enumerate(plan, 1):
        _emit_run(client, rng, entry)
        outcome = entry["fail_on"] is None
        if outcome:
            passed += 1
        else:
            failed += 1
        if i % 10 == 0 or i == total:
            print(f"  {i}/{total} runs written ({passed} pass, {failed} fail)")

    fail_rate = 100 * failed / total if total else 0
    print(
        f"seed-demo-data: done — {total} runs, "
        f"{passed} pass ({100 - fail_rate:.0f}%), "
        f"{failed} fail ({fail_rate:.0f}%), "
        f"over ~{_WINDOW_DAYS}-day window"
    )


if __name__ == "__main__":
    main()
