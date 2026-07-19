"""Instrument discovery command."""

from __future__ import annotations

import json

import click

from testerkit.cli._common import _format_instrument
from testerkit.cli.root import main


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
        testerkit discover              # Scan all protocols
        testerkit discover --visa       # VISA only
        testerkit discover --no-identify  # Skip *IDN? queries (faster)
    """
    from testerkit.instruments.discovery import discover as do_discover
    from testerkit.instruments.discovery import discover_and_identify

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

    click.echo("\nNext: testerkit station init")
