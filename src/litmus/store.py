"""Centralized persistence for all Litmus YAML config files.

Every consumer should call these functions instead of raw yaml.safe_load.
ONE module for all load/save/list/get/create operations.

Public interface — four verbs per entity:
    get_*(id) → Model | None       # Lookup by ID across search paths
    list_*() → list[Model]         # Discover all across search paths
    save_*(model) → None           # Write model to YAML (raises on error)
    create_*(...) → Model | None   # Create new, None if exists

Plus low-level load_*(path) for callers that already have a file path.

All public functions accept an optional `project_root: Path | None` parameter.
When None (the default), falls back to Path.cwd() for backwards compatibility.

Carve-outs from the four-verb pattern:

* ``PartManifest`` only exposes ``load_manifest`` / ``save_manifest`` —
  manifests are co-located with their part folder rather than indexed,
  so callers manage the path explicitly.
* ``StationType`` only exposes ``load_station_type`` / ``save_station_type``
  — types live in ``stations/types/`` and are typically authored once per
  fleet, not enumerated programmatically.
* ``load_project_config`` returns a default ``ProjectConfig(name="litmus")``
  if ``litmus.yaml`` is missing instead of raising; use ``load_project``
  for strict file-must-exist semantics.
* ``save_instrument_asset`` accepts an explicit ``target_path=`` so callers
  (CLI / MCP) can place files into typed subdirectories.

Filename / id agreement: every loader for an id-keyed entity treats the
filename stem as the entity id. If the YAML omits ``id:``, the stem fills
it; if the YAML declares ``id:`` and it disagrees with the stem,
``_check_id_matches_filename`` raises ``ValueError``. Users can author
new files without typing ``id:`` at all.
"""

from __future__ import annotations

import copy
import os
import warnings
from collections.abc import Callable, Iterator
from io import StringIO
from pathlib import Path
from typing import Any, Protocol, TypeVar

import numpy as np
import platformdirs
import yaml
from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from litmus.models.catalog import InstrumentCatalogEntry
from litmus.models.enums import InstrumentType
from litmus.models.instrument_asset import InstrumentAssetFile
from litmus.models.part import Part
from litmus.models.part_manifest import PartManifest
from litmus.models.project import ProfileConfig, ProjectConfig
from litmus.models.station import StationConfig, StationType
from litmus.models.test_config import FixtureConfig
from litmus.utils.paths import (
    _resolve_root,
    get_fixture_paths,
    get_instrument_paths,
    get_part_paths,
    get_station_paths,
)


class _HasId(Protocol):
    @property
    def id(self) -> str: ...


_T = TypeVar("_T", bound=_HasId)

_YAML_LOAD_ERRORS = (yaml.YAMLError, ValidationError, OSError, ValueError)


def _check_id_matches_filename(entity_id: str, path: Path) -> None:
    """Raise if the entity id disagrees with the YAML file's stem.

    Filename and id are required to agree so users can navigate the
    config directory without opening every file. Loaders fill the id
    from the stem when YAML omits ``id:``, so this check only fires when
    the user typed an explicit id that disagrees with the filename. If
    the schema ever moves to a database the filename layer goes away
    and this check becomes a no-op (no callers).
    """
    if entity_id != path.stem:
        raise ValueError(
            f"id mismatch in {path}: file declares id={entity_id!r} "
            f"but filename stem is {path.stem!r}. "
            f"Rename the file or fix the id field so they agree."
        )


_M = TypeVar("_M", bound=BaseModel)


def _validate_with_filename_id(model_cls: type[_M], data: dict[str, Any], path: Path) -> _M:
    """Fill ``id`` from filename stem if absent, validate, then assert agreement.

    Single source for the load-time invariant shared by every id-keyed
    YAML loader.
    """
    entity = model_cls.model_validate({"id": path.stem} | data)
    entity_id = entity.id  # type: ignore[attr-defined]
    _check_id_matches_filename(entity_id, path)
    return entity


__all__ = [
    # Catalog
    "create_catalog_entry",
    "find_by_model",
    "find_catalog_dirs",
    "get_catalog_entry",
    "list_catalog_entries",
    "load_catalog_entry",
    "load_catalog_from_directory",
    "resolve_catalog_ref",
    "save_catalog_entry",
    # Fixture
    "create_fixture",
    "get_fixture",
    "list_fixtures",
    "load_fixture",
    "save_fixture",
    # Instrument asset
    "get_instrument_asset",
    "list_instrument_assets",
    "load_instrument_asset",
    "load_instrument_files",
    "save_instrument_asset",
    # Part
    "create_part",
    "get_part",
    "list_parts",
    "load_manifest",
    "load_part",
    "save_manifest",
    "save_part",
    # Project
    "load_project",
    "load_project_config",
    # Station
    "create_station",
    "find_station_config",
    "get_station",
    "list_stations",
    "load_station",
    "load_station_type",
    "save_station",
    "save_station_type",
    # YAML formatting
    "dump_yaml",
    "format_file",
    "format_file_inplace",
    # Generic helpers
    "detect_file_type",
    "expand_ranges",
    "normalize_and_check_instrument_types",
]

