"""Centralized persistence for all Litmus YAML config files.

Every consumer should call these functions instead of raw yaml.safe_load.
ONE module for all load/save/list/get/create operations.

Public interface — four verbs per entity:
    get_*(id) → Model | None       # Lookup by ID across search paths
    list_*() → list[Model]         # Discover all across search paths
    save_*(model) → bool           # Write model to YAML
    create_*(...) → Model | None   # Create new, None if exists

Plus low-level load_*(path) for callers that already have a file path.
"""

from __future__ import annotations

import copy
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from litmus.catalog.models import InstrumentCatalogEntry
from litmus.config.models import FixtureConfig, TestSequenceConfig
from litmus.products.models import Product
from litmus.schemas import (
    InstrumentAssetFile,
    ProjectConfig,
    StationConfig,
)
from litmus.utils.paths import (
    get_fixture_paths,
    get_instrument_paths,
    get_sequence_paths,
    get_station_paths,
)

# =============================================================================
# Internal helpers
# =============================================================================


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file and return parsed dict (or empty dict)."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def find_yaml_files(
    search_paths: list[Path],
    *,
    prefix_skip: str = "_",
    pattern: str = "*.yaml",
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Iterate over YAML files in search paths, loading their contents.

    Yields:
        Tuples of (file_path, parsed_data) for each valid YAML file.
    """
    for search_dir in search_paths:
        if not search_dir.exists():
            continue
        for yaml_file in search_dir.glob(pattern):
            if prefix_skip and yaml_file.name.startswith(prefix_skip):
                continue
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                    if data is not None:
                        yield yaml_file, data
            except Exception:
                continue


def find_or_create_path(
    resource_id: str,
    search_dirs: list[Path],
    filename: str | None = None,
) -> Path | None:
    """Find an existing file or determine where to create a new one."""
    if filename is None:
        filename = f"{resource_id}.yaml"

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        existing = search_dir / filename
        if existing.exists():
            return existing

    for search_dir in search_dirs:
        if search_dir.exists():
            return search_dir / filename

    return None


def _write_model(path: Path, model_data: dict[str, Any]) -> None:
    """Write a model dict to YAML using Litmus formatting conventions."""
    from litmus.config.fmt import dump_yaml

    path.write_text(dump_yaml(model_data))


# =============================================================================
# Project
# =============================================================================


def load_project(path: Path) -> ProjectConfig:
    """Load and validate a litmus.yaml project config file."""
    return ProjectConfig.model_validate(_read_yaml(path))


# =============================================================================
# Station: load / get / list / save / create
# =============================================================================


def load_station(path: Path) -> StationConfig:
    """Load and validate a station YAML file."""
    return StationConfig.model_validate(_read_yaml(path))


def get_station(station_id: str) -> StationConfig | None:
    """Load station configuration by ID."""
    for yaml_file, _ in find_yaml_files(get_station_paths()):
        try:
            station = load_station(yaml_file)
            if station.id == station_id:
                return station
        except Exception:
            continue
    return None


def list_stations() -> list[StationConfig]:
    """List all available stations."""
    stations: list[StationConfig] = []
    for yaml_file, _ in find_yaml_files(get_station_paths()):
        try:
            stations.append(load_station(yaml_file))
        except Exception:
            continue
    return stations


def save_station(station: StationConfig) -> bool:
    """Save station configuration to YAML file."""
    search_paths = get_station_paths()

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
        for stations_dir in search_paths:
            if stations_dir.exists():
                target_file = stations_dir / f"{station.id}.yaml"
                break

    if target_file is None:
        return False

    _write_model(target_file, station.model_dump(exclude_none=True))
    return True


def create_station(
    station_id: str, name: str, location: str = "", description: str = "",
) -> StationConfig | None:
    """Create a new station configuration file.

    Returns StationConfig if successful, None if station already exists.
    """
    stations_dir = Path.cwd() / "stations"
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


def get_fixture(fixture_id: str) -> FixtureConfig | None:
    """Load fixture configuration by ID."""
    for yaml_file, _ in find_yaml_files(get_fixture_paths()):
        try:
            fixture = load_fixture(yaml_file)
            if fixture.id == fixture_id or yaml_file.stem == fixture_id:
                return fixture
        except Exception:
            continue
    return None


def list_fixtures() -> list[FixtureConfig]:
    """List all available fixtures."""
    fixtures: list[FixtureConfig] = []
    seen_ids: set[str] = set()
    for yaml_file, _ in find_yaml_files(get_fixture_paths()):
        try:
            fixture = load_fixture(yaml_file)
        except Exception:
            continue
        if fixture.id in seen_ids:
            continue
        seen_ids.add(fixture.id)
        fixtures.append(fixture)
    return fixtures


def save_fixture(fixture: FixtureConfig) -> bool:
    """Save fixture configuration to YAML file."""
    search_paths = get_fixture_paths()

    target_file = None
    for fixtures_dir in search_paths:
        if fixtures_dir.exists():
            existing = fixtures_dir / f"{fixture.id}.yaml"
            if existing.exists():
                target_file = existing
                break

    if target_file is None:
        for fixtures_dir in search_paths:
            if fixtures_dir.exists():
                target_file = fixtures_dir / f"{fixture.id}.yaml"
                break

    if target_file is None:
        fixtures_dir = Path.cwd() / "fixtures"
        fixtures_dir.mkdir(exist_ok=True)
        target_file = fixtures_dir / f"{fixture.id}.yaml"

    _write_model(target_file, fixture.model_dump(exclude_none=True))
    return True


def create_fixture(
    fixture_id: str,
    name: str,
    product_id: str = "",
    product_revision: str = "",
    description: str = "",
) -> FixtureConfig | None:
    """Create a new fixture configuration file.

    Returns FixtureConfig if successful, None if fixture already exists.
    """
    fixtures_dir = Path.cwd() / "fixtures"
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


def load_sequence(path: Path) -> TestSequenceConfig:
    """Load and validate a sequence YAML file."""
    return TestSequenceConfig.model_validate(_read_yaml(path))


def get_sequence(sequence_id: str) -> TestSequenceConfig | None:
    """Load sequence configuration by ID."""
    for yaml_file, _ in find_yaml_files(get_sequence_paths()):
        try:
            seq = load_sequence(yaml_file)
            if seq.id == sequence_id or yaml_file.stem == sequence_id:
                return seq
        except Exception:
            continue
    return None


def list_sequences() -> list[TestSequenceConfig]:
    """List all available sequences."""
    sequences: list[TestSequenceConfig] = []
    for yaml_file, _ in find_yaml_files(get_sequence_paths()):
        try:
            sequences.append(load_sequence(yaml_file))
        except Exception:
            continue
    return sequences


def save_sequence(sequence: TestSequenceConfig) -> bool:
    """Save sequence configuration to YAML file."""
    search_paths = get_sequence_paths()

    target_file = None
    for seq_dir in search_paths:
        if seq_dir.exists():
            existing = seq_dir / f"{sequence.id}.yaml"
            if existing.exists():
                target_file = existing
                break

    if target_file is None:
        for seq_dir in search_paths:
            if seq_dir.exists():
                target_file = seq_dir / f"{sequence.id}.yaml"
                break

    if target_file is None:
        sequences_dir = Path.cwd() / "sequences"
        sequences_dir.mkdir(exist_ok=True)
        target_file = sequences_dir / f"{sequence.id}.yaml"

    _write_model(target_file, sequence.model_dump(exclude_none=True))
    return True


def create_sequence(
    sequence_id: str,
    name: str,
    product_family: str = "",
    test_phase: str = "validation",
    description: str = "",
) -> TestSequenceConfig | None:
    """Create a new sequence configuration file.

    Returns TestSequenceConfig if successful, None if sequence already exists.
    """
    sequences_dir = Path.cwd() / "sequences"
    sequences_dir.mkdir(exist_ok=True)

    sequence_file = sequences_dir / f"{sequence_id}.yaml"
    if sequence_file.exists():
        return None

    seq = TestSequenceConfig(
        id=sequence_id,
        name=name,
        test_phase=test_phase,
        product_family=product_family or None,
        description=description or None,
    )
    _write_model(sequence_file, seq.model_dump(exclude_none=True))
    return seq


# =============================================================================
# Product: load / get / list / save / create
# =============================================================================

_MAX_PRODUCT_INHERIT_DEPTH = 5


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
    data.setdefault("id", path.stem)
    return Product.model_validate(data)


def _load_product_with_inheritance(
    path: Path, products_dir: Path, seen: set[str], depth: int,
) -> dict[str, Any]:
    """Load raw YAML and recursively merge base products."""
    if depth > _MAX_PRODUCT_INHERIT_DEPTH:
        raise ValueError(
            f"Product inheritance depth exceeds {_MAX_PRODUCT_INHERIT_DEPTH} for {path}"
        )

    data = _read_yaml(path)
    product_id = data.get("id", path.stem)
    base_ref = data.get("base")

    if not base_ref:
        return data

    if product_id in seen:
        raise ValueError(
            f"Circular product inheritance: {product_id!r} already in chain {seen}"
        )
    seen.add(product_id)

    base_path = products_dir / base_ref / "spec.yaml"
    if not base_path.exists():
        base_path = products_dir / f"{base_ref}.yaml"
    if not base_path.exists():
        raise ValueError(
            f"Base product {base_ref!r} not found "
            f"(referenced by {product_id!r} in {path})"
        )

    base_data = _load_product_with_inheritance(base_path, products_dir, seen, depth + 1)
    return _merge_product_data(base_data, data)


def _merge_product_data(
    base: dict[str, Any], variant: dict[str, Any],
) -> dict[str, Any]:
    """Merge base and variant product YAML with section-level override."""
    merged: dict[str, Any] = {}

    for key in ("name", "description", "revision", "part_number", "datasheet", "schematic"):
        if key in base:
            merged[key] = base[key]

    merged.update(variant)

    for section in ("pins", "signal_groups", "characteristics"):
        if section in variant:
            merged[section] = variant[section]
        elif section in base:
            merged[section] = base[section]

    return merged


def load_products_from_directory(specs_dir: Path) -> dict[str, Product]:
    """Load all product specifications from a directory."""
    products: dict[str, Product] = {}
    for path in specs_dir.glob("*.yaml"):
        if path.name.startswith("_"):
            continue
        try:
            product = load_product(path, products_dir=specs_dir)
            products[product.id] = product
        except Exception as e:
            warnings.warn(f"Failed to load product from {path}: {e}")
    return products


def _get_product_paths() -> list[Path]:
    """Get search paths for product folders."""
    cwd = Path.cwd()
    return [cwd / "products", cwd / "demo" / "products"]


def get_product(product_id: str) -> Product | None:
    """Load a Product model by ID."""
    for products_dir in _get_product_paths():
        if not products_dir.exists():
            continue
        # Direct lookup by folder name
        spec_file = products_dir / product_id / "spec.yaml"
        if spec_file.exists():
            try:
                return load_product(spec_file)
            except Exception:
                pass
        # Fallback: search all folders
        for product_folder in products_dir.iterdir():
            if not product_folder.is_dir():
                continue
            spec_file = product_folder / "spec.yaml"
            if spec_file.exists():
                try:
                    product = load_product(spec_file)
                    if product.id == product_id:
                        return product
                except Exception:
                    continue
    return None


def list_products() -> list[Product]:
    """List all available products as Product models."""
    products: list[Product] = []
    seen_ids: set[str] = set()
    for products_dir in _get_product_paths():
        if not products_dir.exists():
            continue
        for product_folder in products_dir.iterdir():
            if not product_folder.is_dir():
                continue
            spec_file = product_folder / "spec.yaml"
            if not spec_file.exists():
                continue
            try:
                product = load_product(spec_file)
                if product.id in seen_ids:
                    continue
                seen_ids.add(product.id)
                products.append(product)
            except Exception:
                continue
    return products


def save_product(product: Product) -> bool:
    """Save product specification to YAML file."""
    search_paths = _get_product_paths()

    target_file = None
    for products_dir in search_paths:
        product_folder = products_dir / product.id
        if product_folder.exists():
            target_file = product_folder / "spec.yaml"
            break

    if target_file is None:
        products_dir = Path.cwd() / "products"
        products_dir.mkdir(exist_ok=True)
        product_folder = products_dir / product.id
        product_folder.mkdir(exist_ok=True)
        target_file = product_folder / "spec.yaml"

    _write_model(target_file, product.model_dump(exclude_none=True))
    return True


def create_product(
    product_id: str, name: str, description: str = "",
) -> Product | None:
    """Create a new product folder with spec.yaml.

    Returns Product if successful, None if product already exists.
    """
    from litmus.products.folder import ProductFolder

    products_dir = Path.cwd() / "products"
    products_dir.mkdir(exist_ok=True)

    try:
        folder = ProductFolder.create(
            base_path=products_dir,
            product_id=product_id,
            name=name,
            description=description or None,
        )
    except FileExistsError:
        return None

    spec = folder.load_spec()
    if spec:
        return spec

    # Fallback: construct minimal Product
    return Product(id=product_id, name=name, description=description or None)


# =============================================================================
# Catalog: load / get / list / save / create  (+ helpers)
# =============================================================================

_MAX_CATALOG_INHERIT_DEPTH = 5


def load_catalog_entry(
    path: Path, catalog_dir: Path | None = None,
) -> InstrumentCatalogEntry:
    """Load a single catalog entry from a YAML file, resolving inheritance."""
    if catalog_dir is None:
        catalog_dir = path.parent
    data = _load_catalog_with_inheritance(path, catalog_dir, seen=set(), depth=0)
    return _build_catalog_entry(data, path)


def _load_catalog_with_inheritance(
    path: Path, catalog_dir: Path, seen: set[str], depth: int,
) -> dict[str, Any]:
    """Load raw YAML and recursively merge base catalog entries."""
    if depth > _MAX_CATALOG_INHERIT_DEPTH:
        raise ValueError(
            f"Catalog inheritance depth exceeds {_MAX_CATALOG_INHERIT_DEPTH} for {path}"
        )

    with open(path) as f:
        data = yaml.safe_load(f)

    entry_id = data.get("id", path.stem)
    base_ref = data.get("base")

    if not base_ref:
        return data

    if entry_id in seen:
        raise ValueError(
            f"Circular catalog inheritance: {entry_id!r} already in chain {seen}"
        )
    seen.add(entry_id)

    base_path = path.parent / f"{base_ref}.yaml"
    if not base_path.exists():
        base_path = catalog_dir / f"{base_ref}.yaml"
    if not base_path.exists():
        raise ValueError(
            f"Base catalog entry {base_ref!r} not found "
            f"(referenced by {entry_id!r} in {path})"
        )

    base_data = _load_catalog_with_inheritance(base_path, catalog_dir, seen, depth + 1)
    return _merge_catalog_data(base_data, data)


def _merge_catalog_data(
    base: dict[str, Any], variant: dict[str, Any],
) -> dict[str, Any]:
    """Merge base and variant catalog YAML dicts."""
    base_entry = dict(base)
    variant_entry = dict(variant)
    merged_entry: dict[str, Any] = {}

    for key in ("manufacturer", "type", "name", "description"):
        if key in base_entry:
            merged_entry[key] = base_entry[key]

    merged_entry.update(variant_entry)

    for section in ("channels", "attributes", "interfaces"):
        if section not in variant_entry and section in base_entry:
            merged_entry[section] = base_entry[section]

    base_caps = base_entry.get("capabilities") or []
    variant_caps = variant_entry.get("capabilities") or []

    if variant_caps and base_caps:
        merged_entry["capabilities"] = _merge_capabilities(base_caps, variant_caps)
    elif not variant_caps and base_caps:
        merged_entry["capabilities"] = base_caps

    return merged_entry


def _cap_key(cap: dict[str, Any]) -> tuple[str, str]:
    return (cap.get("function", ""), cap.get("direction", ""))


def _merge_capabilities(
    base_caps: list[dict[str, Any]], variant_caps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge variant capabilities into base capabilities by (function, direction)."""
    merged: list[dict[str, Any]] = [copy.deepcopy(c) for c in base_caps]
    base_index: dict[tuple[str, str], int] = {}
    for i, cap in enumerate(base_caps):
        key = _cap_key(cap)
        if key not in base_index:
            base_index[key] = i

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
    parsed: dict[str, Any] = dict(data)
    parsed.setdefault("id", path.stem)
    if not parsed.get("name"):
        mfr = parsed.get("manufacturer", "")
        model = parsed.get("model", "")
        parsed["name"] = f"{mfr} {model}".strip() or path.stem
    parsed["model"] = str(parsed.get("model", ""))
    return InstrumentCatalogEntry.model_validate(parsed)


