"""RunStore — query API for parquet test run data.

Mirrors EventStore's pattern: parquet files are the source of truth,
a DuckDB daemon indexes them, and RunStore provides a clean query API.
ParquetBackend keeps the write path; RunStore owns reads + ref management.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.flight as flight
import pyarrow.parquet as pq

from litmus.data import runs_duckdb_manager
from litmus.data._sql_helpers import sql_escape as _sql_escape


class RunStore:
    """Query API for parquet test run data.

    Uses a ref-counted in-memory DuckDB daemon for indexed queries — same
    lifecycle pattern as EventStore. Queries go via Arrow Flight (gRPC).
    """

    def __init__(self, *, _results_dir: Path | None = None) -> None:
        if _results_dir is not None:
            results_dir = _results_dir
        else:
            from litmus.config.project import load_project_config
            results_dir = Path(load_project_config().results_dir)

        self._runs_dir = results_dir / "runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)

        # Start daemon and get gRPC location
        self._location = runs_duckdb_manager.acquire(self._runs_dir)

        # Lazy Flight client
        self._client: flight.FlightClient | None = None

    def _get_client(self) -> flight.FlightClient:
        """Get or create a Flight client to the DuckDB daemon."""
        if self._client is None:
            self._client = flight.connect(self._location)
        return self._client

    def _flight_query(self, sql: str, *, _retries: int = 2) -> list[dict[str, Any]]:
        """Execute a SQL query via Flight and return list of dicts.

        Retries on transient gRPC errors (e.g. daemon restart).
        """
        import time

        last_exc: Exception | None = None
        for attempt in range(_retries + 1):
            try:
                client = self._get_client()
                ticket = flight.Ticket(f"runs\0{sql}".encode())
                reader = client.do_get(ticket)
                table = reader.read_all()
                return table.to_pylist()
            except Exception as exc:
                last_exc = exc
                self._client = None
                if attempt < _retries:
                    time.sleep(0.2)
                    # Re-acquire in case daemon restarted with new port
                    try:
                        self._location = runs_duckdb_manager.acquire(self._runs_dir)
                    except Exception:
                        pass
        warnings.warn(
            f"RunStore Flight query failed after {_retries + 1} attempts: {last_exc}",
            stacklevel=2,
        )
        return []

    # --- Query API ---

    def list_runs(self, limit: int = 50) -> list[dict]:
        """List recent test runs, most recent first."""
        rows = self._flight_query(f"""
            SELECT file_path, run_id, dut_serial, station_id,
                   outcome, started_at, num_measurements
            FROM runs
            ORDER BY started_at DESC
            LIMIT {limit}
        """)

        return [
            {
                "test_run_id": r["run_id"],
                "started_at": r["started_at"],
                "dut_serial": r["dut_serial"],
                "station_id": r["station_id"],
                "outcome": r["outcome"],
                "total_measurements": r["num_measurements"],
                "_file": r["file_path"],
            }
            for r in rows
        ]

    def find_run_file(self, run_id: str) -> Path | None:
        """Find the parquet file for a run_id (prefix match)."""
        prefix = run_id[:8] if len(run_id) >= 8 else run_id
        rows = self._flight_query(f"""
            SELECT file_path FROM runs
            WHERE run_id LIKE '{_sql_escape(prefix)}%'
            LIMIT 1
        """)

        if not rows:
            return None
        p = Path(rows[0]["file_path"])
        return p if p.exists() else None

    def get_run(self, run_id: str) -> dict | None:
        """Get a specific test run summary by ID (prefix match)."""
        pq_file = self.find_run_file(run_id)
        if pq_file is None:
            return None

        try:
            table = pq.read_table(pq_file)
            if table.num_rows == 0:
                return None
            row = table.to_pylist()[0]
            return {
                "test_run_id": row.get("run_id"),
                "started_at": row.get("run_started_at"),
                "ended_at": row.get("run_ended_at"),
                "dut_serial": row.get("dut_serial"),
                "dut_part_number": row.get("dut_part_number"),
                "product_id": row.get("product_id"),
                "station_id": row.get("station_id"),
                "station_type": row.get("station_type"),
                "test_sequence_id": row.get("sequence_id"),
                "test_phase": row.get("test_phase"),
                "operator": row.get("operator_id"),
                "outcome": row.get("run_outcome"),
                "total_measurements": table.num_rows,
                "_file": str(pq_file),
            }
        except Exception:
            return None

    def get_measurements(self, run_id: str, *, _file: str | None = None) -> list[dict]:
        """Get all measurements for a specific test run."""
        if _file:
            pq_file = Path(_file)
        else:
            pq_file = self.find_run_file(run_id)

        if pq_file is None or not pq_file.exists():
            return []

        try:
            table = pq.read_table(pq_file)
            return table.to_pylist()
        except Exception:
            return []

    # --- Ref management (for materialize) ---

    def find_channel_refs(self, session_shorts: set[str]) -> list[dict]:
        """Query run_channel_refs WHERE session_short IN (...)."""
        if not session_shorts:
            return []

        quoted = ", ".join(f"'{_sql_escape(s)}'" for s in session_shorts)
        rows = self._flight_query(f"""
            SELECT file_path, col_name, row_idx, uri, channel_id, session_short
            FROM run_channel_refs
            WHERE session_short IN ({quoted})
        """)

        return [
            {
                "file_path": r["file_path"],
                "col_name": r["col_name"],
                "row_idx": r["row_idx"],
                "uri": r["uri"],
                "channel_id": r["channel_id"],
                "session_short": r["session_short"],
            }
            for r in rows
        ]

    def rewrite_refs(self, file_path: Path, replacements: dict[str, dict[int, str]]) -> None:
        """Atomic parquet column rewrite. Reads, replaces URIs, write-tmp, os.replace."""
        if not replacements:
            return

        table = pq.read_table(file_path)
        arrays: dict[str, list[object]] = {}
        for col_name, row_map in replacements.items():
            col = table.column(col_name)
            new_values = [v.as_py() for v in col]
            for row_idx, new_uri in row_map.items():
                new_values[row_idx] = new_uri
            arrays[col_name] = new_values

        new_columns = []
        for name in table.column_names:
            if name in arrays:
                new_columns.append(pa.array(arrays[name], type=pa.string()))
            else:
                new_columns.append(table.column(name))

        new_table = pa.table(dict(zip(table.column_names, new_columns)))

        # Preserve parquet file-level metadata
        orig_meta = pq.read_metadata(file_path)
        file_meta = orig_meta.metadata if orig_meta.metadata else None
        if file_meta:
            new_table = new_table.replace_schema_metadata(file_meta)

        tmp_path = file_path.with_suffix(".tmp.parquet")
        pq.write_table(new_table, tmp_path)
        os.replace(tmp_path, file_path)

    @staticmethod
    def ref_dir_for(file_path: Path) -> Path:
        """Return _ref/ sidecar dir path for a parquet file."""
        return file_path.parent / (file_path.stem + "_ref")

    def notify_new_run(self, parquet_path: Path) -> None:
        """Notify the daemon of a new parquet file via do_put.

        Sends the file path so the daemon can read and index it immediately.
        """
        try:
            client = self._get_client()
            descriptor = flight.FlightDescriptor.for_command(b"runs\0runs")
            table = pa.table({"file_path": [str(parquet_path)]})
            batch = table.to_batches()[0]
            writer, _ = client.do_put(descriptor, batch.schema)
            writer.write_batch(batch)
            writer.close()
        except Exception:
            # Non-fatal: daemon will pick up on restart
            self._client = None

    def close(self) -> None:
        """Close Flight client and release daemon ref."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                warnings.warn(
                    f"Failed to close Flight client: {exc}",
                    stacklevel=2,
                )
            self._client = None

        runs_duckdb_manager.release(self._runs_dir)
