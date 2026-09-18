"""``testerkit connect`` — enroll this machine with a TesterKit cloud server.

An RFC 8628 OAuth 2.0 Device Authorization Grant client. On success, the
issued machine token is stored in the global credential store
(``<global-home>/credentials``) and the server URL is persisted to the
global config, so a bare ``testerkit forward`` works afterward with no
flags or environment variables — see ``testerkit.data.data_dir``'s
``resolve_server_token`` / ``resolve_server_url`` for the fallback chain
both this command and ``forward`` share.

Named ``connect`` per the maintainer's call, accepting the soft overlap
with ``testerkit.connect()`` (the Python instrument-connection API): that
is a library import, this is a CLI subcommand — different namespaces,
never confused at a call site.

THE CONTRACT (must match the server exactly):

* ``POST {url}/api/connect/authorize`` — body ``{hostname, machine_id,
  platform, tk_version}``. 200 response: ``{device_code, user_code,
  verification_uri, verification_uri_complete, expires_in, interval}``.
* ``POST {url}/api/connect/token`` — body ``{device_code, grant_type:
  "urn:ietf:params:oauth:grant-type:device_code"}``. 400 responses:
  ``{error: "authorization_pending"}`` (keep polling), ``{error:
  "slow_down"}`` (add 5s to the poll interval), ``{error:
  "expired_token"}`` / ``{error: "access_denied"}`` (fail). 200 response:
  ``{access_token, token_type, org_id, org_name}``.
"""

from __future__ import annotations

import json
import platform
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from webbrowser import open as open_browser

import click

from testerkit import __version__
from testerkit.cli.root import main
from testerkit.data.data_dir import (
    get_or_create_machine_id,
    resolve_server_url,
    save_credentials,
    save_server_url,
)

_URL_ENV = "TESTERKIT_URL"
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


def _post_json(url: str, path: str, payload: dict, *, timeout: float) -> tuple[int, dict]:
    """POST JSON to ``{url}{path}``, returning ``(status_code, body)`` for
    both success and error responses.

    The device-authorization contract encodes retryable/terminal poll
    states as HTTP 400 + an ``error`` field (not just 2xx/5xx), so callers
    need the parsed body even when ``urlopen`` raises ``HTTPError``.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — our own server URL
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            parsed = {}
        return exc.code, parsed


def _authorize(url: str, *, timeout: float) -> dict:
    """``POST /api/connect/authorize`` — starts the device flow."""
    payload = {
        "hostname": socket.gethostname(),
        "machine_id": get_or_create_machine_id(),
        "platform": platform.platform(),
        "tk_version": __version__,
    }
    status, body = _post_json(url, "/api/connect/authorize", payload, timeout=timeout)
    if status != 200:
        raise click.ClickException(f"connect authorize failed ({status}): {body}")
    return body


def _poll_token(url: str, device_code: str, *, timeout: float) -> dict:
    """``POST /api/connect/token`` — one poll attempt.

    Returns the parsed body with ``status`` folded in, so callers can
    branch on both the HTTP status and the ``error``/success fields.
    """
    payload = {"device_code": device_code, "grant_type": _GRANT_TYPE}
    status, body = _post_json(url, "/api/connect/token", payload, timeout=timeout)
    return {"status": status, **body}


def _wait_for_device_approval(
    url: str,
    device_code: str,
    *,
    interval: float,
    expires_in: float,
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> dict:
    """Poll ``/api/connect/token`` until approved, denied, expired, or timed out.

    ``authorization_pending`` keeps waiting at the current interval;
    ``slow_down`` adds 5s to it (per RFC 8628); ``access_denied`` /
    ``expired_token`` fail immediately. ``expires_in`` bounds the whole
    wait even if the server never sends ``expired_token``. ``sleep`` /
    ``now`` are injectable so tests never sleep for real.
    """
    deadline = now() + expires_in
    while True:
        if now() >= deadline:
            raise click.ClickException(
                "device code expired before approval — run `testerkit connect` again"
            )
        sleep(interval)
        result = _poll_token(url, device_code, timeout=timeout)
        if result.get("status") == 200:
            return result
        error = result.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5.0
            continue
        if error == "expired_token":
            raise click.ClickException("device code expired — run `testerkit connect` again")
        if error == "access_denied":
            raise click.ClickException("authorization was denied")
        raise click.ClickException(f"unexpected response from server: {result}")


@main.command()
@click.option(
    "--url",
    default=None,
    help=f"Server base URL (or ${_URL_ENV}, or testerkit.yaml `server.url`)",
)
@click.option("--timeout", default=30.0, help="Per-request HTTP timeout (seconds)")
def connect(url: str | None, timeout: float):
    """Enroll this machine with a TesterKit cloud server.

    Runs the RFC 8628 device-authorization flow: prints a URL + code for
    you to approve in a browser, polls until approved, then stores the
    issued machine token (``<global-home>/credentials``) and the server
    URL (the global config) so a bare ``testerkit forward`` works
    afterward with no flags or env vars.
    """
    server = resolve_server_url(url)
    if not server:
        raise click.ClickException(
            f"a server URL is required (--url, ${_URL_ENV}, testerkit.yaml `server.url`, "
            "or a previous `testerkit connect`)"
        )

    try:
        auth = _authorize(server, timeout=timeout)
        click.echo("To connect this machine, open this link (code included):")
        click.echo(f"    {auth['verification_uri_complete']}")
        click.echo(f"  or visit {auth['verification_uri']} and enter code: {auth['user_code']}")
        with suppress(Exception):
            open_browser(auth["verification_uri_complete"])

        token_resp = _wait_for_device_approval(
            server,
            auth["device_code"],
            interval=float(auth["interval"]),
            expires_in=float(auth["expires_in"]),
            timeout=timeout,
        )
    except click.ClickException:
        raise
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise click.ClickException(f"connect failed: {exc}") from exc

    save_credentials(
        token=token_resp["access_token"],
        org_id=token_resp.get("org_id"),
        org_name=token_resp.get("org_name"),
    )
    save_server_url(server)
    click.echo(f"Connected — this machine can now forward to {token_resp.get('org_name')}.")
