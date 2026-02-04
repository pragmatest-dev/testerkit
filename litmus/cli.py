"""Litmus command-line interface."""

import click


@click.group()
@click.version_option(version="0.1.0")
def main():
    """Litmus hardware test platform."""
    pass


# -----------------------------------------------------------------------------
# Project Initialization
# -----------------------------------------------------------------------------


@main.command()
@click.argument("name", required=False)
@click.option("--no-git", is_flag=True, help="Skip git initialization")
def init(name: str | None, no_git: bool):
    """Initialize a new Litmus project.

    Creates a new project directory with scaffolding for hardware tests.

    Example:

        litmus init my_project

        cd my_project

        uv sync
    """
    from pathlib import Path

    from litmus.init import check_command, init_project

    # Prompt for name if not provided
    if not name:
        name = click.prompt("Project name")

    project_path = Path.cwd() / name

    # Check if directory already exists
    if project_path.exists():
        click.echo(f"Error: '{name}' already exists", err=True)
        raise SystemExit(1)

    # Check dependencies and warn if missing
    if not check_command("git") and not no_git:
        click.echo("Warning: git not found, skipping git init")
        click.echo("  Install git: https://git-scm.com/downloads")
        no_git = True

    if not check_command("uv"):
        click.echo("Warning: uv not found")
        click.echo("  Install: curl -LsSf https://astral.sh/uv/install.sh | sh")

    # Create and initialize project
    project_path.mkdir()
    result = init_project(project_path, git=not no_git)

    # Print summary
    click.echo(f"\nCreated {name}/")
    for d in result["created_dirs"]:
        click.echo(f"  {d}/")
    for f in result["created_files"]:
        click.echo(f"  {f}")

    if result["git_initialized"]:
        click.echo("  .git/")

    for warning in result["warnings"]:
        click.echo(f"Warning: {warning}")

    click.echo("\nNext steps:")
    click.echo(f"  cd {name}")
    click.echo("  uv sync")
    click.echo("  # Edit stations/, products/, tests/")
    click.echo("  pytest tests/ --station=<station> --mock-instruments --dut-serial=TEST001")


# -----------------------------------------------------------------------------
# Server Commands
# -----------------------------------------------------------------------------


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
# Journal Management Commands
# -----------------------------------------------------------------------------


@main.command()
@click.option("--results-dir", default="results", help="Results directory")
def journals(results_dir: str):
    """List orphaned journals (from crashed or interrupted runs).

    Journals are temporary JSONL files created during test execution.
    On successful completion, they are converted to parquet and deleted.
    Orphaned journals indicate runs that crashed or were interrupted.
    """
    from litmus.data.backends.parquet import ParquetBackend

    backend = ParquetBackend(results_dir=results_dir)
    orphaned = backend.get_orphaned_journals()

    if not orphaned:
        click.echo("No orphaned journals found.")
        return

    click.echo(f"Found {len(orphaned)} orphaned journal(s):\n")
    click.echo(f"{'Run ID':<12} {'DUT Serial':<15} {'Station':<15} {'Measurements'}")
    click.echo("-" * 55)

    for j in orphaned:
        run_id = (j.get("run_id") or "")[:10]
        dut = j.get("dut_serial") or ""
        station = j.get("station_id") or ""
        count = j.get("measurement_count", 0)
        click.echo(f"{run_id:<12} {dut:<15} {station:<15} {count}")

    click.echo("\nTo recover a journal: litmus recover <journal_dir>")
    click.echo("To recover all: litmus recover --all")


@main.command()
@click.argument("journal_dir", required=False)
@click.option("--results-dir", default="results", help="Results directory")
@click.option("--all", "recover_all", is_flag=True, help="Recover all orphaned journals")
def recover(journal_dir: str | None, results_dir: str, recover_all: bool):
    """Convert orphaned journal(s) to parquet.

    Use this to recover data from crashed or interrupted test runs.

    Examples:
        litmus recover results/.journals/2026-02-03/20260203T120000Z_DUT001
        litmus recover --all
    """
    from pathlib import Path

    from litmus.data.backends.parquet import ParquetBackend

    backend = ParquetBackend(results_dir=results_dir)

    if recover_all:
        orphaned = backend.get_orphaned_journals()
        if not orphaned:
            click.echo("No orphaned journals to recover.")
            return

        for j in orphaned:
            jdir = Path(j["journal_dir"])
            try:
                parquet_path = backend.recover_journal(jdir)
                click.echo(f"Recovered: {jdir.name} -> {parquet_path}")
            except Exception as e:
                click.echo(f"Failed to recover {jdir.name}: {e}", err=True)

        click.echo(f"\nRecovered {len(orphaned)} journal(s).")
        return

    if not journal_dir:
        click.echo("Error: Specify a journal directory or use --all", err=True)
        raise SystemExit(1)

    jdir = Path(journal_dir)
    if not jdir.exists():
        click.echo(f"Error: Journal directory not found: {jdir}", err=True)
        raise SystemExit(1)

    try:
        parquet_path = backend.recover_journal(jdir)
        click.echo(f"Recovered: {parquet_path}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@main.command("cleanup-journals")
@click.option("--results-dir", default="results", help="Results directory")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted without deleting")
def cleanup_journals(results_dir: str, dry_run: bool):
    """Delete journals that have corresponding parquet files.

    Journals are normally deleted after successful conversion to parquet.
    This command cleans up any that were left behind (e.g., due to bugs).
    """
    from litmus.data.backends.parquet import ParquetBackend

    backend = ParquetBackend(results_dir=results_dir)

    if dry_run:
        # List journals that would be deleted
        all_journals = backend.list_journals()
        orphaned = backend.get_orphaned_journals()
        orphaned_dirs = {j["journal_dir"] for j in orphaned}

        deletable = [j for j in all_journals if j["journal_dir"] not in orphaned_dirs]

        if not deletable:
            click.echo("No journals to clean up.")
            return

        click.echo(f"Would delete {len(deletable)} journal(s):")
        for j in deletable:
            click.echo(f"  {j['journal_dir']}")
        return

    deleted = backend.cleanup_journals()
    if deleted == 0:
        click.echo("No journals to clean up.")
    else:
        click.echo(f"Deleted {deleted} journal(s).")


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
