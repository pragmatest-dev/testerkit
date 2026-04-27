"""Shared private helpers used across the pytest plugin.

Pure utility functions with no fixture or hook semantics. Imported by
:mod:`litmus.pytest_plugin.hooks`, :mod:`litmus.pytest_plugin.fixtures`,
and :mod:`litmus.pytest_plugin.autouse`.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pytest


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


def find_station_file(config) -> Path | None:
    """Find station config file from pytest config options.

    Resolution: ``--station-config`` path → ``--station`` ID under
    ``stations/``.
    """
    config_path = config.getoption("--station-config")
    if config_path:
        return Path(config_path)
    station_id = config.getoption("--station")
    return find_yaml_in_subdir(config, "stations", f"{station_id}.yaml")


def find_fixture_file(config) -> Path | None:
    """Find fixture config file from pytest config options.

    Resolution: ``--fixture-config`` path → ``--fixture`` ID under
    ``fixtures/`` → single-file fallback.
    """
    config_path = config.getoption("--fixture-config")
    if config_path:
        return Path(config_path)

    fixture_id = config.getoption("--fixture")
    if fixture_id:
        match = find_yaml_in_subdir(config, "fixtures", f"{fixture_id}.yaml")
        if match is None:
            warnings.warn(
                f"Fixture '{fixture_id}' not found in fixtures/ directory.",
                stacklevel=2,
            )
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
