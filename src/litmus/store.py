"""Centralized persistence for all Litmus YAML config files.

Every consumer should call these functions instead of raw yaml.safe_load.
ONE module for all load/save/list/get/create operations.

Public interface — four verbs per entity:
    get_*(id) → Model | None       # Lookup by ID across search paths
    list_*() → list[Model]         # Discover all across search paths
    save_*(model) → bool           # Write model to YAML
    create_*(...) → Model | None   # Create new, None if exists

Plus low-level load_*(path) for callers that already have a file path.

All public functions accept an optional `project_root: Path | None` parameter.
When None (the default), falls back to Path.cwd() for backwards compatibility.
"""

from __future__ import annotations

import copy
import warnings
from collections.abc import Callable, Iterator
from io import StringIO
from pathlib import Path
from typing import Any, Protocol, TypeVar

import numpy as np
import yaml
from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from litmus.models.catalog import InstrumentCatalogEntry
from litmus.models.enums import InstrumentType
from litmus.models.instrument_asset import InstrumentAssetFile
from litmus.models.product import Product
from litmus.models.product_manifest import ProductManifest
from litmus.models.project import ProfileConfig, ProjectConfig
from litmus.models.station import StationConfig
from litmus.models.station_types import StationType
from litmus.models.test_config import FixtureConfig
from litmus.utils.paths import (
    get_fixture_paths,
    get_instrument_paths,
    get_product_paths,
    get_station_paths,
)


class _HasId(Protocol):
    @property
    def id(self) -> str: ...


_T = TypeVar("_T", bound=_HasId)

_YAML_LOAD_ERRORS = (yaml.YAMLError, ValidationError, OSError, ValueError)

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
    # Product
    "create_product",
    "get_product",
    "list_products",
    "load_manifest",
    "load_product",
    "save_manifest",
    "save_product",
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
    "format_file",
    "format_file_inplace",
    # Generic helpers
    "detect_file_type",
]

# =============================================================================
# Internal helpers
# =============================================================================


def _resolve_root(project_root: Path | None) -> Path:
    """Resolve project root, defaulting to cwd."""
    return project_root if project_root is not None else Path.cwd()


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

    Returns a FILE_LOADERS key ("station", "fixture", "product",
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
        return "product"
    return None


def find_yaml_files(
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
    path.write_text(dump_yaml(model_data))


def _get_by_id(
    entity_id: str,
    loader: Callable[[Path], _T],
    search_paths: list[Path],
) -> _T | None:
    """Look up a YAML-backed entity by ID or filename stem."""
    for yaml_file in find_yaml_files(search_paths):
        try:
            entity = loader(yaml_file)
            if entity.id == entity_id or yaml_file.stem == entity_id:
                return entity
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
    for yaml_file in find_yaml_files(search_paths):
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


def load_project_config(path: Path | str | None = None) -> ProjectConfig:
    """Load project configuration from litmus.yaml.

    Args:
        path: Path to litmus.yaml. If None, looks in cwd.

    Returns:
        Validated ProjectConfig model, or default if file not found.
    """
    if path is None:
        path = Path.cwd() / "litmus.yaml"
    else:
        path = Path(path)

    if not path.exists():
        return ProjectConfig(name="litmus")

    return load_project(path)


# =============================================================================
# Station: load / get / list / save / create
# =============================================================================


def load_station(path: Path) -> StationConfig:
    """Load and validate a station YAML file."""
    return StationConfig.model_validate(_read_yaml(path))


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
) -> bool:
    """Save station configuration to YAML file."""
    search_paths = get_station_paths(project_root)

    # Station-specific: files may use location prefixes (e.g., lab_station1.yaml
    # for id=station1). Other entity types use exact ID matching via _resolve_save_path.
    target_file = None
    for stations_dir in search_paths:
        if stations_dir.exists():
            for f in stations_dir.glob("*.yaml"):
                if f.stem == station.id or f.stem.endswith(f"_{station.id}"):
                    target_file = f
                    break
            if target_file:
                break

    if target_file is None:
        target_file = _resolve_save_path(
            station.id,
            search_paths,
            "stations",
            project_root,
        )

    _write_model(target_file, station.model_dump(exclude_none=True))
    return True


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
    import os

    import platformdirs

    home = Path(os.environ.get("LITMUS_HOME", platformdirs.user_data_dir("litmus")))
    global_path = home / "stations" / f"{station_id}.yaml"
    if global_path.exists():
        try:
            return load_station(global_path)
        except (yaml.YAMLError, ValidationError, OSError):
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
    """Load and validate a fixture YAML file."""
    return FixtureConfig.model_validate(_read_yaml(path))


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
) -> bool:
    """Save fixture configuration to YAML file."""
    target_file = _resolve_save_path(
        fixture.id,
        get_fixture_paths(project_root),
        "fixtures",
        project_root,
    )
    _write_model(target_file, fixture.model_dump(exclude_none=True))
    return True


