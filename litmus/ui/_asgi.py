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

from fastapi.middleware.gzip import GZipMiddleware

_log(f"[ASGI] +{(time.perf_counter() - _start)*1000:.0f}ms - fastapi imported")

from nicegui import core

_log(f"[ASGI] +{(time.perf_counter() - _start)*1000:.0f}ms - nicegui.core imported")

from nicegui.middlewares import RedirectWithPrefixMiddleware, SetCacheControlMiddleware

_log(f"[ASGI] +{(time.perf_counter() - _start)*1000:.0f}ms - middlewares imported")

# Register UI pages on core.app (side-effect imports)
import litmus.ui.app  # noqa: F401, E402

_log(f"[ASGI] +{(time.perf_counter() - _start)*1000:.0f}ms - litmus.ui.app imported")

from litmus.api.app import create_api_router

_log(f"[ASGI] +{(time.perf_counter() - _start)*1000:.0f}ms - api router imported")

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
_log(f"[ASGI] +{(time.perf_counter() - _start)*1000:.0f}ms - READY")
