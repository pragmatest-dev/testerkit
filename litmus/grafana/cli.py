"""Click commands for Grafana integration."""

from __future__ import annotations

from pathlib import Path

import click


@click.group()
def grafana():
    """Grafana dashboard provisioning and data server."""


@grafana.command()
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=5433, type=int, help="PostgreSQL wire protocol port")
@click.option("--results-dir", type=click.Path(path_type=Path), default=None)
@click.option(
    "--refresh-seconds",
    default=30,
    type=int,
    help="Seconds between IPC table refreshes (events, channels)",
)
def serve(host: str, port: int, results_dir: Path | None, refresh_seconds: int) -> None:
    """Start the pgwire server for Grafana.

    Exposes all Litmus data (runs, events, channels) over the PostgreSQL
    wire protocol.  Connect Grafana's built-in PostgreSQL datasource to
    this address.
    """
    try:
        from litmus.grafana.server import serve as _serve
    except ImportError:
        raise click.ClickException(
            "Missing dependency: buenavista\nInstall with: pip install litmus-test[grafana]"
        )

    from litmus.data.results_dir import resolve_results_dir

    resolved = resolve_results_dir(results_dir)
    click.echo(f"Results dir: {resolved}")
    _serve(resolved, host=host, port=port, refresh_seconds=refresh_seconds)


@grafana.command()
@click.option(
    "--grafana-home",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Grafana installation directory (default: auto-detect)",
)
@click.option(
    "--grafana-url", default=None, help="Grafana URL for API setup (e.g. http://localhost:3000)"
)
@click.option("--grafana-token", default=None, help="Grafana API token or service account token")
@click.option("--grafana-user", default=None, help="Grafana username for basic auth")
@click.option("--grafana-password", default=None, help="Grafana password for basic auth")
@click.option("--host", default="127.0.0.1", help="pgwire host for datasource config")
@click.option("--port", default=5433, type=int, help="pgwire port for datasource config")
@click.option("--folder", default="Litmus", help="Grafana folder for dashboards")
def setup(
    grafana_home: Path | None,
    grafana_url: str | None,
    grafana_token: str | None,
    grafana_user: str | None,
    grafana_password: str | None,
    host: str,
    port: int,
    folder: str,
) -> None:
    """Install provisioning config and dashboards into Grafana.

    Two modes:

    \b
    File-based (local Grafana):
        litmus grafana setup --grafana-home /usr/share/grafana

    \b
    API-based (Docker, remote, Grafana Cloud):
        litmus grafana setup --grafana-url http://localhost:3000 \\
            --grafana-user admin --grafana-password admin
        litmus grafana setup --grafana-url http://localhost:3000 --grafana-token glsa_xxx
    """
    if grafana_url:
        _setup_via_api(
            grafana_url, grafana_token, grafana_user, grafana_password, host, port, folder
        )
    else:
        from litmus.grafana.bootstrap import copy_dashboards, render_provisioning

        grafana_home = grafana_home or _detect_grafana_home()

        click.echo(f"Grafana home: {grafana_home}")

        render_provisioning(grafana_home, pgwire_host=host, pgwire_port=port)
        click.echo("Rendered provisioning config (PostgreSQL datasource)")

        dest = copy_dashboards(grafana_home)
        click.echo(f"Copied dashboards to: {dest}")

        click.echo(
            "\nDone! Start the data server with:  litmus grafana serve\n"
            "Then restart Grafana and open http://localhost:3000"
        )


@grafana.command("export")
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(path_type=Path),
    default="grafana-export",
    help="Output directory",
)
def export_cmd(output_dir: Path) -> None:
    """Export dashboards and provisioning templates for manual setup."""
    from litmus.grafana.bootstrap import export_bundle

    export_bundle(output_dir)
    click.echo(f"Exported to: {output_dir}/")
    click.echo("  dashboards/      — JSON dashboard files")
    click.echo("  provisioning/    — Jinja2 templates for datasource + dashboard config")


