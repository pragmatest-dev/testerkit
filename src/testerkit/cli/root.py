"""TesterKit command-line interface."""

from __future__ import annotations

import click

from testerkit import __version__


@click.group()
@click.version_option(version=__version__, prog_name="testerkit")
def main():
    """TesterKit hardware test platform."""
    pass
