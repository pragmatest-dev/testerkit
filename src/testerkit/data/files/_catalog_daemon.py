"""Files catalog daemon — warm DuckDB index over FileStore sidecars.

Spawned by ``FilesCatalogManager.acquire()`` as a detached process.
Owns an in-memory DuckDB catalog rebuilt from sidecars on start, served
over Arrow Flight (SQL ``do_get`` for resolve/list; ``do_put`` of
``files\\0file_catalog`` rows for live upserts from ``FileStore.write``).
Blobs + sidecars are the durable truth; the catalog is a derived cache.

Usage (internal — not called directly)::

    python -m testerkit.data.files._catalog_daemon <files_dir>
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import duckdb

from testerkit.data._daemon_lifecycle import daemon_duckdb_config
from testerkit.data._duckdb_flight_server import (
    shutdown_flight_server_in_daemon,
    start_flight_server_in_daemon,
)
from testerkit.data.files.catalog import (
    FRAME_ARROW_SCHEMA,
    FRAMES_DB,
    ensure_schema,
    scan_sidecars,
    upsert_rows,
)
from testerkit.data.files.catalog_manager import FilesCatalogManager


def daemon_run(files_dir: Path) -> None:
    """Entry point for the catalog daemon process. Blocks until idle timeout."""
    mgr = FilesCatalogManager(files_dir)

    # Persistent catalog (``_index.duckdb``): survives a restart and is
    # brought current by an incremental sidecar scan, vs. the old in-memory
    # rebuild-from-every-sidecar. Blobs + sidecars stay the durable truth.
    conn = duckdb.connect(str(files_dir / "_index.duckdb"), config=daemon_duckdb_config())
    ensure_schema(conn)
    scan_sidecars(conn, files_dir)
    write_lock = threading.Lock()

    def _catalog_put_hook(table: object) -> None:
        # Live ``FileStore.write`` do_put: upsert by uri so a re-pushed
        # (or rescanned) uri refreshes rather than conflicting with the PK.
        upsert_rows(conn, table)  # type: ignore[arg-type]

    def _register_frames(server: object) -> None:
        # Ephemeral stream-frame fan-out: a hook-only db that publishes each
        # do_put frame to live subscribers without persisting it (the durable
        # record stays the on-disk artifact; the EventStore stays
        # lifecycle-only). Registered via ``extra_setup`` so it is live BEFORE
        # the daemon accepts connections — otherwise a client that streams the
        # instant the daemon is ready races the registration and its frame
        # do_put hits an "Unknown database".
        server.register_put_hook(FRAMES_DB, lambda table: table)  # type: ignore[attr-defined]
        server.register_subscribe_schema(FRAMES_DB, FRAME_ARROW_SCHEMA)  # type: ignore[attr-defined]

    server, port_file, _ = start_flight_server_in_daemon(
        mgr=mgr,
        daemon_dir=files_dir,
        db_name="files",
        conn=conn,
        put_hook=_catalog_put_hook,
        port_file_name="_files_catalog_flight_port",
        thread_name="files-catalog-flight",
        extra_setup=_register_frames,
        lock=write_lock,
    )

    mgr.monitor_refs()

    shutdown_flight_server_in_daemon(server, port_file, conn)
    mgr.cleanup_state_files()


if __name__ == "__main__":
    daemon_run(Path(sys.argv[1]))
