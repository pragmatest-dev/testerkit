"""JSON Schema generation commands."""

from __future__ import annotations

from pathlib import Path

import click

from testerkit.cli.root import main


@main.group()
def schema():
    """JSON Schema generation for YAML validation."""
    pass


@schema.command("export")
@click.option("--output-dir", "-o", default="schemas", help="Directory for .schema.json files")
def schema_export(output_dir: str):
    """Export JSON Schema files for all TesterKit YAML types.

    Generates .schema.json files that enable editor validation and
    autocomplete for every TesterKit YAML type — sidecar, profile,
    project, station, fixture, part, catalog, instrument_asset.

    Example:
        testerkit schema export
        testerkit schema export -o testerkit/schemas
    """
    from testerkit.schema_export import export_schemas

    paths = export_schemas(Path(output_dir))
    for p in paths:
        click.echo(f"  {p}")
    click.echo(f"\nExported {len(paths)} schema(s) to {output_dir}/")


@schema.command("refresh")
@click.option(
    "--project-dir",
    default=".",
    help="Project root (defaults to current directory).",
)
def schema_refresh(project_dir: str):
    """Refresh .vscode/schemas/ and .vscode/settings.json after a TesterKit upgrade.

    Regenerates every .schema.json from the installed TesterKit's
    Pydantic models and rewrites the yaml.schemas mapping in
    .vscode/settings.json. Run this after upgrading the testerkit
    package so VS Code autocomplete reflects the latest schema.

    Preserves any other keys you have in settings.json — only the
    yaml.schemas mapping is replaced. If .vscode/settings.json doesn't
    exist yet, it's created.

    Example:
        pip install -U testerkit
        testerkit schema refresh
    """
    import json

    from testerkit.schema_export import export_schemas, vscode_yaml_schemas

    root = Path(project_dir).resolve()
    vscode_dir = root / ".vscode"
    schemas_dir = vscode_dir / "schemas"
    settings_path = vscode_dir / "settings.json"

    paths = export_schemas(schemas_dir)
    settings: dict[str, object] = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            click.echo(
                f"warning: {settings_path} is not valid JSON; rewriting from scratch",
                err=True,
            )
            settings = {}
    settings["yaml.schemas"] = vscode_yaml_schemas()
    vscode_dir.mkdir(exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    rel_settings = settings_path.relative_to(root)
    click.echo(f"Refreshed {len(paths)} schema(s) in {schemas_dir.relative_to(root)}/")
    click.echo(f"Updated {rel_settings}")
