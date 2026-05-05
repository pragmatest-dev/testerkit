"""ASGI app entry point for uvicorn auto-reload support.

When ``litmus serve --reload`` is used, uvicorn watches for file changes and
restarts the worker process.  On each restart the worker re-imports this module
which:

1. Imports ``nicegui`` (creating a fresh ``core.app``).
2. Imports all UI pages (``@ui.page`` decorators register routes on ``core.app``).
3. Adds the FastAPI API router.
4. Calls ``add_run_config()`` so the NiceGUI lifespan check passes.
5. Adds standard middleware that ``ui.run()`` normally applies.

The exported ``app`` is the NiceGUI ASGI application ready for uvicorn.
"""

from __future__ import annotations

import sys
import time


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


_start = time.perf_counter()
_log(f"[ASGI] Starting reload at {time.strftime('%H:%M:%S')}")

from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402

_log(f"[ASGI] +{(time.perf_counter() - _start) * 1000:.0f}ms - fastapi imported")

from nicegui import core  # noqa: E402

_log(f"[ASGI] +{(time.perf_counter() - _start) * 1000:.0f}ms - nicegui.core imported")

from nicegui.middlewares import RedirectWithPrefixMiddleware, SetCacheControlMiddleware  # noqa: E402, I001

_log(f"[ASGI] +{(time.perf_counter() - _start) * 1000:.0f}ms - middlewares imported")

# Register UI pages on core.app (side-effect imports)
import litmus.ui.app  # noqa: F401, E402  # pyright: ignore[reportUnusedImport]

_log(f"[ASGI] +{(time.perf_counter() - _start) * 1000:.0f}ms - litmus.ui.app imported")

from litmus.api.app import create_api_router  # noqa: E402

_log(f"[ASGI] +{(time.perf_counter() - _start) * 1000:.0f}ms - api router imported")

# Add API routes
core.app.include_router(create_api_router())

# Configure NiceGUI the same way ui.run() would
if not core.app.config.has_run_config:
    core.app.config.add_run_config(
        reload=True,
        title="Litmus",
        viewport="width=device-width, initial-scale=1",
        favicon="\u26a1",
        dark=False,
        language="en-US",
        binding_refresh_interval=0.1,
        reconnect_timeout=3.0,
        message_history_length=1000,
        tailwind=True,
        prod_js=True,
        show_welcome_message=True,
    )

    # Middleware that ui.run() normally adds
    core.app.add_middleware(GZipMiddleware)
    core.app.add_middleware(RedirectWithPrefixMiddleware)
    core.app.add_middleware(SetCacheControlMiddleware)

app = core.app


def _install_global_exception_handler() -> None:
    """Toast-on-error so a buggy page never takes the server down.

    NicegUI dispatches event handler exceptions through
    ``app.on_exception``; without a handler they bubble to the
    ASGI worker as 500s and (occasionally) escape to the
    terminal as tracebacks. Either way, a single bad lambda in
    one page should not break navigation to other pages.

    The handler:

    * Logs the full traceback to stderr (operators / devs see it).
    * Posts a small ``ui.notify`` "negative" toast to the affected
      browser session — same UX as NiceGUI's built-in
      "Connection lost" banner, just for application errors.

    The toast itself is best-effort: ``ui.notify`` requires an
    active client context. If the exception fires outside a page
    handler (e.g. background thread, timer callback), the notify
    silently no-ops and we just log.
    """
    import logging
    import traceback

    from nicegui import app as nicegui_app
    from nicegui import ui

    logger = logging.getLogger("litmus.ui")

    def _on_exception(exc: Exception | None = None) -> None:
        # ``exc`` may be None depending on dispatcher; pull from
        # ``sys.exc_info`` as a fallback so we always have *something*
        # to log.
        if exc is None:
            import sys

            _, exc_val, _ = sys.exc_info()
            exc = exc_val if isinstance(exc_val, Exception) else None
        if exc is not None:
            logger.error(
                "UI handler raised %s: %s\n%s",
                type(exc).__name__,
                exc,
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            )
        try:
            label = type(exc).__name__ if exc is not None else "Error"
            ui.notify(
                f"Page error: {label} — see server log for details",
                type="negative",
                position="bottom-right",
                timeout=5000,
            )
        except Exception:  # noqa: BLE001 — toast is best-effort outside client ctx
            pass

    nicegui_app.on_exception(_on_exception)


