"""Shared private helpers used across the pytest plugin.

Pure utility functions with no fixture or hook semantics. Imported by
:mod:`litmus.pytest_plugin.hooks` (pytest_* lifecycle),
:mod:`litmus.pytest_plugin.autouse` (autouse fixtures), and the
session fixtures defined in :mod:`litmus.pytest_plugin.__init__`.
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

from litmus.execution._state import get_active_profile
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


def is_yaml_path(value: str) -> bool:
    """Return True if ``value`` looks like a path to a YAML file.

    Used by the unified ``--station`` / ``--fixture`` / ``--part``
    flags to dispatch between "look up ``<subdir>/<id>.yaml``" and "use
    this string as a path verbatim." A real ID never contains ``/`` or
    a ``.yaml`` / ``.yml`` extension, so the heuristic is safe.
    """
    return "/" in value or value.endswith((".yaml", ".yml"))


def resolve_id_or_path(config, value: str, subdir: str) -> Path | None:
    """Dispatch a CLI value to either a path or an ID lookup.

    Path-shaped values (containing ``/`` or ending in ``.yaml`` /
    ``.yml``) are returned as-is. ID-shaped values are looked up under
    ``<root>/<subdir>/<value>.yaml``. Returns ``None`` when an ID lookup
    finds nothing.
    """
    if is_yaml_path(value):
        return Path(value)
    return find_yaml_in_subdir(config, subdir, f"{value}.yaml")


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

    1. ``--station <id-or-path>`` — ID looks up ``stations/<id>.yaml``;
       a path (containing ``/`` or ending in ``.yaml``/``.yml``) is used
       as-is.
    2. Hostname auto-match — `socket.gethostname()` against every
       station's ``hostname:`` field.
    3. ``ProjectConfig.default_station`` — fallback id.
    4. ``None`` — bringup tier without a station.
    """
    station_value = config.getoption("--station")
    if station_value:
        return resolve_id_or_path(config, station_value, "stations")
    hostname_hit = _hostname_match_station(config)
    if hostname_hit is not None:
        return hostname_hit
    project = load_project_defaults()
    if project.default_station:
        return find_yaml_in_subdir(config, "stations", f"{project.default_station}.yaml")
    return None


def resolve_station_id(config) -> str | None:
    """Return the resolved station ID matching :func:`find_station_file`.

    Mirrors the file-resolution chain so callers that need the id (for
    logging / TestRun stamping) get the same answer as callers that
    need the YAML path. When CLI ``--station`` is explicit it wins;
    otherwise the resolver tries hostname auto-match, then falls back
    to ``ProjectConfig.default_station``.

    When ``--station`` is path-shaped, the ID is read from the YAML's
    ``id:`` field (with file stem as fallback).

    Returns ``None`` when nothing resolves — bringup tier without any
    station declared. Callers that need a non-null string (e.g., the
    run-record stamp) should provide their own fallback.
    """
    station_value = config.getoption("--station")
    if station_value:
        if is_yaml_path(station_value):
            return _read_id_from_yaml(Path(station_value))
        return str(station_value)
    hostname_hit = _hostname_match_station(config)
    if hostname_hit is not None:
        return _read_id_from_yaml(hostname_hit)
    project = load_project_defaults()
    return project.default_station


def _read_id_from_yaml(path: Path) -> str:
    """Read ``id:`` from a YAML file; fall back to the file stem.

    The fallback covers both legitimate cases (YAML omits ``id:`` and
    relies on the filename) and error cases (parse failure, OSError).
    Errors emit a warning so silent stem-fallback doesn't mask config
    bugs.
    """
    try:
        with path.open() as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        warnings.warn(
            f"Could not read {path}: {exc}. Falling back to file stem {path.stem!r} as id.",
            stacklevel=2,
        )
        return path.stem
    if isinstance(data, dict) and isinstance(data.get("id"), str):
        return data["id"]
    return path.stem


def find_fixture_file(config) -> Path | None:
    """Find fixture config file from pytest config options.

    Resolution chain (first match wins):

    1. ``--fixture <id-or-path>`` — ID looks up ``fixtures/<id>.yaml``;
       a path (containing ``/`` or ending in ``.yaml``/``.yml``) is used
       as-is.
    2. Active profile's ``fixture:`` field (read from
       ``get_active_profile()``; profile is installed in
       ``pytest_configure`` before fixture resolution fires).
    3. ``ProjectConfig.default_fixture``.
    4. Single-file fallback (one ``*.yaml`` in ``fixtures/``).
    5. ``None``.

    When ``--fixture <X>`` is explicitly passed AND the active
    profile declares ``fixture: <Y>`` with ``Y != X``, the CLI wins
    but a warning is emitted — explicit beats declarative.
    """
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

    if cli_fixture:
        match = resolve_id_or_path(config, cli_fixture, "fixtures")
        if match is None:
            warnings.warn(
                f"Fixture '{cli_fixture}' not found in fixtures/ directory.",
                stacklevel=2,
            )
        return match

    if profile_fixture:
        match = find_yaml_in_subdir(config, "fixtures", f"{profile_fixture}.yaml")
        if match is None:
            warnings.warn(
                f"Fixture '{profile_fixture}' not found in fixtures/ directory.",
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
    """Prompt for UUT serial or raise if non-interactive.

    Args:
        test_phase: Current test phase (for error message).
        slot_id: If provided, prompt for a specific slot.

    Returns:
        Non-empty serial string.
    """
    label = f" for slot '{slot_id}'" if slot_id else ""

    if sys.stdin.isatty():
        serial = input(
            f"[litmus] test_phase='{test_phase}' requires a UUT serial{label}.\n"
            f"  Enter UUT serial (or Ctrl+C to abort): "
        )
        serial = serial.strip()
        if not serial:
            raise pytest.UsageError(
                f"UUT serial number is required{label} for "
                f"non-development test phases. "
                "Use --uut-serial <serial> or enter a serial when prompted."
            )
        return serial

    raise pytest.UsageError(
        f"UUT serial number is required for test_phase='{test_phase}'{label}. "
        "Use --uut-serial <serial> or --uut-serials slot=serial."
    )
