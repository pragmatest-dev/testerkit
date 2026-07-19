"""Stream the shipped user-facing documentation to stdout.

Single-source replacement for the removed ``testerkit refs`` command: instead of
a separate curated ref corpus, this streams straight from the same
``docs/`` tree that ships in the wheel (see the
``[tool.hatch.build.targets.wheel.force-include]`` rules in ``pyproject.toml``
and ``testerkit.ui.pages.docs.page._resolve_docs_dir``, which this mirrors).
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import click

from testerkit.cli.root import main

# Sections that ship as user-facing docs (mirrors testerkit.ui.pages.docs.page).
KNOWN_SECTIONS = ("concepts", "how-to", "reference", "tutorial", "integration")


def _docs_dir() -> Path:
    """Locate the docs directory across wheel and editable installs.

    Wheel installs bundle the curated user-facing tiers at ``testerkit/_docs/``.
    Editable / source installs (this dev repo) don't get the bundle, so fall
    back to the repo's ``docs/`` directory above ``src/testerkit/``.
    """
    pkg_root = Path(str(importlib.resources.files("testerkit")))
    bundled = pkg_root / "_docs"
    if bundled.exists():
        return bundled
    return pkg_root.parent.parent / "docs"


@main.group()
def docs():
    """Stream the shipped documentation to stdout.

    The shipped doc pages live inside the installed package (or the repo's
    ``docs/`` dir in a dev checkout), so the CLI is the env-stable way for
    agents (and humans) to read them without baking absolute paths into
    project config.
    """
    pass


@docs.command("list")
@click.argument("section", required=False)
def docs_list(section: str | None):
    """List available documentation pages, optionally filtered to SECTION.

    Example:
        testerkit docs list
        testerkit docs list concepts
    """
    root = _docs_dir()
    if section is not None:
        if section not in KNOWN_SECTIONS:
            available = ", ".join(KNOWN_SECTIONS)
            raise click.ClickException(f"Unknown docs section {section!r}. Available: {available}")
        sections = (section,)
    else:
        sections = KNOWN_SECTIONS

    # Only ever walk known shipped sections — a dev checkout's docs/ root
    # also holds docs/_internal (contributor material excluded from the
    # wheel via force-include), which must never show up as "shipped" docs.
    pages: list[Path] = []
    for name in sections:
        section_dir = root / name
        if section_dir.exists():
            pages.extend(section_dir.rglob("*.md"))

    for path in sorted(pages):
        click.echo(str(path.relative_to(root).with_suffix("")))


@docs.command("show")
@click.argument("path")
def docs_show(path: str):
    """Print the named documentation page to stdout.

    PATH is relative to the docs root, e.g. ``concepts/data/three-verbs`` or
    ``how-to/execution/operator-prompts`` (with or without a trailing
    ``.md``).

    Example:
        testerkit docs show concepts/data/three-verbs
        testerkit docs show how-to/execution/operator-prompts
    """
    root = _docs_dir()
    rel = path if path.endswith(".md") else f"{path}.md"
    top_section = rel.split("/", 1)[0]
    doc_path = root / rel
    if top_section not in KNOWN_SECTIONS or not doc_path.exists():
        available = ", ".join(KNOWN_SECTIONS)
        raise click.ClickException(f"Unknown doc path {path!r}. Available sections: {available}")
    click.echo(doc_path.read_text(), nl=False)