_install_global_exception_handler()


# Diagnostic thread-count logger — same one ``create_app`` starts
# in the non-reload path. Both paths converge on the shared helper
# in ``litmus.api.app`` so one source of truth.
from litmus.api.app import _start_thread_count_logger  # noqa: E402

_start_thread_count_logger()


def _hold_serve_level_daemon_refs() -> None:
    """Eagerly spawn the data daemons and hold persistent refs.

    Without this, every page navigation that opens a fresh
    ``RunsQuery`` / ``StepsQuery`` / ``EventStore`` does its own
    acquire/release. Refs thrash 0→1→0→1, and as soon as no page
    is querying for ``LITMUS_DAEMON_IDLE_TIMEOUT`` seconds, the
    daemon idle-shuts-down. The next click finds a dead daemon
    and waits for a fresh spawn — visible as ~10s page loads.

    The ``litmus serve`` process itself should be the persistent
    holder. Acquiring here at module-load time spawns all three
    daemons (runs, events, channels) eagerly and registers an
    ``atexit`` + SIGTERM handler that releases them on shutdown.
    While ``serve`` is alive, refs > 0; idle-shutdown never
    fires for the duration of the UI session.

    Eager (vs. lazy first-acquire) trades ~30MB at startup for
    uniform first-page latency. A first visit to /channels feels
    the same as a first visit to /results.
    """
    import atexit
    import signal
    from collections.abc import Callable
    from pathlib import Path

    from litmus.data import duckdb_manager as _events_mgr
    from litmus.data import runs_duckdb_manager as _runs_mgr
    from litmus.data.channels import flight_manager as _channels_mgr
    from litmus.data.results_dir import resolve_results_dir

    results = Path(resolve_results_dir())
    runs_dir = results / "runs"
    events_dir = results / "events"
    channels_dir = results / "channels"
    runs_dir.mkdir(parents=True, exist_ok=True)
    events_dir.mkdir(parents=True, exist_ok=True)
    channels_dir.mkdir(parents=True, exist_ok=True)

    releases: list[Callable[[], None]] = []
    try:
        _runs_mgr.acquire(runs_dir)
        releases.append(lambda: _runs_mgr.release(runs_dir))
    except Exception as exc:  # noqa: BLE001 — best-effort eager acquire
        _log(f"[ASGI] runs daemon eager acquire failed: {exc}")
    try:
        _events_mgr.acquire(events_dir)
        releases.append(lambda: _events_mgr.release(events_dir))
    except Exception as exc:  # noqa: BLE001
        _log(f"[ASGI] events daemon eager acquire failed: {exc}")
    try:
        _channels_mgr.acquire(channels_dir)
        releases.append(lambda: _channels_mgr.release(channels_dir))
    except Exception as exc:  # noqa: BLE001
        _log(f"[ASGI] channels daemon eager acquire failed: {exc}")

    def _drain() -> None:
        # Pop in reverse-acquire order so release matches acquire
        # ordering (predictable for debugging FileLock contention).
        while releases:
            try:
                releases.pop()()
            except Exception as exc:  # noqa: BLE001 — shutdown is best-effort
                _log(f"[ASGI] daemon release failed (continuing): {exc}")

    atexit.register(_drain)
    # Uvicorn handles SIGINT/SIGTERM itself; this is belt-and-suspenders
    # for direct invocation paths where signals aren't intercepted.
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous = signal.getsignal(sig)

            def _on_signal(signum, frame, _previous=previous):  # noqa: ARG001
                _drain()
                if callable(_previous):
                    _previous(signum, frame)

            signal.signal(sig, _on_signal)
        except (OSError, ValueError):
            # Non-main thread or signal already claimed; atexit covers us.
            pass


_hold_serve_level_daemon_refs()
_log(f"[ASGI] +{(time.perf_counter() - _start) * 1000:.0f}ms - READY")
