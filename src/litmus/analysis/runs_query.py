"""Read-only query client over the runs DuckDB daemon's ``runs`` table.

Runs are populated incrementally from ``_steps.parquet`` sidecars at
ingest (the same source ``StepsQuery`` reads). Queries hit the
precomputed table for constant-cost lookups regardless of file count.

Pairs with :class:`StepsQuery` (per-step view) and
:class:`MeasurementsQuery` (raw measurement view) — same daemon,
same Flight client, different storage shape.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from litmus.data import runs_duckdb_manager
from litmus.data._flight_query import FlightQueryClient
from litmus.data._sql_helpers import sql_escape
from litmus.data.results_dir import resolve_results_dir


class RunRow(BaseModel):
    """One row from the ``runs`` table — denormalized run-level summary.

    Mirrors the daemon's ``runs`` table columns (see ``_rebuild_schema``).
    Field names match column names so ``RunRow(**dict_row)`` works
    directly off ``_query_dicts`` output.
    """

    file_path: str | None = None
    steps_file_path: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    dut_serial: str | None = None
    dut_part_number: str | None = None
    station_id: str | None = None
    station_name: str | None = None
    outcome: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    num_measurements: int | None = None
    num_steps: int | None = None
    test_phase: str | None = None
    product_id: str | None = None
    operator_id: str | None = None
    project_name: str | None = None


class RunsQuery:
    """Read-only client over the runs daemon's ``runs`` table.

    Usage::

        q = RunsQuery()
        recent = q.list_recent(limit=20)
        run = q.get("run-001-abc")
        q.close()
    """

    def __init__(self, *, _results_dir: Path | str | None = None) -> None:
        results_dir = resolve_results_dir(_results_dir)
        self._runs_dir = results_dir / "runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)

        location = runs_duckdb_manager.acquire(self._runs_dir)
        self._flight = FlightQueryClient(
            location,
            "runs",
            reacquire=lambda: runs_duckdb_manager.acquire(self._runs_dir),
            label="RunsQuery",
        )

    def _query_dicts(self, sql: str) -> list[dict[str, Any]]:
        return self._flight.query(sql)

    def close(self) -> None:
        """Release daemon ref and close Flight client."""
        self._flight.close()
        runs_duckdb_manager.release(self._runs_dir)

    def list_recent(self, limit: int = 50) -> list[RunRow]:
        """Return the ``limit`` most recent runs, most recent first."""
        rows = self._query_dicts(f"""
            SELECT *
            FROM runs
            ORDER BY started_at DESC
            LIMIT {int(limit)}
        """)
        return [RunRow(**r) for r in rows]

    def get(self, run_id: str) -> RunRow | None:
        """Return one run by id-prefix (8-char) or ``None`` if not found."""
        prefix = run_id[:8] if len(run_id) >= 8 else run_id
        rows = self._query_dicts(f"""
            SELECT *
            FROM runs
            WHERE run_id LIKE '{sql_escape(prefix)}%'
            LIMIT 1
        """)
        return RunRow(**rows[0]) if rows else None

    def find_for_session(self, session_id: str) -> list[RunRow]:
        """Return all runs sharing a ``session_id`` (multi-DUT siblings)."""
        rows = self._query_dicts(f"""
            SELECT *
            FROM runs
            WHERE session_id = '{sql_escape(session_id)}'
            ORDER BY started_at
        """)
        return [RunRow(**r) for r in rows]

    def count_by_outcome(self) -> dict[str, int]:
        """Return ``{outcome: count}`` over all runs."""
        rows = self._query_dicts("""
            SELECT outcome, COUNT(*) AS n
            FROM runs
            GROUP BY outcome
        """)
        return {(r["outcome"] or "unknown"): r["n"] for r in rows}

    def describe_columns(self) -> list[dict[str, str]]:
        """Return the ``runs`` table's columns: ``[{name, type}, ...]``."""
        return self._query_dicts("DESCRIBE runs")
