"""``testerkit connect`` — device-authorization client unit tests (no live server).

Mirrors ``test_forward.py``'s style: HTTP is monkeypatched (never a real
socket), and isolation is via ``TESTERKIT_HOME=tmp_path`` (a plain file, no
daemon spawned — see ``test_credentials.py``). Covers the poll-loop state
machine (pending / slow_down / approved / expired / denied / overall
timeout) and the end-to-end ``connect`` command wiring: what it stores, and
that a subsequent ``forward`` invocation reads it back with no flags/env.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from testerkit.cli import connect_cmd, forward_cmd, main
from testerkit.data.data_dir import load_credentials, resolve_server_token, resolve_server_url


def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TESTERKIT_HOME", str(tmp_path))
    monkeypatch.delenv("TESTERKIT_URL", raising=False)
    monkeypatch.delenv("TESTERKIT_TOKEN", raising=False)


# --------------------------------------------------------------------------- #
# Poll loop state machine                                                     #
# --------------------------------------------------------------------------- #


def _fake_clock():
    """A fake ``(sleep, now)`` pair: ``sleep`` just advances the fake clock."""
    state = {"t": 0.0}

    def now() -> float:
        return state["t"]

    def sleep(seconds: float) -> None:
        state["t"] += seconds

    return sleep, now


def test_wait_for_device_approval_pending_then_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        {"status": 400, "error": "authorization_pending"},
        {"status": 400, "error": "authorization_pending"},
        {
            "status": 200,
            "access_token": "tk_1",
            "token_type": "bearer",
            "org_id": "o1",
            "org_name": "Acme",
        },
    ]

    def fake_poll(url, device_code, *, timeout):
        return responses.pop(0)

    monkeypatch.setattr(connect_cmd, "_poll_token", fake_poll)
    sleep, now = _fake_clock()

    result = connect_cmd._wait_for_device_approval(
        "http://x", "dc1", interval=2.0, expires_in=600.0, timeout=5.0, sleep=sleep, now=now
    )

    assert result["access_token"] == "tk_1"
    assert result["org_name"] == "Acme"
    assert not responses  # all three consumed


def test_wait_for_device_approval_slow_down_increases_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        {"status": 400, "error": "slow_down"},
        {"status": 200, "access_token": "tk_1"},
    ]
    seen_sleeps: list[float] = []

    def fake_poll(url, device_code, *, timeout):
        return responses.pop(0)

    monkeypatch.setattr(connect_cmd, "_poll_token", fake_poll)
    _, now = _fake_clock()

    def recording_sleep(seconds: float) -> None:
        seen_sleeps.append(seconds)

    result = connect_cmd._wait_for_device_approval(
        "http://x",
        "dc1",
        interval=2.0,
        expires_in=600.0,
        timeout=5.0,
        sleep=recording_sleep,
        now=now,
    )

    assert result["access_token"] == "tk_1"
    # First poll at the original interval, second after +5s slow_down bump.
    assert seen_sleeps == [2.0, 7.0]


def test_wait_for_device_approval_expired_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        connect_cmd, "_poll_token", lambda *a, **k: {"status": 400, "error": "expired_token"}
    )
    sleep, now = _fake_clock()

    with pytest.raises(click.ClickException, match="expired"):
        connect_cmd._wait_for_device_approval(
            "http://x", "dc1", interval=1.0, expires_in=600.0, timeout=5.0, sleep=sleep, now=now
        )


def test_wait_for_device_approval_access_denied_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        connect_cmd, "_poll_token", lambda *a, **k: {"status": 400, "error": "access_denied"}
    )
    sleep, now = _fake_clock()

    with pytest.raises(click.ClickException, match="denied"):
        connect_cmd._wait_for_device_approval(
            "http://x", "dc1", interval=1.0, expires_in=600.0, timeout=5.0, sleep=sleep, now=now
        )


def test_wait_for_device_approval_overall_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if the server never sends ``expired_token``, ``expires_in`` bounds the wait."""
    monkeypatch.setattr(
        connect_cmd,
        "_poll_token",
        lambda *a, **k: {"status": 400, "error": "authorization_pending"},
    )
    sleep, now = _fake_clock()

    with pytest.raises(click.ClickException, match="expired"):
        connect_cmd._wait_for_device_approval(
            "http://x", "dc1", interval=10.0, expires_in=25.0, timeout=5.0, sleep=sleep, now=now
        )


# --------------------------------------------------------------------------- #
# End-to-end ``connect`` command wiring (HTTP monkeypatched)                   #
# --------------------------------------------------------------------------- #


def test_connect_stores_credentials_and_url_that_forward_then_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The exact interop contract: what ``connect`` writes, ``forward``'s own
    resolvers (``resolve_server_url`` / ``resolve_server_token``) read back."""
    _isolate_home(monkeypatch, tmp_path)

    def fake_authorize(url, *, timeout):
        assert url == "https://cloud.example"
        return {
            "device_code": "dc-123",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://cloud.example/activate",
            "verification_uri_complete": "https://cloud.example/activate?user_code=ABCD-EFGH",
            "expires_in": 600,
            "interval": 1,
        }

    def fake_wait(url, device_code, *, interval, expires_in, timeout):
        assert device_code == "dc-123"
        return {
            "status": 200,
            "access_token": "tk_final",
            "token_type": "bearer",
            "org_id": "org_42",
            "org_name": "Acme Corp",
        }

    monkeypatch.setattr(connect_cmd, "_authorize", fake_authorize)
    monkeypatch.setattr(connect_cmd, "_wait_for_device_approval", fake_wait)
    monkeypatch.setattr(connect_cmd, "open_browser", lambda _u: True)

    runner = CliRunner()
    result = runner.invoke(main, ["connect", "--url", "https://cloud.example"])

    assert result.exit_code == 0, result.output
    assert "ABCD-EFGH" in result.output
    assert "Acme Corp" in result.output

    creds = load_credentials()
    assert creds == {"token": "tk_final", "org_id": "org_42", "org_name": "Acme Corp"}
    assert resolve_server_url(None) == "https://cloud.example"
    assert resolve_server_token(None) == "tk_final"

    # And forward's own CLI-level resolution (no flags/env) finds both.
    monkeypatch.setattr(forward_cmd, "_forward_all_once", lambda *a, **k: {})
    fwd = runner.invoke(main, ["forward", "--once", "--data-dir", str(tmp_path / "data")])
    assert fwd.exit_code == 0, fwd.output


def test_connect_requires_a_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)  # no ancestor testerkit.yaml

    runner = CliRunner()
    result = runner.invoke(main, ["connect"])

    assert result.exit_code != 0
    assert "server URL is required" in result.output


def test_connect_never_prints_the_full_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)

    monkeypatch.setattr(
        connect_cmd,
        "_authorize",
        lambda url, *, timeout: {
            "device_code": "dc-1",
            "user_code": "WXYZ-1234",
            "verification_uri": "https://cloud.example/activate",
            "verification_uri_complete": "https://cloud.example/activate?user_code=WXYZ-1234",
            "expires_in": 600,
            "interval": 1,
        },
    )
    monkeypatch.setattr(
        connect_cmd,
        "_wait_for_device_approval",
        lambda *a, **k: {
            "status": 200,
            "access_token": "super-secret-token-value",
            "org_id": "org_1",
            "org_name": "Acme",
        },
    )
    monkeypatch.setattr(connect_cmd, "open_browser", lambda _u: True)

    runner = CliRunner()
    result = runner.invoke(main, ["connect", "--url", "https://cloud.example"])

    assert result.exit_code == 0, result.output
    assert "super-secret-token-value" not in result.output