# =============================================================================
# Internal helpers
# =============================================================================


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file and return parsed dict (or empty dict).

    Range-expander dicts (``{linspace: [...]}`` and friends) are expanded
    in place before the result is handed to callers. See
    :func:`expand_ranges` below for the supported keys.
    """
    with open(path) as f:
        loaded = yaml.safe_load(f) or {}
    return expand_ranges(loaded)


def detect_file_type(path: Path) -> str | None:
    """Auto-detect the Litmus file type from YAML structure.

    Returns a FILE_LOADERS key ("station", "fixture", "part",
    "catalog", "instrument_asset", "project") or None.
    """
    try:
        data = _read_yaml(path)
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if "catalog_entry" in data:
        return "catalog"
    for key in ("station", "fixture", "project"):
        if key in data:
            return key
    if "id" in data and ("protocol" in data or "driver" in data):
        return "instrument_asset"
    if "id" in data and "characteristics" in data:
        return "part"
    return None


def _find_yaml_files(
    search_paths: list[Path],
    *,
    prefix_skip: str = "_",
) -> Iterator[Path]:
    """Iterate over YAML file paths in search paths.

    Yields:
        Path to each YAML file (skipping files starting with *prefix_skip*).
    """
    for search_dir in search_paths:
        if not search_dir.exists():
            continue
        for yaml_file in search_dir.glob("*.yaml"):
            if prefix_skip and yaml_file.name.startswith(prefix_skip):
                continue
            yield yaml_file


def _resolve_save_path(
    entity_id: str,
    search_paths: list[Path],
    default_dir_name: str,
    project_root: Path | None,
) -> Path:
    """Find existing file for entity, or pick a writable directory, or create default."""
    # 1. Find existing file
    for search_dir in search_paths:
        if search_dir.exists():
            existing = search_dir / f"{entity_id}.yaml"
            if existing.exists():
                return existing

    # 2. Use first existing directory
    for search_dir in search_paths:
        if search_dir.exists():
            return search_dir / f"{entity_id}.yaml"

    # 3. Create default directory
    root = _resolve_root(project_root)
    default_dir = root / default_dir_name
    default_dir.mkdir(exist_ok=True)
    return default_dir / f"{entity_id}.yaml"


def _write_model(path: Path, model_data: dict[str, Any]) -> None:
    """Write a model dict to YAML using Litmus formatting conventions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(model_data))


def _get_by_id(
    entity_id: str,
    loader: Callable[[Path], _T],
    search_paths: list[Path],
) -> _T | None:
    """Look up a YAML-backed entity by id.

    Filename-stem equals id by ``_check_id_matches_filename`` invariant,
    so we try ``<dir>/<id>.yaml`` directly first as a fast path before
    walking. The fallback walk handles any layout drift.
    """
    for search_dir in search_paths:
        if not search_dir.exists():
            continue
        direct = search_dir / f"{entity_id}.yaml"
        if direct.exists():
            try:
                return loader(direct)
            except _YAML_LOAD_ERRORS:
                pass
    for yaml_file in _find_yaml_files(search_paths):
        try:
            entity = loader(yaml_file)
            if entity.id == entity_id:
                return entity
        except _YAML_LOAD_ERRORS:
            continue
    return None


def _find_existing_path(
    entity_id: str,
    loader: Callable[[Path], _T],
    yaml_files: Iterator[Path],
) -> Path | None:
    """Find the YAML file whose loaded entity matches ``entity_id``.

    Used by save_* functions that need to overwrite the existing on-disk
    file for an entity rather than creating a new one. Accepts an explicit
    iterator so callers using nested layouts (e.g. parts) can supply
    rglob output.
    """
    for yaml_file in yaml_files:
        try:
            if loader(yaml_file).id == entity_id:
                return yaml_file
        except _YAML_LOAD_ERRORS:
            continue
    return None


def _list_all(
    loader: Callable[[Path], _T],
    search_paths: list[Path],
) -> list[_T]:
    """Discover all entities across *search_paths*, deduplicating by ID."""
    entities: list[_T] = []
    seen_ids: set[str] = set()
    for yaml_file in _find_yaml_files(search_paths):
        try:
            entity = loader(yaml_file)
        except _YAML_LOAD_ERRORS:
            continue
        if entity.id in seen_ids:
            continue
        seen_ids.add(entity.id)
        entities.append(entity)
    return entities


# =============================================================================
# Project
# =============================================================================


def load_project(path: Path) -> ProjectConfig:
    """Load and validate a litmus.yaml project config file.

    Also discovers one-file-per-profile YAMLs under ``<project_root>/profiles/``
    and merges them into ``project.profiles`` keyed by filename stem. A name
    conflict between an inline ``litmus.yaml: profiles:`` entry and a
    ``profiles/*.yaml`` file raises ``ValueError``.
    """
    project = ProjectConfig.model_validate(_read_yaml(path))
    profiles_dir = path.parent / "profiles"
    if profiles_dir.is_dir():
        for yaml_path in sorted(profiles_dir.glob("*.yaml")):
            name = yaml_path.stem
            if name in project.profiles:
                raise ValueError(
                    f"Profile name conflict: {name!r} is declared both in {path} and {yaml_path}"
                )
            project.profiles[name] = ProfileConfig.model_validate(_read_yaml(yaml_path))
    return project


def load_project_config(project_root: Path | None = None) -> ProjectConfig:
    """Load ``litmus.yaml`` from a project root.

    Args:
        project_root: Project root directory. If None, defaults to cwd.

    Returns:
        Validated ProjectConfig model, or a default ``ProjectConfig(name="litmus")``
        if ``litmus.yaml`` is not present in the resolved root.
    """
    path = _resolve_root(project_root) / "litmus.yaml"
    if not path.exists():
        return ProjectConfig(name="litmus")
    return load_project(path)


# =============================================================================
# Station: load / get / list / save / create
# =============================================================================


def load_station(path: Path) -> StationConfig:
    """Load and validate a station YAML file.

    The YAML's ``id`` field is filled from the filename stem if absent,
    and asserted equal to the stem otherwise.
    """
    return _validate_with_filename_id(StationConfig, _read_yaml(path), path)


def get_station(
    station_id: str,
    *,
    project_root: Path | None = None,
) -> StationConfig | None:
    """Load station configuration by ID."""
    return _get_by_id(station_id, load_station, get_station_paths(project_root))


def list_stations(*, project_root: Path | None = None) -> list[StationConfig]:
    """List all available stations."""
    return _list_all(load_station, get_station_paths(project_root))


def save_station(
    station: StationConfig,
    *,
    project_root: Path | None = None,
) -> None:
    """Save station configuration to YAML file."""
    target_file = _resolve_save_path(
        station.id,
        get_station_paths(project_root),
        "stations",
        project_root,
    )
    _write_model(target_file, station.model_dump(exclude_none=True))