def create_fixture(
    fixture_id: str,
    name: str,
    product_id: str = "",
    product_revision: str = "",
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
        product_id=product_id or None,
        product_revision=product_revision or None,
        description=description or None,
    )
    _write_model(fixture_file, fixture.model_dump(exclude_none=True))
    return fixture


# =============================================================================
# Sequence: load / get / list / save / create
# =============================================================================


# =============================================================================
# Product: load / get / list / save / create
# =============================================================================

_MAX_INHERIT_DEPTH = 5


def _check_inherit_depth(depth: int, entity_type: str, path: Path) -> None:
    if depth > _MAX_INHERIT_DEPTH:
        raise ValueError(f"{entity_type} inheritance depth exceeds {_MAX_INHERIT_DEPTH} for {path}")


def load_product(path: Path, products_dir: Path | None = None) -> Product:
    """Load a product specification from YAML, resolving inheritance.

    Args:
        path: Path to the product YAML file.
        products_dir: Directory to search for base products.
    """
    if products_dir is None:
        candidate = path.parent.parent
        if candidate.name == "products" or any(candidate.iterdir()):
            products_dir = candidate
        else:
            products_dir = path.parent

    data = _load_product_with_inheritance(path, products_dir, seen=set(), depth=0)
    return Product.model_validate({"id": path.stem} | data)


def _load_product_with_inheritance(
    path: Path,
    products_dir: Path,
    seen: set[str],
    depth: int,
) -> dict[str, Any]:
    """Load raw YAML and recursively merge base products."""
    _check_inherit_depth(depth, "Product", path)

    data = _read_yaml(path)
    product_id = data.get("id", path.stem)
    base_ref = data.get("base")

    if not base_ref:
        return data

    if product_id in seen:
        raise ValueError(f"Circular product inheritance: {product_id!r} already in chain {seen}")
    seen.add(product_id)

    base_path = products_dir / f"{base_ref}.yaml"
    if not base_path.exists():
        for candidate in products_dir.rglob("*.yaml"):
            if candidate.name.startswith("_"):
                continue
            try:
                candidate_data = _read_yaml(candidate)
                if candidate_data.get("id") == base_ref:
                    base_path = candidate
                    break
            except (yaml.YAMLError, OSError):
                continue
    if not base_path.exists():
        raise ValueError(
            f"Base product {base_ref!r} not found (referenced by {product_id!r} in {path})"
        )

    base_data = _load_product_with_inheritance(base_path, products_dir, seen, depth + 1)
    return _merge_product_data(base_data, data)


def _merge_product_data(
    base: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Any]:
    """Merge base and variant product YAML with section-level override.

    Scalar fields: base provides defaults, variant overrides.
    Section fields (pins, signal_groups, characteristics): variant wins
    entirely if present, otherwise base is inherited. Deep-copied to
    avoid shared references.
    """
    # Start with base scalars, then overlay all variant keys
    merged: dict[str, Any] = {}
    scalar_keys = (
        "name",
        "description",
        "revision",
        "part_number",
        "datasheet",
        "schematic",
        "driver",
    )
    for key in scalar_keys:
        if key in base:
            merged[key] = base[key]
    merged.update(variant)

    # Inherit base sections not in variant; deep-copy all to avoid shared refs
    for section in ("pins", "signal_groups", "characteristics"):
        if section not in merged and section in base:
            merged[section] = copy.deepcopy(base[section])
        elif section in merged:
            merged[section] = copy.deepcopy(merged[section])

    return merged


def get_product(
    product_id: str,
    *,
    project_root: Path | None = None,
) -> Product | None:
    """Load a Product model by ID."""
    for products_dir in get_product_paths(project_root):
        if not products_dir.exists():
            continue
        # Try flat file first (canonical convention)
        flat_file = products_dir / f"{product_id}.yaml"
        if flat_file.exists():
            try:
                return load_product(flat_file)
            except _YAML_LOAD_ERRORS:
                pass
        # Fallback: search by filename in subdirectories
        for yaml_file in products_dir.rglob(f"{product_id}.yaml"):
            if yaml_file.name.startswith("_"):
                continue
            try:
                return load_product(yaml_file)
            except _YAML_LOAD_ERRORS:
                continue
    return None


