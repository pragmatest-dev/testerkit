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
    if reload:
        # In reload mode we use uvicorn directly with our ASGI entry point.
        # On each reload cycle uvicorn re-imports litmus.ui._asgi which
        # re-registers pages and configures NiceGUI from scratch.
        import uvicorn

        uvicorn.run(
            "litmus.ui._asgi:app",
            host=host,
            port=port,
            reload=True,
            reload_dirs=["litmus"],
            reload_includes=["*.py"],
            log_level="warning",
        )
    else:
        from nicegui import ui

        from litmus.api.app import create_app

        create_app()

        ui.run(
            host=host,
            port=port,
            reload=False,
            title="Litmus",
            favicon="⚡",
        )


@main.command()
@click.option("--results-dir", default=None, help="Results directory")
@click.option("--limit", default=20, help="Number of runs to show")
def runs(results_dir: str | None, limit: int):
    """List recent test runs."""
    from litmus.config.project import load_project_config
    from litmus.data.backends.parquet import ParquetBackend

    if results_dir is None:
        project = load_project_config()
        results_dir = project.get("results_dir", "results")

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
@click.option("--results-dir", default=None, help="Results directory")
@click.option(
    "-f", "--format", "fmt",
    type=click.Choice(["html", "pdf", "json", "csv"]),
    default=None, help="Generate report in format",
)
@click.option("-o", "--output", default=None, help="Output file or directory")
@click.option("-t", "--template", default="default", help="Report template name")
def show(run_id: str, results_dir: str | None, fmt: str | None, output: str | None, template: str):
    """Show details for a specific test run.

    Without -f, prints a summary to the terminal.
    With -f, generates a report file (html, pdf, json, csv).

    Examples:
        litmus show abc123
        litmus show abc123 -f html
        litmus show abc123 -f pdf -o reports/
        litmus show abc123 -f json -o result.json
    """
    from litmus.config.project import load_project_config

    project = load_project_config()
    if results_dir is None:
        results_dir = project.get("results_dir", "results")

    if fmt:
        # Report generation mode
        from litmus.reports import generate_report, load_run_data

        try:
            data = load_run_data(run_id, results_dir)
        except FileNotFoundError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)

        out_path = output or "."
        result = generate_report(data, out_path, fmt=fmt, template=template)
        click.echo(f"Report generated: {result}")
        return

    # Terminal display mode
    from litmus.reports import load_run_data

    try:
        data = load_run_data(run_id, results_dir)
    except FileNotFoundError:
        click.echo(f"Run {run_id} not found.")
        return

    click.echo(f"Test Run: {data.run_id}")
    click.echo(f"  DUT Serial: {data.dut_serial}")
    click.echo(f"  Station: {data.station_id}")
    click.echo(f"  Outcome: {data.outcome}")
    click.echo(f"  Started: {data.started_at}")
    click.echo(f"  Ended: {data.ended_at}")
    click.echo(f"  Steps: {len(data.step_names)}")
    click.echo(f"  Measurements: {data.total_measurements} ({data.failed_measurements} failed)")

    if data.measurements:
        click.echo("\nMeasurements:")
        for m in data.measurements:
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
def schema():
    """JSON Schema generation for YAML validation."""
    pass


@schema.command("export")
@click.option(
    "--output-dir", "-o", default="schemas", help="Directory for .schema.json files"
)
def schema_export(output_dir: str):
    """Export JSON Schema files for all Litmus YAML types.

    Generates .schema.json files that enable editor validation and
    autocomplete for catalog, product, station, sequence, and fixture YAML.

    Example:
        litmus schema export
        litmus schema export -o litmus/schemas
    """
    from pathlib import Path

    from litmus.schemas import export_schemas

    paths = export_schemas(Path(output_dir))
    for p in paths:
        click.echo(f"  {p}")
    click.echo(f"\nExported {len(paths)} schema(s) to {output_dir}/")


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


# -----------------------------------------------------------------------------
# Instrument Discovery Commands
# -----------------------------------------------------------------------------