def find_station_config(
    station_id: str,
    *,
    project_root: Path | None = None,
) -> StationConfig:
    """Find and load station config by ID.

    Search order:
    1. ``./stations/{station_id}.yaml`` (project-local, via ``get_station``)
    2. ``~/.local/share/litmus/stations/{station_id}.yaml`` (machine-global)

    Raises:
        FileNotFoundError: If station not found in any location.
    """
    # 1. Project-local search
    result = get_station(station_id, project_root=project_root)
    if result is not None:
        return result

    # 2. Machine-global fallback
    home = Path(os.environ.get("LITMUS_HOME", platformdirs.user_data_dir("litmus")))
    global_path = home / "stations" / f"{station_id}.yaml"
    if global_path.exists():
        try:
            return load_station(global_path)
        except _YAML_LOAD_ERRORS:
            pass

    raise FileNotFoundError(
        f"Station {station_id!r} not found. Searched:\n"
        f"  - ./stations/{station_id}.yaml (project-local)\n"
        f"  - {global_path} (machine-global)"
    )


def create_station(
    station_id: str,
    name: str,
    location: str = "",
    description: str = "",
    *,
    project_root: Path | None = None,
) -> StationConfig | None:
    """Create a new station configuration file.

    Returns StationConfig if successful, None if station already exists.
    """
    root = _resolve_root(project_root)
    stations_dir = root / "stations"
    stations_dir.mkdir(exist_ok=True)

    station_file = stations_dir / f"{station_id}.yaml"
    if station_file.exists():
        return None

    station = StationConfig(
        id=station_id,
        name=name,
        location=location or None,
        description=description or None,
    )
    _write_model(station_file, station.model_dump(exclude_none=True))
    return station


# =============================================================================
# Fixture: load / get / list / save / create
# =============================================================================


def load_fixture(path: Path) -> FixtureConfig:
    """Load and validate a fixture YAML file.

    The YAML's ``id`` field is filled from the filename stem if absent,
    and asserted equal to the stem otherwise.
    """
    return _validate_with_filename_id(FixtureConfig, _read_yaml(path), path)


def get_fixture(
    fixture_id: str,
    *,
    project_root: Path | None = None,
) -> FixtureConfig | None:
    """Load fixture configuration by ID."""
    return _get_by_id(fixture_id, load_fixture, get_fixture_paths(project_root))


def list_fixtures(*, project_root: Path | None = None) -> list[FixtureConfig]:
    """List all available fixtures."""
    return _list_all(load_fixture, get_fixture_paths(project_root))


def save_fixture(
    fixture: FixtureConfig,
    *,
    project_root: Path | None = None,
) -> None:
    """Save fixture configuration to YAML file."""
    target_file = _resolve_save_path(
        fixture.id,
        get_fixture_paths(project_root),
        "fixtures",
        project_root,
    )
    _write_model(target_file, fixture.model_dump(exclude_none=True))


def create_fixture(
    fixture_id: str,
    name: str,
    part_id: str = "",
    part_revision: str = "",
    description: str = "",
    *,
    project_root: Path | None = None,
) -> FixtureConfig | None:
    """Create a new fixture configuration file.

    Returns FixtureConfig if successful, None if fixture already exists.
    """
    root = _resolve_root(project_root)
    fixtures_dir = root / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)

    fixture_file = fixtures_dir / f"{fixture_id}.yaml"
    if fixture_file.exists():
        return None

    fixture = FixtureConfig(
        id=fixture_id,
        name=name,
        part_id=part_id or None,
        part_revision=part_revision or None,
        description=description or None,
    )
    _write_model(fixture_file, fixture.model_dump(exclude_none=True))
    return fixture


# =============================================================================
# Part: load / get / list / save / create
# =============================================================================

_MAX_INHERIT_DEPTH = 5


def _check_inherit_depth(depth: int, entity_type: str, path: Path) -> None:
    if depth > _MAX_INHERIT_DEPTH:
        raise ValueError(f"{entity_type} inheritance depth exceeds {_MAX_INHERIT_DEPTH} for {path}")


def _load_with_inheritance(
    path: Path,
    *,
    label: str,
    find_base: Callable[[str, Path], Path | None],
    merge: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    seen: set[str],
    depth: int,
) -> dict[str, Any]:
    """Recursive ``base:``-resolving YAML loader.

    Shared by :func:`load_part` and :func:`load_catalog_entry` —
    both walk the same scaffolding (depth check, cycle guard, recurse,
    merge) but differ on how to find a base file and how to merge the
    raw data. ``find_base(base_ref, current_path)`` returns the resolved
    base path or ``None``; ``merge(base, variant)`` returns the merged
    dict.
    """
    _check_inherit_depth(depth, label, path)

    data = _read_yaml(path)
    entity_id = data.get("id", path.stem)
    base_ref = data.get("base")

    if not base_ref:
        return data

    if entity_id in seen:
        raise ValueError(
            f"Circular {label.lower()} inheritance: {entity_id!r} already in chain {seen}"
        )
    seen.add(entity_id)

    base_path = find_base(base_ref, path)
    if base_path is None:
        raise ValueError(
            f"Base {label.lower()} {base_ref!r} not found (referenced by {entity_id!r} in {path})"
        )

    base_data = _load_with_inheritance(
        base_path,
        label=label,
        find_base=find_base,
        merge=merge,
        seen=seen,
        depth=depth + 1,
    )
    return merge(base_data, data)


def load_part(path: Path, parts_dir: Path | None = None) -> Part:
    """Load a part specification from YAML, resolving inheritance.

    Args:
        path: Path to the part YAML file.
        parts_dir: Directory to search for base parts.
    """
    if parts_dir is None:
        # Nested layout (`parts/foo/foo.yaml`) → grandparent is the parts
        # root; flat layout (`parts/foo.yaml`) → parent is.
        candidate = path.parent.parent
        parts_dir = candidate if candidate.name == "parts" else path.parent

    def find_base(base_ref: str, _current: Path) -> Path | None:
        # Direct hit by filename stem.
        base_path = parts_dir / f"{base_ref}.yaml"
        if base_path.exists():
            return base_path
        # Fallback: scan for a file whose ``id:`` equals the base_ref.
        for candidate in parts_dir.rglob("*.yaml"):
            if candidate.name.startswith("_"):
                continue
            try:
                candidate_data = _read_yaml(candidate)
                if candidate_data.get("id") == base_ref:
                    return candidate
            except (yaml.YAMLError, OSError):
                continue
        return None

    data = _load_with_inheritance(
        path,
        label="Part",
        find_base=find_base,
        merge=_merge_part_data,
        seen=set(),
        depth=0,
    )
    return _validate_with_filename_id(Part, data, path)