def load_catalog_from_directory(catalog_dir: Path) -> dict[str, InstrumentCatalogEntry]:
    """Load all catalog entries from a directory."""
    if not catalog_dir.exists():
        return {}

    entries: dict[str, InstrumentCatalogEntry] = {}
    for path in sorted(catalog_dir.rglob("*.yaml")):
        if path.name.startswith("_") or ".variants." in path.name:
            continue
        try:
            entry = load_catalog_entry(path, catalog_dir=catalog_dir)
            entries[entry.id] = entry
        except Exception as exc:
            warnings.warn(
                f"catalog: failed to load {path.name}: {exc}",
                stacklevel=2,
            )
            continue

    return entries


def find_catalog_dirs() -> list[Path]:
    """Find catalog directories by searching standard locations."""
    dirs = []
    for candidate in [Path.cwd() / "catalog", Path.cwd() / "demo" / "catalog"]:
        if candidate.is_dir():
            dirs.append(candidate)
    return dirs


def resolve_catalog_ref(catalog_ref: str) -> InstrumentCatalogEntry | None:
    """Resolve a catalog reference ID to a catalog entry."""
    for cat_dir in find_catalog_dirs():
        # Try direct filename match first
        direct_path = cat_dir / f"{catalog_ref}.yaml"
        if direct_path.exists():
            try:
                return load_catalog_entry(direct_path, catalog_dir=cat_dir)
            except Exception as exc:
                warnings.warn(
                    f"catalog: failed to load {direct_path.name}: {exc}",
                    stacklevel=2,
                )
                return None

        # Fallback: search subdirectories
        for path in cat_dir.rglob(f"{catalog_ref}.yaml"):
            if path.name.startswith("_") or ".variants." in path.name:
                continue
            try:
                return load_catalog_entry(path, catalog_dir=cat_dir)
            except Exception as exc:
                warnings.warn(
                    f"catalog: failed to load {path.name}: {exc}",
                    stacklevel=2,
                )
                return None

        # Last resort: search all files for matching ID
        for path in cat_dir.rglob("*.yaml"):
            if path.name.startswith("_") or ".variants." in path.name:
                continue
            try:
                entry = load_catalog_entry(path, catalog_dir=cat_dir)
                if entry.id == catalog_ref:
                    return entry
            except Exception as exc:
                warnings.warn(
                    f"catalog: failed to load {path.name}: {exc}",
                    stacklevel=2,
                )
                continue

    return None


