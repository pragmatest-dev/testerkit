"""Run listing, display, export, and SBOM commands."""

from __future__ import annotations

import json
from pathlib import Path

import click

from litmus.cli._common import _get_data_dir
from litmus.cli.root import main


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


@main.command()
@click.option("--data-dir", default=None, help="Results directory")
@click.option("--limit", default=20, help="Number of runs to show")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def runs(data_dir: str | None, limit: int, as_json: bool):
    """List recent test runs."""
    from litmus.data._flight_errors import FlightPermanentError
    from litmus.data.backends.parquet import ParquetBackend

    data_dir = _get_data_dir(data_dir)

    backend = ParquetBackend(data_dir=data_dir)
    try:
        test_runs = backend.list_runs(limit=limit)
    except FlightPermanentError as exc:
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

    click.echo(f"{'Run ID':<10} {'UUT Serial':<15} {'Project':<20} {'Station':<20} {'Outcome':<10}")
    click.echo("-" * 80)

    for run in test_runs:
        run_id = (run.test_run_id or "")[:8]
        uut = run.uut_serial_number or ""
        project = run.project_name or ""
        station = run.station_id or ""
        outcome = run.outcome or ""
        click.echo(f"{run_id:<10} {uut:<15} {project:<20} {station:<20} {outcome:<10}")


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
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Show each step's full step_path (and the run's parquet file) as a location locator",
)
def show(
    run_id: str,
    data_dir: str | None,
    fmt: str | None,
    output: str | None,
    template: str,
    env: bool,
    verbose: bool,
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
    click.echo(f"  UUT Serial: {data.uut_serial_number}")
    click.echo(f"  Station: {data.station_id}")
    click.echo(f"  Outcome: {data.outcome}")
    click.echo(f"  Started: {data.started_at}")
    click.echo(f"  Ended: {data.ended_at}")
    click.echo(f"  Steps: {len(data.step_names)}")
    click.echo(f"  Measurements: {data.total_measurements} ({data.failed_measurements} failed)")

    # Step results from the daemon's steps view — the same per-step source
    # the UI and API use (StepsQuery). Resolving the run already goes through
    # the daemon (RunsQuery, via load_run_data above), so this adds no new
    # dependency and uses the one blessed per-(step, vector) grouping.
    # include_incomplete=True keeps collected-but-never-ran steps (ended_at
    # NULL) visible. step_path already carries the function identity, so no
    # separate file/function location suffix is shown.
    if not env:
        from litmus.analysis.steps_query import StepsQuery

        with StepsQuery(_data_dir=data_dir) as q:
            steps = q.list_for_run(run_id, include_incomplete=True)
        if steps:
            click.echo("\nStep Results:")
            if verbose and steps[0].file_path:
                click.echo(f"  file: {steps[0].file_path}")
            for s in steps:
                meas_info = f" ({s.measurement_count} measurements)" if s.measurement_count else ""
                outcome = s.outcome or "never_ran"
                # Default shows the concise step_name; -v shows the full
                # step_path (container/function locator).
                name = (s.step_path or s.step_name) if verbose else (s.step_name or s.step_path)
                click.echo(f"  {(s.step_index or 0):>2}. {name or ''}: {outcome}{meas_info}")

    if data.measurements and not env:
        click.echo("\nMeasurements:")
        for m in data.measurements:
            name = m.get("measurement_name") or ""
            value = m.get("value")
            unit = m.get("unit")
            outcome = m.get("outcome") or ""
            value_str = str(value) if value is not None else "—"
            unit_str = f" {unit}" if unit else ""
            click.echo(f"  {name}: {value_str}{unit_str} [{outcome}]")

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
    help="Target format (csv, json, stdf, hdf5, tdms, mdf4)",
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
