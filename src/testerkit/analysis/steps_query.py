"""Read-only query client over the runs DuckDB daemon's ``steps`` table.

Steps are populated by aggregating the unified per-run parquet on
ingest (see :mod:`testerkit.data._runs_duckdb_daemon`). Queries hit the
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

from testerkit.analysis.measurement_facets import ColumnSchema, FixedColumnDescriptor
from testerkit.data import runs_duckdb_manager
from testerkit.data._flight_query import FlightQueryClient
from testerkit.data._sql_helpers import multi_filter_clauses, sql_escape
from testerkit.data.backends._row_helpers import _decode_io_maps
from testerkit.data.data_dir import resolve_data_dir

logger = logging.getLogger(__name__)

# Read-time inputs/outputs map, LEFT JOINed onto ``steps``/``step_vectors``
# (aliased ``s``) from the ``inputs``/``outputs`` tables (projection-
# normalization, 0.3.1 — replaces the stored, prefixed ``dynamic_attrs`` MAP).
# Grouped by the step's own FK coordinates INCLUDING ``step_retry`` (unlike
# the measurements-side join in ``run_store.get_measurements`` — a step's own
# grain has ``step_retry`` as part of its PK, so the join must too or two
# reruns of the same step fan out into each other's values).
#
# This join serves FINALIZED rows (whose inputs/outputs are in the tables). A
# LIVE run's rows aren't in the tables yet — for those the ``steps`` /
# ``step_vectors`` VIEW carries the inflight ``inputs_map`` / ``outputs_map``
# inline. ``_STEP_IO_SELECT`` COALESCEs the join (finalized) over the view
# column (live), so exactly one is non-empty per row.
_STEP_IO_VALUE_EXPR = """CASE value_type
                WHEN 'scalar:bool' THEN CASE WHEN value_bool THEN 'true' ELSE 'false' END
                WHEN 'scalar:int' THEN CAST(value_int AS VARCHAR)
                WHEN 'scalar:float' THEN CAST(value_double AS VARCHAR)
                WHEN 'scalar:datetime' THEN CAST(value_timestamp AS VARCHAR)
                WHEN 'list' THEN value_json
                WHEN 'dict' THEN value_json
                ELSE value_text
            END"""


def _step_io_join(alias: str, table: str) -> str:
    """LEFT JOIN clause aggregating ``table`` (``inputs``/``outputs``) into
    ``{alias}.io_map`` per (run_id, step_path, step_retry, vector_index,
    vector_outer_index), joined onto the ``s`` alias."""
    return f"""
        LEFT JOIN (
            SELECT run_id, step_path, step_retry, vector_index, vector_outer_index,
                   MAP(list(name), list({_STEP_IO_VALUE_EXPR})) AS io_map
            FROM {table}
            GROUP BY run_id, step_path, step_retry, vector_index, vector_outer_index
        ) AS {alias} ON {alias}.run_id = s.run_id
            AND {alias}.step_path = s.step_path
            AND {alias}.step_retry = s.step_retry
            AND {alias}.vector_index IS NOT DISTINCT FROM s.vector_index
            AND {alias}.vector_outer_index IS NOT DISTINCT FROM s.vector_outer_index
    """


_STEP_IO_JOINS = _step_io_join("inputs_agg", "inputs") + _step_io_join("outputs_agg", "outputs")

# SELECT list for step reads: every ``s`` column except the two view-carried
# maps (live side), replaced by a COALESCE that prefers the finalized join
# aggregate. The LEFT JOIN's ``io_map`` is NULL when it misses (finalized rows
# absent → live run) and a non-empty MAP when it hits (a GROUP BY over ≥1 input
# row always yields ≥1 entry), so COALESCE picks finalized-when-present, else
# the view's inflight map.
_STEP_IO_SELECT = (
    "s.* EXCLUDE (inputs_map, outputs_map), "
    "COALESCE(inputs_agg.io_map, s.inputs_map) AS inputs_map, "
    "COALESCE(outputs_agg.io_map, s.outputs_map) AS outputs_map"
)


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
    # Per-vector commanded inputs and recorded outputs — decoded from the
    # query-time inputs_map/outputs_map (see _STEP_IO_JOINS) into a dict per
    # (step, vector). Empty for steps with no inputs/outputs rows.
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)


class StepNode(BaseModel):
    """One node in a hierarchical step tree, built from ``step_path``.

    ``step_path`` uses ``/`` as the separator (e.g.
    ``power/output/voltage``) so the tree is constructed client-side
    by splitting on it. Roots are the top-level steps; leaves are the
    actual test steps.

    A node's identity is its step-summary row (``vector_index IS NULL``);
    its ``vectors`` are that step's own vector rows (``vector_index``
    0..N — a swept step's condition points, or a single row for a plain
    step), never siblings/children in the tree.
    """

    step: StepRow
    vectors: list[StepRow] = Field(default_factory=list)
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

    def _rows_from(self, sql: str) -> list[StepRow]:
        """Run ``sql`` and hydrate each dict row into a :class:`StepRow`.

        Shared by the logical-step (``steps``) and condition-point
        (``step_vectors``) reads — both surfaces share the row shape. ``sql``
        is expected to select from the table aliased ``s`` plus
        ``_STEP_IO_JOINS`` (``inputs_map``/``outputs_map``).
        """
        step_rows: list[StepRow] = []
        for r in self._query_dicts(sql):
            sr = StepRow(**r)
            sr.inputs, sr.outputs = _decode_io_maps(r.get("inputs_map"), r.get("outputs_map"))
            step_rows.append(sr)
        return step_rows

    def list_for_run(
        self,
        run_id: str,
        *,
        include_incomplete: bool = False,
    ) -> list[StepRow]:
        """Return the LOGICAL step rows for a run, ordered by ``step_index``.

        Logical steps only (``vector_index IS NULL``) — a swept step is
        ONE row here; its condition points live in ``step_vectors`` (see
        :meth:`list_vectors_for_run`) and are nested onto the step by
        :meth:`tree_for_run`. Matches the run by id-prefix (8-char) so
        callers can pass either the full UUID or its short form. Each row
        carries the step's own ``inputs``/``outputs``.

        Args:
            run_id: UUID or 8-char prefix.
            include_incomplete: Default ``False`` — only finalized
                steps (``ended_at IS NOT NULL``). UI live views pass
                ``True`` to surface in-flight steps.
        """
        prefix = run_id[:8] if len(run_id) >= 8 else run_id
        ended_clause = "" if include_incomplete else "AND s.ended_at IS NOT NULL"
        return self._rows_from(f"""
            SELECT {_STEP_IO_SELECT}
            FROM steps AS s
            {_STEP_IO_JOINS}
            WHERE s.run_id LIKE '{sql_escape(prefix)}%'
            {ended_clause}
            -- Logical-step grain: no vector dimension to order on (a step's own
            -- vector_index is NULL). Its condition points are ordered in
            -- list_vectors_for_run, which adds vector_outer_index, vector_index.
            ORDER BY s.step_index, s.step_retry
        """)

    def list_vectors_for_run(
        self,
        run_id: str,
        *,
        include_incomplete: bool = False,
    ) -> list[StepRow]:
        """Return the condition-point (vector) rows for a run.

        The sub-grain companion to :meth:`list_for_run`: one row per
        sweep variant / in-body iteration (``vector_index`` 0..N), each
        with its own commanded ``inputs`` and recorded ``outputs``.
        Ordered so each step's vectors read in execution order.
        """
        prefix = run_id[:8] if len(run_id) >= 8 else run_id
        ended_clause = "" if include_incomplete else "AND s.ended_at IS NOT NULL"
        return self._rows_from(f"""
            SELECT {_STEP_IO_SELECT}
            FROM step_vectors AS s
            {_STEP_IO_JOINS}
            WHERE s.run_id LIKE '{sql_escape(prefix)}%'
            {ended_clause}
            ORDER BY s.step_index, s.step_retry, s.vector_outer_index, s.vector_index
        """)

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
        ended_clause = "" if include_incomplete else "AND s.ended_at IS NOT NULL"
        return self._rows_from(f"""
            SELECT {_STEP_IO_SELECT}
            FROM steps AS s
            {_STEP_IO_JOINS}
            WHERE s.session_id = '{sql_escape(session_id)}'
            {ended_clause}
            ORDER BY s.site_index, s.step_index, s.step_retry, s.vector_index
        """)

    def tree_for_run(self, run_id: str) -> list[StepNode]:
        """Return the step tree for a run, built from ``step_path``.

        Top-level paths (no ``/``) are roots. Children are appended
        under their parent path prefix. Order within each level
        matches ``step_index``.

        A logical step is one node (from the ``steps`` grain); its
        condition points (from the ``step_vectors`` grain — ``vector_index``
        0..N, sharing the step's ``step_path``) are attached onto
        ``node.vectors`` rather than becoming siblings/children (which would
        show a swept step as N+1 same-named nodes). A step record is keyed by
        ``(step_path, step_retry, vector_outer_index)`` so a method run under
        each outer sweep iteration keeps its own node and gets its own vectors.
        """
        parent_anchor: dict[str, StepNode] = {}
        node_by_key: dict[tuple[str, int, int | None], StepNode] = {}
        roots: list[StepNode] = []
        for row in self.list_for_run(run_id):
            path = row.step_path or row.step_name or ""
            key = (path, row.step_retry or 0, row.vector_outer_index)
            node = StepNode(step=row)
            node_by_key[key] = node
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
        # Nest condition points under their step node, matched on the full
        # step-execution key (a vector's ``vector_outer_index`` routes it to
        # the right per-outer method record).
        for vec_row in self.list_vectors_for_run(run_id):
            path = vec_row.step_path or vec_row.step_name or ""
            node = node_by_key.get((path, vec_row.step_retry or 0, vec_row.vector_outer_index))
            if node is not None:
                node.vectors.append(vec_row)
        return roots

    def describe_columns(self) -> ColumnSchema:
        """Return the ``steps`` table's column schema."""
        rows = self._query_dicts("DESCRIBE steps")
        fixed = [
            FixedColumnDescriptor(name=str(r["column_name"]), column_type=str(r["column_type"]))
            for r in rows
        ]
        return ColumnSchema(fixed=fixed, fields=[])