def list_products(*, project_root: Path | None = None) -> list[Product]:
    """List all available products as Product models."""
    products: list[Product] = []
    seen_ids: set[str] = set()
    for products_dir in get_product_paths(project_root):
        if not products_dir.exists():
            continue
        for yaml_file in sorted(products_dir.rglob("*.yaml")):
            if yaml_file.name.startswith("_"):
                continue
            try:
                product = load_product(yaml_file)
            except _YAML_LOAD_ERRORS:
                continue
            if product.id in seen_ids:
                continue
            seen_ids.add(product.id)
            products.append(product)
    return products


def save_product(
    product: Product,
    *,
    project_root: Path | None = None,
) -> bool:
    """Save product specification to YAML file."""
    target_file = None
    for products_dir in get_product_paths(project_root):
        if not products_dir.exists():
            continue
        # Preserve existing file location (flat or nested)
        for yaml_file in products_dir.rglob("*.yaml"):
            if yaml_file.name.startswith("_"):
                continue
            try:
                data = _read_yaml(yaml_file)
                if data.get("id") == product.id:
                    target_file = yaml_file
                    break
            except (yaml.YAMLError, OSError):
                continue
        if target_file:
            break

    if target_file is None:
        root = _resolve_root(project_root)
        products_dir = root / "products"
        products_dir.mkdir(exist_ok=True)
        target_file = products_dir / f"{product.id}.yaml"

    _write_model(target_file, product.model_dump(exclude_none=True))
    return True


def create_product(
    product_id: str,
    name: str,
    description: str = "",
    *,
    project_root: Path | None = None,
) -> Product | None:
    """Create a new product YAML file.

    Returns Product if successful, None if product already exists.
    """
    root = _resolve_root(project_root)
    products_dir = root / "products"
    products_dir.mkdir(exist_ok=True)

    target_file = products_dir / f"{product_id}.yaml"
    if target_file.exists():
        return None

    product = Product(id=product_id, name=name, description=description or None)
    _write_model(target_file, product.model_dump(exclude_none=True))
    return product


# =============================================================================
# Product Manifest: load / save
# =============================================================================


def load_manifest(path: Path) -> ProductManifest:
    """Load and validate a product manifest YAML file."""
    return ProductManifest.model_validate(_read_yaml(path))


def save_manifest(manifest: ProductManifest, path: Path) -> None:
    """Save a product manifest to YAML."""
    _write_model(path, manifest.model_dump(exclude_none=True))


# =============================================================================
# Catalog: load / get / list / save / create  (+ helpers)
# =============================================================================


def load_catalog_entry(
    path: Path,
    catalog_dir: Path | None = None,
) -> InstrumentCatalogEntry:
    """Load a single catalog entry from a YAML file, resolving inheritance."""
    if catalog_dir is None:
        catalog_dir = path.parent
    data = _load_catalog_with_inheritance(path, catalog_dir, seen=set(), depth=0)
    return _build_catalog_entry(data, path)


def _load_catalog_with_inheritance(
    path: Path,
    catalog_dir: Path,
    seen: set[str],
    depth: int,
) -> dict[str, Any]:
    """Load raw YAML and recursively merge base catalog entries."""
    _check_inherit_depth(depth, "Catalog", path)

    data = _read_yaml(path)

    entry_id = data.get("id", path.stem)
    base_ref = data.get("base")

    if not base_ref:
        return data

    if entry_id in seen:
        raise ValueError(f"Circular catalog inheritance: {entry_id!r} already in chain {seen}")
    seen.add(entry_id)

    base_path = path.parent / f"{base_ref}.yaml"
    if not base_path.exists():
        base_path = catalog_dir / f"{base_ref}.yaml"
    if not base_path.exists():
        raise ValueError(
            f"Base catalog entry {base_ref!r} not found (referenced by {entry_id!r} in {path})"
        )

    base_data = _load_catalog_with_inheritance(base_path, catalog_dir, seen, depth + 1)
    return _merge_catalog_data(base_data, data)


