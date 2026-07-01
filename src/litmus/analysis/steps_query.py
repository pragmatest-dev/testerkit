"""Read-only query client over the runs DuckDB daemon's ``steps`` table.

Steps are populated by aggregating the unified per-run parquet on
ingest (see :mod:`litmus.data._runs_duckdb_daemon`). Queries hit the
precomputed table for constant-cost lookups regardless of file count.

Pairs with :class:`MeasurementsQuery` (raw measurement view) and
:class:`RunsQuery` (run-level summaries) — same daemon, same Flight
client, different storage shape.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from litmus.analysis.measurement_facets import ColumnSchema, FixedColumnDescriptor
from litmus.data import runs_duckdb_manager
from litmus.data._flight_query import FlightQueryClient
from litmus.data._sql_helpers import multi_filter_clauses, sql_escape
from litmus.data.backends._row_helpers import _decode_dynamic_attrs_map
from litmus.data.data_dir import resolve_data_dir

logger = logging.getLogger(__name__)


class StepRow(BaseModel):
    """One row from the ``steps`` table — full denormalized run + step context.

    Mirrors the columns the daemon's ``steps`` table carries (see
    ``_rebuild_schema``). Field names match the daemon's column names
    so callers can construct via ``StepRow(**dict_row)`` from
    ``_query_dicts`` output.
    """

    file_path: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    # Optional to tolerate the pre-RunStarted-correlation in-flight row
    # (see ``_row_helpers.py``'s placeholder branch); persisted/correlated
    # rows always carry 0+.
    site_index: int | None = None
    site_name: str | None = None
    step_index: int | None = None
    step_name: str | None = None
    step_path: str | None = None
    parent_path: str | None = None
    # vector_index: NULL for step rows; 0..N-1 for vector rows (own position
    # within the sweep). vector_outer_index: NULL at top level; the enclosing
    # outer (class) vector index for method steps nested inside a swept class.
    vector_index: int | None = None
    vector_outer_index: int | None = None
    outcome: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_s: float | None = None
    measurement_count: int | None = None
    # 0-based outer (item) retry — pytest-rerunfailures rerun count of this
    # step. ``0`` for the first attempt; ``N`` for the Nth rerun. Each
    # execution is its own row, so ``WHERE step_retry > 0`` finds reruns.
    step_retry: int | None = None
    markers: str | None = None
    uut_serial_number: str | None = None
    station_id: str | None = None
    # Per-vector commanded inputs (in_*) and recorded outputs (out_*) —
    # populated by the daemon by aggregating the unified parquet's
    # in_*/out_* dynamic columns into a single dict per (step, vector).
    # Empty for steps without any in_*/out_* dynamic columns.
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)


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

    Construct once and reuse — no explicit close needed::

        q = StepsQuery()
        rows = q.list_for_run("run-001-abc")
        tree = q.tree_for_run("run-001-abc")

    Or use as a context manager for deterministic cleanup::

        with StepsQuery() as q:
            rows = q.list_for_run("run-001-abc")
    """

    def __init__(self, *, _data_dir: Path | str | None = None) -> None:
        data_dir = resolve_data_dir(_data_dir)
        self._runs_dir = data_dir / "runs"
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

    def __enter__(self) -> StepsQuery:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_for_run(
        self,
        run_id: str,
        *,
        include_incomplete: bool = False,
    ) -> list[StepRow]:
        """Return every step row for a run, ordered by ``step_index``.

        Matches the run by id-prefix (8-char) so callers can pass
        either the full UUID or its short form. Each step row is
        enriched with per-vector ``inputs`` (in_*) and ``outputs``
        (out_*) collected from the unified parquet — for swept steps
        each vector has its own commanded inputs and recorded outputs.

        Args:
            run_id: UUID or 8-char prefix.
            include_incomplete: Default ``False`` — only finalized
                steps (``ended_at IS NOT NULL``). UI live views pass
                ``True`` to surface in-flight steps.
        """
        prefix = run_id[:8] if len(run_id) >= 8 else run_id
        ended_clause = "" if include_incomplete else "AND ended_at IS NOT NULL"
        rows = self._query_dicts(f"""
            SELECT *
            FROM steps
            WHERE run_id LIKE '{sql_escape(prefix)}%'
            {ended_clause}
            ORDER BY step_index, step_retry, vector_index
        """)
        step_rows: list[StepRow] = []
        for r in rows:
            sr = StepRow(**r)
            sr.inputs, sr.outputs = _decode_dynamic_attrs_map(r.get("dynamic_attrs"))
            step_rows.append(sr)
        return step_rows

    def pareto(
        self,
        *,
        top_n: int = 10,
        phase: str | list[str] | None = None,
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Failure pareto of failing steps grouped by ``step_path``.

        Cross-run aggregate: which test step name has the most
        failures across the matching set of runs. Same semantic as
        :meth:`MeasurementsQuery.pareto` but at step-level instead
        of measurement-level — useful when the operator wants to
        spot a flaky test rather than a flaky measurement within a
        test.

        ``failed_count`` includes ``failed`` + ``errored`` outcomes.
        Optional filters scope to part / phase / station / time
        window — same shape the rest of the metrics tabs use.

        Note: filters apply to the runs context (joined via
        ``run_id``), not to the steps directly, so a "production
        phase / part PN-100" view shows only the failures from
        runs matching those facets.
        """
        run_filters = ["runs.ended_at IS NOT NULL"]
        # Operator-facing filters — see
        # feedback_operator_facing_identifiers.md (universal rule:
        # match against ``station_hostname``, never ``station_id``).
        run_filters.extend(
            multi_filter_clauses(
                {
                    "runs.test_phase": phase,
                    "runs.uut_part_number": part,
                    "runs.station_hostname": station,
                }
            )
        )
        if since:
            run_filters.append(f"runs.started_at >= '{sql_escape(since)}'")
        if until:
            run_filters.append(f"runs.started_at <= '{sql_escape(until)}'")
        where = " AND ".join(run_filters)
        return self._query_dicts(f"""
            SELECT
                steps.step_path AS bucket,
                COUNT(*) FILTER (WHERE steps.outcome IN ('failed', 'errored')) AS failed_count,
                COUNT(*) AS total,
                CAST(
                    100.0 * COUNT(*) FILTER (WHERE steps.outcome IN ('failed', 'errored'))
                    / COUNT(*)
                    AS DOUBLE
                ) AS fail_rate_pct
            FROM steps
            JOIN runs USING (run_id)
            WHERE {where} AND steps.step_path IS NOT NULL
            GROUP BY steps.step_path
            HAVING failed_count > 0
            ORDER BY failed_count DESC, total DESC
            LIMIT {int(top_n)}
        """)

    def list_for_session(
        self,
        session_id: str,
        *,
        include_incomplete: bool = False,
    ) -> list[StepRow]:
        """Return every step row across every run sharing a ``session_id``.

        Used by multi-site timeline / Gantt views: a session spans N
        sibling runs (one per site), and the timeline needs them all.
        Ordered by ``site_index`` then ``step_index`` so each site's
        lane reads top-to-bottom.

        Default excludes in-flight rows; pass ``include_incomplete=True``
        for the live timeline view.
        """
        ended_clause = "" if include_incomplete else "AND ended_at IS NOT NULL"
        rows = self._query_dicts(f"""
            SELECT *
            FROM steps
            WHERE session_id = '{sql_escape(session_id)}'
            {ended_clause}
            ORDER BY site_index, step_index, step_retry, vector_index
        """)
        step_rows: list[StepRow] = []
        for r in rows:
            sr = StepRow(**r)
            sr.inputs, sr.outputs = _decode_dynamic_attrs_map(r.get("dynamic_attrs"))
            step_rows.append(sr)
        return step_rows

    def tree_for_run(self, run_id: str) -> list[StepNode]:
        """Return the step tree for a run, built from ``step_path``.

        Top-level paths (no ``/``) are roots. Children are appended
        under their parent path prefix. Order within each level
        matches ``step_index``.
        """
        rows = self.list_for_run(run_id)
        parent_anchor: dict[str, StepNode] = {}
        roots: list[StepNode] = []
        for row in rows:
            node = StepNode(step=row)
            path = row.step_path or row.step_name or ""
            # Anchor parent attachment to the first node at a path; every
            # execution (rerun / sweep variant) keeps its own node, so reruns
            # are never overwritten and lost.
            parent_anchor.setdefault(path, node)
            if "/" not in path:
                roots.append(node)
                continue
            parent_path = path.rsplit("/", 1)[0]
            parent = parent_anchor.get(parent_path)
            if parent is not None:
                parent.children.append(node)
            else:
                # Orphan — parent path didn't appear before child.
                # Treat as a root so it isn't lost.
                roots.append(node)
        return roots

    def describe_columns(self) -> ColumnSchema:
        """Return the ``steps`` table's column schema."""
        rows = self._query_dicts("DESCRIBE steps")
        fixed = [
            FixedColumnDescriptor(name=str(r["column_name"]), column_type=str(r["column_type"]))
            for r in rows
        ]
        return ColumnSchema(fixed=fixed, fields=[])
