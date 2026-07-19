"""Catalog commands."""

from __future__ import annotations

from pathlib import Path

import click

from testerkit.cli.root import main


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

        testerkit catalog datasheet catalog/keysight/keysight_e8257d.yaml -o /tmp/e8257d.html
    """
    from testerkit.reports.datasheet import generate_datasheet

    out = generate_datasheet(Path(yaml_path), Path(output) if output else None, fmt=fmt)
    click.echo(f"Datasheet written to {out}")
