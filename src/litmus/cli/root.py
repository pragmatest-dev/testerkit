"""Litmus command-line interface."""

from __future__ import annotations

import click

from litmus import __version__


@click.group()
@click.version_option(version=__version__, prog_name="litmus")
def main():
    """Litmus hardware test platform."""
    pass