def find_by_model(
    manufacturer: str, model: str,
) -> InstrumentCatalogEntry | None:
    """Find a catalog entry by manufacturer and model name (case-insensitive)."""
    mfr_lower = manufacturer.lower()
    model_lower = model.lower()

    for cat_dir in find_catalog_dirs():
        for path in sorted(cat_dir.rglob("*.yaml")):
            if path.name.startswith("_") or ".variants." in path.name:
                continue
            try:
                entry = load_catalog_entry(path, catalog_dir=cat_dir)
            except Exception:
                continue
            if (
                entry.manufacturer
                and entry.manufacturer.lower() == mfr_lower
                and entry.model
                and entry.model.lower() == model_lower
            ):
                return entry

    return None


def get_catalog_entry(catalog_id: str) -> InstrumentCatalogEntry | None:
    """Get a catalog entry by ID or type.

    Searches all catalog directories.
    """
    for cat_dir in find_catalog_dirs():
        for entry_id, entry in load_catalog_from_directory(cat_dir).items():
            if entry.id == catalog_id or entry.type == catalog_id:
                return entry
    return None


def list_catalog_entries() -> list[InstrumentCatalogEntry]:
    """List all catalog entries across all catalog directories."""
    all_entries: list[InstrumentCatalogEntry] = []
    seen_ids: set[str] = set()
    for cat_dir in find_catalog_dirs():
        for entry_id, entry in load_catalog_from_directory(cat_dir).items():
            if entry.id in seen_ids:
                continue
            seen_ids.add(entry.id)
            all_entries.append(entry)
    return all_entries


