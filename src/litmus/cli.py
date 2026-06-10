"""Litmus command-line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from litmus import __version__


def _find_parquet_for_run(run_id: str, data_dir: str) -> Path | None:
    """Find the measurement parquet path for a run ID via the daemon's index.

    Goes through ``RunsQuery`` rather than ``ParquetBackend.find_run_file``
    so step-only runs (no measurement parquet on disk) resolve to
    ``None`` cleanly without a glob walk. Same data path as the UI
    and API — one canonical lookup for "where does this run live."
    """
    from litmus.analysis.runs_query import RunsQuery

    with RunsQuery(_data_dir=data_dir) as q:
        row = q.get(run_id)
    return Path(row.file_path) if row is not None and row.file_path else None


@click.group()
@click.version_option(version=__version__, prog_name="litmus")
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
    "--tier",
    type=click.Choice(["bringup", "bench", "factory"], case_sensitive=False),
    default=None,
    help=(
        "Scaffold tier. 'bringup' = Tier 0/1 (MagicMock fixtures, one test, "
        "one sidecar, no station/product YAML). 'bench' = Tier 2 starter "
        "(equivalent to --starter). 'factory' = Tier 3/4 (bench + profiles)."
    ),
)
@click.option(
    "--ai",
    type=click.Choice(["claude-code", "claude-desktop", "copilot"], case_sensitive=False),
    default=None,
    help="Set up AI tool integration (MCP server + project instructions)",
)
@click.option("--name", "project_name", default=None, help="Project name (overrides auto-detect)")
def init(
    name: str | None,
    no_git: bool,
    discover: bool,
    starter: bool | None,
    tier: str | None,
    ai: str | None,
    project_name: str | None,
):
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
    # - If --tier or --starter: skip discovery (they have their own mock station)
    # - If --discover: skip starter (user wants real instruments)
    # - If neither: prompt for starter first; if declined, prompt for discovery
    station = None
    use_starter = False
    tier_lower = tier.lower() if tier else None

    if tier_lower:
        # Explicit --tier flag wins over --starter / --discover / prompts
        pass
    elif starter is True:
        # Explicit --starter flag
        use_starter = True
    elif discover:
        # Explicit --discover flag
        station = _discover_instruments(interactive=False)
    elif starter is False:
        # Explicit --no-starter flag — skip prompts (use --discover for instruments)
        pass
    else:
        # No flags provided - prompt interactively
        if click.confirm("Create starter example files?", default=True):
            use_starter = True
        elif click.confirm("Discover instruments?", default=False):
            station = _discover_instruments(interactive=True)

    result = init_project(
        project_path,
        git=not no_git,
        station=station,
        starter=use_starter,
        name=project_name,
        tier=tier_lower,
    )

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
        # Detect installed tools and prompt (only when stdin is a TTY)
        ai_tools: list[tuple[str, str]] = []
        if check_command("claude"):
            ai_tools.append(("claude-code", "Claude Code"))
        # Check for VS Code / Copilot
        if (project_path / ".vscode").exists() or check_command("code"):
            ai_tools.append(("copilot", "GitHub Copilot"))

        if ai_tools and sys.stdin.isatty():
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
    if tier_lower == "bringup":
        click.echo("  pytest -v             # run smoke tests with MagicMock instruments")
    elif use_starter or tier_lower == "bench":
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
    try:
        from litmus.store import list_stations

        stations = list_stations()
        if stations:
            available_roles = sorted(stations[0].instruments.keys())
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

    # Build function signature: context, <roles>, verify
    param_parts = ["context", *roles, "verify"]
    sig = ", ".join(param_parts)

    lines = [
        f'"""Tests for {test_name}."""',
        "",
        "",
        f"def test_{test_name}({sig}) -> None:",
        f'    """Measure {test_name}."""',
    ]
    # Add a helpful skeleton showing the 3-step pattern
    if roles:
        lines.append("    # 1. GET conditions from context")
        lines.append('    # vin = context.get_param("vin", 5.0)')
        lines.append("    #")
        lines.append("    # 2. SET UP stimulus")
        first_role = roles[0]
        lines.append(f"    # {first_role}.set_voltage(vin)")
        lines.append("    #")
        lines.append("    # 3. MEASURE and VERIFY (framework checks limits)")
        measure_role = roles[1] if len(roles) > 1 else roles[0]
        lines.append(f'    verify("{test_name}", float({measure_role}.measure_voltage()))')
    else:
        lines.append("    # TODO: Add test logic")
        lines.append("    pass")
    lines.append("")
    content = "\n".join(lines)

    tests_dir.mkdir(exist_ok=True)
    target.write_text(content)
    click.echo(f"Created {target}")
    click.echo("\nNext: pytest --mock-instruments")


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
    "--type",
    "-t",
    "file_type",
    type=click.Choice(
        [
            "catalog",
            "product",
            "station",
            "sequence",
            "fixture",
            "instrument_asset",
            "project",
        ]
    ),
    default=None,
    help="Explicit file type (skips auto-detection).",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def validate(paths, file_type, as_json):
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
            "catalog",
            "products",
            "stations",
            "sequences",
            "fixtures",
            "instruments",
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
        if as_json:
            click.echo(json.dumps({"files": [], "passed": 0, "failed": 0}))
        else:
            click.echo("No YAML files found.")
        return

    passed = 0
    failed = 0
    json_results = []

    for f in files:
        rel = f.relative_to(Path.cwd()) if f.is_relative_to(Path.cwd()) else f
        errors = validate_yaml(f, file_type=file_type, catalog_dir=f.parent)
        if errors:
            if as_json:
                json_results.append({"file": str(rel), "status": "FAIL", "errors": errors})
            else:
                click.echo(click.style(f"{rel} FAIL", fg="red"))
                for err in errors:
                    click.echo(err)
            failed += 1
        else:
            if as_json:
                json_results.append({"file": str(rel), "status": "OK", "errors": []})
            else:
                click.echo(click.style(f"{rel} OK", fg="green"))
            passed += 1

    if as_json:
        data = {"files": json_results, "passed": passed, "failed": failed}
        click.echo(json.dumps(data, indent=2))
    else:
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
        import uvicorn

        litmus_pkg = Path(__file__).parent
        uvicorn.run(
            "litmus.ui._asgi:app",
            host=host,
            port=port,
            reload=True,
            reload_dirs=[str(litmus_pkg)],
            reload_includes=["*.py", "*.yaml"],
            log_level="warning",
            timeout_graceful_shutdown=2,
        )
    else:
        from nicegui import ui

        from litmus.api.app import create_app

        create_app()
        # ``timeout_graceful_shutdown=2`` makes Ctrl+C exit within ~2s
        # even when WebSocket clients are still connected. Without it,
        # uvicorn waits indefinitely for connections to close.
        ui.run(
            host=host,
            port=port,
            reload=False,
            title="Litmus",
            favicon="⚡",
            timeout_graceful_shutdown=2,
        )


@main.command()
@click.option("--data-dir", default=None, help="Results directory")
@click.option("--limit", default=20, help="Number of runs to show")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def runs(data_dir: str | None, limit: int, as_json: bool):
    """List recent test runs."""
    from litmus.data._flight_query import IndexOutOfDate
    from litmus.data.backends.parquet import ParquetBackend

    data_dir = _get_data_dir(data_dir)

    backend = ParquetBackend(data_dir=data_dir)
    try:
        test_runs = backend.list_runs(limit=limit)
    except IndexOutOfDate as exc:
        raise click.ClickException(str(exc)) from None

    if not test_runs:
        if as_json:
            click.echo("[]")
        else:
            click.echo("No test runs found.")
        return

    if as_json:
        runs_data = [r.model_dump(exclude={"file_path"}) for r in test_runs]
        click.echo(json.dumps(runs_data, indent=2, default=str))
        return

    click.echo(f"{'Run ID':<10} {'DUT Serial':<15} {'Project':<20} {'Station':<20} {'Outcome':<10}")
    click.echo("-" * 80)

    for run in test_runs:
        run_id = (run.test_run_id or "")[:8]
        dut = run.dut_serial or ""
        project = run.project_name or ""
        station = run.station_id or ""
        outcome = run.outcome or ""
        click.echo(f"{run_id:<10} {dut:<15} {project:<20} {station:<20} {outcome:<10}")


@main.command()
@click.argument("run_id")
@click.option("--data-dir", default=None, help="Results directory")
@click.option(
    "-f",
    "--format",
    "fmt",
    type=click.Choice(["html", "pdf", "json", "csv"]),
    default=None,
    help="Generate report in format",
)
@click.option("-o", "--output", default=None, help="Output file or directory")
@click.option("-t", "--template", default="default", help="Report template name")
@click.option("--env", is_flag=True, default=False, help="Show environment snapshot")
def show(
    run_id: str,
    data_dir: str | None,
    fmt: str | None,
    output: str | None,
    template: str,
    env: bool,
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
    data_dir = _get_data_dir(data_dir)

    from litmus.reports.core import load_run_data

    if fmt:
        # Report generation mode
        from litmus.reports.core import generate_report

        try:
            data = load_run_data(run_id, data_dir)
        except FileNotFoundError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1)

        out_path = output or "."
        result = generate_report(data, out_path, fmt=fmt, template=template)
        click.echo(f"Report generated: {result}")
        return

    # Terminal display mode

    try:
        data = load_run_data(run_id, data_dir)
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

    # Show step results if available
    pq_path = _find_parquet_for_run(run_id, data_dir)
    if pq_path and not env:
        from litmus.data.backends.parquet import read_step_results

        manifest = read_step_results(pq_path)
        if manifest:
            click.echo("\nStep Results:")
            for entry in manifest:
                mc = entry.get("measurement_count")
                meas_info = f" ({mc} measurements)" if mc else ""
                outcome = entry.get("outcome") or "never_ran"
                if outcome == "never_ran":
                    func = entry.get("function", "")
                    loc = f" [{func}]" if func else ""
                else:
                    loc = entry.get("file") or ""
                    if loc and entry.get("function"):
                        loc = f" [{loc}::{entry['function']}]"
                    elif loc:
                        loc = f" [{loc}]"
                click.echo(f"  {entry['index']:>2}. {entry['name']}: {outcome}{meas_info}{loc}")

    if data.measurements and not env:
        click.echo("\nMeasurements:")
        for m in data.measurements:
            name = m.get("measurement_name") or ""
            value = m.get("value")
            units = m.get("units")
            outcome = m.get("outcome") or ""
            value_str = str(value) if value is not None else "—"
            units_str = f" {units}" if units else ""
            click.echo(f"  {name}: {value_str}{units_str} [{outcome}]")

    if env:
        from litmus.sbom import environment_from_parquet, format_environment_table

        pq_path = _find_parquet_for_run(run_id, data_dir)
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
    id_prefix: str,
    data_dir: str,
) -> tuple[list[dict], str]:
    """Read events matching an ID prefix from Arrow IPC files.

    Auto-detects whether the prefix matches a run_id or session_id.
    UUIDs never collide, so a prefix match on either column is unambiguous.

    Returns:
        (events, matched_column) where matched_column is "run_id" or "session_id".
    """
    import json as json_mod

    from litmus.data._ipc_writer import read_ipc_batches

    events_dir = Path(data_dir) / "events"
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
    "-f",
    "--format",
    "fmt",
    required=True,
    help="Target format (csv, json, stdf, hdf5, tdms, mdf4, atml)",
)
@click.option("-o", "--output-dir", default=None, help="Output directory")
@click.option("--data-dir", default=None, help="Data directory")
def export(
    id: str,
    fmt: str,
    output_dir: str | None,
    data_dir: str | None,
):
    """Export a test run or session to a different format via event replay.

    ID can be a run_id or session_id (prefix match). Auto-detected from
    stored events — UUIDs never collide.

    Examples:

        litmus export abc123 -f csv

        litmus export abc123 -f stdf -o exports/stdf/
    """
    from litmus.data.subscribers import get_subscriber_class, replay_to_subscriber

    if data_dir is None:
        data_dir = _get_data_dir(None)

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
        output_dir = f"exports/{fmt}"

    # Find events by run_id or session_id
    events, matched_col = _read_events_by_id(id, data_dir)
    if not events:
        click.echo(f"No events found for '{id}'.", err=True)
        raise SystemExit(1)

    kind = "session" if matched_col == "session_id" else "run"
    click.echo(f"Matched {kind} {id} ({len(events)} events)")

    # Subscriber subclasses define their own __init__ signature (typically
    # taking an output directory); the base class has none, so pyright can't
    # verify the call statically.
    sub = cls(Path(output_dir))  # type: ignore[call-arg]
    replay_to_subscriber(sub, events)

    # Find the output file(s)
    out_dir = Path(output_dir)
    candidates = sorted(f for f in out_dir.iterdir() if f.is_file())
    if candidates:
        for result_path in candidates:
            click.echo(f"Exported: {result_path}")
    else:
        click.echo(f"Export completed but no output file found in {output_dir}.", err=True)


def _list_export_formats() -> list[str]:
    """List available export formats (excluding report formats)."""
    from litmus.data.subscribers import list_subscribers

    return sorted(f for f in list_subscribers() if f not in {"html", "pdf"})


# -----------------------------------------------------------------------------
# SBOM Export
# -----------------------------------------------------------------------------


@main.command()
@click.argument("run_id")
@click.option("--data-dir", default=None, help="Results directory")
@click.option("-o", "--output", default=None, help="Output file (default: stdout)")
def sbom(run_id: str, data_dir: str | None, output: str | None):
    """Export CycloneDX SBOM for a test run's software environment.

    Reads the environment snapshot captured during the test run and
    converts it to CycloneDX 1.6 JSON format.

    Examples:
        litmus sbom abc123
        litmus sbom abc123 -o sbom.json
    """
    from litmus.sbom import environment_from_parquet, generate_cyclonedx

    data_dir = _get_data_dir(data_dir)

    pq_path = _find_parquet_for_run(run_id, data_dir)
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
@click.option("--output-dir", "-o", default="schemas", help="Directory for .schema.json files")
def schema_export(output_dir: str):
    """Export JSON Schema files for all Litmus YAML types.

    Generates .schema.json files that enable editor validation and
    autocomplete for every Litmus YAML type — sidecar, profile,
    project, station, fixture, product, catalog, instrument_asset.

    Example:
        litmus schema export
        litmus schema export -o litmus/schemas
    """
    from litmus.schema_export import export_schemas

    paths = export_schemas(Path(output_dir))
    for p in paths:
        click.echo(f"  {p}")
    click.echo(f"\nExported {len(paths)} schema(s) to {output_dir}/")


@schema.command("refresh")
@click.option(
    "--project-dir",
    default=".",
    help="Project root (defaults to current directory).",
)
def schema_refresh(project_dir: str):
    """Refresh .vscode/schemas/ and .vscode/settings.json after a Litmus upgrade.

    Regenerates every .schema.json from the installed Litmus's
    Pydantic models and rewrites the yaml.schemas mapping in
    .vscode/settings.json. Run this after upgrading the litmus-test
    package so VS Code autocomplete reflects the latest schema.

    Preserves any other keys you have in settings.json — only the
    yaml.schemas mapping is replaced. If .vscode/settings.json doesn't
    exist yet, it's created.

    Example:
        pip install -U litmus-test
        litmus schema refresh
    """
    import json

    from litmus.schema_export import export_schemas, vscode_yaml_schemas

    root = Path(project_dir).resolve()
    vscode_dir = root / ".vscode"
    schemas_dir = vscode_dir / "schemas"
    settings_path = vscode_dir / "settings.json"

    paths = export_schemas(schemas_dir)
    settings: dict[str, object] = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            click.echo(
                f"warning: {settings_path} is not valid JSON; rewriting from scratch",
                err=True,
            )
            settings = {}
    settings["yaml.schemas"] = vscode_yaml_schemas()
    vscode_dir.mkdir(exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    rel_settings = settings_path.relative_to(root)
    click.echo(f"Refreshed {len(paths)} schema(s) in {schemas_dir.relative_to(root)}/")
    click.echo(f"Updated {rel_settings}")


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

    content = template.read_text()

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
        # Preview ALL three side effects of the real run so users can
        # decide whether to commit before doing so. Previously only the
        # MCP JSON was shown, hiding the .claude/commands/ + CLAUDE.md
        # writes entirely.
        cmd = f"claude mcp add litmus -- {litmus_path} mcp serve"
        stubs_src = Path(__file__).parent / "skills" / "commands" / "claude-code"
        stubs_dst = Path.cwd() / ".claude" / "commands"
        claude_md = Path.cwd() / "CLAUDE.md"
        stub_files = sorted(p.name for p in stubs_src.glob("*.md")) if stubs_src.exists() else []

        def _rel(p: Path) -> Path:
            try:
                return p.relative_to(Path.cwd())
            except ValueError:
                return p

        click.echo("`litmus setup claude-code` would do three things:\n")
        click.echo("1. Register the MCP server via the Claude CLI:")
        click.echo(f"   $ {cmd}\n")
        click.echo("   Equivalent MCP JSON if you'd rather configure manually:")
        for line in json.dumps(config, indent=2).splitlines():
            click.echo(f"   {line}")
        click.echo("")
        click.echo(f"2. Copy {len(stub_files)} slash-command stub(s) to {_rel(stubs_dst)}/:")
        for name in stub_files:
            click.echo(f"     {name}")
        click.echo("")
        action = "Create" if not claude_md.exists() else "Update (Litmus section)"
        click.echo(f"3. {action} {_rel(claude_md)}")
        click.echo("   (Litmus context the agent reads on every conversation in this project.)")
        click.echo("")
        click.echo("Re-run without --print-only to apply all three.")
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
            "MCP server for hardware test configuration, instrument discovery, and test execution."
        ),
        "version": __version__,
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
    click.echo("  - save_product_spec: Save a new product specification")


# -----------------------------------------------------------------------------
# Instrument Discovery Commands
# -----------------------------------------------------------------------------


@main.command()
@click.option("--visa", "visa_only", is_flag=True, help="VISA instruments only")
@click.option("--ni", "ni_only", is_flag=True, help="NI devices only")
@click.option("--serial", "serial_only", is_flag=True, help="Serial ports only")
@click.option("--lxi", "lxi_only", is_flag=True, help="LXI network instruments only")
@click.option("--identify/--no-identify", default=True, help="Query *IDN? for each instrument")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def discover(
    visa_only: bool,
    ni_only: bool,
    serial_only: bool,
    lxi_only: bool,
    identify: bool,
    as_json: bool,
):
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

    if not as_json:
        click.echo("Scanning for instruments...")

    if identify:
        results = discover_and_identify(protocols)

        if as_json:
            data = {
                proto: [{"resource": resource, "identity": info} for resource, info in items]
                for proto, items in results.items()
            }
            click.echo(json.dumps(data, indent=2, default=str))
            return

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

        if as_json:
            click.echo(json.dumps(results, indent=2, default=str))
            return

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
def refs():
    """Stream curated reference docs to stdout.

    The shipped ref files live inside the installed package, so the
    CLI is the env-stable way for agents (and humans) to read them
    without baking absolute paths into project config.
    """
    pass


def _refs_dir() -> Path:
    return Path(__file__).parent / "skills" / "refs"


@refs.command("list")
def refs_list():
    """List available reference topics."""
    for path in sorted(_refs_dir().glob("*.md")):
        click.echo(path.stem)


@refs.command("show")
@click.argument("topic")
def refs_show(topic: str):
    """Print the named reference doc to stdout."""
    path = _refs_dir() / f"{topic}.md"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in _refs_dir().glob("*.md"))) or "(none)"
        raise click.ClickException(f"Unknown ref topic {topic!r}. Available: {available}")
    click.echo(path.read_text(), nl=False)


@main.group()
def catalog():
    """Catalog commands."""
    pass


@catalog.command("datasheet")
@click.argument("yaml_path", type=click.Path(exists=True))
@click.option(
    "-f",
    "--format",
    "fmt",
    default="html",
    type=click.Choice(["html", "pdf"]),
    help="Output format (default: html)",
)
@click.option("-o", "--output", "output", default=None, type=click.Path(), help="Output file path")
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
            from litmus.models.instrument import InstrumentInfo
            from litmus.models.instrument_asset import InstrumentAssetFile
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

    from litmus.store import normalize_and_check_instrument_types

    _, type_warnings = normalize_and_check_instrument_types(station_instruments)
    for w in type_warnings:
        click.echo(f"  Warning: {w}", err=True)

    from litmus.store import dump_yaml

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
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def instrument_list(as_json: bool):
    """List all instrument configuration files."""
    from litmus.instruments.loader import find_instruments_dir
    from litmus.store import load_instrument_files

    instruments_dir = find_instruments_dir()
    if not instruments_dir:
        if as_json:
            click.echo("[]")
        else:
            click.echo("No instruments/ directory found")
        return

    instruments = load_instrument_files(instruments_dir)
    if not instruments:
        if as_json:
            click.echo("[]")
        else:
            click.echo("No instrument files found")
        return

    if as_json:
        data = {
            inst_id: asset.model_dump(mode="json", exclude_none=True)
            for inst_id, asset in sorted(instruments.items())
        }
        click.echo(json.dumps(data, indent=2, default=str))
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
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def instrument_show(instrument_id: str, as_json: bool):
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

    if as_json:
        data = {"id": instrument_id, **asset.model_dump(mode="json", exclude_none=True)}
        click.echo(json.dumps(data, indent=2, default=str))
        return

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


def _base_filters(func):
    """Shared filter options for yield and gold commands."""
    func = click.option("--data-dir", default=None, help="Results directory")(func)
    func = click.option("--phase", default=None, help="Test phase (or 'all')")(func)
    func = click.option("--since", default=None, help="Start date (ISO format)")(func)
    func = click.option("--until", "until_date", default=None, help="End date (ISO format)")(func)
    func = click.option("--product", default=None, help="Product ID")(func)
    func = click.option("--station", default=None, help="Station ID")(func)
    func = click.option("--json", "as_json", is_flag=True, help="Output as JSON")(func)
    return func


def _get_data_dir(data_dir):
    """Resolve results directory from option or project config."""
    from litmus.data.data_dir import resolve_data_dir

    return str(resolve_data_dir(data_dir))


def _measurements_query(data_dir: str | None):
    """Create a MeasurementsQuery with resolved results directory."""
    from litmus.analysis.measurements_query import MeasurementsQuery

    return MeasurementsQuery(_data_dir=_get_data_dir(data_dir))


@main.group("metrics")
def metrics_group():
    """Manufacturing-test analytics (yield, pareto, cpk, trend, retest, time-loss)."""
    pass


@metrics_group.command("summary")
@_base_filters
@click.option("--period", type=click.Choice(["day", "week", "month"]), default="day")
def metrics_summary(data_dir, phase, since, until_date, product, station, period, as_json):
    """Yield summary: FPY, final yield, run counts, duration stats."""
    store = _measurements_query(data_dir)
    rows = store.yield_summary(
        product=product,
        station=station,
        phase=phase,
        since=since,
        until=until_date,
        period=period,
    )

    if not rows:
        click.echo("[]" if as_json else "No data found.")
        return

    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    click.echo(
        f"{'Period':<12} {'Product':<16} {'Station':<16} {'Runs':>5} "
        f"{'Pass':>5} {'Fail':>5} {'FPY':>6} {'Final':>6} {'Avg(s)':>7}"
    )
    click.echo("-" * 96)
    for r in rows:
        fpt = r.get("first_pass_total", 0)
        fpp = r.get("first_pass_passed", 0)
        fpy = f"{fpp / fpt * 100:.1f}%" if fpt else "N/A"
        us = r.get("unique_serials", 0)
        fp = r.get("final_passed", 0)
        final = f"{fp / us * 100:.1f}%" if us else "N/A"
        avg_d = r.get("avg_duration_s")
        avg = f"{avg_d:.1f}" if avg_d is not None else "N/A"
        click.echo(
            f"{str(r.get('period', '')):<12} {str(r.get('product', '')):<16} "
            f"{str(r.get('station', '')):<16} {r.get('total_runs', 0):>5} "
            f"{r.get('passed', 0):>5} {r.get('failed', 0):>5} "
            f"{fpy:>6} {final:>6} {avg:>7}"
        )


@metrics_group.command("pareto")
@_base_filters
@click.option("--top", "top_n", default=10, help="Number of top failures")
@click.option(
    "--group-by",
    type=click.Choice(["product", "step", "measurement"]),
    default="product",
    help=(
        "Lens for the pareto: ``product`` groups runs by ``dut_part_number`` "
        "(most-failing SKUs); ``step`` groups steps by ``step_path`` "
        "(most-failing tests); ``measurement`` groups limit-bearing "
        "measurements by name (the historical default)."
    ),
)
def metrics_pareto(data_dir, phase, since, until_date, product, station, top_n, group_by, as_json):
    """Top failures (Pareto). Group by product / step / measurement."""
    if group_by == "step":
        from litmus.analysis.steps_query import StepsQuery

        store = StepsQuery(_data_dir=data_dir or None)
        try:
            rows = store.failure_pareto(
                top_n=top_n,
                phase=phase,
                product=product,
                station=station,
                since=since,
                until=until_date,
            )
        finally:
            store.close()
        header = "Step (step_path)"
    elif group_by == "product":
        from litmus.analysis.runs_query import RunsQuery

        store = RunsQuery(_data_dir=data_dir or None)
        try:
            rows = store.failure_pareto(
                group_by="dut_part_number",
                top_n=top_n,
                phase=phase,
                product=product,
                station=station,
                since=since,
                until=until_date,
            )
        finally:
            store.close()
        header = "Product (dut_part_number)"
    else:  # measurement (historical)
        store = _measurements_query(data_dir)
        raw = store.pareto(
            product=product,
            station=station,
            phase=phase,
            since=since,
            until=until_date,
            top_n=top_n,
        )
        rows = [
            {
                "bucket": f"{r.get('step_name', '')}: {r.get('measurement_name', '')}",
                "failed_count": r.get("fail_count", 0),
                "total": r.get("fail_count", 0),
                "fail_rate_pct": r.get("fail_rate", 0),
            }
            for r in raw
        ]
        header = "Measurement (step: name)"

    if not rows:
        click.echo("[]" if as_json else "No data found.")
        return

    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    click.echo(f"{'#':<4} {header:<40} {'Failed':>7} {'Total':>7} {'Rate':>7}")
    click.echo("-" * 70)
    for i, r in enumerate(rows, 1):
        label = str(r.get("bucket") or "(none)")
        if len(label) > 38:
            label = label[:35] + "..."
        rate = r.get("fail_rate_pct")
        rate_str = f"{rate:>6.1f}%" if rate is not None else "      —"
        click.echo(
            f"{i:<4} {label:<40} {r.get('failed_count', 0):>7} {r.get('total', 0):>7} {rate_str}"
        )


@metrics_group.command("cpk")
@_base_filters
@click.option("--min-samples", default=10, help="Minimum sample count")
def metrics_cpk(data_dir, phase, since, until_date, product, station, min_samples, as_json):
    """Process capability (Cpk/Cp) per measurement."""
    store = _measurements_query(data_dir)
    rows = store.cpk(
        product=product,
        station=station,
        phase=phase,
        since=since,
        until=until_date,
        min_samples=min_samples,
    )

    if not rows:
        click.echo("[]" if as_json else "No measurements with enough samples.")
        return

    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    click.echo(f"{'Measurement':<30} {'N':>5} {'Mean':>10} {'Sigma':>10} {'Cpk':>7} {'Cp':>7}")
    click.echo("-" * 75)
    for r in rows:
        name = str(r.get("measurement_name", ""))
        if len(name) > 28:
            name = name[:25] + "..."
        cpk_val = f"{r['cpk']:.3f}" if r.get("cpk") is not None else "N/A"
        cp_val = f"{r['cp']:.3f}" if r.get("cp") is not None else "N/A"
        click.echo(
            f"{name:<30} {r.get('n') or 0:>5} {r.get('mean') or 0:>10.4f} "
            f"{r.get('sigma') or 0:>10.4f} {cpk_val:>7} {cp_val:>7}"
        )


@metrics_group.command("trend")
@_base_filters
@click.option("--period", type=click.Choice(["day", "week", "month"]), default="day")
def metrics_trend(data_dir, phase, since, until_date, product, station, period, as_json):
    """Yield trend over time."""
    store = _measurements_query(data_dir)
    rows = store.trend(
        product=product,
        station=station,
        phase=phase,
        since=since,
        until=until_date,
        period=period,
    )

    if not rows:
        click.echo("[]" if as_json else "No data found.")
        return

    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    click.echo(f"{'Period':<14} {'Total':>6} {'Passed':>7} {'Yield':>7}")
    click.echo("-" * 38)
    for r in rows:
        click.echo(
            f"{str(r.get('period', '')):<14} {r.get('total', 0):>6} "
            f"{r.get('passed', 0):>7} {r.get('yield_pct', 0):>6.1f}%"
        )


@metrics_group.command("retest")
@_base_filters
@click.option("--period", type=click.Choice(["day", "week", "month"]), default="day")
def metrics_retest(data_dir, phase, since, until_date, product, station, period, as_json):
    """Retest rates: how often DUTs are retried."""
    store = _measurements_query(data_dir)
    rows = store.retest(
        product=product,
        station=station,
        phase=phase,
        since=since,
        until=until_date,
        period=period,
    )

    if not rows:
        click.echo("[]" if as_json else "No data found.")
        return

    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    click.echo(f"{'Period':<14} {'Serials':>8} {'Retested':>9} {'Rate':>7} {'Avg Ret':>8}")
    click.echo("-" * 50)
    for r in rows:
        click.echo(
            f"{str(r.get('period', '')):<14} {r.get('total_serials', 0):>8} "
            f"{r.get('retested_count', 0):>9} {r.get('retest_rate', 0):>6.1f}% "
            f"{r.get('avg_retries', 0):>7.1f}"
        )


@metrics_group.command("time-loss")
@_base_filters
@click.option("--period", type=click.Choice(["day", "week", "month"]), default="day")
def metrics_time_loss(data_dir, phase, since, until_date, product, station, period, as_json):
    """Time lost to failures and errors."""
    store = _measurements_query(data_dir)
    rows = store.time_loss(
        product=product,
        station=station,
        phase=phase,
        since=since,
        until=until_date,
        period=period,
    )

    if not rows:
        click.echo("[]" if as_json else "No data found.")
        return

    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    click.echo(f"{'Period':<14} {'Total(s)':>10} {'Pass(s)':>10} {'Fail(s)':>10} {'Error(s)':>10}")
    click.echo("-" * 58)
    for r in rows:
        click.echo(
            f"{str(r.get('period', '')):<14} "
            f"{r.get('total_time_s', 0) or 0:>10.1f} "
            f"{r.get('pass_time_s', 0) or 0:>10.1f} "
            f"{r.get('fail_time_s', 0) or 0:>10.1f} "
            f"{r.get('error_time_s', 0) or 0:>10.1f}"
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
    "--type",
    "data_types",
    multiple=True,
    help="Data types to prune (e.g. channels, files, events)",
)
@click.option("--data-dir", default=None, help="Results directory")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted")
def data_prune(
    older_than: str,
    data_types: tuple[str, ...],
    data_dir: str | None,
    dry_run: bool,
) -> None:
    """Delete date-partitioned data older than the specified period."""
    from litmus.data.retention import prune_all

    data_dir_path = Path(_get_data_dir(data_dir))

    types = data_types or ("channels", "files", "events")
    try:
        result = prune_all(data_dir_path, older_than, data_types=types, dry_run=dry_run)
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
        click.echo(f"\n{total} items would be removed.")
    else:
        click.echo(f"\n{total} items removed.")


# Starter sentinels — runs whose product / station / serial / fixture
# matches any of these are scaffold/example runs, skipped by default.
# The user can opt them in with --include-starter.
_STARTER_PRODUCT_IDS = {"example_product"}
_STARTER_STATION_IDS = {"starter_station"}
_STARTER_FIXTURE_IDS = {"example_fixture"}
_STARTER_DUT_SERIALS = {"STARTER001", "SMOKE001"}


def _is_starter_parquet(parquet_path: Path) -> bool:
    """Return True if the parquet's first row matches any starter sentinel.

    Reads only the columns needed; one parquet = one run = small file.
    """
    import pyarrow.parquet as pq

    cols = ["product_id", "station_id", "dut_serial", "fixture_id"]
    try:
        t = pq.read_table(parquet_path, columns=cols)
    except (FileNotFoundError, OSError, KeyError):
        return False
    if t.num_rows == 0:
        return False
    row0 = {c: t[c][0].as_py() for c in cols if c in t.column_names}
    if row0.get("product_id") in _STARTER_PRODUCT_IDS:
        return True
    if row0.get("station_id") in _STARTER_STATION_IDS:
        return True
    if row0.get("dut_serial") in _STARTER_DUT_SERIALS:
        return True
    if row0.get("fixture_id") in _STARTER_FIXTURE_IDS:
        return True
    return False


def _copy_run_references(
    src_parquet: Path, src_data: Path, dst_data: Path, *, with_events: bool
) -> tuple[int, int]:
    """Copy the channel slices + files a run references into the destination store.

    Keeps the run whole in the global store (no dangling refs). ``file://`` keys
    copy by exact path (+ ``.meta.json`` sidecar so the catalog rebuilds); channel
    slices copy every matching segment. ``with_events`` also carries each session's
    event timeline. Returns ``(channel_segments_copied, files_copied)``.
    """
    import shutil

    from litmus.data.backends.parquet import extract_refs

    channels, files = extract_refs(src_parquet)
    sessions = {sid for _, sid in channels}

    def _copy(rel: Path) -> bool:
        s, d = src_data / rel, dst_data / rel
        if not s.exists() or d.exists():
            return False
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        return True

    n_files = 0
    for key in files:
        if _copy(Path("files") / key):
            n_files += 1
        _copy(Path("files") / f"{key}.meta.json")  # sidecar → catalog rebuild
        parts = key.split("/")
        if len(parts) >= 2:
            sessions.add(parts[1])  # file://{date}/{session}/{filename}

    n_chan = 0
    chan_root = src_data / "channels"
    if chan_root.is_dir():
        for cid, sid in channels:
            for seg in chan_root.glob(f"*/{cid}_{sid[:8]}*.arrow"):
                if _copy(seg.relative_to(src_data)):
                    n_chan += 1

    if with_events:
        ev_root = src_data / "events"
        if ev_root.is_dir():
            for sid in sessions:
                for ev in ev_root.glob(f"*/{sid}-*.arrow"):
                    _copy(ev.relative_to(src_data))
    return n_chan, n_files


def _global_data_dir() -> Path:
    """Resolve the platformdirs global data directory.

    Mirrors the fallback in litmus.data.data_dir.resolve_data_dir but
    ignores any project override — promote always targets the global
    store regardless of where the current cwd points.
    """
    import os

    import platformdirs

    home = Path(os.environ.get("LITMUS_HOME", platformdirs.user_data_dir("litmus")))
    return home / "data"


@data.command("promote")
@click.option(
    "--include-starter",
    is_flag=True,
    help="Also promote runs that match starter sentinels "
    "(example_product / starter_station / STARTER001 / etc.). "
    "Default skips these as throwaway learning runs.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be promoted; write nothing.")
@click.option(
    "--with-events",
    is_flag=True,
    help="Also carry each run's session event timeline (audit-grade archive).",
)
def data_promote(include_starter: bool, dry_run: bool, with_events: bool) -> None:
    """Move a starter project's local runs + their referenced data to the global store.

    Starter projects ship with ``data_dir: data`` in litmus.yaml so
    learning runs (mock instruments, example_product, STARTER001, etc.)
    don't pollute the platformdirs global store shared across projects
    on this machine. When you're ready to share data across projects,
    `litmus data promote` copies non-starter runs **plus the channel/file
    data they reference** into the global store (the runs stay whole — no
    dangling refs), and removes the ``data_dir`` override from your
    litmus.yaml. ``--with-events`` also carries each run's session events.

    Idempotent. Re-running promote after adding the flag picks up
    anything previously skipped.
    """
    import shutil

    from ruamel.yaml import YAML

    from litmus.connect import _find_project_config

    found = _find_project_config()
    if not found:
        raise click.ClickException(
            "No litmus.yaml found in this directory or any parent. "
            "`litmus data promote` runs inside a project directory."
        )
    project_root, project = found

    if not project.data_dir:
        raise click.ClickException(
            "This project's litmus.yaml has no `data_dir` override, "
            "so it's already using the global store. Nothing to promote."
        )

    src_data = (project_root / project.data_dir).resolve()
    dst_data = _global_data_dir().resolve()
    if src_data == dst_data:
        raise click.ClickException(
            f"Project data_dir resolves to the global store ({src_data}); nothing to promote."
        )

    src_runs_root = src_data / "runs" / "runs"
    if not src_runs_root.exists():
        click.echo(f"No runs found under {src_runs_root}; nothing to promote.")
        return

    parquets = sorted(src_runs_root.glob("*/*.parquet"))
    if not parquets:
        click.echo(f"No parquet files under {src_runs_root}; nothing to promote.")
        return

    to_copy: list[tuple[Path, Path]] = []
    skipped_starter = 0
    skipped_collision = 0

    for src in parquets:
        is_starter = _is_starter_parquet(src)
        if is_starter and not include_starter:
            skipped_starter += 1
            continue
        # dst preserves the YYYY-MM-DD subdir from the source path.
        rel = src.relative_to(src_runs_root)
        dst = dst_data / "runs" / "runs" / rel
        if dst.exists():
            skipped_collision += 1
            continue
        to_copy.append((src, dst))

    click.echo(f"Source:      {src_data}")
    click.echo(f"Destination: {dst_data}")
    click.echo("")
    click.echo(f"Found {len(parquets)} run parquets total.")
    if skipped_starter:
        flag_hint = "" if include_starter else " (use --include-starter to include)"
        click.echo(f"  {skipped_starter} starter / example run(s) — skipped{flag_hint}")
    if skipped_collision:
        click.echo(f"  {skipped_collision} already in global store — skipped (idempotent)")
    click.echo(f"  {len(to_copy)} to promote")

    if dry_run:
        click.echo("\n[dry-run] No files copied. litmus.yaml unchanged.")
        return

    if not to_copy:
        click.echo("\nNothing to promote. litmus.yaml unchanged.")
        return

    # Copy each run parquet + the channel/file data it references, so the run
    # stays whole in the global store (no dangling refs). Each is independent —
    # failures don't roll back, but files are skipped on collision so re-running
    # is safe.
    total_chan = total_files = 0
    for src, dst in to_copy:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        nc, nf = _copy_run_references(src, src_data, dst_data, with_events=with_events)
        total_chan += nc
        total_files += nf
    click.echo(
        f"\nCopied {len(to_copy)} run parquet(s) + {total_chan} channel segment(s) "
        f"+ {total_files} file(s) to {dst_data}/"
    )
    if with_events:
        click.echo("Carried each run's session event timeline (--with-events).")

    # Update litmus.yaml — drop the data_dir override so future runs
    # and queries from this project use the global store. Uses ruamel
    # to preserve formatting + comments.
    litmus_yaml = project_root / "litmus.yaml"
    yaml = YAML()
    yaml.preserve_quotes = True
    with litmus_yaml.open() as f:
        doc = yaml.load(f)
    if "data_dir" in doc:
        del doc["data_dir"]
        with litmus_yaml.open("w") as f:
            yaml.dump(doc, f)
        try:
            display_path = litmus_yaml.relative_to(Path.cwd())
        except ValueError:
            display_path = litmus_yaml
        click.echo(f"Updated {display_path}: removed `data_dir` override.")

    click.echo("")
    click.echo("Future runs and `litmus runs` queries from this project now use the global store.")
    click.echo(
        "The global store now holds the promoted runs + their referenced "
        "channel/file data, so they resolve there."
    )
    click.echo(
        f"The local {src_data} still has unpromoted/starter runs; remove it once "
        f"you've verified the global store:  rm -rf {src_data}"
    )


@data.command("reindex")
@click.option("--data-dir", default=None, help="Results directory")
def data_reindex(data_dir: str | None) -> None:
    """Kill index daemons and rebuild on next access.

    Use this when the index is out of date (e.g. after upgrading litmus).
    """
    from litmus.data.duckdb_manager import DuckDBDaemonManager
    from litmus.data.runs_duckdb_manager import RunsDuckDBManager

    results = Path(_get_data_dir(data_dir))

    for subdir, mgr_cls in [
        ("events", DuckDBDaemonManager),
        ("runs", RunsDuckDBManager),
    ]:
        d = results / subdir
        if d.exists():
            mgr_cls(d).force_restart()
            idx = d / "_index.duckdb"
            if idx.exists():
                idx.unlink()

    click.echo("Index daemons stopped. Index will rebuild on next query.")


def _merge_data_dir(src: Path, dst: Path) -> int:
    """Copy src's store subdirs into dst, skipping collisions. Returns files copied.

    Identities are unique (uuid4 sessions, ts+serial run files) so a plain union is
    safe; an already-present file is skipped, making re-runs idempotent.
    """
    import shutil

    copied = 0
    for sub in ("runs", "events", "channels", "files"):
        s = src / sub
        if not s.is_dir():
            continue
        for item in s.rglob("*"):
            if not item.is_file():
                continue
            d = dst / item.relative_to(src)
            if d.exists():
                continue
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, d)
            copied += 1
    return copied


@data.command("import")
@click.argument("source", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--data-dir", default=None, help="Destination results dir (default: configured).")
def data_import(source: Path, data_dir: str | None) -> None:
    """Merge another ``data_dir`` into this one; the store daemons rebuild from the files.

    Copies SOURCE's runs / events / channels / files into the destination store
    (skipping collisions — identities are unique), then restarts the store daemons
    so they rebuild their warm indexes from the merged files. Use after copying a
    ``data_dir`` from another machine, or merging two stores: the data files are the
    source of truth, so the daemons reconcile by rebuilding — but their state files
    (pids/ports from the other machine) are stale and must be cleared.
    """
    from litmus.data.channels.flight_manager import FlightDaemonManager
    from litmus.data.duckdb_manager import DuckDBDaemonManager
    from litmus.data.files.catalog_manager import FilesCatalogManager
    from litmus.data.runs_duckdb_manager import RunsDuckDBManager

    src = source.resolve()
    dst = Path(_get_data_dir(data_dir)).resolve()
    if src == dst:
        raise click.ClickException(f"Source and destination are the same: {src}")

    copied = _merge_data_dir(src, dst)
    click.echo(f"Merged {copied} file(s) from {src} into {dst}.")

    # Restart the store daemons: clear their now-stale state files + rebuild from
    # the merged data on next access (events/runs also drop the persisted index).
    for sub, mgr_cls in [
        ("events", DuckDBDaemonManager),
        ("runs", RunsDuckDBManager),
        ("channels", FlightDaemonManager),
        ("files", FilesCatalogManager),
    ]:
        d = dst / sub
        if d.exists():
            mgr_cls(d).force_restart()
            idx = d / "_index.duckdb"
            if idx.exists():
                idx.unlink()
    click.echo("Store daemons restarted; warm indexes rebuild on next access.")


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------


def _resolve_daemon_dirs(
    targets: tuple[str, ...] | None,
    *,
    all_flag: bool,
) -> list[tuple[str, Path]]:
    """Resolve user-specified targets to ``[(label, dir), ...]``.

    With ``--all`` (or no targets), return all three canonical
    daemons (events, runs, channels) under the configured
    ``data_dir``. Targets can be the labels themselves
    (``events`` / ``runs`` / ``channels``) or absolute directory
    paths to operate on a non-default project.
    """
    from litmus.data.data_dir import resolve_data_dir

    canonical = {
        "events": Path(resolve_data_dir()) / "events",
        "runs": Path(resolve_data_dir()) / "runs",
        "channels": Path(resolve_data_dir()) / "channels",
    }
    if not targets or all_flag:
        return list(canonical.items())
    out: list[tuple[str, Path]] = []
    for t in targets:
        if t in canonical:
            out.append((t, canonical[t]))
        else:
            p = Path(t).resolve()
            out.append((p.name, p))
    return out


def _manager_for(label: str, daemon_dir: Path):
    """Return the ``DaemonManager`` instance for a daemon-dir label."""
    if label == "channels":
        from litmus.data.channels.flight_manager import FlightDaemonManager

        return FlightDaemonManager(daemon_dir)
    if label == "events":
        from litmus.data.duckdb_manager import DuckDBDaemonManager

        return DuckDBDaemonManager(daemon_dir)
    if label == "runs":
        from litmus.data.runs_duckdb_manager import RunsDuckDBManager

        return RunsDuckDBManager(daemon_dir)
    # Fallback: heuristic by directory name.
    from litmus.data.duckdb_manager import DuckDBDaemonManager

    return DuckDBDaemonManager(daemon_dir)


@main.group("daemon")
def daemon_group():
    """Manage Litmus background daemons (events / runs / channels)."""
    pass


@daemon_group.command("status")
def daemon_status() -> None:
    """Show running daemons, their PIDs, refs, and locations.

    Reads the per-daemon state file directly. No daemon contact
    required — works even if a daemon is unreachable but its state
    file is still on disk (in which case the listed PID may be
    dead; check with ``ps`` if in doubt).
    """
    from litmus.data._daemon_lifecycle import _pid_alive

    rows = _resolve_daemon_dirs((), all_flag=True)
    click.echo(f"{'daemon':<10} {'pid':<8} {'alive':<6} {'refs':<5} location")
    click.echo("-" * 80)
    for label, daemon_dir in rows:
        if not daemon_dir.exists():
            click.echo(f"{label:<10} {'-':<8} {'-':<6} {'-':<5} (no dir)")
            continue
        mgr = _manager_for(label, daemon_dir)
        state = mgr.read_state()
        pid = state.get("pid")
        alive = _pid_alive(pid) if isinstance(pid, int) else False
        refs = len(state.get("refs", []) or [])
        loc = state.get("location", "")
        pid_str = str(pid) if pid is not None else "-"
        alive_str = "yes" if alive else ("no" if pid is not None else "-")
        click.echo(f"{label:<10} {pid_str:<8} {alive_str:<6} {refs:<5} {loc}")


@daemon_group.command("restart")
@click.argument("targets", nargs=-1)
@click.option("--all", "all_flag", is_flag=True, help="Restart every daemon under the project")
def daemon_restart(targets: tuple[str, ...], all_flag: bool) -> None:
    """Restart selected daemons (SIGTERM the running process; respawn on next access).

    Use after editing daemon code while ``litmus serve --reload``
    is running, or after bumping ``_SCHEMA_VERSION`` so the schema
    rebuild path runs at the next acquire.

    Targets can be ``events`` / ``runs`` / ``channels`` (resolved
    against the configured ``data_dir``) or absolute directory
    paths. With ``--all`` or no targets, restarts all three.
    """
    rows = _resolve_daemon_dirs(targets, all_flag=all_flag)
    for label, daemon_dir in rows:
        if not daemon_dir.exists():
            click.echo(f"[{label}] no directory at {daemon_dir} — skipped")
            continue
        mgr = _manager_for(label, daemon_dir)
        try:
            mgr.force_restart()
        except Exception as exc:  # noqa: BLE001 — operator command, surface and keep going
            click.echo(f"[{label}] restart failed: {exc}")
            continue
        click.echo(f"[{label}] restarted (next acquire spawns fresh)")


@daemon_group.command("stop")
@click.argument("targets", nargs=-1)
@click.option("--all", "all_flag", is_flag=True, help="Stop every daemon under the project")
def daemon_stop(targets: tuple[str, ...], all_flag: bool) -> None:
    """Stop selected daemons without respawning.

    Same kill semantics as ``restart`` (SIGTERM the pid in state,
    SIGKILL after grace), but doesn't trigger a respawn. The next
    actual ``acquire()`` from a UI / CLI / test will lazily spawn
    a fresh daemon when needed.
    """
    from litmus.data._daemon_lifecycle import _pid_alive

    rows = _resolve_daemon_dirs(targets, all_flag=all_flag)
    for label, daemon_dir in rows:
        if not daemon_dir.exists():
            click.echo(f"[{label}] no directory at {daemon_dir} — skipped")
            continue
        mgr = _manager_for(label, daemon_dir)
        state = mgr.read_state()
        pid = state.get("pid")
        if not isinstance(pid, int) or not _pid_alive(pid):
            click.echo(f"[{label}] not running")
            continue
        try:
            mgr._kill_daemon(pid)  # noqa: SLF001 — operator-side use of internal helper
            mgr.cleanup_state_files()
        except Exception as exc:  # noqa: BLE001
            click.echo(f"[{label}] stop failed: {exc}")
            continue
        click.echo(f"[{label}] stopped (pid {pid})")


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------


@main.command("benchmark")
@click.option("--full", is_flag=True, help="Run the full sweep (100/1k/10k units, 1/2/4 writers)")
@click.option("--rounds", default=None, type=int, help="Timed rounds per case (override)")
@click.option(
    "-o",
    "--output",
    default=".benchmarks",
    help="Directory for the result folder",
    show_default=True,
)
@click.option("--no-save", is_flag=True, help="Print the summary but don't write a result folder")
def benchmark(full: bool, rounds: int | None, output: str, no_save: bool) -> None:
    """Measure this machine's per-store performance.

    Runs the data-store operations (events / runs / channels / files) at a
    sweep of unit counts and writer counts against a throwaway temp
    directory — one measured row per case — and prints a results table
    plus a cost-model summary. By default it also writes a self-describing
    result folder ``.benchmarks/<date>/`` (report.md + report.json) with
    hardware, versions, options, every row, and the run's RAM/CPU
    footprint. Send it to the maintainers when reporting a perf issue.

    Nothing is sent anywhere automatically; the temp directory and its
    daemons are removed when the run finishes.
    """
    from litmus.benchmark import BenchmarkOptions, run_benchmark, write_report
    from litmus.benchmark.runner import format_summary

    tier = "full" if full else "fast"
    opts = BenchmarkOptions(tier=tier)
    if rounds is not None:
        opts.rounds = rounds
    elif tier == "full":
        opts.rounds = 5

    click.echo(f"Running litmus benchmark ({tier} tier) — temp dir, auto-cleaned…")
    report = run_benchmark(opts, on_progress=lambda m: click.echo(f"  {m}", err=True))
    click.echo("")
    click.echo(format_summary(report))
    if not no_save:
        run_dir = write_report(report, output)
        click.echo("")
        click.echo(f"Wrote {run_dir}/  (report.md + report.json)")
        click.echo(
            "Paste report.md into a GitHub issue (renders as tables), or attach "
            "report.json, when reporting a performance issue."
        )


# ---------------------------------------------------------------------------
# Grafana dashboards
# ---------------------------------------------------------------------------

# Import and register the grafana subgroup
from litmus.grafana.cli import grafana  # noqa: E402

main.add_command(grafana)


if __name__ == "__main__":
    main()
