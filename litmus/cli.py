"""Litmus command-line interface."""

import click


@click.group()
@click.version_option(version="0.1.0")
def main():
    """Litmus hardware test platform."""
    pass


@main.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8000, help="Port to bind to")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def serve(host: str, port: int, reload: bool):
    """Start the operator UI server."""
    from nicegui import ui

    # Import to register pages and API routes
    from litmus.api.app import create_app

    create_app()

    ui.run(
        host=host,
        port=port,
        reload=reload,
        title="Litmus",
        favicon="⚡",
    )


@main.command()
@click.option("--results-dir", default="results", help="Results directory")
@click.option("--limit", default=20, help="Number of runs to show")
def runs(results_dir: str, limit: int):
    """List recent test runs."""
    from litmus.data.backends.parquet import ParquetBackend

    backend = ParquetBackend(results_dir=results_dir)
    test_runs = backend.list_runs(limit=limit)

    if not test_runs:
        click.echo("No test runs found.")
        return

    click.echo(f"{'Run ID':<10} {'DUT Serial':<15} {'Station':<20} {'Outcome':<10}")
    click.echo("-" * 60)

    for run in test_runs:
        run_id = run.get("test_run_id", "")[:8]
        dut = run.get("dut_serial", "")
        station = run.get("station_id", "")
        outcome = run.get("outcome", "")
        click.echo(f"{run_id:<10} {dut:<15} {station:<20} {outcome:<10}")


@main.command()
@click.argument("run_id")
@click.option("--results-dir", default="results", help="Results directory")
def show(run_id: str, results_dir: str):
    """Show details for a specific test run."""
    from litmus.data.backends.parquet import ParquetBackend

    backend = ParquetBackend(results_dir=results_dir)
    run = backend.get_run(run_id)

    if not run:
        click.echo(f"Run {run_id} not found.")
        return

    click.echo(f"Test Run: {run.get('test_run_id', '')}")
    click.echo(f"  DUT Serial: {run.get('dut_serial', '')}")
    click.echo(f"  Station: {run.get('station_id', '')}")
    click.echo(f"  Outcome: {run.get('outcome', '')}")
    click.echo(f"  Started: {run.get('started_at', '')}")
    click.echo(f"  Ended: {run.get('ended_at', '')}")
    click.echo(f"  Steps: {run.get('total_steps', 0)} ({run.get('failed_steps', 0)} failed)")
    click.echo(f"  Vectors: {run.get('total_vectors', 0)} ({run.get('failed_vectors', 0)} failed)")

    # Show measurements
    measurements = backend.get_measurements(run_id)
    if measurements:
        click.echo("\nMeasurements:")
        for m in measurements:
            name = m.get("measurement_name", "")
            value = m.get("value", "")
            units = m.get("units", "")
            outcome = m.get("outcome", "")
            click.echo(f"  {name}: {value} {units} [{outcome}]")


# -----------------------------------------------------------------------------
# MCP Server Commands
# -----------------------------------------------------------------------------


@main.group()
def mcp():
    """MCP server commands for AI-assisted workflows."""
    pass


@mcp.command("serve")
@click.option("--transport", default="stdio", help="Transport type (stdio, sse)")
def mcp_serve(transport: str):
    """Start the MCP server for AI agents.

    The MCP server exposes tools for:
    - Reading product specs, stations, instruments
    - Capability matching
    - Saving new specs, sequences, tests
    - Running tests

    Configure Claude Code to use this server:
        claude mcp add litmus -- litmus mcp serve
    """
    from litmus.mcp.server import create_mcp_server

    mcp_server = create_mcp_server()

    if transport == "stdio":
        mcp_server.run()
    else:
        click.echo(f"Transport '{transport}' not yet supported. Use 'stdio'.")


# -----------------------------------------------------------------------------
# Setup Commands for AI Tools
# -----------------------------------------------------------------------------


@main.group()
def setup():
    """Configure AI tool integrations."""
    pass


