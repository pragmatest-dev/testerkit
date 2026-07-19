"""Benchmark command."""

from __future__ import annotations

import click

from testerkit.cli.root import main


@main.command("benchmark")
@click.option("--full", is_flag=True, help="Run the full sweep (100/1k/10k unit, 1/2/4 writers)")
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
    from testerkit.benchmark import BenchmarkOptions, run_benchmark, write_report
    from testerkit.benchmark.runner import format_summary

    tier = "full" if full else "fast"
    opts = BenchmarkOptions(tier=tier)
    if rounds is not None:
        opts.rounds = rounds
    elif tier == "full":
        opts.rounds = 5

    click.echo(f"Running testerkit benchmark ({tier} tier) — temp dir, auto-cleaned…")
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