def _setup_via_api(
    grafana_url: str,
    token: str | None,
    user: str | None,
    password: str | None,
    pgwire_host: str,
    pgwire_port: int,
    folder: str,
) -> None:
    """Set up Grafana dashboards and datasource via the HTTP API."""
    import json
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    url = grafana_url.rstrip("/")

    def _request(method: str, path: str, body: dict | None = None) -> dict:
        """Make an authenticated request to the Grafana HTTP API."""
        data = json.dumps(body).encode() if body else None
        req = Request(f"{url}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        elif user and password:
            import base64

            creds = base64.b64encode(f"{user}:{password}".encode()).decode()
            req.add_header("Authorization", f"Basic {creds}")
        try:
            with urlopen(req) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            body_text = e.read().decode()
            raise click.ClickException(
                f"Grafana API error {e.code} on {method} {path}: {body_text}"
            ) from e
        except URLError as e:
            raise click.ClickException(f"Cannot connect to {url}: {e.reason}") from e

    # 1. Create or find datasource
    click.echo(f"Connecting to {url}...")
    datasources = _request("GET", "/api/datasources")
    ds_uid = None
    for ds in datasources:
        if ds.get("name") == "Litmus":
            ds_uid = ds["uid"]
            click.echo(f"Found existing datasource: {ds_uid}")
            break

    if not ds_uid:
        ds = _request(
            "POST",
            "/api/datasources",
            {
                "name": "Litmus",
                "type": "grafana-postgresql-datasource",
                "url": f"{pgwire_host}:{pgwire_port}",
                "access": "proxy",
                "database": "litmus",
                "user": "litmus",
                "secureJsonData": {"password": "litmus"},
                "jsonData": {"sslmode": "disable", "postgresVersion": 1500},
            },
        )
        ds_uid = ds["datasource"]["uid"]
        click.echo(f"Created datasource: {ds_uid}")

    # 2. Create or find folder
    folders = _request("GET", "/api/folders")
    folder_uid = None
    for f in folders:
        if f["title"] == folder:
            folder_uid = f["uid"]
            break

    if not folder_uid:
        result = _request("POST", "/api/folders", {"title": folder})
        folder_uid = result["uid"]
        click.echo(f"Created folder: {folder}")

    # 3. Import dashboards
    dashboards_dir = Path(__file__).parent / "dashboards"
    imported = 0
    for dashboard_file in sorted(dashboards_dir.glob("*.json")):
        with open(dashboard_file) as fh:
            try:
                dashboard = json.load(fh)
            except json.JSONDecodeError as exc:
                click.echo(f"  Skipping {dashboard_file.stem}: invalid JSON ({exc})")
                continue

        if "panels" not in dashboard:
            click.echo(f"  Skipping {dashboard_file.stem}: not a dashboard (no panels)")
            continue

        # Replace datasource variable with actual UID
        raw = json.dumps(dashboard).replace("${DS_LITMUS}", ds_uid)
        dashboard = json.loads(raw)
        dashboard["id"] = None

        _request(
            "POST",
            "/api/dashboards/db",
            {
                "dashboard": dashboard,
                "overwrite": True,
                "folderUid": folder_uid,
            },
        )
        imported += 1
        click.echo(f"  Imported {dashboard_file.stem}")

    click.echo(
        f"\nDone! {imported} dashboards imported to '{folder}' folder.\n"
        f"Start the data server with:  litmus grafana serve\n"
        f"Then open {url}"
    )


def _detect_grafana_home() -> Path:
    """Try to find Grafana installation directory."""
    import platform

    candidates: list[Path] = []
    if platform.system() == "Linux":
        candidates = [
            Path("/usr/share/grafana"),
            Path("/etc/grafana"),
            Path.home() / "grafana",
        ]
    elif platform.system() == "Darwin":
        candidates = [
            Path("/opt/homebrew/share/grafana"),
            Path("/usr/local/share/grafana"),
        ]
    elif platform.system() == "Windows":
        candidates = [
            Path("C:/Program Files/GrafanaLabs/grafana"),
        ]

    for p in candidates:
        if p.exists():
            return p

    raise click.ClickException(
        "Could not auto-detect Grafana home. Use --grafana-home to specify it."
    )
