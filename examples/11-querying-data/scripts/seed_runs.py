"""Seed a small varied dataset for the querying example.

Creates 8 runs across 3 DUTs and 2 stations with mixed outcomes —
enough variety that yield queries, DUT-history queries, and trend
analysis return meaningful results. Synthesized via LitmusClient
(the programmatic run-building API for external data movers).

Usage::

    cd examples/11-querying-data
    uv run python scripts/seed_runs.py
    # then:
    uv run python scripts/analyze.py
"""

from __future__ import annotations

import random
import time

from litmus.client import LitmusClient


def main() -> None:
    client = LitmusClient(data_dir="data")

    # DUTs of two part numbers, mixed serials
    dut_specs: list[tuple[str, str]] = [
        ("SN-001", "BUCK-3V3-RevA"),
        ("SN-002", "BUCK-3V3-RevA"),
        ("SN-003", "BUCK-5V0-RevB"),
        ("SN-001", "BUCK-3V3-RevA"),  # retest
        ("SN-004", "BUCK-3V3-RevA"),
        ("SN-002", "BUCK-3V3-RevA"),
        ("SN-005", "BUCK-5V0-RevB"),
        ("SN-003", "BUCK-5V0-RevB"),  # retest
    ]
    stations = ["bench_a", "bench_b"]

    n_runs = 0
    for i, (serial, part_no) in enumerate(dut_specs):
        station = stations[i % len(stations)]
        run = client.start_run(
            dut_serial=serial,
            dut_part_number=part_no,
            station_id=station,
            test_phase="production",
            operator="bench_tech",
        )

        # Rail voltage measurement — target value depends on part number;
        # add jitter so yield queries see real spread.
        target_v = 3.3 if "3V3" in part_no else 5.0
        with run.step("voltage_check") as step:
            v = target_v + random.gauss(0, 0.04)
            step.measure(
                "v_rail",
                v,
                units="V",
                low=target_v * 0.95,
                high=target_v * 1.05,
            )

        # Quiescent current — occasional FAIL above limit
        with run.step("quiescent_current") as step:
            i_q = abs(random.gauss(0.045, 0.012))
            step.measure(
                "i_q",
                i_q,
                units="A",
                low=0.0,
                high=0.060,
            )

        run.finish()
        n_runs += 1
        # Small gap so each parquet filename's timestamp differs — the
        # filename is ``{YYYYMMDDTHHMMSSZ}_{dut_serial}.parquet`` and
        # same-second runs of the same serial would otherwise collide.
        time.sleep(1.1)

    print(f"Seeded {n_runs} runs into examples/11-querying-data/data/")
    print("Now run:  uv run python scripts/analyze.py")


if __name__ == "__main__":
    main()