def _merge_catalog_data(
    base: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Any]:
    """Merge base and variant catalog YAML dicts."""
    merged_entry: dict[str, Any] = {}

    for key in ("manufacturer", "type", "name", "description"):
        if key in base:
            merged_entry[key] = base[key]

    merged_entry.update(variant)

    for section in ("channels", "attributes", "interfaces"):
        if section not in variant and section in base:
            merged_entry[section] = base[section]

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

            if isinstance(v_param, dict) and "specs" in v_param:
                b_param = b_section[param_name]
                if not isinstance(b_param, dict):
                    b_section[param_name] = copy.deepcopy(v_param)
                    continue
                b_specs = b_param.get("specs", [])
                b_param["specs"] = b_specs + copy.deepcopy(v_param["specs"])
                for k, v in v_param.items():
                    if k != "specs":
                        b_param[k] = copy.deepcopy(v)
            else:
                b_section[param_name] = copy.deepcopy(v_param)


def _build_catalog_entry(data: dict[str, Any], path: Path) -> InstrumentCatalogEntry:
    """Build an InstrumentCatalogEntry from merged raw YAML data."""
    defaults: dict[str, Any] = {"id": path.stem}
    if not data.get("name"):
        mfr = data.get("manufacturer", "")
        model = data.get("model", "")
        defaults["name"] = f"{mfr} {model}".strip() or path.stem
    return InstrumentCatalogEntry.model_validate(defaults | data)


def load_catalog_from_directory(catalog_dir: Path) -> dict[str, InstrumentCatalogEntry]:
    """Load all catalog entries from a directory."""
    if not catalog_dir.exists():
        return {}

    entries: dict[str, InstrumentCatalogEntry] = {}
    for path in _iter_catalog_files(catalog_dir):
        try:
            entry = load_catalog_entry(path, catalog_dir=catalog_dir)
            if entry.id:
                entries[entry.id] = entry
        except _YAML_LOAD_ERRORS as exc:
            warnings.warn(
                f"catalog: failed to load {path.name}: {exc}",
                stacklevel=2,
            )
            continue

    return entries


def _skip_catalog_file(path: Path) -> bool:
    """Return True for catalog YAML files that should never be loaded directly."""
    return path.name.startswith("_") or ".variants." in path.name


def _iter_catalog_files(cat_dir: Path) -> Iterator[Path]:
    """Yield all loadable catalog YAML files in a directory tree, sorted."""
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
    """Resolve a catalog reference ID to a catalog entry."""
    for cat_dir in find_catalog_dirs(project_root=project_root):
        for path in _catalog_file_candidates(cat_dir, catalog_ref):
            try:
                return load_catalog_entry(path, catalog_dir=cat_dir)
            except _YAML_LOAD_ERRORS as exc:
                warnings.warn(
                    f"catalog: failed to load {path.name}: {exc}",
                    stacklevel=2,
                )
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

    for cat_dir in find_catalog_dirs(project_root=project_root):
        for path in _iter_catalog_files(cat_dir):
            try:
                entry = load_catalog_entry(path, catalog_dir=cat_dir)
            except _YAML_LOAD_ERRORS as exc:
                warnings.warn(
                    f"catalog: failed to load {path.name}: {exc}",
                    stacklevel=2,
                )
                continue
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
    """Get a catalog entry by ID or type.

    Tries direct filename match first, then rglob by filename (fast paths),
    then falls back to a full directory scan for type-based lookup.
    Project-local catalog entries take precedence over bundled ones.
    """
    for cat_dir in find_catalog_dirs(project_root=project_root):
        for path in _catalog_file_candidates(cat_dir, catalog_id):
            try:
                return load_catalog_entry(path, catalog_dir=cat_dir)
            except _YAML_LOAD_ERRORS as exc:
                warnings.warn(
                    f"catalog: failed to load {path.name}: {exc}",
                    stacklevel=2,
                )
                continue

        # Slow path: full scan for type-based lookup
        for entry in load_catalog_from_directory(cat_dir).values():
            if entry.type == catalog_id:
                return entry
    return None


def list_catalog_entries(
    *,
    project_root: Path | None = None,
) -> list[InstrumentCatalogEntry]:
    """List all catalog entries across all catalog directories."""
    all_entries: list[InstrumentCatalogEntry] = []
    seen_ids: set[str] = set()
    for cat_dir in find_catalog_dirs(project_root=project_root):
        for entry in load_catalog_from_directory(cat_dir).values():
            if not entry.id or entry.id in seen_ids:
                continue
            seen_ids.add(entry.id)
            all_entries.append(entry)
    return all_entries


