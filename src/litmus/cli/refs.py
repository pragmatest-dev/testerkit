"""Reference docs streaming commands."""

from __future__ import annotations

from pathlib import Path

import click

from litmus.cli.root import main

_PKG_ROOT = Path(__file__).resolve().parent.parent


@main.group()
def refs():
    """Stream curated reference docs to stdout.

    The shipped ref files live inside the installed package, so the
    CLI is the env-stable way for agents (and humans) to read them
    without baking absolute paths into project config.
    """
    pass


def _refs_dir() -> Path:
    return _PKG_ROOT / "skills" / "refs"


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
