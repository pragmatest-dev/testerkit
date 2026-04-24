"""Flight server daemon manager.

Subclasses ``DaemonManager`` for the Arrow Flight channel server.
Extends the base with gRPC location tracking — the daemon writes its
actual port to a file, which ``acquire()`` reads and stores in state.
"""

from __future__ import annotations

import sys
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
        """Acquire a reference and return the gRPC location string."""
        super().acquire()
        state = self.read_state()
        location = state.get("location")
        if not location:
            raise RuntimeError(f"Flight daemon started but no location in state: {self._dir}")
        return location


# Module-level convenience API


def acquire(channels_dir: Path, host: str = "127.0.0.1", port: int = 0) -> str:
    """Acquire a reference to the Flight server, starting it if needed.

    Returns the gRPC location string (e.g. ``grpc://127.0.0.1:12345``).
    """
    return FlightDaemonManager(channels_dir, host, port).acquire_location()


def release(channels_dir: Path) -> None:
    """Release our reference to the Flight server."""
    FlightDaemonManager(channels_dir).release()
