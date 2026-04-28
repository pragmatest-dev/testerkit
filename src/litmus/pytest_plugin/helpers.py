"""Shared private helpers used across the pytest plugin.

Pure utility functions with no fixture or hook semantics. Imported by
:mod:`litmus.pytest_plugin.hooks`, :mod:`litmus.pytest_plugin.fixtures`,
and :mod:`litmus.pytest_plugin.autouse`.
"""

from __future__ import annotations

import os
import socket
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest
import yaml

from litmus.execution.profiles import load_project_defaults


def config_search_roots(config) -> list[Path]:
    """Project search roots derived from the active pytest config."""
    return [config.rootpath, Path(config.invocation_params.dir)]


def find_yaml_in_subdir(config, subdir: str, filename: str) -> Path | None:
    """Return ``<root>/<subdir>/<filename>`` for the first root that has it, or ``None``."""
    for root in config_search_roots(config):
        target = root / subdir / filename
        if target.exists():
            return target
    return None


def _hostname_match_station(config) -> Path | None:
    """Find a station YAML whose ``hostname:`` field matches this machine.

    Walks ``stations/*.yaml`` under the project's search roots, parses
    each minimally (just ``id`` + ``hostname``), and returns the file
    with a hostname matching :func:`socket.gethostname`. Single match
    wins. Multiple matches → emit a warning listing the candidates and
    return None (operator must explicitly disambiguate). Zero matches
    → None (caller falls through to ``ProjectConfig.default_station``).
    """
    host = socket.gethostname()
    matches: list[tuple[str, Path]] = []
    for root in config_search_roots(config):
        stations_dir = root / "stations"
        if not stations_dir.is_dir():
            continue
        for yaml_path in stations_dir.glob("*.yaml"):
            try:
                with yaml_path.open() as fh:
                    data = yaml.safe_load(fh) or {}
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("hostname") == host:
                station_id = data.get("id") or yaml_path.stem
                matches.append((station_id, yaml_path))
    if len(matches) == 1:
        return matches[0][1]
    if len(matches) > 1:
        candidates = ", ".join(sorted(m[0] for m in matches))
        warnings.warn(
            f"Multiple stations match hostname {host!r}: {candidates}. "
            "Pass --station=<id> to disambiguate.",
            stacklevel=2,
        )
    return None


def find_station_file(config) -> Path | None:
    """Find station config file from pytest config options.

    Resolution chain (first match wins):

    1. ``--station-config <path>`` — explicit file path.
    2. ``--station <id>`` — look up ``stations/<id>.yaml``.
    3. Hostname auto-match — `socket.gethostname()` against every
       station's ``hostname:`` field.
    4. ``ProjectConfig.default_station`` — fallback id.
    5. ``None`` — bringup tier without a station.
    """
    config_path = config.getoption("--station-config")
    if config_path:
        return Path(config_path)
    station_id = config.getoption("--station")
    if station_id:
        return find_yaml_in_subdir(config, "stations", f"{station_id}.yaml")
    hostname_hit = _hostname_match_station(config)
    if hostname_hit is not None:
        return hostname_hit
    project = load_project_defaults()
    if project.default_station:
        return find_yaml_in_subdir(config, "stations", f"{project.default_station}.yaml")
    return None


def resolve_station_id(config) -> str:
    """Return the resolved station ID matching :func:`find_station_file`.

    Mirrors the file-resolution chain so callers that need the id (for
    logging / TestRun stamping) get the same answer as callers that
    need the YAML path. When CLI ``--station`` is explicit it wins;
    otherwise the resolver tries hostname auto-match, then falls back
    to ``ProjectConfig.default_station``.

    Always returns a non-empty string — when no station YAML is found
    on disk, the project's `default_station` is still returned (it's
    the canonical id even if no file exists).
    """
    station_id = config.getoption("--station")
    if station_id:
        return str(station_id)
    hostname_hit = _hostname_match_station(config)
    if hostname_hit is not None:
        try:
            with hostname_hit.open() as fh:
                data = yaml.safe_load(fh) or {}
            if isinstance(data, dict) and isinstance(data.get("id"), str):
                return data["id"]
        except (OSError, yaml.YAMLError):
            pass
        return hostname_hit.stem
    project = load_project_defaults()
    return project.default_station