def _merge_dicts_with_sections(
    base: dict[str, Any],
    variant: dict[str, Any],
    scalar_keys: tuple[str, ...],
    section_keys: tuple[str, ...],
) -> dict[str, Any]:
    """Inheritance merge for ``base:``-style YAML refs.

    Scalars from *scalar_keys* default from *base* and are overridden by
    *variant*; any other variant keys carry through unchanged. Sections in
    *section_keys* are taken whole from variant if present, else from base,
    and deep-copied so callers can mutate without leaking back into base.
    """
    merged: dict[str, Any] = {}
    for key in scalar_keys:
        if key in base:
            merged[key] = base[key]
    merged.update(variant)
    for section in section_keys:
        if section in variant:
            merged[section] = copy.deepcopy(variant[section])
        elif section in base:
            merged[section] = copy.deepcopy(base[section])
    return merged


_PART_SCALAR_KEYS = (
    "name",
    "description",
    "revision",
    "part_number",
    "datasheet",
    "schematic",
    "driver",
)
_PART_SECTION_KEYS = ("pins", "signal_groups", "characteristics")


def _merge_part_data(
    base: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Any]:
    """Merge base and variant part YAML with section-level override."""
    return _merge_dicts_with_sections(base, variant, _PART_SCALAR_KEYS, _PART_SECTION_KEYS)


def _part_yaml_files(parts_dir: Path, glob: str = "*.yaml") -> Iterator[Path]:
    """Iterate ``<parts_dir>/**/<glob>``, skipping ``_``-prefixed files.

    Used by :func:`get_part`, :func:`list_parts`, and
    :func:`save_part` so the underscore-prefix skip is applied
    consistently. Sorted output for deterministic iteration. Yields
    nothing if *parts_dir* does not exist.
    """
    if not parts_dir.exists():
        return
    for yaml_file in sorted(parts_dir.rglob(glob)):
        if yaml_file.name.startswith("_"):
            continue
        yield yaml_file


def get_part(
    part_id: str,
    *,
    project_root: Path | None = None,
) -> Part | None:
    """Load a Part model by ID."""
    for parts_dir in get_part_paths(project_root):
        flat_file = parts_dir / f"{part_id}.yaml"
        if flat_file.exists():
            try:
                return load_part(flat_file)
            except _YAML_LOAD_ERRORS as exc:
                warnings.warn(
                    f"part: failed to load {flat_file.name}: {exc}",
                    stacklevel=2,
                )
                # Canonical file exists but failed — don't paper over with a
                # nested duplicate; move on to the next parts_dir.
                continue
        for yaml_file in _part_yaml_files(parts_dir, f"{part_id}.yaml"):
            if yaml_file == flat_file:
                continue
            try:
                return load_part(yaml_file)
            except _YAML_LOAD_ERRORS:
                continue
    return None


def list_parts(*, project_root: Path | None = None) -> list[Part]:
    """List all available parts as Part models."""
    parts: list[Part] = []
    seen_ids: set[str] = set()
    for parts_dir in get_part_paths(project_root):
        if not parts_dir.exists():
            continue
        for yaml_file in _part_yaml_files(parts_dir):
            try:
                part = load_part(yaml_file)
            except _YAML_LOAD_ERRORS:
                continue
            if part.id in seen_ids:
                continue
            seen_ids.add(part.id)
            parts.append(part)
    return parts


def save_part(
    part: Part,
    *,
    project_root: Path | None = None,
) -> None:
    """Save part specification to YAML file."""
    target_file: Path | None = None
    for parts_dir in get_part_paths(project_root):
        if not parts_dir.exists():
            continue
        # Preserve existing file location (flat or nested) by matching id.
        target_file = _find_existing_path(part.id, load_part, _part_yaml_files(parts_dir))
        if target_file is not None:
            break

    if target_file is None:
        root = _resolve_root(project_root)
        parts_dir = root / "parts"
        parts_dir.mkdir(exist_ok=True)
        target_file = parts_dir / f"{part.id}.yaml"

    _write_model(target_file, part.model_dump(exclude_none=True))


def create_part(
    part_id: str,
    name: str,
    description: str = "",
    *,
    project_root: Path | None = None,
) -> Part | None:
    """Create a new part YAML file.

    Returns Part if successful, None if part already exists.
    """
    root = _resolve_root(project_root)
    parts_dir = root / "parts"
    parts_dir.mkdir(exist_ok=True)

    target_file = parts_dir / f"{part_id}.yaml"
    if target_file.exists():
        return None

    part = Part(id=part_id, name=name, description=description or None)
    _write_model(target_file, part.model_dump(exclude_none=True))
    return part


# =============================================================================
# Part Manifest: load / save
# =============================================================================


def load_manifest(path: Path) -> PartManifest:
    """Load and validate a part manifest YAML file."""
    return PartManifest.model_validate(_read_yaml(path))


def save_manifest(manifest: PartManifest, path: Path) -> None:
    """Save a part manifest to YAML at the given path.

    Manifests live next to their part folder (not in a globally-
    indexed location like the other ``save_*`` entities), so the
    caller passes the explicit path rather than a project root.
    """
    _write_model(path, manifest.model_dump(exclude_none=True))


# =============================================================================
# Catalog: load / get / list / save / create  (+ helpers)
# =============================================================================