@main.command()
@click.option("--visa", "visa_only", is_flag=True, help="VISA instruments only")
@click.option("--ni", "ni_only", is_flag=True, help="NI devices only")
@click.option("--serial", "serial_only", is_flag=True, help="Serial ports only")
@click.option("--lxi", "lxi_only", is_flag=True, help="LXI network instruments only")
@click.option("--identify/--no-identify", default=True, help="Query *IDN? for each instrument")
def discover(visa_only: bool, ni_only: bool, serial_only: bool, lxi_only: bool, identify: bool):
    """Scan for available instruments.

    This is a SLOW operation that scans all configured backends.
    Use at setup time, not during test execution.

    Examples:
        litmus discover              # Scan all protocols
        litmus discover --visa       # VISA only
        litmus discover --no-identify  # Skip *IDN? queries (faster)
    """
    from litmus.instruments.discovery import discover as do_discover
    from litmus.instruments.discovery import discover_and_identify

    # Determine which protocols to scan
    protocols = None
    if visa_only:
        protocols = ["visa"]
    elif ni_only:
        protocols = ["ni"]
    elif serial_only:
        protocols = ["serial"]
    elif lxi_only:
        protocols = ["lxi"]

    click.echo("Scanning for instruments...")

    if identify:
        results = discover_and_identify(protocols)
        for proto, items in results.items():
            if not items:
                click.echo(f"\n{proto.upper()}: No instruments found")
                continue

            click.echo(f"\n{proto.upper()}: Found {len(items)} instrument(s)")
            click.echo("-" * 60)

            for resource, info in items:
                if info:
                    mfr = info.manufacturer or "Unknown"
                    model = info.model or "Unknown"
                    serial = info.serial or ""
                    serial_str = f" (SN: {serial})" if serial else ""
                    click.echo(f"  {resource}")
                    click.echo(f"    → {mfr} {model}{serial_str}")
                else:
                    click.echo(f"  {resource}")
                    click.echo("    → Could not query identity")
    else:
        results = do_discover(protocols)
        for proto, resources in results.items():
            if not resources:
                click.echo(f"\n{proto.upper()}: No instruments found")
                continue

            click.echo(f"\n{proto.upper()}: Found {len(resources)} instrument(s)")
            click.echo("-" * 60)

            for resource in resources:
                click.echo(f"  {resource}")


# -----------------------------------------------------------------------------
# Station Management Commands
# -----------------------------------------------------------------------------


@main.group()
def station():
    """Station management commands."""
    pass


