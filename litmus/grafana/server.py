"""PostgreSQL wire-protocol server backed by DuckDB.

Exposes all Litmus data stores (runs/parquet, events/IPC, channels/IPC)
as a single SQL endpoint that Grafana's built-in PostgreSQL datasource
can query directly.  No plugins, no file mounts, no data duplication.

Uses Buena Vista for the pgwire layer and PyArrow for zero-copy IPC reads.

Usage (standalone):
    python -m litmus.grafana.server [--results-dir PATH] [--port 5433]
"""

from __future__ import annotations

import logging
import re
import threading
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pyarrow as pa
import pyarrow.ipc as ipc

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from buenavista import postgres  # pyright: ignore[reportMissingImports]


def _sql_path(p: Path) -> str:
    """Convert a path to a forward-slash string for use in SQL queries."""
    return str(p).replace("\\", "/")


def _read_ipc_files(directory: Path) -> pa.Table | None:
    """Read all Arrow IPC files under *directory* into a single table."""
    arrow_files = sorted(directory.rglob("*.arrow"))
    if not arrow_files:
        return None
    tables: list[pa.Table] = []
    for f in arrow_files:
        try:
            reader = ipc.open_stream(pa.OSFile(str(f), "rb"))
            tables.append(reader.read_all())
        except (OSError, pa.ArrowInvalid) as exc:
            warnings.warn(f"Skipping {f}: {exc}", stacklevel=2)
    if not tables:
        return None
    return pa.concat_tables(tables, promote_options="default")


