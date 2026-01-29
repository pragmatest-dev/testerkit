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


if __name__ == "__main__":
    main()