@station.command("init")
@click.option("--station-id", prompt="Station ID", help="Unique station identifier")
@click.option("--name", prompt="Station name", help="Human-readable station name")
@click.option("--location", default=None, help="Physical location")
def station_init(station_id: str, name: str, location: str | None):
    """Initialize a new station configuration.

    Interactively discovers instruments and creates station/instrument files.

    Example:
        litmus station init
        litmus station init --station-id bench_01 --name "Engineering Bench"
    """
    from pathlib import Path

    import yaml

    from litmus.instruments.discovery import discover_and_identify

    # Create directories
    stations_dir = Path("stations")
    instruments_dir = Path("instruments")
    stations_dir.mkdir(exist_ok=True)
    instruments_dir.mkdir(exist_ok=True)

    # Discover instruments
    click.echo("\nDiscovering instruments...")
    results = discover_and_identify(["visa"])

    # Collect instruments for this station
    station_instruments = {}
    station_resources = {}
    instrument_count = 0

    for proto, items in results.items():
        for resource, info in items:
            instrument_count += 1

            if info:
                mfr = info.manufacturer or "Unknown"
                model = info.model or "Unknown"
                serial = info.serial or ""
                click.echo(f"\n[{instrument_count}] {resource}")
                click.echo(f"    {mfr} {model} (SN: {serial})")
            else:
                click.echo(f"\n[{instrument_count}] {resource}")
                click.echo("    Could not query identity")

            # Ask for role
            role = click.prompt("  Assign role (e.g., dmm, psu) or 'skip'", default="skip")
            if role.lower() == "skip":
                continue

            # Generate instrument ID
            if info and info.serial:
                inst_id = f"{role}_{info.serial}".lower().replace(" ", "_")
            else:
                inst_id = f"{role}_{instrument_count}".lower()

            inst_id = click.prompt("  Instrument ID", default=inst_id)

            # Ask for driver
            driver = click.prompt(
                "  Driver class (e.g., pymeasure.instruments.keithley.Keithley2000)",
                default="",
            )

            # Create instrument file
            inst_data = {
                "id": inst_id,
                "protocol": proto,
            }

            if driver:
                inst_data["driver"] = driver

            if info:
                inst_data["info"] = {
                    "manufacturer": info.manufacturer,
                    "model": info.model,
                    "serial": info.serial,
                    "firmware": info.firmware,
                }

            # Write instrument file
            inst_file = instruments_dir / f"{inst_id}.yaml"
            with open(inst_file, "w") as f:
                yaml.dump(inst_data, f, default_flow_style=False, sort_keys=False)
            click.echo(f"  Created {inst_file}")

            station_instruments[role] = inst_id
            station_resources[inst_id] = resource

    # Create station file
    station_data = {
        "station": {
            "id": station_id,
            "name": name,
        },
        "instruments": station_instruments,
        "resources": station_resources,
    }

    if location:
        station_data["station"]["location"] = location

    station_file = stations_dir / f"{station_id}.yaml"
    with open(station_file, "w") as f:
        yaml.dump(station_data, f, default_flow_style=False, sort_keys=False)

    click.echo(f"\nCreated {station_file}")
    click.echo(f"Created {len(station_instruments)} instrument file(s)")
    click.echo(f"\nRun tests with: pytest --station={station_id}")


@station.command("validate")
@click.argument("station_id", required=False)
@click.option("--strict", is_flag=True, help="Fail on any mismatch")
def station_validate(station_id: str | None, strict: bool):
    """Validate station instruments against configuration.

    Checks that expected instruments are present and identity matches.

    Example:
        litmus station validate bench_01
        litmus station validate --strict
    """
    from litmus.instruments.discovery import get_info_visa
    from litmus.instruments.loader import (
        find_instruments_dir,
        find_stations_dir,
        load_instrument_files,
        load_station_file,
        resolve_station_instruments,
    )

    # Find station file
    stations_dir = find_stations_dir()
    if not stations_dir:
        click.echo("Error: No stations/ directory found", err=True)
        raise SystemExit(1)

    # If no station_id, list available
    if not station_id:
        click.echo("Available stations:")
        for f in stations_dir.glob("*.yaml"):
            click.echo(f"  {f.stem}")
        return

    station_file = stations_dir / f"{station_id}.yaml"
    if not station_file.exists():
        click.echo(f"Error: Station file not found: {station_file}", err=True)
        raise SystemExit(1)

    # Load configs
    station_config = load_station_file(station_file)
    instruments_dir = find_instruments_dir()
    instrument_files = load_instrument_files(instruments_dir) if instruments_dir else {}
    records = resolve_station_instruments(station_config, instrument_files)

    click.echo(f"Validating station: {station_id}")
    click.echo("-" * 50)

    errors = []
    warnings_list = []

    for role, record in records.items():
        click.echo(f"\n{role}: {record.instrument_id}")
        click.echo(f"  Resource: {record.resource}")

        # Query actual instrument
        actual_info = None
        if record.protocol == "visa":
            actual_info = get_info_visa(record.resource)

        if actual_info is None:
            msg = f"  [ERROR] Could not connect to {record.resource}"
            click.echo(click.style(msg, fg="red"))
            errors.append(f"{role}: not reachable")
            continue

        # Compare identity
        expected = record.info
        if expected:
            matches, mismatches = actual_info.matches(expected)
            if matches:
                click.echo(click.style("  [OK] Identity matches", fg="green"))
            else:
                for m in mismatches:
                    msg = f"  [WARN] {m}"
                    click.echo(click.style(msg, fg="yellow"))
                    warnings_list.append(f"{role}: {m}")
        else:
            click.echo("  [INFO] No expected identity configured")

        # Show actual identity
        click.echo(f"  Actual: {actual_info.manufacturer} {actual_info.model}")
        if actual_info.serial:
            click.echo(f"          SN: {actual_info.serial}")

        # Check calibration
        if record.calibration and record.calibration.due_date:
            days = record.calibration.days_until_due()
            if days is not None:
                if days < 0:
                    msg = f"  [WARN] Calibration EXPIRED ({-days} days overdue)"
                    click.echo(click.style(msg, fg="red"))
                    warnings_list.append(f"{role}: calibration expired")
                elif days < 30:
                    msg = f"  [WARN] Calibration due in {days} days"
                    click.echo(click.style(msg, fg="yellow"))
                else:
                    click.echo(f"  [OK] Calibration valid ({days} days remaining)")

    # Summary
    click.echo("\n" + "-" * 50)
    if errors:
        click.echo(click.style(f"Errors: {len(errors)}", fg="red"))
        for e in errors:
            click.echo(f"  - {e}")
    if warnings_list:
        click.echo(click.style(f"Warnings: {len(warnings_list)}", fg="yellow"))

    if errors and strict:
        raise SystemExit(1)
    if not errors and not warnings_list:
        click.echo(click.style("All instruments validated successfully!", fg="green"))


