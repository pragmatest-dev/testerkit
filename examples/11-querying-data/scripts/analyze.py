"""Consumer-side data discovery and analysis.

The "I produced the data, now what?" half of the story. Demonstrates
the public Query API surfaces a data analyst (or external tool, or
custom UI) would reach for:

* ``RunsQuery`` — list recent runs, filter by outcome / UUT / station
* ``MeasurementsQuery`` — yield summary, distinct UUT serials,
  parametric queries
* ``EventStore`` — replay the timeline of events for a run

Pairs with the operator UI's discovery pages (``/runs``, ``/metrics``,
``/events``, ``/channels``, ``/files``) — the UI and the Query API
read through the same primitives. UI gives clickable navigation; the
Query API gives programmatic access for analysts and external tools.

Run ``scripts/seed_runs.py`` first to populate ``data/``. The runs
daemon ingests parquet files asynchronously — if a query returns
empty immediately after seeding, wait a few seconds and re-run.
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from testerkit.queries import EventStore, MeasurementsQuery, RunsQuery


def _hr() -> None:
    print("-" * 72)


def main() -> None:
    data_dir = Path("data")
    if not data_dir.exists():
        raise SystemExit("No data/ directory — run scripts/seed_runs.py first.")

    print("TesterKit data discovery — querying through the public API")
    _hr()

    # Poll the runs daemon for ingest completion. After seeding, the
    # daemon ingests parquet files asynchronously — typically <10 s
    # for a handful, but it depends on daemon startup state. In
    # production, the daemon is already running and ingest is
    # sub-second; this poll is just for the freshly-seeded case.
    deadline = time.monotonic() + 30
    expected = len(list(data_dir.glob("runs/**/*.parquet")))
    while time.monotonic() < deadline:
        with RunsQuery(_data_dir=data_dir) as runs_q:
            if len(runs_q.list_recent(limit=expected + 5)) >= expected:
                break
        time.sleep(1.0)

    # 1. Recent runs — what's in this project?
    with RunsQuery(_data_dir=data_dir) as runs_q:
        runs = runs_q.list_recent(limit=20)
        print(f"\n{len(runs)} recent runs (most recent first):")
        for r in runs[:8]:
            outcome = r.outcome or "in_flight"
            started = r.started_at.isoformat(timespec="seconds") if r.started_at else "?"
            print(
                f"  {r.uut_serial_number or '-':<10}  {r.uut_part_number or '-':<18}  "
                f"{r.station_id or '-':<10}  {outcome:<8}  started {started}"
            )

        # 2. Filter by UUT — every run on SN-001
        target_serial = "SN-001"
        sn_runs = [r for r in runs if r.uut_serial_number == target_serial]
        print(f"\nRuns for UUT {target_serial}: {len(sn_runs)}")
        for r in sn_runs:
            run_short = (r.run_id or "?")[:8]
            started = r.started_at.isoformat(timespec="seconds") if r.started_at else "?"
            print(f"  {run_short}  {r.outcome}  at {started}")

        # 3. Outcome counts across all runs
        counts = Counter(r.outcome for r in runs)
        print("\nOutcome distribution across recent runs:")
        for outcome, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {outcome or 'in_flight':<10}  {n:>3}")

    # 4. Yield summary — one row per (part, station, phase, period)
    _hr()
    with MeasurementsQuery(_data_dir=data_dir) as m_q:
        try:
            yield_rows = m_q.yield_summary()
            print(f"\nYield summary — {len(yield_rows)} (part, station, phase) groupings:")
            for row in yield_rows[:8]:
                part = row.part
                station = row.station
                total = row.total_runs
                passed = row.passed
                pct = (passed / total * 100) if total else 0
                print(f"  {part:<18}  {station:<10}  {passed:>3}/{total:<3} passed  ({pct:5.1f} %)")
        except Exception as exc:  # noqa: BLE001
            print(f"\nyield_summary() unavailable for this dataset: {exc}")

        # 5. Distinct UUTs seen — pure observability
        try:
            uuts = m_q.distinct_values("uut_serial_number")
            values = sorted(d.value for d in uuts if d.value is not None)
            print(f"\nDistinct uut_serial_number values: {values}")
        except Exception as exc:  # noqa: BLE001
            print(f"distinct_values('uut_serial_number') unavailable: {exc}")

    # 6. EventStore — timeline replay
    _hr()
    print("\nEvent timeline (latest 10 lifecycle events):")
    event_store = EventStore(_data_dir=data_dir)
    try:
        events = event_store.events(limit=10)
        for evt in events[-10:]:
            event_type = evt.get("event_type", "?")
            session = (evt.get("session_id") or "")[:8]
            ts = evt.get("received_at", "")
            print(f"  {ts}  {event_type:<28}  session={session}")
    finally:
        event_store.close()

    _hr()
    print("\nUI counterpart for these queries:")
    print("  uv run testerkit serve --reload")
    print("  http://localhost:8000/runs       — same RunsQuery, clickable")
    print("  http://localhost:8000/metrics    — yield + Pareto + Cpk")
    print("  http://localhost:8000/measurements — full parametric query")
    print("  http://localhost:8000/events     — timeline replay")


if __name__ == "__main__":
    main()
