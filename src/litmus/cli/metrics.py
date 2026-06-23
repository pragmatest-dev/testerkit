"""Manufacturing-test analytics (metrics) commands."""

from __future__ import annotations

import json

import click

from litmus.cli._common import _get_data_dir
from litmus.cli.root import main


def _base_filters(func):
    """Shared filter options for yield and gold commands."""
    func = click.option("--data-dir", default=None, help="Results directory")(func)
    func = click.option("--phase", default=None, help="Test phase (or 'all')")(func)
    func = click.option("--since", default=None, help="Start date (ISO format)")(func)
    func = click.option("--until", "until_date", default=None, help="End date (ISO format)")(func)
    func = click.option("--part", default=None, help="Part ID")(func)
    func = click.option("--station", default=None, help="Station ID")(func)
    func = click.option("--json", "as_json", is_flag=True, help="Output as JSON")(func)
    return func


def _measurements_query(data_dir: str | None):
    """Create a MeasurementsQuery with resolved results directory."""
    from litmus.analysis.measurements_query import MeasurementsQuery

    return MeasurementsQuery(_data_dir=_get_data_dir(data_dir))


@main.group("metrics")
def metrics_group():
    """Manufacturing-test analytics (yield, pareto, ppk, trend, retest, time-loss)."""
    pass