@station.command("update")
@click.argument("station_id")
def station_update(station_id: str):
    """Re-discover and update instrument identity in configuration.

    Queries current instruments and updates info sections in instrument files.

    Example:
        litmus station update bench_01
    """
    import yaml

    from litmus.instruments.discovery import get_info_visa
    from litmus.instruments.loader import (
        find_instruments_dir,
        find_stations_dir,
        load_instrument_files,
        load_station_file,
        resolve_station_instruments,
    )

    stations_dir = find_stations_dir()
    instruments_dir = find_instruments_dir()

    if not stations_dir:
        click.echo("Error: No stations/ directory found", err=True)
        raise SystemExit(1)

    station_file = stations_dir / f"{station_id}.yaml"
    if not station_file.exists():
        click.echo(f"Error: Station file not found: {station_file}", err=True)
        raise SystemExit(1)

    station_config = load_station_file(station_file)
    instrument_files = load_instrument_files(instruments_dir) if instruments_dir else {}
    records = resolve_station_instruments(station_config, instrument_files)

    click.echo(f"Updating station: {station_id}")

    updated = 0
    for role, record in records.items():
        if record.protocol != "visa":
            continue

        actual_info = get_info_visa(record.resource)
        if actual_info is None:
            click.echo(f"  {role}: Could not query (skipped)")
            continue

        # Update instrument file if it exists
        if instruments_dir:
            inst_file = instruments_dir / f"{record.instrument_id}.yaml"
            if inst_file.exists():
                with open(inst_file) as f:
                    data = yaml.safe_load(f)

                data["info"] = {
                    "manufacturer": actual_info.manufacturer,
                    "model": actual_info.model,
                    "serial": actual_info.serial,
                    "firmware": actual_info.firmware,
                }

                with open(inst_file, "w") as f:
                    yaml.dump(data, f, default_flow_style=False, sort_keys=False)

                click.echo(f"  {role}: Updated {inst_file}")
                updated += 1

    click.echo(f"\nUpdated {updated} instrument file(s)")


# -----------------------------------------------------------------------------
# Instrument Management Commands
# -----------------------------------------------------------------------------


@main.group()
def instrument():
    """Instrument management commands."""
    pass


@instrument.command("list")
def instrument_list():
    """List all instrument configuration files."""
    from litmus.instruments.loader import find_instruments_dir, load_instrument_files

    instruments_dir = find_instruments_dir()
    if not instruments_dir:
        click.echo("No instruments/ directory found")
        return

    instruments = load_instrument_files(instruments_dir)
    if not instruments:
        click.echo("No instrument files found")
        return

    click.echo(f"Found {len(instruments)} instrument(s):\n")
    click.echo(f"{'ID':<25} {'Protocol':<10} {'Model':<30}")
    click.echo("-" * 65)

    for inst_id, data in sorted(instruments.items()):
        protocol = data.get("protocol", "visa")
        info = data.get("_info")
        model = ""
        if info:
            mfr = info.manufacturer or ""
            mdl = info.model or ""
            model = f"{mfr} {mdl}".strip()

        click.echo(f"{inst_id:<25} {protocol:<10} {model:<30}")


