"""Operator UI server command."""

from __future__ import annotations

from pathlib import Path

import click

from testerkit.cli.root import main

_PKG_ROOT = Path(__file__).resolve().parent.parent


@main.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8000, help="Port to bind to")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def serve(host: str, port: int, reload: bool):
    """Start the operator UI server."""
    if reload:
        import uvicorn

        testerkit_pkg = _PKG_ROOT
        uvicorn.run(
            "testerkit.ui._asgi:app",
            host=host,
            port=port,
            reload=True,
            reload_dirs=[str(testerkit_pkg)],
            reload_includes=["*.py", "*.yaml"],
            log_level="warning",
            timeout_graceful_shutdown=2,
        )
    else:
        from nicegui import ui

        from testerkit.api.app import create_app

        create_app()
        # ``timeout_graceful_shutdown=2`` makes Ctrl+C exit within ~2s
        # even when WebSocket clients are still connected. Without it,
        # uvicorn waits indefinitely for connections to close.
        ui.run(
            host=host,
            port=port,
            reload=False,
            title="TesterKit",
            favicon="⚡",
            timeout_graceful_shutdown=2,
        )
