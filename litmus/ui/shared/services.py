"""Data services for UI - business logic wrappers over litmus.store.

NO direct yaml.safe_load or Path I/O here — all persistence goes through litmus.store.
"""

from typing import Literal

from litmus.instruments.loader import resolve_station_instruments
from litmus.matching import service as matching_service
from litmus.products.folder import ProductFolder
from litmus.store import (
    create_catalog_entry as store_create_catalog_entry,
)
from litmus.store import (
    create_fixture as store_create_fixture,
)
from litmus.store import (
    create_product as store_create_product,
)
from litmus.store import (
    create_sequence as store_create_sequence,
)
from litmus.store import (
    create_station as store_create_station,
)
from litmus.store import (
    find_catalog_dirs,
    load_catalog_from_directory,
    load_instrument_files,
)
from litmus.store import (
    get_catalog_entry as store_get_catalog_entry,
)
from litmus.store import (
    get_fixture as store_get_fixture,
)
from litmus.store import (
    get_instrument_asset as store_get_instrument_asset,
)
from litmus.store import (
    get_product as store_get_product,
)
from litmus.store import (
    get_sequence as store_get_sequence,
)
from litmus.store import (
    get_station as store_get_station,
)
from litmus.store import (
    list_fixtures as store_list_fixtures,
)
from litmus.store import (
    list_instrument_assets as store_list_instrument_assets,
)
from litmus.store import (
    list_sequences as store_list_sequences,
)
from litmus.store import (
    list_stations as store_list_stations,
)
from litmus.store import (
    load_station_type as store_load_station_type,
)
from litmus.store import (
    save_catalog_entry as store_save_catalog_entry,
)
from litmus.store import (
    save_fixture as store_save_fixture,
)
from litmus.store import (
    save_product as store_save_product,
)
from litmus.store import (
    save_sequence as store_save_sequence,
)
from litmus.store import (
    save_station as store_save_station,
)
from litmus.store import (
    save_station_type as store_save_station_type,
)

# Re-export for backwards compatibility with UI pages
from litmus.utils.paths import get_instrument_paths

# -----------------------------------------------------------------------------
# Product Services
# -----------------------------------------------------------------------------


def discover_products() -> list[dict]:
    """Discover products from the products/ directory.

    Flat files (products/id.yaml) are the canonical convention.
    Manifest-based folders and other nested layouts are also supported via rglob.
    """
    from pathlib import Path

    products = []
    seen_ids: set[str] = set()

    products_dirs = [Path.cwd() / "products"]

    for products_dir in products_dirs:
        if not products_dir.exists():
            continue

        # 1. Check manifest-based folders (full workflow with tracking)
        for folder in ProductFolder.list_all(products_dir):
            spec = folder.load_spec()
            product_id = folder.product_id

            if product_id in seen_ids:
                continue
            seen_ids.add(product_id)

            if spec:
                products.append({
                    "id": spec.id,
                    "name": spec.name,
                    "description": spec.description or "",
                    "revision": spec.revision or "",
                    "pins": None,
                    "characteristics": {
                        name: char.model_dump() for name, char in spec.characteristics.items()
                    },
                    "file": (
                        str(folder.path / folder.manifest.files.spec)
                        if folder.manifest.files.spec else None
                    ),
                    "folder_path": str(folder.path),
                    "workflow_step": folder.current_step.value if folder.current_step else None,
                    "completed_steps": [s.value for s in folder.manifest.completed_steps],
                    "files": folder.manifest.files.model_dump(),
                })
            else:
                products.append({
                    "id": product_id,
                    "name": folder.name,
                    "description": folder.manifest.description or "",
                    "revision": "",
                    "pins": None,
                    "characteristics": {},
                    "file": None,
                    "folder_path": str(folder.path),
                    "workflow_step": folder.current_step.value if folder.current_step else None,
                    "completed_steps": [s.value for s in folder.manifest.completed_steps],
                    "files": folder.manifest.files.model_dump(),
                })

        # 2. Discover all YAML files (flat and nested, no manifest required)
        from litmus.store import load_product
        for yaml_file in sorted(products_dir.rglob("*.yaml")):
            if yaml_file.name.startswith("_"):
                continue
            try:
                p = load_product(yaml_file)
            except (OSError, ValueError, KeyError):
                continue
            if p.id in seen_ids:
                continue
            seen_ids.add(p.id)
            products.append({
                "id": p.id,
                "name": p.name,
                "description": p.description or "",
                "revision": p.revision or "",
                "pins": None,
                "characteristics": {
                    name: char.model_dump() for name, char in p.characteristics.items()
                },
                "file": str(yaml_file),
                "folder_path": str(yaml_file.parent),
                "workflow_step": None,
                "completed_steps": [],
                "files": {},
            })

    return products


def load_product_model(product_id: str):
    """Load a Product model by ID."""
    return store_get_product(product_id)


