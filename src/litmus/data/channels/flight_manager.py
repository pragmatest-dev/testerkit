"""Flight server daemon manager.

Subclasses ``DaemonManager`` for the Arrow Flight channel server.
Extends the base with gRPC location tracking — the daemon writes its
actual port to a file, which ``acquire()`` reads and stores in state.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from litmus.data._daemon_lifecycle import DaemonManager


class FlightDaemonManager(DaemonManager):
    """Manages the Arrow Flight channel streaming daemon."""

    _state_name = "_flight.json"
    _lock_name = "_flight.lock"
    _ready_name = "_flight_port"  # port file doubles as ready signal
    _pid_name = "_flight_pid"

    def __init__(
        self,
        channels_dir: Path,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        super().__init__(channels_dir)
        self._host = host
        self._port = port

    def _spawn_cmd(self) -> list[str]:
        return [
            sys.executable,
            "-m",
            "litmus.data.channels._flight_daemon",
            str(self._dir),
            self._host,
            str(self._port),
        ]

    def _post_spawn_state(self) -> dict:
        """Read the location from the port file after daemon starts."""
        port_file = self._dir / self._ready_name
        return {"location": port_file.read_text().strip()}

    def acquire_location(self) -> str:
        """Acquire a reference and return the gRPC location string.

        Reuse-path acquires can land before the daemon has written its
        location into state on slow CI runners — poll briefly so the
        transient case doesn't leak as a RuntimeError. Mirrors the
        same retry on :func:`litmus.data.runs_duckdb_manager.acquire`.
        """
        super().acquire()
        deadline = time.monotonic() + 5.0
        while True:
            location = self.read_state().get("location")
            if location:
                return location
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Flight daemon started but no location in state after 5s: {self._dir}"
                )
            time.sleep(0.05)


# Module-level convenience API


def acquire(channels_dir: Path, host: str = "127.0.0.1", port: int = 0) -> str:
    """Acquire a reference to the Flight server, starting it if needed.

    Returns the gRPC location string (e.g. ``grpc://127.0.0.1:12345``).
    """
    return FlightDaemonManager(channels_dir, host, port).acquire_location()


def release(channels_dir: Path) -> None:
    """Release our reference to the Flight server."""
    FlightDaemonManager(channels_dir).release()
