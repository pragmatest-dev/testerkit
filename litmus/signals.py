"""Module-level SIGTERM + atexit cleanup callback registry.

One signal handler dispatches to all registered callbacks. This avoids
conflicts when multiple StationConnections or StationConnection + pytest
coexist in a single process.

Callbacks are keyed by a string (typically ``session_id``) so they can
be deregistered when a session ends cleanly.
"""

from __future__ import annotations

import atexit
import signal
from collections.abc import Callable
from typing import Any

_callbacks: dict[str, Callable[[], None]] = {}
_installed = False


def _run_all() -> None:
    """Execute all registered callbacks (best-effort)."""
    for key in list(_callbacks):
        try:
            _callbacks.pop(key)()
        except Exception:
            pass


def _install_handlers() -> None:
    """Install SIGTERM handler and atexit hook (once)."""
    global _installed
    if _installed:
        return

    atexit.register(_run_all)

    prev_handler = signal.getsignal(signal.SIGTERM)

    def _sigterm(signum: int, frame: Any) -> None:
        _run_all()
        # Chain to previous handler if callable
        if callable(prev_handler) and prev_handler not in (signal.SIG_DFL, signal.SIG_IGN):
            prev_handler(signum, frame)

    try:
        signal.signal(signal.SIGTERM, _sigterm)
    except (OSError, ValueError):
        pass  # Can't set signal handler (e.g. not main thread)

    _installed = True


def register_cleanup(key: str, callback: Callable[[], None]) -> None:
    """Register a cleanup callback (called on SIGTERM/atexit)."""
    _install_handlers()
    _callbacks[key] = callback


def deregister_cleanup(key: str) -> None:
    """Remove a cleanup callback (session ended cleanly)."""
    _callbacks.pop(key, None)
