"""YAML validation command."""

from __future__ import annotations

import json
from pathlib import Path

import click

from litmus.cli.root import main


@main.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--type",
    "-t",
    "file_type",
    type=click.Choice(
        [
            "catalog",
            "part",
            "station",
            "sequence",
            "fixture",
            "instrument_asset",
            "project",
        ]
    ),
    default=None,
    help="Explicit file type (skips auto-detection).",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def validate(paths, file_type, as_json):
    """Validate YAML configuration files.

    Checks catalog, part, station, sequence, fixture, instrument, and
    project YAML files against their Pydantic schemas and reports errors
    with field paths.

    If no paths given, scans standard directories in the current project.

    Examples:
        litmus validate catalog/keysight_34461a.yaml
        litmus validate instruments/ --type instrument_asset
        litmus validate catalog/ stations/
        litmus validate
    """
    from litmus.validation import validate_yaml

    # Collect files
    files: list[Path] = []
    if paths:
        for p in paths:
            p = Path(p)
            if p.is_dir():
                files.extend(sorted(p.rglob("*.yaml")))
            else:
                files.append(p)
    else:
        # Auto-scan standard directories
        scan_dirs = [
            "catalog",
            "parts",
            "stations",
            "sequences",
            "fixtures",
            "instruments",
        ]
        for dirname in scan_dirs:
            d = Path.cwd() / dirname
            if d.is_dir():
                files.extend(sorted(d.rglob("*.yaml")))
        # Also check litmus.yaml in project root
        project_yaml = Path.cwd() / "litmus.yaml"
        if project_yaml.exists():
            files.append(project_yaml)

    if not files:
        if as_json:
            click.echo(json.dumps({"files": [], "passed": 0, "failed": 0}))
        else:
            click.echo("No YAML files found.")
        return

    passed = 0
    failed = 0
    json_results = []

    for f in files:
        rel = f.relative_to(Path.cwd()) if f.is_relative_to(Path.cwd()) else f
        errors = validate_yaml(f, file_type=file_type, catalog_dir=f.parent)
        if errors:
            if as_json:
                json_results.append({"file": str(rel), "status": "FAIL", "errors": errors})
            else:
                click.echo(click.style(f"{rel} FAIL", fg="red"))
                for err in errors:
                    click.echo(err)
            failed += 1
        else:
            if as_json:
                json_results.append({"file": str(rel), "status": "OK", "errors": []})
            else:
                click.echo(click.style(f"{rel} OK", fg="green"))
            passed += 1

    if as_json:
        data = {"files": json_results, "passed": passed, "failed": failed}
        click.echo(json.dumps(data, indent=2))
    else:
        click.echo(f"\n{passed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)