@metrics_group.command("summary")
@_base_filters
@click.option("--period", type=click.Choice(["day", "week", "month"]), default="day")
def metrics_summary(data_dir, phase, since, until_date, part, station, period, as_json):
    """Yield summary: FPY, final yield, run counts, RTY, DPMO, DPPM, duration stats."""
    with _measurements_query(data_dir) as store:
        rows = store.yield_summary(
            part=part,
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
            click.echo(json.dumps([r.model_dump() for r in rows], indent=2, default=str))
            return

        click.echo(
            f"{'Period':<12} {'Part':<16} {'Station':<16} {'Runs':>5} "
            f"{'Pass':>5} {'Fail':>5} {'FPY':>6} {'Final':>6} "
            f"{'RTY':>6} {'DPMO':>8} {'DPPM':>8} {'Avg(s)':>7}"
        )
        click.echo("-" * 120)
        for r in rows:
            fpt = r.first_pass_total
            fpp = r.first_pass_passed
            fpy = f"{fpp / fpt * 100:.1f}%" if fpt else "N/A"
            us = r.unique_serials
            fp = r.final_passed
            final = f"{fp / us * 100:.1f}%" if us else "N/A"
            rty = f"{r.rty * 100:.1f}%" if r.rty is not None else "N/A"
            dpmo = f"{r.dpmo:.0f}" if r.dpmo is not None else "N/A"
            dppm = f"{r.dppm:.0f}" if r.dppm is not None else "N/A"
            avg_d = r.avg_duration_s
            avg = f"{avg_d:.1f}" if avg_d is not None else "N/A"
            click.echo(
                f"{str(r.period):<12} {str(r.part):<16} "
                f"{str(r.station):<16} {r.total_runs:>5} "
                f"{r.passed:>5} {r.failed:>5} "
                f"{fpy:>6} {final:>6} "
                f"{rty:>6} {dpmo:>8} {dppm:>8} {avg:>7}"
            )


@metrics_group.command("pareto")
@_base_filters
@click.option("--top", "top_n", default=10, help="Number of top failures")
@click.option(
    "--group-by",
    type=click.Choice(["part", "step", "measurement"]),
    default="part",
    help=(
        "Lens for the pareto: ``part`` groups runs by ``uut_part_number`` "
        "(most-failing SKUs); ``step`` groups steps by ``step_path`` "
        "(most-failing tests); ``measurement`` groups limit-bearing "
        "measurements by name (the historical default)."
    ),
)
def metrics_pareto(data_dir, phase, since, until_date, part, station, top_n, group_by, as_json):
    """Top failures (Pareto). Group by part / step / measurement."""
    if group_by == "step":
        from litmus.analysis.steps_query import StepsQuery

        store = StepsQuery(_data_dir=data_dir or None)
        try:
            rows = store.pareto(
                top_n=top_n,
                phase=phase,
                part=part,
                station=station,
                since=since,
                until=until_date,
            )
        finally:
            store.close()
        header = "Step (step_path)"
    elif group_by == "part":
        from litmus.analysis.runs_query import RunsQuery

        store = RunsQuery(_data_dir=data_dir or None)
        try:
            rows = store.pareto(
                group_by="uut_part_number",
                top_n=top_n,
                phase=phase,
                part=part,
                station=station,
                since=since,
                until=until_date,
            )
        finally:
            store.close()
        header = "Part (uut_part_number)"
    else:  # measurement (historical)
        store = _measurements_query(data_dir)
        try:
            raw = store.pareto(
                part=part,
                station=station,
                phase=phase,
                since=since,
                until=until_date,
                top_n=top_n,
            )
        finally:
            store.close()
        rows = [r.to_bucket_dict() for r in raw]
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


@metrics_group.command("ppk")
@_base_filters
@click.option("--min-samples", default=10, help="Minimum sample count")
def metrics_ppk(data_dir, phase, since, until_date, part, station, min_samples, as_json):
    """Process performance (Ppk/Pp) per measurement."""
    with _measurements_query(data_dir) as store:
        rows = store.ppk(
            part=part,
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
            click.echo(json.dumps([r.model_dump() for r in rows], indent=2, default=str))
            return

        click.echo(
            f"{'Measurement':<24} {'Characteristic':<16} {'Pin':<10} "
            f"{'N':>5} {'Mean':>10} {'Sigma':>10} {'Ppk':>7} {'Pp':>7}"
        )
        click.echo("-" * 95)
        for r in rows:
            name = str(r.measurement_name)
            if len(name) > 22:
                name = name[:19] + "..."
            char = str(r.characteristic_id or "-")
            if len(char) > 14:
                char = char[:11] + "..."
            pin = str(r.uut_pin or "-")
            if len(pin) > 8:
                pin = pin[:5] + "..."
            ppk_val = f"{r.ppk:.3f}" if r.ppk is not None else "N/A"
            pp_val = f"{r.pp:.3f}" if r.pp is not None else "N/A"
            click.echo(
                f"{name:<24} {char:<16} {pin:<10} {r.n or 0:>5} {r.mean or 0:>10.4f} "
                f"{r.sigma or 0:>10.4f} {ppk_val:>7} {pp_val:>7}"
            )


@metrics_group.command("trend")
@_base_filters
@click.option("--period", type=click.Choice(["day", "week", "month"]), default="day")
def metrics_trend(data_dir, phase, since, until_date, part, station, period, as_json):
    """Yield trend over time."""
    with _measurements_query(data_dir) as store:
        rows = store.trend(
            part=part,
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
            click.echo(json.dumps([r.model_dump() for r in rows], indent=2, default=str))
            return

        click.echo(f"{'Period':<14} {'Total':>6} {'Passed':>7} {'Yield':>7}")
        click.echo("-" * 38)
        for r in rows:
            click.echo(f"{str(r.period):<14} {r.total:>6} {r.passed:>7} {r.yield_pct or 0:>6.1f}%")


@metrics_group.command("retest")
@_base_filters
@click.option("--period", type=click.Choice(["day", "week", "month"]), default="day")
def metrics_retest(data_dir, phase, since, until_date, part, station, period, as_json):
    """Retest rates: how often UUTs are retried."""
    with _measurements_query(data_dir) as store:
        rows = store.retest(
            part=part,
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
            click.echo(json.dumps([r.model_dump() for r in rows], indent=2, default=str))
            return

        click.echo(f"{'Period':<14} {'Serials':>8} {'Retested':>9} {'Rate':>7} {'Avg Ret':>8}")
        click.echo("-" * 50)
        for r in rows:
            click.echo(
                f"{str(r.period):<14} {r.total_serials:>8} "
                f"{r.retested_count:>9} {r.retest_rate or 0:>6.1f}% "
                f"{r.avg_retries or 0:>7.1f}"
            )


@metrics_group.command("time-loss")
@_base_filters
@click.option("--period", type=click.Choice(["day", "week", "month"]), default="day")
def metrics_time_loss(data_dir, phase, since, until_date, part, station, period, as_json):
    """Time lost to failures and errors."""
    with _measurements_query(data_dir) as store:
        rows = store.time_loss(
            part=part,
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
            click.echo(json.dumps([r.model_dump() for r in rows], indent=2, default=str))
            return

        click.echo(
            f"{'Period':<14} {'Total(s)':>10} {'Pass(s)':>10} {'Fail(s)':>10} {'Error(s)':>10}"
        )
        click.echo("-" * 58)
        for r in rows:
            click.echo(
                f"{str(r.period):<14} "
                f"{r.total_time_s or 0:>10.1f} "
                f"{r.pass_time_s or 0:>10.1f} "
                f"{r.fail_time_s or 0:>10.1f} "
                f"{r.error_time_s or 0:>10.1f}"
            )
