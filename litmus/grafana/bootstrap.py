"""Grafana provisioning file management."""

from __future__ import annotations

import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TEMPLATES_DIR = Path(__file__).parent / "provisioning"
_DASHBOARDS_DIR = Path(__file__).parent / "dashboards"


def render_provisioning(
    grafana_home: Path,
    pgwire_host: str = "127.0.0.1",
    pgwire_port: int = 5433,
) -> None:
    """Render provisioning YAML into Grafana's conf directory."""
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), keep_trailing_newline=True)
    provisioning_dir = grafana_home / "conf" / "provisioning"

    context = {
        "pgwire_host": pgwire_host,
        "pgwire_port": pgwire_port,
        "dashboards_dir": str(
            grafana_home / "conf" / "provisioning" / "dashboards" / "litmus"
        ).replace("\\", "/"),
    }

    # Datasource provisioning
    ds_dir = provisioning_dir / "datasources"
    ds_dir.mkdir(parents=True, exist_ok=True)
    tmpl = env.get_template("datasources.yaml.j2")
    (ds_dir / "litmus.yaml").write_text(tmpl.render(context))

    # Dashboard provisioning
    db_dir = provisioning_dir / "dashboards"
    db_dir.mkdir(parents=True, exist_ok=True)
    tmpl = env.get_template("dashboards.yaml.j2")
    (db_dir / "litmus.yaml").write_text(tmpl.render(context))


def copy_dashboards(grafana_home: Path) -> Path:
    """Copy dashboard JSON files to Grafana's provisioned dashboards path."""
    dest = grafana_home / "conf" / "provisioning" / "dashboards" / "litmus"
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(_DASHBOARDS_DIR, dest)
    return dest


def export_bundle(output_dir: Path) -> None:
    """Export dashboards + provisioning templates for manual setup."""
    output_dir.mkdir(parents=True, exist_ok=True)

    dash_dest = output_dir / "dashboards"
    shutil.rmtree(dash_dest, ignore_errors=True)
    shutil.copytree(_DASHBOARDS_DIR, dash_dest)

    prov_dest = output_dir / "provisioning"
    shutil.rmtree(prov_dest, ignore_errors=True)
    shutil.copytree(_TEMPLATES_DIR, prov_dest)
