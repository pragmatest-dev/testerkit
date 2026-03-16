"""Litmus command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import click


def _find_parquet_for_run(run_id: str, results_dir: str) -> Path | None:
    """Find the parquet file for a given run ID."""
    from litmus.data.backends.parquet import ParquetBackend

    backend = ParquetBackend(results_dir=results_dir)
    return backend.find_run_file(run_id)


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
@click.option("--discover", is_flag=True, help="Auto-discover instruments and create station file")
@click.option(
    "--starter/--no-starter",
    default=None,
    help="Generate starter example files (prompts if not specified)",
)
@click.option(
    "--ai",
    type=click.Choice(["claude-code", "claude-desktop", "copilot"], case_sensitive=False),
    default=None,
    help="Set up AI tool integration (MCP server + project instructions)",
)
def init(name: str | None, no_git: bool, discover: bool, starter: bool | None, ai: str | None):
    """Initialize a new Litmus project.

    With NAME: creates a new directory and scaffolds inside it.
    Without NAME: scaffolds the current directory (like ``uv init``).

    All files are skip-if-exists, so it's safe to run on an existing project.

    Examples:

        litmus init my_project

        litmus init my_project --starter

        litmus init --discover

        litmus init my_project --discover
    """
    from litmus.init import check_command, init_project

    if name:
        # New-directory mode
        project_path = Path.cwd() / name
        if project_path.exists():
            click.echo(f"Error: '{name}' already exists", err=True)
            raise SystemExit(1)
        project_path.mkdir()
        cwd_mode = False
    else:
        # Scaffold CWD mode
        project_path = Path.cwd()
        cwd_mode = True

    # Check dependencies and warn if missing
    if not check_command("git") and not no_git:
        click.echo("Warning: git not found, skipping git init")
        click.echo("  Install git: https://git-scm.com/downloads")
        no_git = True

    if not check_command("uv"):
        click.echo("Warning: uv not found")
        click.echo("  Install: curl -LsSf https://astral.sh/uv/install.sh | sh")

    # Instrument discovery vs starter files
    # - If --starter: skip discovery (starter has its own mock station)
    # - If --discover: skip starter (user wants real instruments)
    # - If neither: prompt for starter first; if declined, prompt for discovery
    station = None
    use_starter = False

    if starter is True:
        # Explicit --starter flag
        use_starter = True
    elif discover:
        # Explicit --discover flag
        station = _discover_instruments(interactive=False)
    elif starter is False:
        # Explicit --no-starter flag, prompt for discovery
        if click.confirm("Discover instruments?", default=False):
            station = _discover_instruments(interactive=True)
    else:
        # No flags provided - prompt interactively
        if click.confirm("Create starter example files?", default=True):
            use_starter = True
        elif click.confirm("Discover instruments?", default=False):
            station = _discover_instruments(interactive=True)

    result = init_project(project_path, git=not no_git, station=station, starter=use_starter)

    # Print summary
    if cwd_mode:
        click.echo(f"\nInitialized litmus project in {project_path.name}/")
    else:
        click.echo(f"\nCreated {name}/")
    for d in result["created_dirs"]:
        click.echo(f"  {d}/")
    for f in result["created_files"]:
        click.echo(f"  {f}")

    if result["git_initialized"]:
        click.echo("  .git/")

    for warning in result["warnings"]:
        click.echo(f"Warning: {warning}")

    # AI tool setup
    if ai is None:
        # Detect installed tools and prompt
        ai_tools: list[tuple[str, str]] = []
        if check_command("claude"):
            ai_tools.append(("claude-code", "Claude Code"))
        # Check for VS Code / Copilot
        if (project_path / ".vscode").exists() or check_command("code"):
            ai_tools.append(("copilot", "GitHub Copilot"))

        if ai_tools:
            choices = [name for name, _ in ai_tools]
            labels = [label for _, label in ai_tools]
            click.echo(f"\nDetected AI tools: {', '.join(labels)}")
            if click.confirm("Set up AI assistance?", default=True):
                if len(ai_tools) == 1:
                    ai = choices[0]
                else:
                    ai = click.prompt(
                        "Which tool?",
                        type=click.Choice(choices, case_sensitive=False),
                        default=choices[0],
                    )

    if ai:
        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(project_path)
            ctx = click.get_current_context()
            if ai == "claude-code":
                ctx.invoke(setup_claude_code, print_only=False)
            elif ai == "claude-desktop":
                ctx.invoke(setup_claude_desktop, legacy=False, print_only=False)
            elif ai == "copilot":
                ctx.invoke(setup_copilot, print_only=False)
        finally:
            os.chdir(original_cwd)

    click.echo("\nNext steps:")
    if not cwd_mode:
        click.echo(f"  cd {name}")
        click.echo("  uv sync")
    if use_starter:
        click.echo("  pytest                # run tests with mock instruments")
    else:
        click.echo("  pytest tests/ --mock-instruments --dut-serial=TEST001")
    click.echo("  litmus serve          # open operator UI at localhost:8000")


@main.command("new-test")
@click.argument("name")
def new_test(name: str):
    """Scaffold a new test file.

    Creates tests/test_<name>.py with instrument fixtures from your station.

    Examples:

        litmus new-test output_voltage

        litmus new-test smoke_check
    """
    # Normalize name: strip test_ prefix if present, we'll add it back
    test_name = name.removeprefix("test_")
    filename = f"test_{test_name}.py"

    tests_dir = Path.cwd() / "tests"
    target = tests_dir / filename

    if target.exists():
        click.echo(f"Error: {target} already exists", err=True)
        raise SystemExit(1)

    # Try to discover available roles from station config
    available_roles: list[str] = []
    stations: list = []
    try:
        from litmus.store import list_stations

        stations = list_stations()
        if stations:
            station = stations[0]
            available_roles = sorted(station.instruments.keys())
    except (ImportError, OSError, ValueError):
        pass

    # Prompt for instruments
    hint = ""
    if available_roles:
        hint = f" (available from station: {', '.join(available_roles)})"
    roles_input = click.prompt(
        f"Instruments to use in test{hint}, or Enter to skip",
        default="",
        show_default=False,
    )

    roles = [r.strip() for r in roles_input.split(",") if r.strip()] if roles_input else []

    # Resolve driver types for selected roles
    role_types: dict[str, tuple[str, str]] = {}
    if roles and available_roles and stations:
        try:
            from litmus.execution.typing_utils import resolve_role_types

            role_types = resolve_role_types(stations[0].instruments)
        except (ImportError, OSError, ValueError):
            pass

    # Build import lines for typed roles
    import_lines: list[str] = []
    # Collect imports: module -> {class_names}
    imports_by_module: dict[str, set[str]] = {}
    for role in roles:
        if role in role_types:
            module, cls = role_types[role]
            imports_by_module.setdefault(module, set()).add(cls)
    for module in sorted(imports_by_module):
        classes = sorted(imports_by_module[module])
        import_lines.append(f"from {module} import {', '.join(classes)}")

    # Build function signature with type annotations
    param_parts = ["context"]
    for role in roles:
        if role in role_types:
            _, cls = role_types[role]
            param_parts.append(f"{role}: {cls}")
        else:
            param_parts.append(role)
    sig = ", ".join(param_parts)

    lines = [
        f'"""Tests for {test_name}."""',
        "",
    ]
    if import_lines:
        lines.extend(import_lines)
        lines.append("")
    lines.extend([
        "from litmus.execution import litmus_test",
        "",
        "",
        "@litmus_test",
        f"def test_{test_name}({sig}):",
        f'    """Measure {test_name}."""',
    ])
    # Add a helpful skeleton showing the 3-step pattern
    if roles:
        lines.append("    # 1. GET conditions from context")
        lines.append('    # vin = context.get_in("vin", 5.0)')
        lines.append("    #")
        lines.append("    # 2. SET UP stimulus")
        first_role = roles[0]
        lines.append(f"    # {first_role}.set_voltage(vin)")
        lines.append("    #")
        lines.append("    # 3. MEASURE and RETURN (framework checks limits)")
        if len(roles) > 1:
            measure_role = roles[1]
        else:
            measure_role = roles[0]
        lines.append(f"    return {measure_role}.measure_voltage()")
    else:
        lines.append("    # TODO: Add test logic")
        lines.append("    pass")
    lines.append("")
    content = "\n".join(lines)

    tests_dir.mkdir(exist_ok=True)
    target.write_text(content)
    click.echo(f"Created {target}")
    click.echo("\nNext: pytest --mock-instruments")


@main.command("type-tests")
@click.option("--station", default=None, help="Station ID (default: auto-discover)")
@click.option("--dry-run", is_flag=True, help="Print changes without writing files")
def type_tests(station: str | None, dry_run: bool):
    """Add driver type annotations to @litmus_test functions.

    Scans tests/ for @litmus_test decorated functions and adds driver type
    annotations to parameters that match station instrument roles.

    Examples:

        litmus type-tests

        litmus type-tests --station bench_1

        litmus type-tests --dry-run
    """
    from litmus.execution.typing_utils import resolve_role_types, scan_test_files
    from litmus.store import list_stations

    # Find station
    stations = list_stations()
    if not stations:
        click.echo("No station configs found in stations/", err=True)
        raise SystemExit(1)

    if station:
        matched = [s for s in stations if s.id == station]
        if not matched:
            click.echo(f"Station '{station}' not found", err=True)
            raise SystemExit(1)
        station_config = matched[0]
    else:
        station_config = stations[0]
        if len(stations) > 1:
            click.echo(f"Using station: {station_config.id} (use --station to specify)")

    role_types = resolve_role_types(station_config.instruments)
    if not role_types:
        click.echo("No typed drivers found in station config (all mock-only?)")
        return

    # Scan test files
    test_dir = Path.cwd() / "tests"
    if not test_dir.exists():
        click.echo("No tests/ directory found", err=True)
        raise SystemExit(1)

    edits = scan_test_files(test_dir, role_types)
    if not edits:
        click.echo("All @litmus_test functions already have type annotations.")
        return

    for filepath, new_source, changes in edits:
        rel = filepath.relative_to(Path.cwd())
        click.echo(f"\n{rel}:")
        for change in changes:
            click.echo(change)
        if not dry_run:
            filepath.write_text(new_source)

    total = sum(len(c) for _, _, c in edits)
    if dry_run:
        click.echo(f"\n{total} annotation(s) to add across {len(edits)} file(s). (dry run)")
    else:
        click.echo(f"\n{total} annotation(s) added across {len(edits)} file(s).")


def _format_instrument(resource: str, info: object | None) -> str:
    """Format a discovered instrument for display.

    Returns a one-line string like 'Keysight 34465A (TCPIP::...)' or
    'TCPIP::... (could not identify)'.
    """
    if info and getattr(info, "manufacturer", None) and getattr(info, "model", None):
        mfr = info.manufacturer  # type: ignore[union-attr]
        model = info.model  # type: ignore[union-attr]
        serial = getattr(info, "serial", None) or ""
        serial_str = f" (SN: {serial})" if serial else ""
        return f"{mfr} {model}{serial_str} ({resource})"
    return f"{resource} (could not identify)"


def _discover_instruments(interactive: bool = True) -> dict[str, dict[str, dict[str, str]]] | None:
    """Discover instruments and build station data.

    Args:
        interactive: If True, prompt user for role names.
            If False, auto-name from catalog type.

    Returns:
        Station dict with ``instruments`` mapping, or None if nothing found.
    """
    from litmus.instruments.discovery import discover_and_identify

    click.echo("\nDiscovering instruments...")
    results = discover_and_identify(["visa"])

    from litmus.instruments.discovery import InstrumentInfo

    # Flatten all discovered instruments
    all_instruments: list[tuple[str, InstrumentInfo | None]] = []
    for _proto, items in results.items():
        all_instruments.extend(items)

    if not all_instruments:
        click.echo("  No instruments found.")
        return None

    # First pass: determine default role for each instrument
    pending: list[tuple[str, InstrumentInfo | None, str]] = []
    for resource, info in all_instruments:
        click.echo(f"  {_format_instrument(resource, info)}")

        # Determine default role from catalog type
        role = None
        if info and info.manufacturer and info.model:
            try:
                from litmus.store import find_by_model

                entry = find_by_model(info.manufacturer, info.model)
                if entry and entry.type:
                    role = entry.type
            except (ImportError, OSError, ValueError):
                pass

        if not role and info and info.model:
            role = info.model.lower().replace("-", "_").replace(" ", "_")

        if not role:
            role = "instrument"

        pending.append((resource, info, role))

    # Second pass: prompt for roles (interactive) or auto-assign
    assigned: list[tuple[str, str, InstrumentInfo | None]] = []
    for resource, info, default_role in pending:
        if interactive:
            label = info.model if info and info.model else resource
            role = click.prompt(f"    {label} role", default=default_role)
            if role.lower() == "skip":
                continue
        else:
            role = default_role
        assigned.append((role, resource, info))

    if not assigned:
        return None

    # Deduplicate roles: if multiple instruments share a role, number them
    role_counts: dict[str, int] = {}
    for role, _r, _i in assigned:
        role_counts[role] = role_counts.get(role, 0) + 1

    role_index: dict[str, int] = {}
    station_instruments: dict[str, dict] = {}
    for role, resource, info in assigned:
        if role_counts[role] > 1:
            idx = role_index.get(role, 0) + 1
            role_index[role] = idx
            final_role = f"{role}{idx}"
        else:
            final_role = role

        station_instruments[final_role] = {"resource": resource}

    return {"instruments": station_instruments}


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------


@main.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--type", "-t", "file_type",
    type=click.Choice([
        "catalog", "product", "station", "sequence",
        "fixture", "instrument_asset", "project",
    ]),
    default=None,
    help="Explicit file type (skips auto-detection).",
)
def validate(paths, file_type):
    """Validate YAML configuration files.

    Checks catalog, product, station, sequence, fixture, instrument, and
    project YAML files against their Pydantic schemas and reports errors
    with field paths.

    If no paths given, scans standard directories in the current project.

    Examples:
        litmus validate catalog/keysight_34461a.yaml
        litmus validate instruments/ --type instrument_asset
        litmus validate catalog/ stations/
        litmus validate
    """
    from litmus.validation import validate_yaml

    # Collect files
    files: list[Path] = []
    if paths:
        for p in paths:
            p = Path(p)
            if p.is_dir():
                files.extend(sorted(p.rglob("*.yaml")))
            else:
                files.append(p)
    else:
        # Auto-scan standard directories
        scan_dirs = [
            "catalog", "products", "stations", "sequences",
            "fixtures", "instruments",
        ]
        for dirname in scan_dirs:
            d = Path.cwd() / dirname
            if d.is_dir():
                files.extend(sorted(d.rglob("*.yaml")))
        # Also check litmus.yaml in project root
        project_yaml = Path.cwd() / "litmus.yaml"
        if project_yaml.exists():
            files.append(project_yaml)

    if not files:
        click.echo("No YAML files found.")
        return

    passed = 0
    failed = 0

    for f in files:
        rel = f.relative_to(Path.cwd()) if f.is_relative_to(Path.cwd()) else f
        errors = validate_yaml(f, file_type=file_type, catalog_dir=f.parent)
        if errors:
            click.echo(click.style(f"{rel} FAIL", fg="red"))
            for err in errors:
                click.echo(err)
            failed += 1
        else:
            click.echo(click.style(f"{rel} OK", fg="green"))
            passed += 1

    click.echo(f"\n{passed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)


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

        # Watch both litmus package AND current working directory
        litmus_pkg = Path(__file__).parent
        uvicorn.run(
            "litmus.ui._asgi:app",
            host=host,
            port=port,
            reload=True,
            reload_dirs=[str(litmus_pkg), "."],
            reload_includes=["*.py", "*.yaml"],
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
    from litmus.data.backends.parquet import ParquetBackend

    results_dir = _get_results_dir(results_dir)

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
@click.option("--env", is_flag=True, default=False, help="Show environment snapshot")
def show(
    run_id: str, results_dir: str | None, fmt: str | None,
    output: str | None, template: str, env: bool,
):
    """Show details for a specific test run.

    Without -f, prints a summary to the terminal.
    With -f, generates a report file (html, pdf, json, csv).

    Examples:
        litmus show abc123
        litmus show abc123 -f html
        litmus show abc123 -f pdf -o reports/
        litmus show abc123 -f json -o result.json
    """
    results_dir = _get_results_dir(results_dir)

    from litmus.reports import load_run_data

    if fmt:
        # Report generation mode
        from litmus.reports import generate_report

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

    # Show step manifest if available
    pq_path = _find_parquet_for_run(run_id, results_dir)
    if pq_path and not env:
        from litmus.data.backends.parquet import read_step_manifest

        manifest = read_step_manifest(pq_path)
        if manifest:
            click.echo("\nStep Manifest:")
            for entry in manifest:
                mc = entry.get("measurement_count")
                meas_info = f" ({mc} measurements)" if mc else ""
                loc = entry.get("file") or ""
                if loc and entry.get("function"):
                    loc = f" [{loc}::{entry['function']}]"
                elif loc:
                    loc = f" [{loc}]"
                click.echo(
                    f"  {entry['index']:>2}. {entry['name']}: "
                    f"{entry.get('outcome', '?')}{meas_info}{loc}"
                )

    if data.measurements and not env:
        click.echo("\nMeasurements:")
        for m in data.measurements:
            name = m.get("measurement_name", "")
            value = m.get("value", "")
            units = m.get("units", "")
            outcome = m.get("outcome", "")
            click.echo(f"  {name}: {value} {units} [{outcome}]")

    if env:
        from litmus.sbom import environment_from_parquet, format_environment_table

        pq_path = _find_parquet_for_run(run_id, results_dir)
        if pq_path:
            snapshot = environment_from_parquet(pq_path)
            if snapshot:
                click.echo(f"\n{format_environment_table(snapshot)}")
            else:
                click.echo("\nNo environment data captured for this run.")
        else:
            click.echo("\nParquet file not found.")


# -----------------------------------------------------------------------------
# Export
# -----------------------------------------------------------------------------


def _read_events_by_id(
    id_prefix: str, results_dir: str,
) -> tuple[list[dict], str]:
    """Read events matching an ID prefix from Arrow IPC files.

    Auto-detects whether the prefix matches a run_id or session_id.
    UUIDs never collide, so a prefix match on either column is unambiguous.

    Returns:
        (events, matched_column) where matched_column is "run_id" or "session_id".
    """
    import json as json_mod

    from litmus.data._ipc_writer import read_ipc_batches

    events_dir = Path(results_dir) / "events"
    if not events_dir.exists():
        return [], ""

    # First pass: determine which column matches
    matched_col = ""
    for arrow_file in sorted(events_dir.rglob("*.arrow")):
        table = read_ipc_batches(arrow_file)
        if table is None:
            continue
        for col_name in ("run_id", "session_id"):
            col = table.column(col_name)
            for i in range(table.num_rows):
                val = col[i].as_py()
                if val and val.startswith(id_prefix):
                    matched_col = col_name
                    break
            if matched_col:
                break
        if matched_col:
            break

    if not matched_col:
        return [], ""

    # Second openly collect all matching events
    all_events: list[dict] = []
    for arrow_file in sorted(events_dir.rglob("*.arrow")):
        table = read_ipc_batches(arrow_file)
        if table is None:
            continue
        col = table.column(matched_col)
        json_col = table.column("json")
        for i in range(table.num_rows):
            val = col[i].as_py()
            if val and val.startswith(id_prefix):
                try:
                    evt = json_mod.loads(json_col[i].as_py())
                    all_events.append(evt)
                except (json_mod.JSONDecodeError, TypeError):
                    continue
    return all_events, matched_col


@main.command()
@click.argument("id")
@click.option(
    "-f", "--format", "fmt", required=True,
    help="Target format (csv, json, stdf, hdf5, tdms, mdf4, atml)",
)
@click.option("-o", "--output-dir", default=None, help="Output directory")
@click.option("--results-dir", default=None, help="Results directory")
@click.option(
    "--transport", default=None,
    help="Ship exported file via transport (s3, sftp, file, etc.)",
)
def export(
    id: str, fmt: str, output_dir: str | None,
    results_dir: str | None, transport: str | None,
):
    """Export a test run or session to a different format via event replay.

    ID can be a run_id or session_id (prefix match). Auto-detected from
    stored events — UUIDs never collide.

    Examples:

        litmus export abc123 -f csv

        litmus export abc123 -f stdf -o results/stdf/

        litmus export abc123 -f csv --transport s3
    """
    from litmus.data.subscribers import get_subscriber_class, replay_to_subscriber

    if results_dir is None:
        results_dir = _get_results_dir(None)

    # Look up subscriber class for the format
    cls = get_subscriber_class(fmt)
    if cls is None:
        click.echo(
            f"No subscriber registered for format '{fmt}'. "
            f"Available: {', '.join(_list_export_formats())}",
            err=True,
        )
        raise SystemExit(1)

    if output_dir is None:
        output_dir = f"results/exports/{fmt}"

    # Find events by run_id or session_id
    events, matched_col = _read_events_by_id(id, results_dir)
    if not events:
        click.echo(f"No events found for '{id}'.", err=True)
        raise SystemExit(1)

    kind = "session" if matched_col == "session_id" else "run"
    click.echo(f"Matched {kind} {id} ({len(events)} events)")

    sub = cls(Path(output_dir))
    replay_to_subscriber(sub, events)

    # Find the output file(s)
    out_dir = Path(output_dir)
    candidates = sorted(f for f in out_dir.iterdir() if f.is_file())
    if candidates:
        for result_path in candidates:
            click.echo(f"Exported: {result_path}")

        if transport:
            from litmus.data.transports import get_transport
            from litmus.schemas import OutputConfig

            t = get_transport(transport)
            cfg = OutputConfig(format=fmt, transport=transport, output_dir=output_dir)
            for result_path in candidates:
                dest = t.send(result_path, cfg)
                click.echo(f"Shipped: {dest}")
    else:
        click.echo(f"Export completed but no output file found in {output_dir}.", err=True)


def _list_export_formats() -> list[str]:
    """List available export formats (excluding report formats)."""
    from litmus.data.subscribers import list_subscribers

    return sorted(
        f for f in list_subscribers()
        if f not in {"html", "pdf"}
    )


# -----------------------------------------------------------------------------
# SBOM Export
# -----------------------------------------------------------------------------


@main.command()
@click.argument("run_id")
@click.option("--results-dir", default=None, help="Results directory")
@click.option("-o", "--output", default=None, help="Output file (default: stdout)")
def sbom(run_id: str, results_dir: str | None, output: str | None):
    """Export CycloneDX SBOM for a test run's software environment.

    Reads the environment snapshot captured during the test run and
    converts it to CycloneDX 1.6 JSON format.

    Examples:
        litmus sbom abc123
        litmus sbom abc123 -o sbom.json
    """
    from litmus.sbom import environment_from_parquet, generate_cyclonedx

    results_dir = _get_results_dir(results_dir)

    pq_path = _find_parquet_for_run(run_id, results_dir)
    if not pq_path:
        click.echo(f"Run {run_id} not found.", err=True)
        raise SystemExit(1)

    snapshot = environment_from_parquet(pq_path)
    if not snapshot:
        click.echo("No environment data captured for this run.", err=True)
        raise SystemExit(1)

    try:
        sbom_str = generate_cyclonedx(snapshot)
    except ImportError as e:
        click.echo(str(e), err=True)
        raise SystemExit(1)

    if output:
        Path(output).write_text(sbom_str)
        click.echo(f"SBOM written to {output}")
    else:
        click.echo(sbom_str)


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


def _get_litmus_path() -> Path:
    """Find the litmus executable path."""
    import sys

    litmus_path = Path(sys.executable).parent / "litmus"
    if not litmus_path.exists():
        litmus_path = Path("litmus")
    return litmus_path


def _mcp_server_entry() -> dict[str, str | list[str]]:
    """Build the MCP server config entry for litmus."""
    return {
        "command": str(_get_litmus_path()),
        "args": ["mcp", "serve"],
    }


def _write_mcp_config(mcp_file: Path) -> None:
    """Merge litmus MCP server entry into an MCP config file and write it."""
    config = {"mcpServers": {"litmus": _mcp_server_entry()}}

    if mcp_file.exists():
        existing = json.loads(mcp_file.read_text())
        if "mcpServers" not in existing:
            existing["mcpServers"] = {}
        existing["mcpServers"]["litmus"] = config["mcpServers"]["litmus"]
        config = existing

    mcp_file.parent.mkdir(parents=True, exist_ok=True)
    mcp_file.write_text(json.dumps(config, indent=2) + "\n")
    click.echo(f"Wrote {mcp_file}")


def _copy_skill_stubs(source_dir: Path, target_dir: Path) -> list[str]:
    """Copy .md skill stubs from package source to project target.

    Always overwrites (stubs are tiny pointers to package workflows).
    Returns list of created file names.
    """
    created = []
    if not source_dir.exists():
        return created
    target_dir.mkdir(parents=True, exist_ok=True)
    for src_file in sorted(source_dir.glob("*.md")):
        dst_file = target_dir / src_file.name
        dst_file.write_text(src_file.read_text())
        created.append(src_file.name)
    return created


_MARKER_START = "<!-- litmus:start -->"
_MARKER_END = "<!-- litmus:end -->"


def _write_instructions(target_path: Path, header: str = "") -> str | None:
    """Write or update project instructions from the shared template.

    Returns:
        "created"  — file didn't exist, wrote full template
        "updated"  — file existed, appended/replaced managed section
        None       — no change needed (content already up to date)
    """
    template = Path(__file__).parent / "skills" / "templates" / "project-instructions.md"
    if not template.exists():
        return None

    # Resolve {LITMUS_REFS} to installed package path
    refs_path = Path(__file__).parent / "skills" / "refs"
    content = template.read_text().replace("{LITMUS_REFS}", str(refs_path))

    if header:
        content = header + "\n\n" + content

    managed = f"{_MARKER_START}\n{content}\n{_MARKER_END}\n"

    if not target_path.exists():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(managed)
        return "created"

    existing = target_path.read_text()

    if _MARKER_START in existing:
        # Replace content between markers
        start = existing.index(_MARKER_START)
        end = existing.index(_MARKER_END) + len(_MARKER_END)
        # Include trailing newline if present
        if end < len(existing) and existing[end] == "\n":
            end += 1
        old_section = existing[start:end]
        if old_section == managed:
            return None
        target_path.write_text(existing[:start] + managed + existing[end:])
        return "updated"

    # No markers yet — append managed section
    separator = "\n" if existing.endswith("\n") else "\n\n"
    target_path.write_text(existing + separator + managed)
    return "updated"


@setup.command("claude-code")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_claude_code(print_only: bool):
    """Configure Litmus MCP server for Claude Code.

    Registers the MCP server, copies skill command stubs, and generates
    a CLAUDE.md project instructions file if one doesn't exist.

    Example:
        litmus setup claude-code
    """
    import subprocess

    litmus_path = _get_litmus_path()
    config = {"name": "litmus", **_mcp_server_entry()}

    if print_only:
        click.echo("Add this to your Claude Code MCP configuration:\n")
        click.echo(json.dumps(config, indent=2))
        click.echo("\nOr run: litmus setup claude-code")
        return

    # 1. Register MCP server via claude CLI
    try:
        result = subprocess.run(
            ["claude", "mcp", "add", "litmus", "--", str(litmus_path), "mcp", "serve"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            click.echo("✓ Registered Litmus MCP server")
        else:
            click.echo("Could not add via claude CLI. Add manually:")
            click.echo(f"\n  claude mcp add litmus -- {litmus_path} mcp serve\n")
    except (FileNotFoundError, subprocess.CalledProcessError):
        click.echo("Claude CLI not found or failed. Add manually:")
        click.echo(f"\n  claude mcp add litmus -- {litmus_path} mcp serve\n")

    # 2. Copy command stubs to .claude/commands/
    stubs_src = Path(__file__).parent / "skills" / "commands" / "claude-code"
    stubs_dst = Path.cwd() / ".claude" / "commands"
    created = _copy_skill_stubs(stubs_src, stubs_dst)
    if created:
        click.echo(f"✓ Copied commands to .claude/commands/ ({len(created)} files)")

    # 3. Generate/update CLAUDE.md
    result = _write_instructions(Path.cwd() / "CLAUDE.md")
    if result == "created":
        click.echo("✓ Created CLAUDE.md")
    elif result == "updated":
        click.echo("✓ Updated CLAUDE.md (Litmus section)")
    else:
        click.echo("· CLAUDE.md already up to date")


@setup.command("claude-desktop")
@click.option("--legacy", is_flag=True, help="Use legacy JSON config instead of .mcpb bundle")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_claude_desktop(legacy: bool, print_only: bool):
    """Configure Litmus for Claude Desktop.

    Builds a .mcpb Desktop Extension bundle that can be double-clicked
    to install. Use --legacy for older Claude Desktop versions.

    Example:
        litmus setup claude-desktop
    """
    import os
    import sys
    import zipfile

    litmus_path = _get_litmus_path()

    is_wsl = os.environ.get("WSL_DISTRO_NAME") is not None or (
        Path("/proc/version").exists() and "microsoft" in Path("/proc/version").read_text().lower()
    )
    username = os.environ.get("USERNAME") or os.environ.get("USER", "").split("@")[-1]

    if legacy:
        # Legacy path: direct JSON config editing
        if sys.platform == "win32":
            config_dir = Path(os.environ.get("APPDATA", "")) / "Claude"
        elif is_wsl:
            config_dir = Path(f"/mnt/c/Users/{username}/AppData/Roaming/Claude")
        elif sys.platform == "darwin":
            config_dir = Path.home() / "Library" / "Application Support" / "Claude"
        else:
            config_dir = Path.home() / ".config" / "Claude"

        if is_wsl:
            server_config = {
                "command": "wsl.exe",
                "args": [str(litmus_path), "mcp", "serve"],
            }
        else:
            server_config = {
                "command": str(litmus_path),
                "args": ["mcp", "serve"],
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
        click.echo(f"✓ Wrote MCP config: {config_file}")
        click.echo("  Restart Claude Desktop to use Litmus tools.")
        return

    # Build .mcpb Desktop Extension bundle
    manifest = {
        "schema_version": "1.0",
        "name": "litmus",
        "display_name": "Litmus Hardware Test Platform",
        "description": (
            "MCP server for hardware test configuration,"
            " instrument discovery, and test execution."
        ),
        "version": "0.1.0",
        "author": "Litmus",
        "server": {
            "transport": "stdio",
            "command": "wsl.exe" if is_wsl else str(litmus_path),
            "args": [str(litmus_path), "mcp", "serve"] if is_wsl else ["mcp", "serve"],
        },
    }

    if print_only:
        click.echo("manifest.json:\n")
        click.echo(json.dumps(manifest, indent=2))
        return

    # Determine output location
    if is_wsl:
        desktop = Path(f"/mnt/c/Users/{username}/Desktop")
        if desktop.exists():
            mcpb_path = desktop / "litmus.mcpb"
        else:
            mcpb_path = Path.cwd() / "litmus.mcpb"
    elif sys.platform == "darwin":
        mcpb_path = Path.home() / "Desktop" / "litmus.mcpb"
    else:
        mcpb_path = Path.cwd() / "litmus.mcpb"

    with zipfile.ZipFile(mcpb_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")

        # Bundle skills as reference
        skills_dir = Path(__file__).parent / "skills"
        if skills_dir.exists():
            for file in sorted(skills_dir.rglob("*")):
                if file.is_file() and "__pycache__" not in str(file):
                    arcname = "skills" / file.relative_to(skills_dir)
                    zf.write(file, str(arcname))

    click.echo("✓ Built litmus.mcpb (Desktop Extension)")
    click.echo(f"  → {mcpb_path}")
    click.echo("  Double-click to install in Claude Desktop.")


@setup.command("copilot")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_copilot(print_only: bool):
    """Configure Litmus for GitHub Copilot (VS Code + CLI).

    Sets up MCP server, prompt stubs, and instruction files for both
    Copilot in VS Code and Copilot CLI (which also reads AGENTS.md).

    Example:
        litmus setup copilot
    """
    mcp_config = {
        "servers": {
            "litmus": {
                "type": "stdio",
                "command": "uv",
                "args": ["run", "litmus", "mcp", "serve"],
            }
        }
    }

    if print_only:
        click.echo(".vscode/mcp.json:\n")
        click.echo(json.dumps(mcp_config, indent=2))
        return

    # 1. Create/merge .vscode/mcp.json
    vscode_dir = Path.cwd() / ".vscode"
    vscode_dir.mkdir(exist_ok=True)
    mcp_file = vscode_dir / "mcp.json"

    if mcp_file.exists():
        existing = json.loads(mcp_file.read_text())
        if "servers" not in existing:
            existing["servers"] = {}
        existing["servers"]["litmus"] = mcp_config["servers"]["litmus"]
        final_config = existing
    else:
        final_config = mcp_config

    mcp_file.write_text(json.dumps(final_config, indent=2) + "\n")
    click.echo("✓ Wrote .vscode/mcp.json (litmus MCP server)")

    # 2. Copy prompt stubs to .github/prompts/
    stubs_src = Path(__file__).parent / "skills" / "commands" / "copilot"
    stubs_dst = Path.cwd() / ".github" / "prompts"
    created = _copy_skill_stubs(stubs_src, stubs_dst)
    if created:
        click.echo(f"✓ Copied prompts to .github/prompts/ ({len(created)} files)")

    # 3. Generate/update .github/copilot-instructions.md
    copilot_instructions = Path.cwd() / ".github" / "copilot-instructions.md"
    result = _write_instructions(copilot_instructions)
    if result == "created":
        click.echo("✓ Created .github/copilot-instructions.md")
    elif result == "updated":
        click.echo("✓ Updated .github/copilot-instructions.md (Litmus section)")
    else:
        click.echo("· .github/copilot-instructions.md already up to date")

    # 4. Generate/update AGENTS.md (for Copilot CLI + other tools)
    result = _write_instructions(Path.cwd() / "AGENTS.md")
    if result == "created":
        click.echo("✓ Created AGENTS.md")
    elif result == "updated":
        click.echo("✓ Updated AGENTS.md (Litmus section)")
    else:
        click.echo("· AGENTS.md already up to date")


@setup.command("cursor")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_cursor(print_only: bool):
    """Configure Litmus MCP server for Cursor.

    Creates or updates .cursor/mcp.json in the current project.

    Example:
        litmus setup cursor
    """
    if print_only:
        config = {"mcpServers": {"litmus": _mcp_server_entry()}}
        click.echo("Add this to .cursor/mcp.json:\n")
        click.echo(json.dumps(config, indent=2))
        return

    mcp_file = Path.cwd() / ".cursor" / "mcp.json"
    _write_mcp_config(mcp_file)
    click.echo("Restart Cursor to use Litmus tools.")


@setup.command("cline")
@click.option("--print-only", is_flag=True, help="Print config instead of installing")
def setup_cline(print_only: bool):
    """Configure Litmus MCP server for Cline (VS Code extension).

    Creates or updates cline_mcp_settings.json in VS Code settings.

    Example:
        litmus setup cline
    """
    config = {"mcpServers": {"litmus": _mcp_server_entry()}}

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

    settings_dir = next((d for d in vscode_dirs if d.exists()), None)

    if not settings_dir:
        click.echo("VS Code settings directory not found. Add manually:")
        click.echo(json.dumps(config, indent=2))
        return

    mcp_file = settings_dir / "cline_mcp_settings.json"
    _write_mcp_config(mcp_file)
    click.echo("Restart VS Code to use Litmus tools with Cline.")


@setup.command("show")
def setup_show():
    """Show current MCP server configuration.

    Displays the command to start the Litmus MCP server.
    """
    litmus_path = _get_litmus_path()

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
                click.echo(f"  {_format_instrument(resource, info)}")
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

    click.echo("\nNext: litmus station init")


# -----------------------------------------------------------------------------
# Catalog Commands
# -----------------------------------------------------------------------------


@main.group()
def catalog():
    """Catalog commands."""
    pass


@catalog.command("datasheet")
@click.argument("yaml_path", type=click.Path(exists=True))
@click.option("-f", "--format", "fmt", default="html", type=click.Choice(["html", "pdf"]),
              help="Output format (default: html)")
@click.option("-o", "--output", "output", default=None, type=click.Path(),
              help="Output file path")
def catalog_datasheet(yaml_path: str, fmt: str, output: str | None):
    """Generate a formatted datasheet from a catalog YAML file.

    Example:

        litmus catalog datasheet catalog/keysight/keysight_e8257d.yaml -o /tmp/e8257d.html
    """
    from litmus.reports.datasheet import generate_datasheet

    out = generate_datasheet(Path(yaml_path), Path(output) if output else None, fmt=fmt)
    click.echo(f"Datasheet written to {out}")


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

            click.echo(f"\n[{instrument_count}] {_format_instrument(resource, info)}")

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
            from litmus.instruments.models import InstrumentInfo
            from litmus.schemas import InstrumentAssetFile
            from litmus.store import save_instrument_asset

            asset = InstrumentAssetFile(
                id=inst_id,
                protocol=proto,
                driver=driver or None,
                info=info if info else InstrumentInfo(),
            )
            inst_file = instruments_dir / f"{inst_id}.yaml"
            save_instrument_asset(asset, target_path=inst_file)
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

    from litmus.config.normalize import check_instrument_types

    _, type_warnings = check_instrument_types(station_instruments)
    for w in type_warnings:
        click.echo(f"  Warning: {w}", err=True)

    from litmus.config.fmt import dump_yaml

    station_file = stations_dir / f"{station_id}.yaml"
    station_file.write_text(dump_yaml(station_data))

    click.echo(f"\nCreated {station_file}")
    click.echo(f"Created {len(station_instruments)} instrument file(s)")
    click.echo(f"\nRun tests with: pytest --station={station_id}")
    click.echo("Write a test:   litmus new-test <name>")


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
        resolve_station_instruments,
    )
    from litmus.store import load_instrument_files, load_station

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
    station_config = load_station(station_file)
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
    from litmus.instruments.discovery import get_info_visa
    from litmus.instruments.loader import (
        find_instruments_dir,
        find_stations_dir,
        resolve_station_instruments,
    )
    from litmus.store import load_instrument_files, load_station

    stations_dir = find_stations_dir()
    instruments_dir = find_instruments_dir()

    if not stations_dir:
        click.echo("Error: No stations/ directory found", err=True)
        raise SystemExit(1)

    station_file = stations_dir / f"{station_id}.yaml"
    if not station_file.exists():
        click.echo(f"Error: Station file not found: {station_file}", err=True)
        raise SystemExit(1)

    station_config = load_station(station_file)
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
                from litmus.store import load_instrument_asset, save_instrument_asset

                asset = load_instrument_asset(inst_file)
                asset.info = actual_info
                save_instrument_asset(asset, target_path=inst_file)

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
    from litmus.instruments.loader import find_instruments_dir
    from litmus.store import load_instrument_files

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

    for inst_id, asset in sorted(instruments.items()):
        protocol = asset.protocol
        model = ""
        if asset.info:
            mfr = asset.info.manufacturer or ""
            mdl = asset.info.model or ""
            model = f"{mfr} {mdl}".strip()

        click.echo(f"{inst_id:<25} {protocol:<10} {model:<30}")


@instrument.command("show")
@click.argument("instrument_id")
def instrument_show(instrument_id: str):
    """Show details for a specific instrument."""
    from litmus.instruments.loader import find_instruments_dir
    from litmus.store import load_instrument_asset

    instruments_dir = find_instruments_dir()
    if not instruments_dir:
        click.echo("No instruments/ directory found", err=True)
        raise SystemExit(1)

    inst_file = instruments_dir / f"{instrument_id}.yaml"
    if not inst_file.exists():
        click.echo(f"Instrument not found: {instrument_id}", err=True)
        raise SystemExit(1)

    asset = load_instrument_asset(inst_file)
    info = asset.info
    cal = asset.calibration

    click.echo(f"Instrument: {instrument_id}")
    click.echo("-" * 40)
    click.echo(f"Protocol: {asset.protocol}")
    if asset.driver:
        click.echo(f"Driver: {asset.driver}")

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
    from litmus.instruments.loader import find_instruments_dir
    from litmus.store import load_instrument_asset, save_instrument_asset

    instruments_dir = find_instruments_dir()
    if not instruments_dir:
        click.echo("No instruments/ directory found", err=True)
        raise SystemExit(1)

    inst_file = instruments_dir / f"{instrument_id}.yaml"
    if not inst_file.exists():
        click.echo(f"Instrument not found: {instrument_id}", err=True)
        raise SystemExit(1)

    from datetime import date as date_type

    asset = load_instrument_asset(inst_file)

    if due_date:
        asset.calibration.due_date = date_type.fromisoformat(due_date)
    if last_cal:
        asset.calibration.last_cal = date_type.fromisoformat(last_cal)
    if certificate:
        asset.calibration.certificate = certificate
    if lab:
        asset.calibration.lab = lab

    save_instrument_asset(asset, target_path=inst_file)

    click.echo(f"Updated calibration for {instrument_id}")


# -----------------------------------------------------------------------------
# Yield / Manufacturing Metrics Commands
# -----------------------------------------------------------------------------


def _common_filters(func):
    """Shared filter options for yield commands."""
    func = click.option("--results-dir", default=None, help="Results directory")(func)
    func = click.option("--phase", default=None, help="Test phase (or 'all')")(func)
    func = click.option("--since", default=None, help="Start date (ISO format)")(func)
    func = click.option("--until", "until_date", default=None, help="End date (ISO format)")(func)
    func = click.option("--product", default=None, help="Product ID")(func)
    func = click.option("--station", default=None, help="Station ID")(func)
    func = click.option("--lot", default=None, help="Lot number")(func)
    return func


def _apply_filters(table, phase, since, until_date, product, station, lot):
    """Apply common filters to a PyArrow table."""
    from litmus.analysis.query import apply_all_filters

    return apply_all_filters(
        table,
        phase=phase,
        product_id=product,
        station_id=station,
        lot=lot,
        since=since,
        until=until_date,
    )


def _get_results_dir(results_dir):
    """Resolve results directory from option or project config."""
    from litmus.data.results_dir import resolve_results_dir

    return str(resolve_results_dir(results_dir))


@main.group("yield")
def yield_group():
    """Yield and manufacturing metrics."""
    pass


@yield_group.command("summary")
@_common_filters
@click.option(
    "--group-by", "group_by",
    type=click.Choice(["product", "station", "lot"]), default=None,
)
def yield_summary(results_dir, phase, since, until_date, product, station, lot, group_by):
    """Show yield summary (FPY, final yield, RTY)."""
    from litmus.analysis.query import deduplicate_runs, load_runs

    results_dir = _get_results_dir(results_dir)
    table = load_runs(results_dir)
    table = _apply_filters(table, phase, since, until_date, product, station, lot)
    runs = deduplicate_runs(table)

    if not runs:
        click.echo("No runs found.")
        return

    if group_by:
        _yield_summary_grouped(runs, group_by)
    else:
        _yield_summary_flat(runs)


def _yield_summary_flat(runs):
    from collections import defaultdict

    from litmus.analysis.metrics import (
        calculate_final_yield,
        calculate_fpy,
        calculate_rty,
    )

    fpy = calculate_fpy(runs)
    final = calculate_final_yield(runs)

    # RTY: FPY per phase
    by_phase = defaultdict(list)
    for r in runs:
        p = r.get("test_phase") or "unknown"
        by_phase[p].append(r)

    fpy_by_phase = {p: calculate_fpy(phase_runs) for p, phase_runs in by_phase.items()}
    rty = calculate_rty(fpy_by_phase)

    serials = {r.get("dut_serial") for r in runs if r.get("dut_serial")}

    click.echo(f"Runs: {len(runs)}  |  Unique serials: {len(serials)}")
    click.echo(f"First-pass yield:  {fpy * 100:.1f}%")
    click.echo(f"Final yield:       {final * 100:.1f}%")

    if len(fpy_by_phase) > 1:
        click.echo(f"Rolled throughput: {rty * 100:.1f}%")
        for p, val in sorted(fpy_by_phase.items()):
            click.echo(f"  {p}: {val * 100:.1f}%")


def _yield_summary_grouped(runs, group_by):
    from collections import defaultdict

    from litmus.analysis.metrics import calculate_final_yield, calculate_fpy

    key_map = {"product": "product_id", "station": "station_id", "lot": "dut_lot_number"}
    field = key_map[group_by]

    groups = defaultdict(list)
    for r in runs:
        g = r.get(field) or "unknown"
        groups[g].append(r)

    click.echo(f"{'Group':<25} {'Runs':>5} {'FPY':>7} {'Final':>7}")
    click.echo("-" * 48)
    for g in sorted(groups):
        g_runs = groups[g]
        fpy = calculate_fpy(g_runs)
        final = calculate_final_yield(g_runs)
        click.echo(f"{g:<25} {len(g_runs):>5} {fpy * 100:>6.1f}% {final * 100:>6.1f}%")


@yield_group.command("pareto")
@_common_filters
@click.option("--top", "top_n", default=10, help="Number of top failures")
def yield_pareto(results_dir, phase, since, until_date, product, station, lot, top_n):
    """Top failure modes (Pareto analysis)."""
    from litmus.analysis.metrics import pareto_analysis
    from litmus.analysis.query import load_runs

    results_dir = _get_results_dir(results_dir)
    table = load_runs(results_dir)
    table = _apply_filters(table, phase, since, until_date, product, station, lot)
    measurements = table.to_pylist()

    if not measurements:
        click.echo("No measurements found.")
        return

    results = pareto_analysis(measurements, top_n=top_n)
    if not results:
        click.echo("No failures found.")
        return

    total_meas = len(measurements)
    total_fails = sum(r["count"] for r in results)
    click.echo(f"Total measurements: {total_meas}  |  Total failures: {total_fails}")
    click.echo()
    click.echo(f"{'#':<4} {'Step / Measurement':<40} {'Count':>6} {'%':>6} {'Cum%':>6}")
    click.echo("-" * 66)
    for i, r in enumerate(results, 1):
        label = f"{r['step_name']}: {r['measurement_name']}"
        if len(label) > 38:
            label = label[:35] + "..."
        click.echo(
            f"{i:<4} {label:<40} {r['count']:>6}"
            f" {r['pct']:>5.1f}% {r['cumulative_pct']:>5.1f}%"
        )


@yield_group.command("cpk")
@click.argument("step_name")
@_common_filters
@click.option("--measurement", default=None, help="Measurement name (if step has multiple)")
@click.option("--min-samples", default=30, help="Minimum sample count")
def yield_cpk(
    step_name, results_dir, phase, since, until_date,
    product, station, lot, measurement, min_samples,
):
    """Process capability (Cpk) for a measurement step."""
    from litmus.analysis.metrics import calculate_cpk
    from litmus.analysis.query import load_runs

    results_dir = _get_results_dir(results_dir)
    table = load_runs(results_dir)
    table = _apply_filters(table, phase, since, until_date, product, station, lot)
    rows = table.to_pylist()

    # Filter to step
    rows = [r for r in rows if r.get("step_name") == step_name]
    if measurement:
        rows = [r for r in rows if r.get("measurement_name") == measurement]

    if not rows:
        click.echo(f"No measurements found for step '{step_name}'.")
        return

    # Get values and limits
    values = [
        r["value"] for r in rows
        if r.get("value") is not None and isinstance(r["value"], (int, float))
    ]
    lsl_vals = [r["low_limit"] for r in rows if r.get("low_limit") is not None]
    usl_vals = [r["high_limit"] for r in rows if r.get("high_limit") is not None]

    lsl = lsl_vals[0] if lsl_vals else None
    usl = usl_vals[0] if usl_vals else None

    if not values:
        click.echo("No numeric values found.")
        return

    result = calculate_cpk(values, lsl, usl, min_samples=min_samples)

    meas_name = measurement or rows[0].get("measurement_name", "")
    units = rows[0].get("units", "")
    click.echo(f"Step: {step_name}")
    if meas_name:
        click.echo(f"Measurement: {meas_name}")
    click.echo(f"Samples: {result['n']}")
    click.echo(f"Mean: {result['mean']:.4f} {units}" if result["mean"] is not None else "Mean: N/A")
    click.echo(f"Sigma: {result['sigma']:.4f}" if result["sigma"] is not None else "Sigma: N/A")
    if lsl is not None:
        click.echo(f"LSL: {lsl}")
    if usl is not None:
        click.echo(f"USL: {usl}")
    if result["cp"] is not None:
        click.echo(f"Cp:  {result['cp']:.3f}")
    if result["cpk"] is not None:
        click.echo(f"Cpk: {result['cpk']:.3f}")
    if result.get("warning"):
        click.echo(f"Warning: {result['warning']}")


@yield_group.command("trend")
@_common_filters
@click.option("--period", type=click.Choice(["day", "week", "month"]), default="day")
def yield_trend(results_dir, phase, since, until_date, product, station, lot, period):
    """Yield trend over time."""
    from litmus.analysis.metrics import trend_by_period
    from litmus.analysis.query import deduplicate_runs, load_runs

    results_dir = _get_results_dir(results_dir)
    table = load_runs(results_dir)
    table = _apply_filters(table, phase, since, until_date, product, station, lot)
    runs = deduplicate_runs(table)

    if not runs:
        click.echo("No runs found.")
        return

    results = trend_by_period(runs, period=period)

    click.echo(f"{'Period':<14} {'Total':>6} {'Passed':>7} {'Yield':>7}")
    click.echo("-" * 38)
    for r in results:
        click.echo(f"{r['period']:<14} {r['total']:>6} {r['passed']:>7} {r['yield_pct']:>6.1f}%")


@yield_group.command("time")
@_common_filters
@click.option("--by", "by_what", type=click.Choice(["run", "step"]), default="run")
def yield_time(results_dir, phase, since, until_date, product, station, lot, by_what):
    """Test time analysis."""
    from litmus.analysis.metrics import timing_stats
    from litmus.analysis.query import deduplicate_runs, load_runs

    results_dir = _get_results_dir(results_dir)
    table = load_runs(results_dir)
    table = _apply_filters(table, phase, since, until_date, product, station, lot)

    if by_what == "step":
        rows = table.to_pylist()
    else:
        rows = deduplicate_runs(table)

    if not rows:
        click.echo("No data found.")
        return

    stats = timing_stats(rows, by=by_what)

    if stats["count"] == 0:
        click.echo("No timing data available.")
        return

    label = "Run" if by_what == "run" else "Step"
    click.echo(f"{label} time statistics ({stats['count']} samples):")
    click.echo(f"  Avg:  {stats['avg_s']:.1f}s")
    click.echo(f"  Min:  {stats['min_s']:.1f}s")
    click.echo(f"  Max:  {stats['max_s']:.1f}s")
    click.echo(f"  P95:  {stats['p95_s']:.1f}s")

    if "per_step" in stats and stats["per_step"]:
        click.echo(f"\n{'Step':<35} {'Avg':>7} {'Min':>7} {'Max':>7} {'P95':>7} {'N':>5}")
        click.echo("-" * 72)
        for step_name, s in stats["per_step"].items():
            if len(step_name) > 33:
                step_name = step_name[:30] + "..."
            click.echo(
                f"{step_name:<35} {s['avg_s']:>6.1f}s {s['min_s']:>6.1f}s "
                f"{s['max_s']:>6.1f}s {s['p95_s']:>6.1f}s {s['count']:>5}"
            )


# ---------------------------------------------------------------------------
# Data management
# ---------------------------------------------------------------------------


@main.group()
def data():
    """Data retention and management."""
    pass


@data.command("prune")
@click.option("--older-than", required=True, help="Retention period (e.g. 30d, 90d)")
@click.option(
    "--type", "data_types", multiple=True,
    help="Data types to prune (e.g. channels, events)",
)
@click.option("--results-dir", default=None, help="Results directory")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted")
def data_prune(
    older_than: str,
    data_types: tuple[str, ...],
    results_dir: str | None,
    dry_run: bool,
) -> None:
    """Delete date-partitioned data older than the specified period."""
    from litmus.data.retention import prune_all

    results_dir_path = Path(_get_results_dir(results_dir))

    types = data_types or ("channels", "events")
    try:
        result = prune_all(results_dir_path, older_than, data_types=types, dry_run=dry_run)
    except ValueError as e:
        raise click.BadParameter(str(e), param_hint="'--older-than'") from e

    total = 0
    for subdir, paths in result.items():
        for p in paths:
            prefix = "[dry-run] " if dry_run else ""
            click.echo(f"{prefix}Removed {subdir}/{p.name}")
            total += 1
    if total == 0:
        click.echo("Nothing to prune.")
    elif dry_run:
        click.echo(f"\n{total} directories would be removed.")
    else:
        click.echo(f"\n{total} directories removed.")


# ---------------------------------------------------------------------------
# Upload queue
# ---------------------------------------------------------------------------


@main.group()
def uploads():
    """Manage the upload queue for cloud transports."""
    pass


@uploads.command("status")
@click.option("--results-dir", default=None, help="Results directory")
def uploads_status(results_dir: str | None) -> None:
    """Show pending/failed uploads."""
    from litmus.data.transports.upload_queue import status

    rows = status(_get_results_dir(results_dir))
    if not rows:
        click.echo("Upload queue is empty.")
        return
    for row in rows:
        error_str = f", error: {row.last_error}" if row.last_error else ""
        click.echo(
            f"[{row.status}] {row.local_path} → {row.transport} "
            f"(attempts: {row.attempts}{error_str})"
        )


@uploads.command("retry")
@click.option("--results-dir", default=None, help="Results directory")
@click.option("--max-attempts", default=3, help="Max retry attempts per upload")
def uploads_retry(results_dir: str | None, max_attempts: int) -> None:
    """Retry all pending/failed uploads."""
    from litmus.data.transports.upload_queue import drain

    count = drain(_get_results_dir(results_dir), max_attempts=max_attempts)
    click.echo(f"{count} upload(s) completed.")


@uploads.command("clear")
@click.option("--results-dir", default=None, help="Results directory")
def uploads_clear(results_dir: str | None) -> None:
    """Remove completed entries from the upload queue."""
    from litmus.data.transports.upload_queue import clear_done

    count = clear_done(_get_results_dir(results_dir))
    click.echo(f"{count} completed entry/entries removed.")


# ---------------------------------------------------------------------------
# Grafana dashboards
# ---------------------------------------------------------------------------

# Import and register the grafana subgroup
from litmus.grafana.cli import grafana  # noqa: E402

main.add_command(grafana)


if __name__ == "__main__":
    main()
