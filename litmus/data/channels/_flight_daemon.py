"""Standalone Flight server daemon.

Spawned by ``flight_manager.acquire()`` as a detached process.
Monitors ref count and exits after idle timeout.

Usage (internal — not called directly)::

    python -m litmus.data.channels._flight_daemon <channels_dir> <host> <port>
"""

from __future__ import annotations

import sys
from pathlib import Path

from litmus.data.channels.flight_manager import daemon_run

if __name__ == "__main__":
    channels_dir = Path(sys.argv[1])
    host = sys.argv[2]
    port = int(sys.argv[3])
    daemon_run(channels_dir, host, port)
