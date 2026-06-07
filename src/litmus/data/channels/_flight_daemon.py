"""Standalone Flight server daemon.

Spawned by ``FlightDaemonManager.acquire()`` as a detached process.
Monitors ref count and exits after idle timeout.

Usage (internal — not called directly)::

    python -m litmus.data.channels._flight_daemon <channels_dir> <host> <port>
"""

from __future__ import annotations

import sys
import threading
import warnings
from pathlib import Path
from uuid import UUID

import pyarrow as pa

from litmus.data.channels.flight_manager import FlightDaemonManager
from litmus.data.channels.server import ChannelFlightServer
from litmus.data.channels.store import ChannelStore


def daemon_run(channels_dir: Path, host: str, port: int) -> None:
    """Entry point for the daemon process. Blocks until idle timeout."""
    mgr = FlightDaemonManager(channels_dir, host, port)

    # index=True: the daemon owns the warm at-rest index and serves
    # query from it. Opt 1 — it indexes producer files + live do_put
    # rows; it does NOT persist its own segment copy.
    store = ChannelStore(channels_dir.parent, UUID(int=0), index=True)
    store.open()

    location = f"grpc://{host}:{port}"
    server = ChannelFlightServer(store, location)
    actual_location = f"grpc://{host}:{server.port}"

    # Write port file (doubles as ready signal) and PID
    (channels_dir / "_flight_port").write_text(actual_location)
    mgr.write_ready()
    mgr.update_state(location=actual_location)

    # Serve in background thread
    threading.Thread(target=server.serve, daemon=True).start()

    # Block until idle timeout
    mgr.monitor_refs()

    # Shut down — narrow catches let real bugs surface in daemon logs;
    # transport / file errors are expected during shutdown and become warnings.
    try:
        server.shutdown()
    except (OSError, RuntimeError, pa.ArrowException) as exc:
        warnings.warn(f"Failed to shut down Flight server: {exc}", stacklevel=2)
    try:
        store.close()
    except (OSError, RuntimeError, pa.ArrowException) as exc:
        warnings.warn(f"Failed to close ChannelStore: {exc}", stacklevel=2)

    mgr.cleanup_state_files()


if __name__ == "__main__":
    channels_dir = Path(sys.argv[1])
    host = sys.argv[2]
    port = int(sys.argv[3])
    daemon_run(channels_dir, host, port)
