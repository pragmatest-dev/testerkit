"""DuckDB run index daemon.

Spawned as a detached process by ``RunsDuckDBManager.acquire()``.
Maintains an in-memory DuckDB index rebuilt from parquet files on startup.
New runs are pushed via ``do_put`` (file path in Arrow table), queries via ``do_get``.

Usage: python -m litmus.data._runs_duckdb_daemon <runs_dir>
"""

from __future__ import annotations

import sys
import threading
import warnings
from pathlib import Path

import duckdb
import pyarrow as pa

from litmus.data._duckdb_flight_server import DuckDBFlightServer
from litmus.data._sql_helpers import sql_escape as _sql_escape
from litmus.data.ref import parse_channel_uri
from litmus.data.runs_duckdb_manager import RunsDuckDBManager


def daemon_run(runs_dir: Path) -> None:
    """Entry point for the daemon process. Blocks until idle timeout."""
    mgr = RunsDuckDBManager(runs_dir)

    conn = duckdb.connect()  # in-memory, no file
    conn.execute("""
        CREATE TABLE runs (
            file_path VARCHAR NOT NULL,
            run_id VARCHAR,
            session_id VARCHAR,
            dut_serial VARCHAR,
            station_id VARCHAR,
            outcome VARCHAR,
            started_at VARCHAR,
            num_measurements INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE steps (
            file_path VARCHAR NOT NULL,
            run_id VARCHAR,
            session_id VARCHAR,
            step_index INTEGER,
            step_name VARCHAR,
            step_path VARCHAR,
            outcome VARCHAR,
            started_at VARCHAR,
            ended_at VARCHAR,
            duration_s DOUBLE,
            has_measurements BOOLEAN,
            measurement_count INTEGER,
            vector_count INTEGER,
            markers VARCHAR,
            dut_serial VARCHAR,
            station_id VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE run_channel_refs (
            file_path VARCHAR NOT NULL,
            col_name VARCHAR NOT NULL,
            row_idx INTEGER NOT NULL,
            uri VARCHAR NOT NULL,
            channel_id VARCHAR NOT NULL,
            session_short VARCHAR NOT NULL
        )
    """)

    # Bulk rebuild from parquet files — route by filename suffix
    for pq_file in sorted(runs_dir.rglob("*.parquet")):
        if pq_file.name.endswith(".tmp.parquet"):
            continue
        if _is_steps_file(pq_file.name):
            _index_steps_file(conn, str(pq_file))
        else:
            _index_parquet_file(conn, str(pq_file))

    # Custom do_put hook: receives a table with a single "file_path" column
    def _on_put(table: pa.Table) -> None:
        for row in table.to_pylist():
            fpath = row.get("file_path", "")
            if not fpath:
                continue
            if _is_steps_file(fpath):
                _index_steps_file(conn, fpath)
            else:
                _index_parquet_file(conn, fpath)

    # Start Flight server
    server = DuckDBFlightServer("grpc://127.0.0.1:0")
    server.register("runs", conn)
    server.register_put_hook("runs", _on_put)
    location = f"grpc://127.0.0.1:{server.port}"
    port_file = runs_dir / "_runs_duckdb_flight_port"
    port_file.write_text(location)
    threading.Thread(target=server.serve, daemon=True, name="runs-duckdb-flight").start()

    # Signal ready and store Flight location in state
    mgr.write_ready()
    mgr.update_state(location=location)

    # Block until idle timeout
    mgr.monitor_refs()

    # Shut down
    server.shutdown()
    port_file.unlink(missing_ok=True)
    try:
        conn.close()
    except Exception as exc:
        warnings.warn(f"Failed to close DuckDB connection: {exc}", stacklevel=2)

    mgr.cleanup_state_files()


def _is_steps_file(filename: str) -> bool:
    """Return True if *filename* is a steps parquet sidecar."""
    return filename.endswith("_steps.parquet")


def _index_parquet_file(conn: duckdb.DuckDBPyConnection, fkey: str) -> bool:
    """Index a measurement parquet file into the runs and run_channel_refs tables."""
    escaped = _sql_escape(fkey)
    try:
        result = conn.execute(f"""
            SELECT
                run_id,
                CAST(session_id AS VARCHAR) AS session_id,
                CAST(dut_serial AS VARCHAR) AS dut_serial,
                CAST(station_id AS VARCHAR) AS station_id,
                CAST(run_outcome AS VARCHAR) AS outcome,
                CAST(run_started_at AS VARCHAR) AS started_at,
                COUNT(*) AS num_measurements
            FROM read_parquet('{escaped}')
            GROUP BY run_id, session_id, dut_serial, station_id, run_outcome, run_started_at
            LIMIT 1
        """).fetchone()

        if result is None:
            return False

        run_id, session_id, dut_serial, station_id, outcome, started_at, num_meas = result
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [fkey, run_id, session_id, dut_serial, station_id, outcome, started_at, num_meas],
        )

        # Scan out_* columns for channel:// URIs
        try:
            schema_rows = conn.execute(f"""
                SELECT name FROM parquet_schema('{escaped}')
                WHERE name LIKE 'out\\_%' ESCAPE '\\'
            """).fetchall()
            out_cols = [r[0] for r in schema_rows]
        except duckdb.Error as exc:
            warnings.warn(f"Could not read schema for {fkey}: {exc}", stacklevel=2)
            out_cols = []

        for col_name in out_cols:
            try:
                escaped_col = col_name.replace('"', '""')
                rows = conn.execute(f"""
                    SELECT row_number() OVER () - 1 AS row_idx, "{escaped_col}" AS val
                    FROM read_parquet('{escaped}')
                    WHERE "{escaped_col}" IS NOT NULL
                      AND "{escaped_col}" LIKE 'channel://%'
                """).fetchall()

                for row_idx, uri in rows:
                    try:
                        channel_id, uri_session_id = parse_channel_uri(uri)
                        session_short = uri_session_id[:8]
                        conn.execute(
                            "INSERT INTO run_channel_refs VALUES (?, ?, ?, ?, ?, ?)",
                            [fkey, col_name, row_idx, uri, channel_id, session_short],
                        )
                    except ValueError:
                        continue
            except duckdb.Error as exc:
                warnings.warn(f"Could not scan column '{col_name}' in {fkey}: {exc}", stacklevel=2)
                continue

        return True
    except Exception as exc:
        if not isinstance(exc, duckdb.IOException):
            warnings.warn(f"Error ingesting {fkey}: {exc}", stacklevel=2)
        return False


def _index_steps_file(conn: duckdb.DuckDBPyConnection, fkey: str) -> bool:
    """Index a steps parquet file into the steps table."""
    escaped = _sql_escape(fkey)
    try:
        rows = conn.execute(f"""
            SELECT
                run_id,
                CAST(session_id AS VARCHAR) AS session_id,
                "index" AS step_index,
                name AS step_name,
                step_path,
                outcome,
                CAST(started_at AS VARCHAR) AS started_at,
                CAST(ended_at AS VARCHAR) AS ended_at,
                duration_s,
                has_measurements,
                measurement_count,
                vector_count,
                markers,
                CAST(dut_serial AS VARCHAR) AS dut_serial,
                CAST(station_id AS VARCHAR) AS station_id
            FROM read_parquet('{escaped}')
        """).fetchall()

        if not rows:
            return False

        conn.executemany(
            "INSERT INTO steps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [[fkey, *row] for row in rows],
        )
        return True
    except Exception as exc:
        if not isinstance(exc, duckdb.IOException):
            warnings.warn(f"Error ingesting steps file {fkey}: {exc}", stacklevel=2)
        return False


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
