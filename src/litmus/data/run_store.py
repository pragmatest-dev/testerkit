"""RunStore — query API for parquet test run data.

Mirrors EventStore's pattern: parquet files are the source of truth,
a DuckDB daemon indexes them, and RunStore provides a clean query API.
ParquetBackend keeps the write path; RunStore owns reads + ref management.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.flight as flight
import pyarrow.parquet as pq

from litmus.data import runs_duckdb_manager
from litmus.data._flight_query import FlightQueryClient, call_options
from litmus.data._sql_helpers import sql_escape as _sql_escape
from litmus.data.data_dir import resolve_data_dir
from litmus.data.models import RunSummary

logger = logging.getLogger(__name__)


class DataUnavailable(Exception):
    """Raised when a run's parquet data is not locally accessible.

    The run's metadata (outcome, started_at, measurement stats) remains
    queryable from the index.  Raw scalars and ref data require the file.
    """

    def __init__(self, file_path: str, *, status: str = "missing") -> None:
        self.file_path = file_path
        self.status = status
        super().__init__(f"Run data {status}: {file_path}")


class RunStore:
    """Query API for parquet test run data.

    Uses a ref-counted in-memory DuckDB daemon for indexed queries — same
    lifecycle pattern as EventStore. Queries go via Arrow Flight (gRPC).
    """

    def __init__(self, *, _data_dir: Path | None = None) -> None:
        data_dir = resolve_data_dir(_data_dir)

        self._runs_dir = data_dir / "runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)

        # Start daemon and get gRPC location
        self._location = runs_duckdb_manager.acquire(self._runs_dir)

        # Flight query client (shared retry logic with EventStore)
        self._flight = FlightQueryClient(
            self._location,
            "runs",
            reacquire=lambda: runs_duckdb_manager.acquire(self._runs_dir),
            label="RunStore",
        )

    def _flight_query(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SQL query via Flight and return list of dicts."""
        return self._flight.query(sql)

    # --- Query API ---

    @staticmethod
    def _id_prefix(run_id: str, length: int = 8) -> str:
        """Return an 8-char (or shorter) prefix for LIKE matching against the index."""
        return run_id[:length] if len(run_id) >= length else run_id

    def list_runs(self, limit: int = 50) -> list[RunSummary]:
        """List recent test runs, most recent first."""
        rows = self._flight_query(f"""
            SELECT file_path, run_id, session_id, uut_serial, station_id,
                   outcome, started_at, num_measurements,
                   test_phase, part_id, operator_id,
                   project_name
            FROM runs
            ORDER BY started_at DESC
            LIMIT {limit}
        """)

        return [
            RunSummary(
                test_run_id=r["run_id"],
                session_id=r.get("session_id"),
                started_at=r.get("started_at"),
                uut_serial=r.get("uut_serial"),
                station_id=r.get("station_id"),
                outcome=r.get("outcome"),
                total_measurements=r.get("num_measurements", 0),
                test_phase=r.get("test_phase"),
                part_id=r.get("part_id"),
                operator=r.get("operator_id"),
                project_name=r.get("project_name"),
                file_path=r.get("file_path"),
            )
            for r in rows
        ]

    def find_run_file(self, run_id: str) -> Path | None:
        """Find the measurements parquet for a run_id (prefix match).

        Returns ``None`` when the runs row has no measurements
        parquet (measurement-less run — only setup / action /
        skipped steps) or when the file is missing from disk.
        """
        prefix = self._id_prefix(run_id)
        rows = self._flight_query(f"""
            SELECT file_path FROM runs
            WHERE run_id LIKE '{_sql_escape(prefix)}%'
            LIMIT 1
        """)

        if not rows or rows[0].get("file_path") is None:
            return None
        p = Path(rows[0]["file_path"])
        return p if p.exists() else None

    def get_run(self, run_id: str) -> RunSummary | None:
        """Get a run summary by ID (prefix match), runs-table-first.

        The runs TABLE carries every run-level field needed to
        render the summary card. We only crack open the measurements
        parquet to fill in fields the table doesn't denormalize
        (``slot_id``, ``station_type``). For measurement-less runs
        (``file_path`` IS NULL) we return the table row as-is.
        """
        prefix = self._id_prefix(run_id)
        rows = self._flight_query(f"""
            SELECT * FROM runs
            WHERE run_id LIKE '{_sql_escape(prefix)}%'
            LIMIT 1
        """)
        if not rows:
            return None
        r = rows[0]
        run_id_val = r.get("run_id")
        if not run_id_val:
            return None

        summary = RunSummary(
            test_run_id=run_id_val,
            session_id=r.get("session_id"),
            started_at=r.get("started_at"),
            ended_at=r.get("ended_at"),
            uut_serial=r.get("uut_serial"),
            uut_part_number=r.get("uut_part_number"),
            part_id=r.get("part_id"),
            station_id=r.get("station_id"),
            station_name=r.get("station_name"),
            station_hostname=r.get("station_hostname"),
            fixture_id=r.get("fixture_id"),
            test_phase=r.get("test_phase"),
            project_name=r.get("project_name"),
            operator=r.get("operator_id"),
            outcome=r.get("outcome"),
            total_measurements=r.get("num_measurements", 0) or 0,
            file_path=r.get("file_path"),
        )

        # Fields not in the runs table — sourced from the measurements
        # parquet when available. Skipped silently for measurement-less
        # runs.
        pq_path_str = r.get("file_path")
        if pq_path_str:
            pq_path = Path(pq_path_str)
            if pq_path.exists():
                try:
                    table = pq.read_table(pq_path)
                    if table.num_rows > 0:
                        first = table.to_pylist()[0]
                        summary.slot_id = first.get("slot_id")
                        summary.station_type = first.get("station_type")
                except Exception as exc:
                    logger.debug("Failed to enrich run %s from parquet: %s", run_id, exc)
        return summary

    def get_measurements(self, run_id: str, *, _file: str | None = None) -> list[dict[str, Any]]:
        """Get all measurements for a specific test run.

        Goes through the daemon's ``measurements`` view (a parquet glob
        with ``union_by_name=true``) instead of reading the parquet file
        directly — DuckDB does the multi-file scan in C++ with predicate
        pushdown on ``run_id`` and avoids client-side parquet decoding.

        ``_file`` is no longer honored (was a test-only escape hatch);
        callers should pass ``run_id`` and trust the daemon to find the
        rows. Kept in the signature for backwards-compat at the call
        sites that pass it as keyword.
        """
        _ = _file  # ignore — daemon resolves run_id directly
        prefix = self._id_prefix(run_id)
        try:
            rows = self._flight_query(f"""
                SELECT *
                FROM measurements
                WHERE run_id LIKE '{_sql_escape(prefix)}%'
                ORDER BY step_index, measurement_name
            """)
        except Exception as exc:
            logger.debug("Failed to query measurements for %s: %s", run_id, exc)
            return []
        # Expand dynamic_attrs MAP into top-level keys so callers can access
        # dynamic columns (out_*, in_*, value, units, etc.) as regular dict keys.
        # DuckDB MAP(VARCHAR,VARCHAR) arrives from Arrow as a list of (key, value)
        # tuples rather than a Python dict. Numeric strings are coerced to float
        # for backwards compatibility with callers expecting native types.
        for row in rows:
            da = row.pop("dynamic_attrs", None)
            if not da:
                continue
            items = da.items() if isinstance(da, dict) else da
            for k, v in items:
                if k is None:
                    continue
                if isinstance(v, str):
                    try:
                        row[k] = float(v)
                    except ValueError:
                        row[k] = v
                else:
                    row[k] = v
        return rows

    # --- Ref management (for materialize) ---

    def get_steps(self, run_id: str) -> list[dict[str, Any]]:
        """Get steps for a run from the daemon's ``steps`` view."""
        prefix = self._id_prefix(run_id)
        return self._flight_query(f"""
            SELECT step_index, step_name, step_path, outcome,
                   CAST(started_at AT TIME ZONE 'UTC' AS VARCHAR) AS started_at,
                   CAST(ended_at AT TIME ZONE 'UTC' AS VARCHAR) AS ended_at,
                   duration_s, has_measurements, measurement_count, vector_count, markers
            FROM steps
            WHERE run_id LIKE '{_sql_escape(prefix)}%'
            ORDER BY step_index
        """)

    def find_channel_refs(self, session_shorts: set[str]) -> list[dict[str, Any]]:
        """Query measurement_refs WHERE session_short IN (...)."""
        if not session_shorts:
            return []

        quoted = ", ".join(f"'{_sql_escape(s)}'" for s in session_shorts)
        return self._flight_query(f"""
            SELECT file_path, step_index, measurement_name, col_name,
                   row_idx, uri, channel_id, session_short, session_id
            FROM measurement_refs
            WHERE session_short IN ({quoted})
        """)

    def get_measurement(
        self,
        file_path: str | Path,
        measurement_name: str,
        *,
        step_index: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get rows for a specific measurement name, with predicate pushdown.

        Uses pyarrow row-group filters so only matching row groups are read.
        Raises DataUnavailable if the file is missing or marked non-ok in the index.
        Returns [] if the parquet file exists but lacks a measurement_name column
        (e.g. a file written by an older schema version).
        """
        pq_file = Path(file_path)
        if not pq_file.exists():
            status_rows = self._flight_query(
                f"SELECT status FROM _ingested WHERE path = '{_sql_escape(str(pq_file))}'"
            )
            status = status_rows[0]["status"] if status_rows else "missing"
            raise DataUnavailable(str(pq_file), status=status)

        filters: list[tuple] = [("measurement_name", "=", measurement_name)]
        if step_index is not None:
            filters.append(("step_index", "=", step_index))

        try:
            table = pq.read_table(pq_file, filters=filters)
            return table.to_pylist()
        except Exception as exc:
            logger.debug(
                "Failed to read measurement %r from %s: %s", measurement_name, pq_file, exc
            )
            return []

    @staticmethod
    def ref_dir_for(file_path: Path) -> Path:
        """Return _ref/ sidecar dir path for a parquet file."""
        return file_path.parent / (file_path.stem + "_ref")

    def notify_new_run(self, parquet_path: Path) -> None:
        """Notify the daemon of a new unified per-run parquet via do_put."""
        paths = [str(parquet_path)]
        try:
            client = self._flight.get_client()
            descriptor = flight.FlightDescriptor.for_command(b"runs\0runs")
            table = pa.table({"file_path": paths})
            batches = table.to_batches()
            writer, reader = client.do_put(descriptor, table.schema, options=call_options())
            for batch in batches:
                writer.write_batch(batch)
            # Drain ACKs — each ACK confirms the server committed one batch,
            # so by the time the last ACK arrives the daemon has fully ingested
            # the file into all index tables including measurements_materialized.
            for _ in batches:
                reader.read()
            writer.close()
        except Exception:
            logger.debug("Failed to notify runs daemon of new run %s", parquet_path, exc_info=True)
            self._flight.reset()

    def close(self) -> None:
        """Close Flight client and release daemon ref."""
        try:
            self._flight.close()
        except Exception as exc:
            warnings.warn(f"FlightQueryClient close failed: {exc}", stacklevel=2)
        try:
            runs_duckdb_manager.release(self._runs_dir)
        except Exception as exc:
            warnings.warn(f"runs_duckdb_manager.release failed: {exc}", stacklevel=2)
