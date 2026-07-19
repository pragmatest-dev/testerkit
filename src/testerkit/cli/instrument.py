"""Instrument management commands."""

from __future__ import annotations

import json

import click

from testerkit.cli.root import main


@main.group()
def instrument():
    """Instrument management commands."""
    pass


@instrument.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def instrument_list(as_json: bool):
    """List all instrument configuration files."""
    from testerkit.instruments.loader import find_instruments_dir
    from testerkit.store import load_instrument_files

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
    from testerkit.instruments.loader import find_instruments_dir
    from testerkit.store import load_instrument_asset

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
        testerkit instrument cal keithley_dmm_001 --due 2025-12-15 --cert CAL-2025-001
    """
    from testerkit.instruments.loader import find_instruments_dir
    from testerkit.store import load_instrument_asset, save_instrument_asset

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