def save_catalog_entry(entry: InstrumentCatalogEntry) -> bool:
    """Save a catalog entry to catalog/."""
    catalog_dir = Path.cwd() / "catalog"
    catalog_dir.mkdir(exist_ok=True)

    target_file = catalog_dir / f"{entry.id}.yaml"
    _write_model(target_file, entry.model_dump(exclude_none=True))
    return True


def create_catalog_entry(
    instrument_type: str,
    name: str,
    description: str = "",
    manufacturer: str = "User",
) -> InstrumentCatalogEntry | None:
    """Create a new catalog entry in catalog/.

    Returns InstrumentCatalogEntry if successful, None if already exists.
    """
    catalog_dir = Path.cwd() / "catalog"
    catalog_dir.mkdir(exist_ok=True)

    catalog_file = catalog_dir / f"{instrument_type}.yaml"
    if catalog_file.exists():
        return None

    entry = InstrumentCatalogEntry.model_validate({
        "id": instrument_type,
        "type": instrument_type,
        "manufacturer": manufacturer,
        "model": name,
        "name": name,
        "description": description or None,
        "capabilities": [],
    })

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
        except Exception:
            pass
    return instruments


def get_instrument_asset(instrument_id: str) -> InstrumentAssetFile | None:
    """Load a single instrument asset file by ID."""
    for instruments_dir in get_instrument_paths():
        if not instruments_dir.exists():
            continue
        for yaml_file in instruments_dir.glob("*.yaml"):
            try:
                asset = load_instrument_asset(yaml_file)
                if asset.id == instrument_id:
                    return asset
            except Exception:
                continue
    return None