def create_connection(results_dir: Path) -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB connection with VIEWs over Litmus data.

    - ``measurements`` / ``runs``: VIEWs over Parquet files (lazy, live)
    - ``events``: registered Arrow table from IPC files (zero-copy)
    - ``channels``: registered Arrow table from IPC files (zero-copy)
    """
    conn = duckdb.connect()
    results_str = _sql_path(results_dir)

    # Parquet-based views (lazy — DuckDB reads on query)
    conn.execute(
        f"CREATE OR REPLACE VIEW measurements AS "
        f"SELECT * REPLACE("
        f"run_started_at AT TIME ZONE 'UTC' AS run_started_at, "
        f"run_ended_at AT TIME ZONE 'UTC' AS run_ended_at, "
        f"step_started_at AT TIME ZONE 'UTC' AS step_started_at, "
        f"step_ended_at AT TIME ZONE 'UTC' AS step_ended_at, "
        f"vector_started_at AT TIME ZONE 'UTC' AS vector_started_at, "
        f"vector_ended_at AT TIME ZONE 'UTC' AS vector_ended_at, "
        f"measurement_timestamp AT TIME ZONE 'UTC' AS measurement_timestamp) "
        f"FROM read_parquet('{results_str}/runs/**/*.parquet', "
        f"union_by_name=true)"
    )
    conn.execute(
        "CREATE OR REPLACE VIEW runs AS "
        "SELECT run_id, first(session_id) AS session_id, "
        "min(run_started_at) AS started_at, "
        "max(run_ended_at) AS ended_at, "
        "first(run_outcome) AS outcome, first(dut_serial) AS dut_serial, "
        "first(dut_part_number) AS part_number, first(dut_lot_number) AS lot, "
        "first(station_id) AS station_id, first(station_name) AS station_name, "
        "first(product_id) AS product_id, first(product_name) AS product_name, "
        "first(test_phase) AS phase, count(*) AS num_measurements "
        "FROM measurements GROUP BY run_id"
    )

    # Arrow IPC data — load into DuckDB tables via PyArrow.
    # We use CREATE TABLE AS (arrow_scan) for cursor visibility across
    # BuenaVista sessions (conn.register is not visible from cursors).
    _load_all_ipc_tables(conn, results_dir)

    return conn


_IPC_TABLES: list[tuple[str, str, str]] = [
    (
        "events",
        "events",
        "occurred_at AT TIME ZONE 'UTC' AS occurred_at, "
        "received_at AT TIME ZONE 'UTC' AS received_at",
    ),
    ("channels", "channels", "timestamp AT TIME ZONE 'UTC' AS timestamp"),
]


def _load_all_ipc_tables(conn: duckdb.DuckDBPyConnection, results_dir: Path) -> None:
    """Load all IPC-backed tables from the results directory."""
    for subdir, table_name, replace_expr in _IPC_TABLES:
        _load_ipc_table(conn, results_dir / subdir, table_name, replace_expr)


def _replace_expr_columns(replace_expr: str) -> list[str]:
    """Extract column names from a SQL REPLACE clause (e.g. 'col AT TIME ZONE ...')."""
    return re.findall(r"\b(\w+)\s+AT\s+TIME\s+ZONE", replace_expr)


def _load_ipc_table(
    conn: duckdb.DuckDBPyConnection,
    directory: Path,
    table_name: str,
    replace_expr: str,
) -> None:
    """Load Arrow IPC files from *directory* into a DuckDB table.

    *replace_expr* is a SQL REPLACE clause for AT TIME ZONE conversions
    needed for Buena Vista pgwire compatibility (TIMESTAMPTZ serialization).
    """
    if not directory.exists():
        return
    arrow_table = _read_ipc_files(directory)
    if arrow_table is None:
        return

    # Validate that columns named in replace_expr exist in the Arrow schema.
    schema_cols = set(arrow_table.schema.names)
    for col in _replace_expr_columns(replace_expr):
        if col not in schema_cols:
            _log.warning(
                "IPC table '%s': column '%s' not found in Arrow schema "
                "— time zone conversion will fail",
                table_name,
                col,
            )

    tmp = f"_{table_name}_arrow"
    conn.register(tmp, arrow_table)
    conn.execute(
        f"CREATE OR REPLACE TABLE {table_name} AS SELECT * REPLACE({replace_expr}) FROM {tmp}"
    )
    conn.unregister(tmp)


def _create_server(
    conn: duckdb.DuckDBPyConnection,
    host: str = "127.0.0.1",
    port: int = 5433,
) -> postgres.BuenaVistaServer:
    """Create a Buena Vista pgwire server wrapping a DuckDB connection."""
    from buenavista import bv_dialects, postgres  # pyright: ignore[reportMissingImports]
    from buenavista.backends.duckdb import DuckDBConnection  # pyright: ignore[reportMissingImports]
    from buenavista.examples.duckdb_postgres import (  # pyright: ignore[reportMissingImports]
        DuckDBPostgresRewriter,
    )

    rewriter = DuckDBPostgresRewriter(bv_dialects.BVPostgres(), bv_dialects.BVDuckDB())
    server = postgres.BuenaVistaServer((host, port), DuckDBConnection(conn), rewriter=rewriter)
    return server


def serve(
    results_dir: Path,
    host: str = "127.0.0.1",
    port: int = 5433,
    *,
    refresh_seconds: int = 30,
) -> None:
    """Start the pgwire server (blocks forever).

    A background thread re-reads Arrow IPC files every *refresh_seconds*
    so Grafana sees new events/channels without a server restart.
    """
    conn = create_connection(results_dir)
    server = _create_server(conn, host, port)

    # Background refresh for IPC-backed tables
    stop_event = threading.Event()

    def _refresh_loop() -> None:
        consecutive_failures = 0
        while not stop_event.wait(refresh_seconds):
            try:
                _refresh_ipc_tables(conn, results_dir)
                consecutive_failures = 0
            except (OSError, pa.ArrowInvalid, duckdb.Error) as exc:
                consecutive_failures += 1
                if consecutive_failures >= 5:
                    _log.error(
                        "IPC refresh failing repeatedly (%d times): %s — "
                        "Grafana may see stale data. Restart with: litmus grafana serve",
                        consecutive_failures,
                        exc,
                    )
                else:
                    warnings.warn(f"IPC refresh failed: {exc}", stacklevel=2)

    refresh_thread = threading.Thread(target=_refresh_loop, daemon=True, name="grafana-ipc-refresh")
    refresh_thread.start()

    print(f"Litmus pgwire server listening on {host}:{port}")
    print("Connect Grafana PostgreSQL datasource to this address.")
    try:
        server.serve_forever()
    finally:
        stop_event.set()
        server.shutdown()
        conn.close()


def _refresh_ipc_tables(conn: duckdb.DuckDBPyConnection, results_dir: Path) -> None:
    """Re-read Arrow IPC files and replace in-memory tables."""
    _load_all_ipc_tables(conn, results_dir)


if __name__ == "__main__":
    import sys

    from litmus.data.results_dir import resolve_results_dir

    port = 5433
    rd = None
    args = sys.argv[1:]
    while args:
        if args[0] == "--port" and len(args) > 1:
            port = int(args[1])
            args = args[2:]
        elif args[0] == "--results-dir" and len(args) > 1:
            rd = Path(args[1])
            args = args[2:]
        else:
            args = args[1:]

    serve(resolve_results_dir(rd), port=port)