def find_fixture_file(config) -> Path | None:
    """Find fixture config file from pytest config options.

    Resolution chain (first match wins):

    1. ``--fixture-config <path>`` — explicit file path.
    2. ``--fixture <id>`` — explicit CLI id.
    3. Active profile's ``fixture:`` field (read from
       ``get_active_profile()``; profile is installed in
       ``pytest_configure`` before fixture resolution fires).
    4. ``ProjectConfig.default_fixture``.
    5. Single-file fallback (one ``*.yaml`` in ``fixtures/``).
    6. ``None``.

    When ``--fixture <X>`` is explicitly passed AND the active
    profile declares ``fixture: <Y>`` with ``Y != X``, the CLI wins
    but a warning is emitted — explicit beats declarative.
    """
    from litmus.execution._state import get_active_profile

    config_path = config.getoption("--fixture-config")
    if config_path:
        return Path(config_path)

    cli_fixture = config.getoption("--fixture")
    profile = get_active_profile()
    profile_fixture = profile.fixture if profile is not None else None

    if cli_fixture and profile_fixture and cli_fixture != profile_fixture:
        warnings.warn(
            f"--fixture={cli_fixture!r} overrides active profile's "
            f"fixture={profile_fixture!r}. CLI wins (explicit beats "
            "declarative).",
            stacklevel=2,
        )

    fixture_id = cli_fixture or profile_fixture
    if fixture_id:
        match = find_yaml_in_subdir(config, "fixtures", f"{fixture_id}.yaml")
        if match is None:
            warnings.warn(
                f"Fixture '{fixture_id}' not found in fixtures/ directory.",
                stacklevel=2,
            )
        return match

    # ProjectConfig.default_fixture
    project = load_project_defaults()
    if project.default_fixture:
        match = find_yaml_in_subdir(config, "fixtures", f"{project.default_fixture}.yaml")
        if match is not None:
            return match

    # Single-file fallback
    for root in config_search_roots(config):
        fixtures_dir = root / "fixtures"
        if fixtures_dir.exists():
            yaml_files = list(fixtures_dir.glob("*.yaml"))
            if len(yaml_files) == 1:
                return yaml_files[0]
    return None


def safe_get_session_fixture(request: pytest.FixtureRequest, name: str) -> Any:
    """Safely fetch a session-scoped fixture value, returning ``None`` if unavailable.

    Only attempts to access fixtures that exist at session scope to avoid
    ScopeMismatch errors from test-defined fixtures with the same name.
    Setup failures (e.g. ValidationError on YAML load) are surfaced as a
    warning and resolved to ``None`` — the autouse fixtures that consume
    this helper are best-effort lookups, not gating preconditions.
    """
    try:
        return request.getfixturevalue(name)
    except pytest.FixtureLookupError:
        return None
    except (ValueError, TypeError, OSError) as exc:
        warnings.warn(
            f"Fixture {name!r} setup failed: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        return None


def join_marker_names(markers: Any, sort: bool = False) -> str | None:
    """Return a comma-joined marker-name string, or ``None`` when empty.

    Accepts anything iterable that yields objects with a ``.name``
    attribute — ``item.iter_markers()`` or ``item.own_markers``.
    ``sort=True`` produces deterministic output for the collection
    manifest; leaving it unsorted preserves source order for code
    identity (which is what the audit cares about).
    """
    if not markers:
        return None
    names = [m.name for m in markers]
    if sort:
        names.sort()
    return ",".join(names) or None


def node_cls_func(node: pytest.Item) -> tuple[str | None, str | None]:
    """Extract ``(class_name, original_func_name)`` for a pytest node.

    Strips parametrize ``[...]`` suffixes so that parametrized tests
    map to the same base function name as their non-parametrized
    siblings — needed for sidecar / profile cascade lookups and for
    stable code-identity stamping.
    """
    cls = getattr(node, "cls", None)
    cls_name = cls.__name__ if cls is not None else None
    func_name = getattr(node, "originalname", None) or node.name.split("[")[0]
    return cls_name, func_name


def mocks_active(config: pytest.Config) -> bool:
    """Return whether mock instruments are requested.

    Single source of truth for every consumer (``pytest_sessionstart``,
    ``_build_run_metadata``, the ``mock_instruments`` session fixture,
    ``slot_runner``). Resolution order, highest priority first:

    1. CLI flag — ``--mock-instruments`` (True) or ``--no-mock-instruments``
       (False). Either explicit flag wins.
    2. Env var ``LITMUS_MOCK_INSTRUMENTS=1`` — set by the API runner so
       a server-launched subprocess inherits the operator's choice.
    3. ``litmus.yaml: mock_instruments:`` — project default.
    4. ``False`` if nothing else set.
    """
    cli = config.getoption("mock_instruments", default=None)
    if cli is not None:
        return bool(cli)
    env = os.environ.get("LITMUS_MOCK_INSTRUMENTS")
    if env is not None:
        return env == "1"
    return load_project_defaults().mock_instruments


def prompt_for_serial(test_phase: str, slot_id: str | None = None) -> str:
    """Prompt for DUT serial or raise if non-interactive.

    Args:
        test_phase: Current test phase (for error message).
        slot_id: If provided, prompt for a specific slot.

    Returns:
        Non-empty serial string.
    """
    label = f" for slot '{slot_id}'" if slot_id else ""

    if sys.stdin.isatty():
        serial = input(
            f"[litmus] test_phase='{test_phase}' requires a DUT serial{label}.\n"
            f"  Enter DUT serial (or Ctrl+C to abort): "
        )
        serial = serial.strip()
        if not serial:
            raise pytest.UsageError(
                f"DUT serial number is required{label} for "
                f"non-development test phases. "
                "Use --dut-serial <serial> or enter a serial when prompted."
            )
        return serial

    raise pytest.UsageError(
        f"DUT serial number is required for test_phase='{test_phase}'{label}. "
        "Use --dut-serial <serial> or --dut-serials slot=serial."
    )