@instrument.command("show")
@click.argument("instrument_id")
def instrument_show(instrument_id: str):
    """Show details for a specific instrument."""
    from litmus.instruments.loader import find_instruments_dir, load_instrument_file

    instruments_dir = find_instruments_dir()
    if not instruments_dir:
        click.echo("No instruments/ directory found", err=True)
        raise SystemExit(1)

    inst_file = instruments_dir / f"{instrument_id}.yaml"
    if not inst_file.exists():
        click.echo(f"Instrument not found: {instrument_id}", err=True)
        raise SystemExit(1)

    data = load_instrument_file(inst_file)
    info = data.get("_info")
    cal = data.get("_calibration")

    click.echo(f"Instrument: {instrument_id}")
    click.echo("-" * 40)
    click.echo(f"Protocol: {data.get('protocol', 'visa')}")
    if data.get("driver"):
        click.echo(f"Driver: {data['driver']}")

    if info:
        click.echo("\nIdentity:")
        click.echo(f"  Manufacturer: {info.manufacturer or 'N/A'}")
        click.echo(f"  Model: {info.model or 'N/A'}")
        click.echo(f"  Serial: {info.serial or 'N/A'}")
        click.echo(f"  Firmware: {info.firmware or 'N/A'}")

    if cal and (cal.due_date or cal.last_cal or cal.certificate):
        click.echo("\nCalibration:")
        if cal.due_date:
            days = cal.days_until_due()
            status = ""
            if days is not None:
                if days < 0:
                    status = click.style(f" (EXPIRED, {-days} days overdue)", fg="red")
                elif days < 30:
                    status = click.style(f" (due soon, {days} days)", fg="yellow")
                else:
                    status = f" ({days} days remaining)"
            click.echo(f"  Due date: {cal.due_date}{status}")
        if cal.last_cal:
            click.echo(f"  Last cal: {cal.last_cal}")
        if cal.certificate:
            click.echo(f"  Certificate: {cal.certificate}")
        if cal.lab:
            click.echo(f"  Lab: {cal.lab}")


@instrument.command("cal")
@click.argument("instrument_id")
@click.option("--due", "due_date", help="Calibration due date (YYYY-MM-DD)")
@click.option("--last", "last_cal", help="Last calibration date (YYYY-MM-DD)")
@click.option("--cert", "certificate", help="Certificate number")
@click.option("--lab", help="Calibration lab name")
def instrument_cal(
    instrument_id: str,
    due_date: str | None,
    last_cal: str | None,
    certificate: str | None,
    lab: str | None,
):
    """Update calibration information for an instrument.

    Example:
        litmus instrument cal keithley_dmm_001 --due 2025-12-15 --cert CAL-2025-001
    """
    import yaml

    from litmus.instruments.loader import find_instruments_dir

    instruments_dir = find_instruments_dir()
    if not instruments_dir:
        click.echo("No instruments/ directory found", err=True)
        raise SystemExit(1)

    inst_file = instruments_dir / f"{instrument_id}.yaml"
    if not inst_file.exists():
        click.echo(f"Instrument not found: {instrument_id}", err=True)
        raise SystemExit(1)

    with open(inst_file) as f:
        data = yaml.safe_load(f)

    if "calibration" not in data:
        data["calibration"] = {}

    if due_date:
        data["calibration"]["due_date"] = due_date
    if last_cal:
        data["calibration"]["last_cal"] = last_cal
    if certificate:
        data["calibration"]["certificate"] = certificate
    if lab:
        data["calibration"]["lab"] = lab

    with open(inst_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    click.echo(f"Updated calibration for {instrument_id}")


if __name__ == "__main__":
    main()
