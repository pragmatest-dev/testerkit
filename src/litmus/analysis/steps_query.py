"""Read-only query client over the runs DuckDB daemon's ``steps`` table.

Steps are populated incrementally from ``_steps.parquet`` sidecars at
ingest (see :mod:`litmus.data._runs_duckdb_daemon`). Queries hit the
precomputed table for constant-cost lookups regardless of file count.

Pairs with :class:`MeasurementsQuery` (raw measurement view) and
:class:`RunsQuery` (run-level summaries) — same daemon, same Flight
client, different storage shape.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from litmus.data import runs_duckdb_manager
from litmus.data._flight_query import FlightQueryClient
from litmus.data._sql_helpers import sql_escape
from litmus.data.results_dir import resolve_results_dir


class StepRow(BaseModel):
    """One row from the ``steps`` table — full denormalized run + step context.

    Mirrors the columns the daemon's ``steps`` table carries (see
    ``_rebuild_schema``). Field names match the daemon's column names
    so callers can construct via ``StepRow(**dict_row)`` from
    ``_query_dicts`` output.
    """

    file_path: str
    run_id: str | None = None
    session_id: str | None = None
    slot_id: str | None = None
    step_index: int | None = None
    step_name: str | None = None
    step_path: str | None = None
    outcome: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_s: float | None = None
    has_measurements: bool | None = None
    measurement_count: int | None = None
    vector_count: int | None = None
    markers: str | None = None
    dut_serial: str | None = None
    station_id: str | None = None


class StepNode(BaseModel):
    """One node in a hierarchical step tree, built from ``step_path``.

    ``step_path`` uses ``/`` as the separator (e.g.
    ``power/output/voltage``) so the tree is constructed client-side
    by splitting on it. Roots are the top-level steps; leaves are the
    actual test steps.
    """

    step: StepRow
    children: list[StepNode] = Field(default_factory=list)


class StepsQuery:
    """Read-only client over the runs daemon's ``steps`` table.

    Usage::

        q = StepsQuery()
        rows = q.list_for_run("run-001-abc")
        tree = q.tree_for_run("run-001-abc")
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
            label="StepsQuery",
        )

    def _query_dicts(self, sql: str) -> list[dict[str, Any]]:
        return self._flight.query(sql)

    def close(self) -> None:
        """Release daemon ref and close Flight client."""
        self._flight.close()
        runs_duckdb_manager.release(self._runs_dir)

    def list_for_run(self, run_id: str) -> list[StepRow]:
        """Return every step row for a run, ordered by ``step_index``.

        Matches the run by id-prefix (8-char) so callers can pass
        either the full UUID or its short form.
        """
        prefix = run_id[:8] if len(run_id) >= 8 else run_id
        rows = self._query_dicts(f"""
            SELECT *
            FROM steps
            WHERE run_id LIKE '{sql_escape(prefix)}%'
            ORDER BY step_index
        """)
        return [StepRow(**r) for r in rows]

    def list_for_session(self, session_id: str) -> list[StepRow]:
        """Return every step row across every run sharing a ``session_id``.

        Used by multi-slot timeline / Gantt views: a session spans N
        sibling runs (one per slot), and the timeline needs them all.
        Ordered by ``slot_id`` then ``step_index`` so each slot's
        lane reads top-to-bottom.
        """
        rows = self._query_dicts(f"""
            SELECT *
            FROM steps
            WHERE session_id = '{sql_escape(session_id)}'
            ORDER BY slot_id, step_index
        """)
        return [StepRow(**r) for r in rows]

    def tree_for_run(self, run_id: str) -> list[StepNode]:
        """Return the step tree for a run, built from ``step_path``.

        Top-level paths (no ``/``) are roots. Children are appended
        under their parent path prefix. Order within each level
        matches ``step_index``.
        """
        rows = self.list_for_run(run_id)
        nodes_by_path: dict[str, StepNode] = {}
        roots: list[StepNode] = []
        for row in rows:
            node = StepNode(step=row)
            path = row.step_path or row.step_name or ""
            nodes_by_path[path] = node
            if "/" not in path:
                roots.append(node)
                continue
            parent_path = path.rsplit("/", 1)[0]
            parent = nodes_by_path.get(parent_path)
            if parent is not None:
                parent.children.append(node)
            else:
                # Orphan — parent path didn't appear before child.
                # Treat as a root so it isn't lost.
                roots.append(node)
        return roots

    def describe_columns(self) -> list[dict[str, str]]:
        """Return the ``steps`` table's columns: ``[{name, type}, ...]``."""
        return self._query_dicts("DESCRIBE steps")
