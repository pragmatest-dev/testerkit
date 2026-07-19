"""MCP server commands for AI-assisted workflows."""

from __future__ import annotations

import click

from testerkit.cli.root import main


@main.group()
def mcp():
    """MCP server commands for AI-assisted workflows."""
    pass


@mcp.command("serve")
@click.option("--transport", default="stdio", help="Transport type (stdio, sse)")
def mcp_serve(transport: str):
    """Start the MCP server for AI agents.

    The MCP server exposes tools for:
    - Reading part specs, stations, instruments
    - Capability matching
    - Saving new specs and tests
    - Running tests

    Configure Claude Code to use this server:
        claude mcp add testerkit -- testerkit mcp serve
    """
    from testerkit.mcp.server import create_mcp_server

    mcp_server = create_mcp_server()

    if transport == "stdio":
        mcp_server.run()
    else:
        click.echo(f"Transport '{transport}' not yet supported. Use 'stdio'.")