def load_catalog_entry(
    path: Path,
    catalog_dir: Path | None = None,
) -> InstrumentCatalogEntry:
    """Load a single catalog entry from a YAML file, resolving inheritance."""
    resolved_catalog_dir = catalog_dir if catalog_dir is not None else path.parent

    def find_base(base_ref: str, current: Path) -> Path | None:
        # Sibling file first, then catalog-dir top-level.
        sibling = current.parent / f"{base_ref}.yaml"
        if sibling.exists():
            return sibling
        top = resolved_catalog_dir / f"{base_ref}.yaml"
        if top.exists():
            return top
        return None

    data = _load_with_inheritance(
        path,
        label="Catalog",
        find_base=find_base,
        merge=_merge_catalog_data,
        seen=set(),
        depth=0,
    )
    return _validate_with_filename_id(InstrumentCatalogEntry, _with_catalog_defaults(data), path)


_CATALOG_SCALAR_KEYS = ("manufacturer", "type", "name", "description")
_CATALOG_SECTION_KEYS = ("channels", "attributes", "interfaces")


def _merge_catalog_data(
    base: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Any]:
    """Merge base and variant catalog YAML dicts.

    Capabilities are merged by ``(function, direction)`` rather than
    overridden whole — that's catalog-specific, so it lives here rather
    than in :func:`_merge_dicts_with_sections`.
    """
    merged_entry = _merge_dicts_with_sections(
        base, variant, _CATALOG_SCALAR_KEYS, _CATALOG_SECTION_KEYS
    )

    base_caps = base.get("capabilities") or []
    variant_caps = variant.get("capabilities") or []

    if variant_caps and base_caps:
        merged_entry["capabilities"] = _merge_capabilities(base_caps, variant_caps)
    elif variant_caps:
        merged_entry["capabilities"] = variant_caps
    elif base_caps:
        merged_entry["capabilities"] = base_caps

    return merged_entry


def _cap_key(cap: dict[str, Any]) -> tuple[str, str]:
    return (cap.get("function", ""), cap.get("direction", ""))