def create_product(product_id: str, name: str, description: str = "") -> dict | None:
    """Create a new product folder.

    Returns dict with product info if successful, None if product already exists.
    """
    product = store_create_product(product_id, name, description)
    if product is None:
        return None
    from pathlib import Path

    products_dir = Path.cwd() / "products"
    folder_path = products_dir / product_id
    return {
        "id": product.id,
        "name": product.name,
        "description": product.description or "",
        "folder_path": str(folder_path),
    }


def get_required_capabilities(product) -> list[dict]:
    """Get required instrument capabilities for a product."""
    if not product:
        return []

    capabilities = []
    for char_name, char in product.characteristics.items():
        capabilities.append({
            "characteristic": char_name,
            "direction": char.direction.value,
            "function": char.function.value,
            "signals": ", ".join(char.signals.keys()) if char.signals else "",
        })
    return capabilities


def get_compatible_stations_for_product(product_id: str) -> list[dict]:
    """Get stations that have instruments satisfying product requirements."""
    product = store_get_product(product_id)
    if not product:
        return []

    matches = matching_service.find_compatible_stations(product)
    return [
        {"id": m.station_id, "name": m.station_name, "location": m.station_name}
        for m in matches
        if m.compatible
    ]


def get_partial_stations_for_product(product_id: str) -> list[dict]:
    """Get stations with partial capability coverage for a product."""
    product = store_get_product(product_id)
    if not product:
        return []

    partial_matches = matching_service.find_partial_stations(product)
    return [
        {
            "id": m.station_id,
            "name": m.station_name,
            "location": m.location,
            "coverage": m.coverage_pct,
            "missing": m.missing,
        }
        for m in partial_matches
    ]


def get_all_station_matches_for_product(product_id: str) -> dict[str, list]:
    """Get all stations categorized by compatibility level."""
    product = store_get_product(product_id)
    if not product:
        return {"compatible": [], "partial": [], "incompatible": []}

    return matching_service.find_all_station_matches(product)


def save_product(product_id: str, product_data: dict) -> bool:
    """Save product specification to YAML file."""
    from litmus.products.models import Product

    product_dict = {
        "id": product_data.get("id", product_id),
        "name": product_data.get("name", ""),
        "description": product_data.get("description", ""),
        "characteristics": product_data.get("characteristics", {}),
    }
    if product_data.get("revision"):
        product_dict["revision"] = product_data["revision"]
    if product_data.get("pins"):
        product_dict["pins"] = product_data["pins"]

    product = Product.model_validate(product_dict)
    return store_save_product(product)


# -----------------------------------------------------------------------------
# Station Services
# -----------------------------------------------------------------------------


def discover_stations():
    """Discover station configurations from YAML files."""
    return store_list_stations()


def load_station_config(station_id: str):
    """Load station configuration by ID."""
    return store_get_station(station_id)


def create_station(
    station_id: str, name: str, location: str = "", description: str = "",
):
    """Create a new station configuration file."""
    return store_create_station(station_id, name, location, description)


def save_station(station_id: str, station_data: dict, instruments_data: dict) -> bool:
    """Save station configuration to YAML file."""
    from litmus.config.normalize import check_instrument_types
    from litmus.schemas import StationConfig

    check_instrument_types(instruments_data)
    station_dict = {**station_data, "instruments": instruments_data}
    station = StationConfig.model_validate(station_dict)
    return store_save_station(station)


def get_station_capabilities(config):
    """Get capabilities from all instruments in a station."""
    if not config:
        return []
    return matching_service.get_station_capabilities(config)


def station_compatible_with_product(station_config, product) -> bool:
    """Check if a station is compatible with a product."""
    if not station_config or not product:
        return False
    result = matching_service.check_station_compatibility(product.id, station_config.id)
    return result.get("compatible", False) if result else False


# -----------------------------------------------------------------------------
# Instrument Services
# -----------------------------------------------------------------------------


def discover_instrument_types():
    """Discover available instrument types from catalog entries.

    Returns one InstrumentCatalogEntry per unique type (first seen wins).
    """
    entries = []
    seen_types: set[str] = set()
    for cat_dir in find_catalog_dirs():
        for entry_id, entry in load_catalog_from_directory(cat_dir).items():
            if entry.type in seen_types:
                continue
            seen_types.add(entry.type)
            entries.append(entry)
    return entries


def load_catalog_entry_by_type(instrument_type: str):
    """Load a catalog entry by type or ID."""
    return store_get_catalog_entry(instrument_type)


def save_catalog_entry(instrument_type: str, data: dict) -> bool:
    """Save a catalog entry to catalog/."""
    from litmus.catalog.models import InstrumentCatalogEntry

    inst = data.get("instrument", {})
    entry = InstrumentCatalogEntry.model_validate({
        "id": inst.get("type", instrument_type),
        "type": inst.get("type", instrument_type),
        "manufacturer": inst.get("manufacturer", "User"),
        "model": inst.get("name", instrument_type),
        "name": inst.get("name", ""),
        "description": inst.get("description"),
        "capabilities": data.get("capabilities", []),
    })
    return store_save_catalog_entry(entry)