@setup.command("claude-code")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_claude_code(print_only: bool):
    """Configure Litmus MCP server for Claude Code.

    Adds the Litmus MCP server to Claude Code's configuration.

    Example:
        litmus setup claude-code
    """
    import json
    import subprocess
    import sys
    from pathlib import Path

    # Find the litmus executable path
    litmus_path = Path(sys.executable).parent / "litmus"
    if not litmus_path.exists():
        # Fall back to assuming it's on PATH
        litmus_path = Path("litmus")

    config = {
        "name": "litmus",
        "command": str(litmus_path),
        "args": ["mcp", "serve"],
    }

    if print_only:
        click.echo("Add this to your Claude Code MCP configuration:\n")
        click.echo(json.dumps(config, indent=2))
        click.echo("\nOr run: litmus setup claude-code")
        return

    # Try to add via claude CLI
    try:
        result = subprocess.run(
            ["claude", "mcp", "add", "litmus", "--", str(litmus_path), "mcp", "serve"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            click.echo("Litmus MCP server added to Claude Code.")
            click.echo("Restart Claude Code to use Litmus tools.")
        else:
            click.echo("Could not add via claude CLI. Add manually:")
            click.echo(f"\n  claude mcp add litmus -- {litmus_path} mcp serve\n")
    except FileNotFoundError:
        click.echo("Claude CLI not found. Add manually:")
        click.echo(f"\n  claude mcp add litmus -- {litmus_path} mcp serve\n")


@setup.command("claude-desktop")
@click.argument("project_path", required=False, type=click.Path(exists=True))
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_claude_desktop(project_path: str | None, print_only: bool):
    """Configure Litmus MCP server for Claude Desktop.

    Examples:
        litmus setup claude-desktop /path/to/project
        litmus setup claude-desktop  # uses current directory
    """
    import json
    import os
    import sys
    from pathlib import Path

    litmus_path = Path(sys.executable).parent / "litmus"
    project_dir = Path(project_path).resolve() if project_path else Path.cwd()

    # Determine config location by platform
    if sys.platform == "win32":
        config_dir = Path(os.environ.get("APPDATA", "")) / "Claude"
    elif sys.platform == "darwin":
        config_dir = Path.home() / "Library" / "Application Support" / "Claude"
    else:
        config_dir = Path.home() / ".config" / "Claude"

    server_config = {
        "command": str(litmus_path),
        "args": ["mcp", "serve"],
        "cwd": str(project_dir),
    }

    if print_only:
        click.echo("claude_desktop_config.json:\n")
        click.echo(json.dumps({"mcpServers": {"litmus": server_config}}, indent=2))
        click.echo(f"\nConfig location: {config_dir / 'claude_desktop_config.json'}")
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "claude_desktop_config.json"

    if config_file.exists():
        config = json.loads(config_file.read_text())
    else:
        config = {}

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    config["mcpServers"]["litmus"] = server_config

    config_file.write_text(json.dumps(config, indent=2) + "\n")
    click.echo(f"Wrote {config_file}")
    click.echo(f"Project: {project_dir}")
    click.echo("Restart Claude Desktop to use Litmus tools.")


@setup.command("cursor")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_cursor(print_only: bool):
    """Configure Litmus MCP server for Cursor.

    Creates or updates .cursor/mcp.json in the current project.

    Example:
        litmus setup cursor
    """
    import json
    import sys
    from pathlib import Path

    # Find the litmus executable path
    litmus_path = Path(sys.executable).parent / "litmus"
    if not litmus_path.exists():
        litmus_path = Path("litmus")

    config = {
        "mcpServers": {
            "litmus": {
                "command": str(litmus_path),
                "args": ["mcp", "serve"],
            }
        }
    }

    if print_only:
        click.echo("Add this to .cursor/mcp.json:\n")
        click.echo(json.dumps(config, indent=2))
        return

    # Create/update .cursor/mcp.json
    cursor_dir = Path.cwd() / ".cursor"
    cursor_dir.mkdir(exist_ok=True)
    mcp_file = cursor_dir / "mcp.json"

    if mcp_file.exists():
        # Merge with existing config
        existing = json.loads(mcp_file.read_text())
        if "mcpServers" not in existing:
            existing["mcpServers"] = {}
        existing["mcpServers"]["litmus"] = config["mcpServers"]["litmus"]
        config = existing

    mcp_file.write_text(json.dumps(config, indent=2) + "\n")
    click.echo(f"Wrote {mcp_file}")
    click.echo("Restart Cursor to use Litmus tools.")


@setup.command("cline")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_cline(print_only: bool):
    """Configure Litmus MCP server for Cline (VS Code extension).

    Creates or updates cline_mcp_settings.json in VS Code settings.

    Example:
        litmus setup cline
    """
    import json
    import sys
    from pathlib import Path

    # Find the litmus executable path
    litmus_path = Path(sys.executable).parent / "litmus"
    if not litmus_path.exists():
        litmus_path = Path("litmus")

    config = {
        "mcpServers": {
            "litmus": {
                "command": str(litmus_path),
                "args": ["mcp", "serve"],
            }
        }
    }

    if print_only:
        click.echo("Add this to your Cline MCP settings:\n")
        click.echo(json.dumps(config, indent=2))
        return

    # Try to find VS Code settings directory
    home = Path.home()
    vscode_dirs = [
        home / ".config" / "Code" / "User",  # Linux
        home / "Library" / "Application Support" / "Code" / "User",  # macOS
        home / "AppData" / "Roaming" / "Code" / "User",  # Windows
    ]

    settings_dir = None
    for d in vscode_dirs:
        if d.exists():
            settings_dir = d
            break

    if not settings_dir:
        click.echo("VS Code settings directory not found. Add manually:")
        click.echo(json.dumps(config, indent=2))
        return

    mcp_file = settings_dir / "cline_mcp_settings.json"

    if mcp_file.exists():
        existing = json.loads(mcp_file.read_text())
        if "mcpServers" not in existing:
            existing["mcpServers"] = {}
        existing["mcpServers"]["litmus"] = config["mcpServers"]["litmus"]
        config = existing

    mcp_file.write_text(json.dumps(config, indent=2) + "\n")
    click.echo(f"Wrote {mcp_file}")
    click.echo("Restart VS Code to use Litmus tools with Cline.")


@setup.command("show")
def setup_show():
    """Show current MCP server configuration.

    Displays the command to start the Litmus MCP server.
    """
    import sys
    from pathlib import Path

    litmus_path = Path(sys.executable).parent / "litmus"
    if not litmus_path.exists():
        litmus_path = Path("litmus")

    click.echo("Litmus MCP Server")
    click.echo("-" * 40)
    click.echo(f"Command: {litmus_path} mcp serve")
    click.echo("Transport: stdio")
    click.echo()
    click.echo("Available tools:")
    click.echo("  - list_products: List all product specifications")
    click.echo("  - get_product_spec: Get a product specification by ID")
    click.echo("  - list_stations: List all test stations")
    click.echo("  - get_station_config: Get a station configuration by ID")
    click.echo("  - find_compatible_stations: Find stations for a product")
    click.echo("  - check_station_compatibility: Check if station can test product")
    click.echo("  - derive_required_capabilities: Get capability requirements")
    click.echo("  - get_instrument_library: Get instrument definitions")
    click.echo("  - list_sequences: List test sequences")
    click.echo("  - save_product_spec: Save a new product specification")
    click.echo("  - save_test_sequence: Save a new test sequence")


if __name__ == "__main__":
    main()