def save_catalog_entry(
    entry: InstrumentCatalogEntry,
    *,
    project_root: Path | None = None,
) -> bool:
    """Save a catalog entry to catalog/."""
    root = _resolve_root(project_root)
    catalog_dir = root / "catalog"
    catalog_dir.mkdir(exist_ok=True)

    target_file = catalog_dir / f"{entry.id}.yaml"
    _write_model(target_file, entry.model_dump(exclude_none=True))
    return True


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
    """Load and validate an instrument asset YAML file."""
    return InstrumentAssetFile.model_validate(_read_yaml(path))


def load_instrument_files(instruments_dir: Path) -> dict[str, InstrumentAssetFile]:
    """Load all instrument asset files from a directory."""
    if not instruments_dir.exists():
        return {}

    instruments: dict[str, InstrumentAssetFile] = {}
    for path in instruments_dir.glob("*.yaml"):
        try:
            asset = load_instrument_asset(path)
            instruments[asset.id] = asset
        except (yaml.YAMLError, ValidationError, OSError):
            pass
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
) -> bool:
    """Save an instrument asset file.

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
    return True


# =============================================================================
# Station Type: load / save
# =============================================================================


def save_station_type(
    station_type: StationType,
    *,
    project_root: Path | None = None,
) -> bool:
    """Save station type YAML to stations/types/{id}.yaml."""
    root = _resolve_root(project_root)
    types_dir = root / "stations" / "types"
    types_dir.mkdir(parents=True, exist_ok=True)

    target_file = types_dir / f"{station_type.id}.yaml"
    _write_model(target_file, station_type.model_dump(exclude_none=True))
    return True


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
        return StationType.model_validate(_read_yaml(yaml_file))
    except (yaml.YAMLError, ValidationError, OSError):
        return None


# =============================================================================
# YAML formatting — file I/O wrappers around dump_yaml
# =============================================================================


def format_file(path: Path) -> str:
    """Load a YAML file and return it formatted with Litmus conventions."""
    plain = _read_yaml(path)
    return dump_yaml(plain)


def format_file_inplace(path: Path) -> bool:
    """Format a YAML file in-place. Returns True if changed."""
    original = path.read_text()
    formatted = format_file(path)
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
    "product": load_product,
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
                try:
                    return _RANGE_EXPANDERS[key](value)
                except Exception as exc:
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
    # Products
    "characteristics",
    "vectors",
    "limits",
    # Sequences
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


def _fmt_make_yaml() -> YAML:
    ry = YAML()
    ry.default_flow_style = False
    ry.width = 120
    ry.indent(mapping=2, sequence=2, offset=0)
    return ry


def dump_yaml(data: dict[str, Any]) -> str:
    """Dump a dict to a YAML string with Litmus conventions."""
    styled = _fmt_apply_style(data)
    ry = _fmt_make_yaml()
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


def normalize_instrument_type(raw: str) -> str:
    """Lowercase, strip, and resolve aliases to canonical type."""
    normalized = raw.strip().lower()
    return _INSTRUMENT_TYPE_ALIASES.get(normalized, normalized)


def check_instrument_types(
    instruments: dict[str, dict],
) -> tuple[dict[str, dict], list[str]]:
    """Normalize instrument types and warn about unknown ones.

    Args:
        instruments: Dict of instrument name → config dict. Each config
            must have a ``type`` key.

    Returns:
        ``(instruments with normalized types, list of warning strings)``
    """
    msgs: list[str] = []
    for name, config in instruments.items():
        if "type" not in config:
            continue
        original = config["type"]
        config["type"] = normalize_instrument_type(original)
        if config["type"] != original:
            msgs.append(f"instruments.{name}: Normalized type '{original}' → '{config['type']}'")
        if config["type"] not in _KNOWN_INSTRUMENT_TYPES:
            known = _known_catalog_types()
            if known and config["type"] not in known:
                msgs.append(
                    f"instruments.{name}: Type '{config['type']}' "
                    "not in InstrumentType enum or any catalog entry — "
                    "this is fine for custom types, but check for typos."
                )
    return instruments, msgs


def _known_catalog_types() -> set[str]:
    """Collect instrument types present in loaded catalog entries."""
    try:
        types: set[str] = set()
        for cat_dir in find_catalog_dirs():
            for entry in load_catalog_from_directory(cat_dir).values():
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