def discover_instrument_assets():
    """Discover per-device instrument asset files."""
    return store_list_instrument_assets()


def load_instrument_asset_by_id(instrument_id: str):
    """Load a single instrument asset file by ID."""
    return store_get_instrument_asset(instrument_id)


def resolve_station_instrument_records(station_id: str) -> dict:
    """Resolve a station's instruments to InstrumentRecord objects."""
    config = store_get_station(station_id)
    if not config:
        return {}

    all_instrument_files: dict = {}
    for instruments_dir in get_instrument_paths():
        all_instrument_files.update(load_instrument_files(instruments_dir))

    return resolve_station_instruments(config, all_instrument_files)


def create_catalog_entry(
    instrument_type: str,
    name: str,
    description: str = "",
    icon: str = "device_unknown",
):
    """Create a new catalog entry in catalog/."""
    return store_create_catalog_entry(instrument_type, name, description)


# -----------------------------------------------------------------------------
# Test & Sequence Services
# -----------------------------------------------------------------------------


def discover_tests() -> list[dict]:
    """Discover available test directories."""
    from pathlib import Path

    tests = []
    search_paths = [Path.cwd() / "tests"]

    for tests_dir in search_paths:
        if not tests_dir.exists():
            continue
        for test_file in tests_dir.rglob("test_*.py"):
            test_dir = test_file.parent
            cwd = Path.cwd()
            relative = test_dir.relative_to(cwd) if test_dir.is_relative_to(cwd) else test_dir
            test_entry = {"path": str(relative), "name": test_dir.name}
            if test_entry not in tests:
                tests.append(test_entry)
    return tests


def discover_sequences():
    """Discover test sequences from YAML files."""
    return store_list_sequences()


def create_sequence(
    sequence_id: str,
    name: str,
    product_family: str = "",
    test_phase: Literal["validation", "characterization", "production"] = "validation",
    description: str = "",
):
    """Create a new sequence configuration file."""
    return store_create_sequence(sequence_id, name, product_family, test_phase, description)


def load_sequence_config(sequence_id: str):
    """Load sequence configuration by ID."""
    return store_get_sequence(sequence_id)


def save_sequence(sequence_id: str, sequence_data: dict, steps: list, dialogs: dict) -> bool:
    """Save sequence configuration to YAML file."""
    from litmus.config.models import TestSequenceConfig

    seq_dict = {**sequence_data, "steps": steps}
    if dialogs:
        seq_dict["dialogs"] = dialogs
    seq = TestSequenceConfig.model_validate(seq_dict)
    return store_save_sequence(seq)


# -----------------------------------------------------------------------------
# Fixture Services
# -----------------------------------------------------------------------------


def discover_fixtures():
    """Discover fixture configurations from YAML files."""
    return store_list_fixtures()


def load_fixture_config(fixture_id: str):
    """Load fixture configuration by ID."""
    return store_get_fixture(fixture_id)


def create_fixture(
    fixture_id: str,
    name: str,
    product_id: str = "",
    product_revision: str = "",
    description: str = "",
):
    """Create a new fixture configuration file."""
    return store_create_fixture(fixture_id, name, product_id, product_revision, description)


def save_fixture(fixture_id: str, fixture_data: dict, points_data: dict) -> bool:
    """Save fixture configuration to YAML file."""
    from litmus.config.models import FixtureConfig

    fixture_dict = {**fixture_data, "points": points_data}
    fixture = FixtureConfig.model_validate(fixture_dict)
    return store_save_fixture(fixture)


# -----------------------------------------------------------------------------
# Station Type Services
# -----------------------------------------------------------------------------


def save_station_type(type_id: str, data: dict) -> bool:
    """Save station type YAML."""
    return store_save_station_type(type_id, data)


def load_station_type(type_id: str) -> dict | None:
    """Load station type by ID."""
    return store_load_station_type(type_id)


def get_instrument_channels_from_library(instrument_type: str) -> list[str]:
    """Get channel names from a catalog entry matching the given type."""
    entry = store_get_catalog_entry(instrument_type)
    if entry:
        if entry.channels:
            return list(entry.channels.keys())
        return ["1"]
    return ["1"]


def get_fixtures_for_product(product_family: str):
    """Get all fixtures for a product family."""
    all_fixtures = discover_fixtures()
    return [f for f in all_fixtures if (f.product_family or "") == product_family]


def get_compatible_stations_for_fixture(fixture_id: str):
    """Get stations that have all instruments referenced by a fixture."""
    fixture = load_fixture_config(fixture_id)
    if not fixture:
        return []

    required_instruments = {
        p.instrument for p in fixture.points.values() if p.instrument
    }

    compatible = []
    for station in discover_stations():
        if not station.instruments:
            continue
        station_instruments = set(station.instruments.keys())
        if required_instruments <= station_instruments:
            compatible.append(station)

    return compatible
