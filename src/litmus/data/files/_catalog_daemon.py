"""Files catalog daemon — warm DuckDB index over FileStore sidecars.

Spawned by ``FilesCatalogManager.acquire()`` as a detached process.
Owns an in-memory DuckDB catalog rebuilt from sidecars on start, served
over Arrow Flight (SQL ``do_get`` for resolve/list; ``do_put`` of
``files\\0file_catalog`` rows for live upserts from ``FileStore.write``).
Blobs + sidecars are the durable truth; the catalog is a derived cache.

Usage (internal — not called directly)::

    python -m litmus.data.files._catalog_daemon <files_dir>
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import duckdb

from litmus.data._duckdb_flight_server import (
    shutdown_flight_server_in_daemon,
    start_flight_server_in_daemon,
)
from litmus.data.files.catalog import (
    FRAME_ARROW_SCHEMA,
    FRAMES_DB,
    ensure_schema,
    scan_sidecars,
)
from litmus.data.files.catalog_manager import FilesCatalogManager


def daemon_run(files_dir: Path) -> None:
    """Entry point for the catalog daemon process. Blocks until idle timeout."""
    mgr = FilesCatalogManager(files_dir)

    conn = duckdb.connect(":memory:")
    ensure_schema(conn)
    scan_sidecars(conn, files_dir)
    write_lock = threading.Lock()

    server, port_file, _ = start_flight_server_in_daemon(
        mgr=mgr,
        daemon_dir=files_dir,
        db_name="files",
        conn=conn,
        put_hook=None,
        port_file_name="_files_catalog_flight_port",
        thread_name="files-catalog-flight",
        lock=write_lock,
    )

    # Ephemeral stream-frame fan-out: a hook-only db that publishes each
    # do_put frame to live subscribers without persisting it (the durable
    # record stays the on-disk artifact; the EventStore stays
    # lifecycle-only). Lets consumers range-read a growing artifact
    # push-style (req 5) instead of polling its size.
    server.register_put_hook(FRAMES_DB, lambda table: table)
    server.register_subscribe_schema(FRAMES_DB, FRAME_ARROW_SCHEMA)

    mgr.monitor_refs()

    shutdown_flight_server_in_daemon(server, port_file, conn)
    mgr.cleanup_state_files()


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