def _merge_capabilities(
    base_caps: list[dict[str, Any]],
    variant_caps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge variant capabilities into base capabilities by (function, direction).

    When function+direction keys match: signals/conditions/controls/attributes
    are deep-merged at the parameter level. Other fields (channels, modes,
    readback) are overwritten by the variant. New capabilities in the variant
    are appended.
    """
    merged: list[dict[str, Any]] = [copy.deepcopy(c) for c in base_caps]
    base_index: dict[tuple[str, str], int] = {_cap_key(cap): i for i, cap in enumerate(base_caps)}

    for vcap in variant_caps:
        key = _cap_key(vcap)
        if key in base_index:
            _deep_merge_cap(merged[base_index[key]], vcap)
        else:
            merged.append(copy.deepcopy(vcap))

    return merged


def _deep_merge_cap(base_cap: dict[str, Any], variant_cap: dict[str, Any]) -> None:
    """Deep-merge a variant capability into a base capability in place."""
    for section in ("signals", "conditions", "controls", "attributes"):
        v_section = variant_cap.get(section)
        if not v_section or not isinstance(v_section, dict):
            continue

        if section not in base_cap or not isinstance(base_cap.get(section), dict):
            base_cap[section] = copy.deepcopy(v_section)
            continue

        b_section = base_cap[section]
        for param_name, v_param in v_section.items():
            if param_name not in b_section:
                b_section[param_name] = copy.deepcopy(v_param)
                continue

            if isinstance(v_param, dict) and "bands" in v_param:
                b_param = b_section[param_name]
                if not isinstance(b_param, dict):
                    b_section[param_name] = copy.deepcopy(v_param)
                    continue
                b_bands = b_param.get("bands", [])
                b_param["bands"] = b_bands + copy.deepcopy(v_param["bands"])
                for k, v in v_param.items():
                    if k != "bands":
                        b_param[k] = copy.deepcopy(v)
            else:
                b_section[param_name] = copy.deepcopy(v_param)


def _with_catalog_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """Inject derived defaults for a catalog entry's display ``name``.

    The display ``name`` falls back to ``"{manufacturer} {model}"`` when
    omitted, since that's a derivable label, not an identity. Returns a
    new dict so the caller can pass it to ``_validate_with_filename_id``.
    """
    if data.get("name"):
        return data
    mfr = data.get("manufacturer", "")
    model = data.get("model", "")
    derived = f"{mfr} {model}".strip()
    if not derived:
        return data
    return {"name": derived} | data


def _try_load_catalog_entry(path: Path, cat_dir: Path) -> InstrumentCatalogEntry | None:
    """Load a catalog entry, warning on failure and returning None.

    Used by every catalog-iteration helper that needs to be lenient with
    individual unparseable files while still surfacing the failure.
    """
    try:
        return load_catalog_entry(path, catalog_dir=cat_dir)
    except _YAML_LOAD_ERRORS as exc:
        warnings.warn(
            f"catalog: failed to load {path.name}: {exc}",
            stacklevel=2,
        )
        return None


def _iter_loaded_catalog_entries(
    cat_dirs: list[Path],
) -> Iterator[tuple[InstrumentCatalogEntry, Path]]:
    """Yield ``(entry, path)`` for every loadable catalog YAML across *cat_dirs*.

    Skips files that fail to load (with a warning via ``_try_load_catalog_entry``).
    No deduplication — callers handle dedup based on their needs.
    """
    for cat_dir in cat_dirs:
        if not cat_dir.exists():
            continue
        for path in _iter_catalog_files(cat_dir):
            entry = _try_load_catalog_entry(path, cat_dir)
            if entry is not None:
                yield entry, path


def load_catalog_from_directory(catalog_dir: Path) -> dict[str, InstrumentCatalogEntry]:
    """Load all catalog entries from a directory keyed by entry id."""
    return {entry.id: entry for entry, _ in _iter_loaded_catalog_entries([catalog_dir])}


def _skip_catalog_file(path: Path) -> bool:
    """Return True for catalog YAML files that should never be loaded directly."""
    return path.name.startswith("_") or ".variants." in path.name


def _iter_catalog_files(cat_dir: Path) -> Iterator[Path]:
    """Yield all loadable catalog YAML files in a directory tree, sorted.

    Yields nothing if *cat_dir* does not exist.
    """
    if not cat_dir.exists():
        return
    for path in sorted(cat_dir.rglob("*.yaml")):
        if not _skip_catalog_file(path):
            yield path


def _find_catalog_variants(base_path: Path) -> list[Path]:
    """Find catalog YAML files that inherit from the given base entry."""
    base_id = base_path.stem
    variants = []
    for candidate in sorted(base_path.parent.glob("*.yaml")):
        if candidate == base_path or ".variants." in candidate.name:
            continue
        try:
            data = _read_yaml(candidate)
            if data.get("base") == base_id:
                variants.append(candidate)
        except (yaml.YAMLError, OSError):
            continue
    return variants


def find_catalog_dirs(*, project_root: Path | None = None) -> list[Path]:
    """Find catalog directories relative to project root.

    Server should be run from the project root (e.g., `cd demo && litmus serve`),
    so `catalog/` resolves to that project's catalog.

    Also includes bundled generic catalog from the litmus package.
    """
    dirs: list[Path] = []
    root = _resolve_root(project_root)

    # Project-local catalog (takes precedence)
    catalog_dir = root / "catalog"
    if catalog_dir.is_dir():
        dirs.append(catalog_dir)

    # Bundled generic catalog from site-packages
    bundled = Path(__file__).parent / "catalog" / "generic"
    if bundled.is_dir():
        dirs.append(bundled)

    return dirs


def _catalog_file_candidates(cat_dir: Path, name: str) -> list[Path]:
    """Return candidate paths for a catalog entry by filename stem.

    Direct match is tried first; rglob finds subdirectory hits. Paths are
    deduplicated so a file at ``cat_dir/{name}.yaml`` never appears twice.
    """
    seen: set[Path] = set()
    results: list[Path] = []

    direct = cat_dir / f"{name}.yaml"
    if direct.exists():
        seen.add(direct.resolve())
        results.append(direct)

    for path in cat_dir.rglob(f"{name}.yaml"):
        if _skip_catalog_file(path):
            continue
        if path.resolve() not in seen:
            seen.add(path.resolve())
            results.append(path)

    return results


def resolve_catalog_ref(
    catalog_ref: str,
    *,
    project_root: Path | None = None,
) -> InstrumentCatalogEntry | None:
    """Resolve a YAML ``catalog_ref:`` value to its catalog entry.

    Lookup is **id-only** — matches a catalog file by id (filename stem).
    Use :func:`get_catalog_entry` if you also want a fallback to type-id
    matching (e.g. ``"dmm"`` resolving to the first DMM entry).
    """
    for cat_dir in find_catalog_dirs(project_root=project_root):
        for path in _catalog_file_candidates(cat_dir, catalog_ref):
            entry = _try_load_catalog_entry(path, cat_dir)
            if entry is not None:
                return entry
    return None


def find_by_model(
    manufacturer: str,
    model: str,
    *,
    project_root: Path | None = None,
) -> InstrumentCatalogEntry | None:
    """Find a catalog entry by manufacturer and model name (case-insensitive)."""
    mfr_lower = manufacturer.lower()
    model_lower = model.lower()

    for entry, _ in _iter_loaded_catalog_entries(find_catalog_dirs(project_root=project_root)):
        if (
            entry.manufacturer
            and entry.manufacturer.lower() == mfr_lower
            and entry.model
            and entry.model.lower() == model_lower
        ):
            return entry

    return None


def get_catalog_entry(
    catalog_id: str,
    *,
    project_root: Path | None = None,
) -> InstrumentCatalogEntry | None:
    """Get a catalog entry by id or by instrument type.

    Tries direct filename match first, then rglob by filename (fast paths).
    If no filename match is found, falls back to a full directory scan and
    returns the first entry whose ``type`` equals *catalog_id* (so passing
    ``"dmm"`` returns the first DMM entry). Use :func:`resolve_catalog_ref`
    if you want id-only resolution without the type fallback.

    Project-local catalog entries take precedence over bundled ones.
    """
    for cat_dir in find_catalog_dirs(project_root=project_root):
        for path in _catalog_file_candidates(cat_dir, catalog_id):
            entry = _try_load_catalog_entry(path, cat_dir)
            if entry is not None:
                return entry

        # Slow path: stream entries to find first type match.
        for entry, _ in _iter_loaded_catalog_entries([cat_dir]):
            if entry.type == catalog_id:
                return entry
    return None


def list_catalog_entries(
    *,
    project_root: Path | None = None,
) -> list[InstrumentCatalogEntry]:
    """List all catalog entries across all catalog directories.

    Project-local entries take precedence over bundled ones (first-wins
    deduplication, matching the order returned by ``find_catalog_dirs``).
    """
    all_entries: list[InstrumentCatalogEntry] = []
    seen_ids: set[str] = set()
    for entry, _ in _iter_loaded_catalog_entries(find_catalog_dirs(project_root=project_root)):
        if entry.id in seen_ids:
            continue
        seen_ids.add(entry.id)
        all_entries.append(entry)
    return all_entries


def save_catalog_entry(
    entry: InstrumentCatalogEntry,
    *,
    project_root: Path | None = None,
) -> None:
    """Save a catalog entry to catalog/."""
    root = _resolve_root(project_root)
    catalog_dir = root / "catalog"
    catalog_dir.mkdir(exist_ok=True)

    target_file = catalog_dir / f"{entry.id}.yaml"
    _write_model(target_file, entry.model_dump(exclude_none=True))


def create_catalog_entry(
    instrument_type: str,
    name: str,
    description: str = "",
    manufacturer: str = "User",
    *,
    project_root: Path | None = None,
) -> InstrumentCatalogEntry | None:
    """Create a new catalog entry in catalog/.

    Returns InstrumentCatalogEntry if successful, None if already exists.
    """
    root = _resolve_root(project_root)
    catalog_dir = root / "catalog"
    catalog_dir.mkdir(exist_ok=True)

    catalog_file = catalog_dir / f"{instrument_type}.yaml"
    if catalog_file.exists():
        return None

    entry = InstrumentCatalogEntry.model_validate(
        {
            "id": instrument_type,
            "type": instrument_type,
            "manufacturer": manufacturer,
            "model": name,
            "name": name,
            "description": description or None,
            "capabilities": [],
        }
    )

    _write_model(catalog_file, entry.model_dump(exclude_none=True))
    return entry


# =============================================================================
# Instrument Asset: load / get / list / save
# =============================================================================


def load_instrument_asset(path: Path) -> InstrumentAssetFile:
    """Load and validate an instrument asset YAML file.

    The YAML's ``id`` field is filled from the filename stem if absent,
    and asserted equal to the stem otherwise.
    """
    return _validate_with_filename_id(InstrumentAssetFile, _read_yaml(path), path)


def load_instrument_files(instruments_dir: Path) -> dict[str, InstrumentAssetFile]:
    """Load all instrument asset files from a directory."""
    if not instruments_dir.exists():
        return {}

    instruments: dict[str, InstrumentAssetFile] = {}
    for path in instruments_dir.glob("*.yaml"):
        try:
            asset = load_instrument_asset(path)
        except _YAML_LOAD_ERRORS:
            continue
        instruments[asset.id] = asset
    return instruments


def get_instrument_asset(
    instrument_id: str,
    *,
    project_root: Path | None = None,
) -> InstrumentAssetFile | None:
    """Load a single instrument asset file by ID."""
    return _get_by_id(instrument_id, load_instrument_asset, get_instrument_paths(project_root))


def list_instrument_assets(
    *,
    project_root: Path | None = None,
) -> list[InstrumentAssetFile]:
    """List all instrument asset files."""
    return _list_all(load_instrument_asset, get_instrument_paths(project_root))


def save_instrument_asset(
    asset: InstrumentAssetFile,
    *,
    target_path: Path | None = None,
    project_root: Path | None = None,
) -> None:
    """Save an instrument asset file.

    Unlike ``save_station`` / ``save_part`` / etc., this function accepts
    an explicit ``target_path``. Instrument assets are organized into typed
    subdirectories (``instruments/<type>/<id>.yaml``) rather than the flat
    layout the other entities use, and CLI / MCP callers compute the
    subdir path themselves. When ``target_path`` is omitted we fall back to
    the standard discovery via ``_resolve_save_path``.

    Args:
        asset: The instrument asset to save.
        target_path: Exact file path to write. When provided, skip directory
            discovery and write directly to this path.
        project_root: Project root for directory discovery (ignored when
            *target_path* is given).
    """
    if target_path is not None:
        target_file = target_path
    else:
        target_file = _resolve_save_path(
            asset.id,
            get_instrument_paths(project_root),
            "instruments",
            project_root,
        )

    _write_model(target_file, asset.model_dump(exclude_none=True))


# =============================================================================
# Station Type: load / save
# =============================================================================


def save_station_type(
    station_type: StationType,
    *,
    project_root: Path | None = None,
) -> None:
    """Save station type YAML to stations/types/{id}.yaml."""
    root = _resolve_root(project_root)
    types_dir = root / "stations" / "types"
    types_dir.mkdir(parents=True, exist_ok=True)

    target_file = types_dir / f"{station_type.id}.yaml"
    _write_model(target_file, station_type.model_dump(exclude_none=True))


def load_station_type(
    type_id: str,
    *,
    project_root: Path | None = None,
) -> StationType | None:
    """Load station type by ID."""
    root = _resolve_root(project_root)
    types_dir = root / "stations" / "types"
    if not types_dir.is_dir():
        return None
    yaml_file = types_dir / f"{type_id}.yaml"
    if not yaml_file.exists():
        return None
    try:
        return _validate_with_filename_id(StationType, _read_yaml(yaml_file), yaml_file)
    except (yaml.YAMLError, ValidationError, OSError):
        return None


# =============================================================================
# YAML formatting — file I/O wrappers around dump_yaml
# =============================================================================


def format_file(path: Path) -> str:
    """Load a YAML file and return it formatted with Litmus conventions."""
    return dump_yaml(_read_yaml(path))


def format_file_inplace(path: Path) -> bool:
    """Format a YAML file in-place with Litmus conventions.

    Returns True if the file content changed.
    """
    original = path.read_text()
    formatted = dump_yaml(expand_ranges(yaml.safe_load(original) or {}))
    if formatted != original:
        path.write_text(formatted)
        return True
    return False


# =============================================================================
# Loader registry (single source of truth for validation.py)
# =============================================================================

FILE_LOADERS: dict[str, Callable[[Path], object]] = {
    "station": load_station,
    "fixture": load_fixture,
    "instrument_asset": load_instrument_asset,
    "project": load_project,
    "part": load_part,
    "catalog": load_catalog_entry,
}


# =============================================================================
# YAML range expanders — load-time
# =============================================================================
#
# Any list-producing position in a Litmus YAML file accepts a single-key
# dict whose key names a generator. The loader walks the tree and
# replaces every such dict with the expanded list *before* Pydantic
# validation — so schema shapes only ever see plain lists.
#
# Supported generators:
#     {linspace: [start, stop, num]}     -> numpy.linspace
#     {arange:   [start, stop, step]}    -> numpy.arange  (stop exclusive)
#     {logspace: [start, stop, num]}     -> numpy.logspace (base 10)
#     {geomspace:[start, stop, num]}     -> numpy.geomspace
#     {repeat:   [value, n]}             -> [value] * n
#     {range:    [start, stop[, step]]}  -> list(range(...))


_RANGE_EXPANDERS: dict[str, Callable[[list[Any]], list[Any]]] = {
    "linspace": lambda args: np.linspace(*args).tolist(),
    "arange": lambda args: np.arange(*args).tolist(),
    "logspace": lambda args: np.logspace(*args).tolist(),
    "geomspace": lambda args: np.geomspace(*args).tolist(),
    "repeat": lambda args: [args[0]] * int(args[1]),
    "range": lambda args: list(range(*args)),
}


def expand_ranges(data: Any) -> Any:
    """Recursively replace ``{<expander>: [...]}`` nodes with expanded lists.

    Returns a new object so the caller can reassign. Unknown dict shapes
    pass through unchanged; lists and nested dicts are walked.
    """
    if isinstance(data, dict):
        if len(data) == 1:
            (key, value) = next(iter(data.items()))
            if key in _RANGE_EXPANDERS and isinstance(value, list):
                # Expanders delegate to numpy / builtins, which can raise a
                # wide range of errors (TypeError, ValueError, ZeroDivisionError,
                # numpy-specific). Re-wrap them all into one ValueError with the
                # offending key + args so users can fix the YAML.
                try:
                    return _RANGE_EXPANDERS[key](value)
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(
                        f"Range expander {key!r} failed on args {value!r}: {exc}"
                    ) from exc
        return {k: expand_ranges(v) for k, v in data.items()}
    if isinstance(data, list):
        return [expand_ranges(item) for item in data]
    return data


# =============================================================================
# YAML output formatter — write-time
# =============================================================================
#
# Block-style for structural keys, flow-style for compact leaf dicts and
# short scalar lists. Strings are double-quoted to dodge YAML's reserved
# words (on/off/yes/no).
#
# All ``_fmt_*`` helpers below are private implementation detail of
# :func:`dump_yaml`; nothing else in the module imports them.


_FMT_BLOCK_KEYS = {
    # Catalog
    "when",
    "signals",
    "conditions",
    "controls",
    "attributes",
    "capabilities",
    "channels",
    "catalog_entry",
    "specs",
    # Parts
    "characteristics",
    "vectors",
    "limits",
    # Steps (execution grain)
    "steps",
    # Station / fixture
    "instruments",
    "roles",
    "pins",
}


def _fmt_is_scalar(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool, type(None)))


def _fmt_quote_if_needed(v: Any) -> Any:
    """Quote all string values for safe, unambiguous YAML output."""
    if isinstance(v, str):
        return DoubleQuotedScalarString(v)
    return v


def _fmt_apply_style(data: Any, key: str | None = None) -> Any:
    """Recursively apply Litmus YAML style rules."""
    if isinstance(data, dict):
        cm = CommentedMap()
        for k, v in data.items():
            cm[k] = _fmt_apply_style(v, key=k)
        if key not in _FMT_BLOCK_KEYS and all(_fmt_is_scalar(v) for v in data.values()):
            cm.fa.set_flow_style()
        return cm
    if isinstance(data, list):
        cs = CommentedSeq()
        for item in data:
            cs.append(_fmt_apply_style(_fmt_quote_if_needed(item)))
        if all(_fmt_is_scalar(v) for v in data) and len(data) <= 8:
            cs.fa.set_flow_style()
        return cs
    return _fmt_quote_if_needed(data)


def dump_yaml(data: dict[str, Any]) -> str:
    """Dump a dict to a YAML string with Litmus conventions."""
    styled = _fmt_apply_style(data)
    ry = YAML()
    ry.default_flow_style = False
    ry.width = 120
    ry.indent(mapping=2, sequence=2, offset=0)
    buf = StringIO()
    ry.dump(styled, buf)
    return buf.getvalue()


# =============================================================================
# Instrument-type normalization — load-time
# =============================================================================
#
# `type:` values get lowercased and aliased to canonical InstrumentType
# vocabulary. Unknown types soft-warn (custom types are fine) but typos
# get surfaced.


_INSTRUMENT_TYPE_ALIASES: dict[str, str] = {
    "digital_multimeter": InstrumentType.DMM,
    "scope": InstrumentType.OSCILLOSCOPE,
    "power_supply": InstrumentType.PSU,
    "dc_power_supply": InstrumentType.PSU,
    "fgen": InstrumentType.FUNCTION_GENERATOR,
    "eload": InstrumentType.ELECTRONIC_LOAD,
    "rf_siggen": InstrumentType.RF_SIGNAL_GENERATOR,
    "signal_generator": InstrumentType.RF_SIGNAL_GENERATOR,
    "picoammeter": InstrumentType.ELECTROMETER,
    "optical_power_meter": InstrumentType.POWER_METER,
}

_KNOWN_INSTRUMENT_TYPES = {t.value for t in InstrumentType}


def _normalize_instrument_type(raw: str) -> str:
    """Lowercase, strip, and resolve aliases to canonical type."""
    normalized = raw.strip().lower()
    return _INSTRUMENT_TYPE_ALIASES.get(normalized, normalized)


def normalize_and_check_instrument_types(
    instruments: dict[str, dict],
    *,
    project_root: Path | None = None,
) -> tuple[dict[str, dict], list[str]]:
    """Normalize instrument types and warn about unknown ones.

    Args:
        instruments: Dict of instrument name → config dict. Each config
            must have a ``type`` key.
        project_root: Project root for catalog discovery; defaults to cwd.

    Returns:
        ``(instruments with normalized types, list of warning strings)``
    """
    msgs: list[str] = []
    for name, config in instruments.items():
        if "type" not in config:
            continue
        original = config["type"]
        config["type"] = _normalize_instrument_type(original)
        if config["type"] != original:
            msgs.append(f"instruments.{name}: Normalized type '{original}' → '{config['type']}'")
        if config["type"] not in _KNOWN_INSTRUMENT_TYPES:
            known = _known_catalog_types(project_root=project_root)
            if known and config["type"] not in known:
                msgs.append(
                    f"instruments.{name}: Type '{config['type']}' "
                    "not in InstrumentType enum or any catalog entry — "
                    "this is fine for custom types, but check for typos."
                )
    return instruments, msgs


def _known_catalog_types(*, project_root: Path | None = None) -> set[str]:
    """Collect instrument types present in loaded catalog entries."""
    try:
        types: set[str] = set()
        for entry, _ in _iter_loaded_catalog_entries(find_catalog_dirs(project_root=project_root)):
            if entry.type:
                types.add(entry.type.lower())
        return types | _KNOWN_INSTRUMENT_TYPES
    except OSError as exc:
        warnings.warn(
            f"Could not load instrument catalog: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        return _KNOWN_INSTRUMENT_TYPES