def list_instrument_assets() -> list[InstrumentAssetFile]:
    """List all instrument asset files."""
    assets: list[InstrumentAssetFile] = []
    seen_ids: set[str] = set()
    for instruments_dir in get_instrument_paths():
        if not instruments_dir.exists():
            continue
        for yaml_file in instruments_dir.glob("*.yaml"):
            try:
                asset = load_instrument_asset(yaml_file)
            except Exception:
                continue
            if asset.id in seen_ids:
                continue
            seen_ids.add(asset.id)
            assets.append(asset)
    return assets


def save_instrument_asset(asset: InstrumentAssetFile) -> bool:
    """Save an instrument asset file."""
    search_paths = get_instrument_paths()

    target_file = None
    for instruments_dir in search_paths:
        if instruments_dir.exists():
            existing = instruments_dir / f"{asset.id}.yaml"
            if existing.exists():
                target_file = existing
                break

    if target_file is None:
        for instruments_dir in search_paths:
            if instruments_dir.exists():
                target_file = instruments_dir / f"{asset.id}.yaml"
                break

    if target_file is None:
        instruments_dir = Path.cwd() / "instruments"
        instruments_dir.mkdir(exist_ok=True)
        target_file = instruments_dir / f"{asset.id}.yaml"

    _write_model(target_file, asset.model_dump(exclude_none=True))
    return True


# =============================================================================
# Station Type (raw YAML — no Pydantic model yet)
# =============================================================================


def save_station_type(type_id: str, data: dict) -> bool:
    """Save station type YAML to stations/types/{type_id}.yaml."""
    types_dir = Path.cwd() / "stations" / "types"
    types_dir.mkdir(parents=True, exist_ok=True)

    target_file = types_dir / f"{type_id}.yaml"
    with open(target_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    return True


def load_station_type(type_id: str) -> dict | None:
    """Load station type by ID (raw YAML — no Pydantic model yet)."""
    search_paths = [
        Path.cwd() / "stations" / "types",
        Path.cwd() / "demo" / "stations" / "types",
    ]
    for types_dir in search_paths:
        yaml_file = types_dir / f"{type_id}.yaml"
        if not yaml_file.exists():
            continue
        try:
            with open(yaml_file) as f:
                return yaml.safe_load(f)
        except Exception:
            return None
    return None
