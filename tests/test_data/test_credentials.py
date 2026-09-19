"""Tests for the credential store + URL/token resolution chains.

Mirrors ``test_machine_id.py``'s isolation pattern — a bare per-machine file
(no daemon), isolated via ``TESTERKIT_HOME=tmp_path`` per this repo's
convention (``tests/test_conventions.py`` only forbids ``tmp_path`` for
constructors that spawn a daemon; this is a plain JSON/YAML file).
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from testerkit.data.data_dir import (
    load_credentials,
    resolve_server_token,
    resolve_server_url,
    save_credentials,
    save_server_url,
)


def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TESTERKIT_HOME", str(tmp_path))
    monkeypatch.delenv("TESTERKIT_SERVER_URL", raising=False)
    monkeypatch.delenv("TESTERKIT_TOKEN", raising=False)


# --------------------------------------------------------------------------- #
# Credential store round-trip                                                 #
# --------------------------------------------------------------------------- #


def test_save_and_load_credentials_round_trips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)

    save_credentials(token="tk_secret123", org_id="org_1", org_name="Acme")
    loaded = load_credentials()

    assert loaded == {"token": "tk_secret123", "org_id": "org_1", "org_name": "Acme"}


def test_save_credentials_writes_mode_0600(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolate_home(monkeypatch, tmp_path)

    save_credentials(token="tk_secret123")

    path = tmp_path / "credentials"
    assert path.exists()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_credentials_is_a_separate_file_from_machine_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)

    from testerkit.data.data_dir import get_or_create_machine_id

    machine_id = get_or_create_machine_id()
    save_credentials(token="tk_secret123")

    assert (tmp_path / "machine_id").exists()
    assert (tmp_path / "credentials").exists()
    assert (tmp_path / "machine_id").read_text().strip() == machine_id
    assert json.loads((tmp_path / "credentials").read_text())["token"] == "tk_secret123"


def test_load_credentials_missing_file_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    assert load_credentials() is None


# --------------------------------------------------------------------------- #
# Token resolution precedence                                                 #
# --------------------------------------------------------------------------- #


def test_resolve_server_token_explicit_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setenv("TESTERKIT_TOKEN", "tk_env")
    save_credentials(token="tk_store")

    assert resolve_server_token("tk_explicit") == "tk_explicit"


def test_resolve_server_token_env_beats_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setenv("TESTERKIT_TOKEN", "tk_env")
    save_credentials(token="tk_store")

    assert resolve_server_token(None) == "tk_env"


def test_resolve_server_token_falls_back_to_credential_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    save_credentials(token="tk_store")

    assert resolve_server_token(None) == "tk_store"


def test_resolve_server_token_none_when_nothing_resolves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    assert resolve_server_token(None) is None


# --------------------------------------------------------------------------- #
# URL resolution precedence                                                   #
# --------------------------------------------------------------------------- #


def test_resolve_server_url_explicit_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setenv("TESTERKIT_SERVER_URL", "https://env.example")
    save_server_url("https://store.example")

    assert resolve_server_url("https://explicit.example") == "https://explicit.example"


def test_resolve_server_url_env_beats_global_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.setenv("TESTERKIT_SERVER_URL", "https://env.example")
    save_server_url("https://store.example")

    assert resolve_server_url(None) == "https://env.example"


def test_resolve_server_url_falls_back_to_global_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    save_server_url("https://store.example")

    assert resolve_server_url(None) == "https://store.example"


def test_resolve_server_url_project_config_beats_global_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A project ``testerkit.yaml`` ``server.url`` wins over the global config
    (env still wins over both, checked separately)."""
    _isolate_home(monkeypatch, tmp_path)
    save_server_url("https://global.example")

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "testerkit.yaml").write_text(
        "name: demo\nserver:\n  url: https://project.example\n"
    )
    monkeypatch.chdir(project_root)

    assert resolve_server_url(None) == "https://project.example"


def test_resolve_server_url_none_when_nothing_resolves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)  # no ancestor testerkit.yaml
    assert resolve_server_url(None) is None


def test_save_server_url_persists_to_global_config_yaml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)

    save_server_url("https://store.example")

    config_path = tmp_path / "config.yaml"
    assert config_path.exists()
    assert "https://store.example" in config_path.read_text()
