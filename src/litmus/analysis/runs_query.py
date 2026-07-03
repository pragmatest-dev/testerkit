"""Read-only query client over the runs DuckDB daemon's ``runs`` table.

Runs are populated by aggregating the unified per-run parquet on
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

from litmus.analysis.measurement_facets import ColumnSchema, FixedColumnDescriptor
from litmus.data import runs_duckdb_manager
from litmus.data._flight_query import FlightQueryClient
from litmus.data._sql_helpers import multi_filter_clauses, sql_escape
from litmus.data.data_dir import resolve_data_dir

# Operator-facing group-by dimensions only — internal IDs like
# ``station_id`` / ``part_id`` are not exposed.
# See feedback_operator_facing_identifiers.md.
_VALID_PARETO_GROUP_BY = frozenset(
    {
        "uut_part_number",
        "station_hostname",
        "operator_id",
        "test_phase",
        "fixture_id",
    }
)

# Group-by dimensions for usage_stats — broader than the pareto set:
# asset/utilization reporting also allows the internal ``station_id`` and
# ``project_name``.
_VALID_USAGE_STATS_COLUMNS = frozenset(
    {
        "uut_part_number",
        "station_hostname",
        "station_id",
        "fixture_id",
        "test_phase",
        "operator_id",
        "project_name",
    }
)


class RunRow(BaseModel):
    """One row from the ``runs`` table — denormalized run-level summary.

    Mirrors the daemon's ``runs`` table columns (see ``_rebuild_schema``).
    Field names match column names so ``RunRow(**dict_row)`` works
    directly off ``_query_dicts`` output.
    """

    file_path: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    site_index: int | None = None
    site_name: str | None = None
    uut_serial_number: str | None = None
    uut_part_number: str | None = None
    uut_revision: str | None = None
    uut_lot_number: str | None = None
    station_id: str | None = None
    station_name: str | None = None
    station_hostname: str | None = None
    fixture_id: str | None = None
    outcome: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    num_measurements: int | None = None
    num_steps: int | None = None
    test_phase: str | None = None
    part_id: str | None = None
    part_name: str | None = None
    part_revision: str | None = None
    operator_id: str | None = None
    operator_name: str | None = None
    project_name: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    git_commit: str | None = None
    git_branch: str | None = None
    git_remote: str | None = None
    python_version: str | None = None
    litmus_version: str | None = None
    env_fingerprint: str | None = None


class RunsQuery:
    """Read-only client over the runs daemon's ``runs`` table.

    Construct once and reuse — no explicit close needed::

        q = RunsQuery()
        recent = q.list_recent(limit=20)
        run = q.get("run-001-abc")

    Or use as a context manager for deterministic cleanup::

        with RunsQuery() as q:
            recent = q.list_recent(limit=20)
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
            label="RunsQuery",
        )

    def _query_dicts(self, sql: str) -> list[dict[str, Any]]:
        return self._flight.query(sql)

    def close(self) -> None:
        """Release daemon ref and close Flight client."""
        self._flight.close()
        runs_duckdb_manager.release(self._runs_dir)

    def __enter__(self) -> RunsQuery:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def list_recent(
        self,
        limit: int = 50,
        *,
        offset: int = 0,
        include_incomplete: bool = False,
        phase: str | list[str] | None = None,
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        lot: str | list[str] | None = None,
        outcome: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[RunRow]:
        """Return one page of recent runs, most recent first.

        Args:
            limit: Max rows in the page.
            offset: Skip this many rows before returning ``limit``.
                ``offset = (page - 1) * rows_per_page`` for the
                Quasar server-side-pagination pattern.
            include_incomplete: Default ``False`` — only finalized
                runs (``ended_at IS NOT NULL``). UI list pages that
                surface in-flight runs pass ``True``.
            phase / part / station / lot / outcome: Multi-value
                filters. ``str`` collapses to ``=``, ``list`` to
                ``IN (…)``. ``None`` / empty contributes nothing.
                ``part`` filters by ``uut_part_number`` (operator-
                facing); ``station`` by ``station_hostname``; ``lot``
                by ``uut_lot_number``.
            since / until: ISO date or datetime strings. Filter
                ``started_at`` to ``[since, until]`` (inclusive).
                ``None`` / empty contributes nothing.
        """
        clauses: list[str] = []
        if not include_incomplete:
            clauses.append("ended_at IS NOT NULL")
        clauses.extend(
            multi_filter_clauses(
                {
                    "test_phase": phase,
                    "uut_part_number": part,
                    "station_hostname": station,
                    "uut_lot_number": lot,
                    "outcome": outcome,
                }
            )
        )
        if since:
            clauses.append(f"started_at >= '{sql_escape(since)}'")
        if until:
            clauses.append(f"started_at <= '{sql_escape(until)}'")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._query_dicts(f"""
            SELECT *
            FROM runs
            {where}
            ORDER BY started_at DESC
            LIMIT {int(limit)} OFFSET {int(offset)}
        """)
        return [RunRow(**r) for r in rows]

    def get(self, run_id: str) -> RunRow | None:
        """Return one run by id-prefix (8-char) or ``None`` if not found.

        Returns whatever's in the table — including in-flight rows.
        Single-id lookup; the caller asked for this specific run.
        """
        prefix = run_id[:8] if len(run_id) >= 8 else run_id
        rows = self._query_dicts(f"""
            SELECT *
            FROM runs
            WHERE run_id LIKE '{sql_escape(prefix)}%'
            LIMIT 1
        """)
        return RunRow(**rows[0]) if rows else None

    def list_for_session(
        self,
        session_id: str,
        *,
        include_incomplete: bool = False,
    ) -> list[RunRow]:
        """Return all runs sharing a ``session_id`` (multi-UUT siblings).

        Default excludes in-flight rows; pass ``include_incomplete=True``
        to surface running peers (e.g. live multi-site view).
        """
        ended_clause = "" if include_incomplete else "AND ended_at IS NOT NULL"
        rows = self._query_dicts(f"""
            SELECT *
            FROM runs
            WHERE session_id = '{sql_escape(session_id)}'
            {ended_clause}
            ORDER BY started_at
        """)
        return [RunRow(**r) for r in rows]

    def pareto(
        self,
        *,
        group_by: str = "uut_part_number",
        top_n: int = 10,
        phase: str | list[str] | None = None,
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """Pareto of failing runs grouped by ``group_by`` column.

        Answers "where are the failures concentrated?" at the run
        level — most-failing parts, most-failing stations, etc.
        Default groups by ``uut_part_number`` (a.k.a. part) since
        that's the most useful pareto for an operator: which part
        SKU is hurting yield.

        ``failed_count`` includes both ``failed`` and ``errored``
        outcomes — both mean the run did not pass. ``terminated`` /
        ``aborted`` are excluded (operator stops, not part
        failures). Sorted by ``failed_count`` descending; ties broken
        by ``total`` descending so a low-volume group with 100% fail
        rate doesn't outrank a high-volume group with the same fail
        count.

        Args:
            group_by: Column to group by — ``uut_part_number``
                (part), ``station_hostname``, ``operator_id``,
                ``test_phase``, ``fixture_id``. Validated against an
                allowlist; bad values raise ``ValueError``.
            top_n: Max rows.
            phase / part / station / since / until: Filter the
                run set before grouping. Same semantics as the
                ``MeasurementsQuery`` filters.
        """
        if group_by not in _VALID_PARETO_GROUP_BY:
            raise ValueError(
                f"Invalid group_by={group_by!r}; must be one of {sorted(_VALID_PARETO_GROUP_BY)}"
            )
        clauses = ["ended_at IS NOT NULL", f"{group_by} IS NOT NULL"]
        # Operator-facing filters match against ``station_hostname``
        # (the machine name the operator knows), not internal IDs.
        # See feedback_operator_facing_identifiers.md.
        clauses.extend(
            multi_filter_clauses(
                {
                    "test_phase": phase,
                    "uut_part_number": part,
                    "station_hostname": station,
                }
            )
        )
        if since:
            clauses.append(f"started_at >= '{sql_escape(since)}'")
        if until:
            clauses.append(f"started_at <= '{sql_escape(until)}'")
        where = " AND ".join(clauses)
        return self._query_dicts(f"""
            SELECT
                {group_by} AS bucket,
                COUNT(*) FILTER (WHERE outcome IN ('failed', 'errored')) AS failed_count,
                COUNT(*) AS total,
                CAST(
                    100.0 * COUNT(*) FILTER (WHERE outcome IN ('failed', 'errored'))
                    / COUNT(*)
                    AS DOUBLE
                ) AS fail_rate_pct
            FROM runs
            WHERE {where}
            GROUP BY {group_by}
            HAVING failed_count > 0
            ORDER BY failed_count DESC, total DESC
            LIMIT {int(top_n)}
        """)

    def count(
        self,
        *,
        include_incomplete: bool = False,
        phase: str | list[str] | None = None,
        part: str | list[str] | None = None,
        station: str | list[str] | None = None,
        lot: str | list[str] | None = None,
        outcome: str | list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> int:
        """Total number of runs matching the same filters as :meth:`list_recent`.

        ``include_incomplete=False`` (default) counts only finalized
        runs (``ended_at IS NOT NULL``). Filter args mirror
        :meth:`list_recent` so the count and the page agree on what's
        being shown.
        """
        clauses: list[str] = []
        if not include_incomplete:
            clauses.append("ended_at IS NOT NULL")
        clauses.extend(
            multi_filter_clauses(
                {
                    "test_phase": phase,
                    "uut_part_number": part,
                    "station_hostname": station,
                    "uut_lot_number": lot,
                    "outcome": outcome,
                }
            )
        )
        if since:
            clauses.append(f"started_at >= '{sql_escape(since)}'")
        if until:
            clauses.append(f"started_at <= '{sql_escape(until)}'")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._query_dicts(f"SELECT COUNT(*) AS n FROM runs{where}")
        return int(rows[0]["n"]) if rows else 0

    def distinct_filter_values(self) -> dict[str, list[str]]:
        """Return distinct values for each filterable run column.

        Used by the /results filter strip to populate the
        multi-select dropdowns. Only non-null, non-empty values
        appear; sort is alphabetical for consistent UI.

        Per-column failure isolation: any column the daemon's
        schema doesn't know about raises ``FlightPermanentError``
        (fast-fail, classified at the Flight layer); we catch
        per-column and yield an empty list for it. The other
        columns are unaffected. New columns can be added without
        coordinating a schema bump first — the dropdown stays
        empty until the column actually exists.
        """
        from litmus.data._flight_errors import FlightPermanentError

        out: dict[str, list[str]] = {}
        for column in ("test_phase", "uut_part_number", "station_hostname"):
            try:
                rows = self._query_dicts(
                    f"SELECT DISTINCT {column} AS v FROM runs "
                    f"WHERE {column} IS NOT NULL AND {column} != '' "
                    f"ORDER BY {column}"
                )
                out[column] = [r["v"] for r in rows]
            except FlightPermanentError:
                out[column] = []
        return out

    def count_by_outcome(self) -> dict[str, int]:
        """Return ``{outcome: count}`` over all runs."""
        rows = self._query_dicts("""
            SELECT outcome, COUNT(*) AS n
            FROM runs
            GROUP BY outcome
        """)
        return {(r["outcome"] or "unknown"): r["n"] for r in rows}

    def usage_stats(self, by: str) -> list[dict[str, Any]]:
        """Aggregate run stats grouped by a column, entirely in SQL.

        Returns ``[{value, runs, pass_count, fail_count, errored_count,
        last_run}, ...]`` sorted by ``runs`` descending. ``by`` must be
        a column present in the ``runs`` table; an invalid name raises
        ``ValueError`` before any SQL is sent.

        Using SQL aggregation instead of Python-side grouping means the
        daemon returns one row per distinct value rather than up to
        ``limit`` full run rows — safe regardless of total run count.
        """
        if by not in _VALID_USAGE_STATS_COLUMNS:
            raise ValueError(
                f"usage_stats: invalid group-by column {by!r}. "
                f"Must be one of {sorted(_VALID_USAGE_STATS_COLUMNS)}."
            )
        sql = f"""
            SELECT
                {by} AS value,
                COUNT(*) AS runs,
                COUNT(*) FILTER (WHERE outcome = 'passed') AS pass_count,
                COUNT(*) FILTER (WHERE outcome = 'failed') AS fail_count,
                COUNT(*) FILTER (WHERE outcome = 'errored') AS errored_count,
                MAX(started_at) AS last_run
            FROM runs
            WHERE {by} IS NOT NULL
            GROUP BY {by}
            ORDER BY runs DESC
        """
        return self._query_dicts(sql)

    def describe_columns(self) -> ColumnSchema:
        """Return the ``runs`` table's column schema."""
        rows = self._query_dicts("DESCRIBE runs")
        fixed = [
            FixedColumnDescriptor(name=str(r["column_name"]), column_type=str(r["column_type"]))
            for r in rows
        ]
        return ColumnSchema(fixed=fixed, fields=[])
