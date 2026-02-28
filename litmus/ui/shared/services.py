"""Data services for UI - loading, saving, and discovery functions."""

from pathlib import Path

import yaml

from litmus.instruments.loader import (
    load_instrument_files,
    resolve_station_instruments,
)
from litmus.matching import service as matching_service
from litmus.products.folder import ProductFolder

# -----------------------------------------------------------------------------
# Product Services
# -----------------------------------------------------------------------------


def discover_products() -> list[dict]:
    """Discover products from folders.

    Checks products/ and demo/products/ folders for product specifications.
    Supports both manifest-based folders and plain spec.yaml folders.
    """
    products = []
    seen_ids: set[str] = set()

    products_dirs = [
        Path.cwd() / "products",
        Path.cwd() / "demo" / "products",
    ]

    for products_dir in products_dirs:
        if not products_dir.exists():
            continue

        # 1. Check manifest-based folders (full workflow)
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
                    "file": str(folder.path / "spec.yaml"),
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

        # 2. Fallback: discover spec.yaml folders without manifest.yaml
        for item in sorted(products_dir.iterdir()):
            if not item.is_dir():
                continue
            product_id = item.name
            if product_id in seen_ids:
                continue
            spec_file = item / "spec.yaml"
            if not spec_file.exists():
                continue

            seen_ids.add(product_id)
            # Try loading via matching service for full model
            model = matching_service.load_product_by_id(product_id)
            if model:
                products.append({
                    "id": model.id,
                    "name": model.name,
                    "description": model.description or "",
                    "revision": model.revision or "",
                    "pins": None,
                    "characteristics": {
                        name: char.model_dump()
                        for name, char in model.characteristics.items()
                    },
                    "file": str(spec_file),
                    "folder_path": str(item),
                    "workflow_step": None,
                    "completed_steps": [],
                    "files": {},
                })
            else:
                # Raw YAML fallback
                with open(spec_file) as f:
                    data = yaml.safe_load(f) or {}
                prod = data.get("product", {})
                products.append({
                    "id": prod.get("id", product_id),
                    "name": prod.get("name", product_id),
                    "description": prod.get("description", ""),
                    "revision": prod.get("revision", ""),
                    "pins": None,
                    "characteristics": {},
                    "file": str(spec_file),
                    "folder_path": str(item),
                    "workflow_step": None,
                    "completed_steps": [],
                    "files": {},
                })

    return products


def load_product_model(product_id: str):
    """Load a Product model by ID."""
    return matching_service.load_product_by_id(product_id)


def create_product(product_id: str, name: str, description: str = "") -> dict | None:
    """Create a new product folder.

    Args:
        product_id: Unique identifier for the product
        name: Human-readable product name
        description: Optional description

    Returns:
        Dict with product info if successful, None if product already exists
    """
    products_dir = Path.cwd() / "products"
    products_dir.mkdir(exist_ok=True)

    try:
        folder = ProductFolder.create(
            base_path=products_dir,
            product_id=product_id,
            name=name,
            description=description or None,
        )
        return {
            "id": product_id,
            "name": name,
            "description": description,
            "folder_path": str(folder.path),
        }
    except FileExistsError:
        return None


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
    product = matching_service.load_product_by_id(product_id)
    if not product:
        return []

    matches = matching_service.find_compatible_stations(product)
    return [
        {"id": m.station_id, "name": m.station_name, "location": m.station_name}
        for m in matches
        if m.compatible
    ]


def get_partial_stations_for_product(product_id: str) -> list[dict]:
    """Get stations with partial capability coverage for a product.

    Returns stations that have some but not all required capabilities.
    Useful for procurement planning.
    """
    product = matching_service.load_product_by_id(product_id)
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
    """Get all stations categorized by compatibility level.

    Returns dict with 'compatible', 'partial', and 'incompatible' lists.
    """
    product = matching_service.load_product_by_id(product_id)
    if not product:
        return {"compatible": [], "partial": [], "incompatible": []}

    return matching_service.find_all_station_matches(product)


def save_product(product_id: str, product_data: dict) -> bool:
    """Save product specification to YAML file.

    Saves to products/{product_id}/spec.yaml, creating the folder if needed.
    """
    # Check if product folder already exists
    search_paths = [
        Path.cwd() / "products",
        Path.cwd() / "demo" / "products",
    ]

    target_file = None
    for products_dir in search_paths:
        product_folder = products_dir / product_id
        if product_folder.exists():
            target_file = product_folder / "spec.yaml"
            break

    # Create new product folder if not found
    if target_file is None:
        products_dir = Path.cwd() / "products"
        products_dir.mkdir(exist_ok=True)
        product_folder = products_dir / product_id
        product_folder.mkdir(exist_ok=True)
        target_file = product_folder / "spec.yaml"

    yaml_data = {
        "product": {
            "id": product_data.get("id", product_id),
            "name": product_data.get("name", ""),
            "description": product_data.get("description", ""),
        },
        "characteristics": product_data.get("characteristics", {}),
    }

    if product_data.get("revision"):
        yaml_data["product"]["revision"] = product_data["revision"]

    if product_data.get("pins"):
        yaml_data["product"]["pins"] = product_data["pins"]

    # Validate against Product model before writing
    from litmus.products.models import Product

    try:
        Product.model_validate({
            **yaml_data["product"],
            "characteristics": yaml_data.get("characteristics", {}),
            "pins": yaml_data["product"].get("pins", {}),
        })
    except Exception:
        pass  # Best-effort validation — don't block save for existing data

    with open(target_file, "w") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False)

    return True


# -----------------------------------------------------------------------------
# Station Services
# -----------------------------------------------------------------------------


def discover_stations() -> list[dict]:
    """Discover station configurations from YAML files."""
    stations = matching_service.list_stations()
    for s in stations:
        if s.get("location") is None:
            s["location"] = "Unknown"
        if s.get("description") is None:
            s["description"] = ""
    return stations


def load_station_config(station_id: str) -> dict | None:
    """Load station configuration by ID."""
    return matching_service.load_station_config(station_id)


def create_station(
    station_id: str, name: str, location: str = "", description: str = ""
) -> dict | None:
    """Create a new station configuration file.

    Args:
        station_id: Unique identifier for the station
        name: Human-readable station name
        location: Physical location
        description: Optional description

    Returns:
        Dict with station info if successful, None if station already exists
    """
    stations_dir = Path.cwd() / "stations"
    stations_dir.mkdir(exist_ok=True)

    station_file = stations_dir / f"{station_id}.yaml"
    if station_file.exists():
        return None

    station_data = {
        "id": station_id,
        "name": name,
    }
    if location:
        station_data["location"] = location
    if description:
        station_data["description"] = description

    yaml_data = {
        "station": station_data,
        "instruments": {},
    }

    with open(station_file, "w") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False)

    return {
        "id": station_id,
        "name": name,
        "location": location,
        "description": description,
        "file": str(station_file),
    }


def save_station(station_id: str, station_data: dict, instruments_data: dict) -> bool:
    """Save station configuration to YAML file."""
    search_paths = [
        Path.cwd() / "stations",
        Path.cwd() / "demo" / "stations",
    ]

    target_file = None
    for stations_dir in search_paths:
        if stations_dir.exists():
            for f in stations_dir.glob("*.yaml"):
                if f.stem == station_id or f.stem.endswith(f"_{station_id}"):
                    target_file = f
                    break
            if target_file:
                break

    if target_file is None:
        for stations_dir in search_paths:
            if stations_dir.exists():
                target_file = stations_dir / f"{station_id}.yaml"
                break

    if target_file is None:
        return False

    from litmus.config.normalize import check_instrument_types

    # Normalize instrument types before saving
    check_instrument_types(instruments_data)

    yaml_data = {
        "station": station_data,
        "instruments": instruments_data,
    }

    with open(target_file, "w") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False)

    return True


def get_station_capabilities(config: dict) -> list[dict]:
    """Get capabilities from all instruments in a station."""
    if not config:
        return []

    station_caps = matching_service.get_station_capabilities(config)
    return [
        {
            "instrument": cap.instrument_name,
            "name": cap.name,
            "direction": cap.direction.value,
            "function": cap.function.value,
        }
        for cap in station_caps
    ]


def station_compatible_with_product(station_config: dict, product) -> bool:
    """Check if a station is compatible with a product."""
    station_id = station_config.get("station", {}).get("id")
    if not station_id or not product:
        return False

    result = matching_service.check_station_compatibility(product.id, station_id)
    return result.get("compatible", False) if result else False


# -----------------------------------------------------------------------------
# Instrument Services
# -----------------------------------------------------------------------------


def discover_instrument_types() -> list[dict]:
    """Discover available instrument types from catalog entries.

    Scans catalog directories and groups entries by type.
    Returns one entry per unique type (first seen wins).
    """
    from litmus.catalog.loader import find_catalog_dirs, load_catalog_from_directory

    instruments = []
    seen_types: set[str] = set()

    for cat_dir in find_catalog_dirs():
        for entry_id, entry in load_catalog_from_directory(cat_dir).items():
            if entry.type in seen_types:
                continue
            seen_types.add(entry.type)

            cap_names = []
            for cap in entry.capabilities:
                name = f"{cap.function.value}_{cap.direction.value}"
                if name not in cap_names:
                    cap_names.append(name)

            instruments.append({
                "type": entry.type,
                "name": entry.name or entry.type,
                "description": entry.description or "",
                "icon": "device_unknown",
                "capabilities": cap_names,
                "capability_details": [cap.model_dump() for cap in entry.capabilities],
                "source": str(cat_dir),
            })

    return instruments


def load_instrument_definition(instrument_type: str) -> dict | None:
    """Load instrument definition by type from catalog.

    Searches catalog entries for the first entry matching the given type.
    Returns a dict with 'instrument' and 'capabilities' keys for
    backwards compatibility with UI pages.
    """
    from litmus.catalog.loader import find_catalog_dirs, load_catalog_from_directory

    for cat_dir in find_catalog_dirs():
        for entry_id, entry in load_catalog_from_directory(cat_dir).items():
            if entry.type == instrument_type or entry.id == instrument_type:
                return {
                    "instrument": {
                        "type": entry.type,
                        "name": entry.name or entry.type,
                        "description": entry.description or "",
                        "icon": "device_unknown",
                    },
                    "capabilities": [cap.model_dump() for cap in entry.capabilities],
                }
    return None


def save_instrument_definition(instrument_type: str, data: dict) -> bool:
    """Save instrument definition to YAML file.

    Saves to user's instruments/ directory (creates if needed).
    """
    instruments_dir = Path.cwd() / "instruments"
    instruments_dir.mkdir(exist_ok=True)

    target_file = instruments_dir / f"{instrument_type}.yaml"

    with open(target_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    return True


def discover_instrument_assets() -> list[dict]:
    """Discover per-device instrument asset files (identity + calibration).

    Asset files have an 'id' key at top level (not 'instrument' which is library).
    """
    from litmus.loaders import load_instrument_asset

    assets = []
    seen_ids: set[str] = set()

    search_paths = [
        Path.cwd() / "instruments",
        Path.cwd() / "demo" / "instruments",
    ]

    for instruments_dir in search_paths:
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

            assets.append({
                "id": asset.id,
                "driver": asset.driver or "",
                "protocol": asset.protocol,
                "resource": asset.resource or "",
                "manufacturer": asset.info.manufacturer or "",
                "model": str(asset.info.model) if asset.info.model is not None else "",
                "serial": str(asset.info.serial) if asset.info.serial is not None else "",
                "firmware": str(asset.info.firmware) if asset.info.firmware is not None else "",
                "cal_due": asset.calibration.due_date,
                "cal_last": asset.calibration.last_cal,
                "cal_certificate": asset.calibration.certificate or "",
                "cal_lab": asset.calibration.lab or "",
                "file": str(yaml_file),
            })

    return assets


def load_instrument_asset_by_id(instrument_id: str) -> dict | None:
    """Load a single instrument asset file by ID."""
    from litmus.loaders import load_instrument_asset as _load_asset

    search_paths = [
        Path.cwd() / "instruments",
        Path.cwd() / "demo" / "instruments",
    ]

    for instruments_dir in search_paths:
        if not instruments_dir.exists():
            continue
        for yaml_file in instruments_dir.glob("*.yaml"):
            try:
                asset = _load_asset(yaml_file)
                if asset.id == instrument_id:
                    return asset.model_dump()
            except Exception:
                continue

    return None


def resolve_station_instrument_records(station_id: str) -> dict:
    """Resolve a station's instruments to InstrumentRecord objects.

    Returns dict mapping role name to InstrumentRecord.
    """
    config = load_station_config(station_id)
    if not config:
        return {}

    # Load all instrument asset files from both search paths
    all_instrument_files: dict = {}
    search_paths = [
        Path.cwd() / "instruments",
        Path.cwd() / "demo" / "instruments",
    ]
    for instruments_dir in search_paths:
        all_instrument_files.update(load_instrument_files(instruments_dir))

    return resolve_station_instruments(config, all_instrument_files)


def create_instrument_definition(
    instrument_type: str,
    name: str,
    description: str = "",
    icon: str = "device_unknown",
) -> dict | None:
    """Create a new instrument definition file.

    Args:
        instrument_type: Unique type identifier
        name: Human-readable name
        description: Optional description
        icon: Material icon name

    Returns:
        Dict with instrument info if successful, None if already exists
    """
    instruments_dir = Path.cwd() / "instruments"
    instruments_dir.mkdir(exist_ok=True)

    instrument_file = instruments_dir / f"{instrument_type}.yaml"
    if instrument_file.exists():
        return None

    data = {
        "instrument": {
            "type": instrument_type,
            "name": name,
            "description": description,
            "icon": icon,
        },
        "capabilities": [],
    }

    with open(instrument_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    return {
        "type": instrument_type,
        "name": name,
        "file": str(instrument_file),
    }


# -----------------------------------------------------------------------------
# Test & Sequence Services
# -----------------------------------------------------------------------------


def discover_tests() -> list[dict]:
    """Discover available test directories."""
    tests = []
    search_paths = [
        Path.cwd() / "tests",
        Path.cwd() / "demo" / "tests",
    ]

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


def discover_sequences() -> list[dict]:
    """Discover test sequences from YAML files."""
    from litmus.loaders import load_sequence

    sequences = []
    search_paths = [
        Path.cwd() / "sequences",
        Path.cwd() / "demo" / "sequences",
    ]

    for seq_dir in search_paths:
        if not seq_dir.exists():
            continue
        for yaml_file in seq_dir.glob("*.yaml"):
            try:
                seq_file = load_sequence(yaml_file)
                seq = seq_file.sequence
                sequences.append({
                    "id": seq.id,
                    "name": seq.name or seq.id,
                    "description": seq.description or "",
                    "product_family": seq.product_family,
                    "test_phase": seq.test_phase or "validation",
                    "steps": [s.model_dump() for s in seq_file.steps],
                })
            except Exception:
                continue
    return sequences


def create_sequence(
    sequence_id: str,
    name: str,
    product_family: str = "",
    test_phase: str = "validation",
    description: str = "",
) -> dict | None:
    """Create a new sequence configuration file.

    Args:
        sequence_id: Unique identifier for the sequence
        name: Human-readable sequence name
        product_family: Associated product family
        test_phase: Test phase (validation, characterization, production)
        description: Optional description

    Returns:
        Dict with sequence info if successful, None if sequence already exists
    """
    sequences_dir = Path.cwd() / "sequences"
    sequences_dir.mkdir(exist_ok=True)

    sequence_file = sequences_dir / f"{sequence_id}.yaml"
    if sequence_file.exists():
        return None

    sequence_data = {
        "id": sequence_id,
        "name": name,
        "test_phase": test_phase,
    }
    if product_family:
        sequence_data["product_family"] = product_family
    if description:
        sequence_data["description"] = description

    yaml_data = {
        "sequence": sequence_data,
        "steps": [],
    }

    with open(sequence_file, "w") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False)

    return {
        "id": sequence_id,
        "name": name,
        "product_family": product_family,
        "test_phase": test_phase,
        "file": str(sequence_file),
    }


def load_sequence_config(sequence_id: str) -> dict | None:
    """Load sequence configuration by ID."""
    from litmus.loaders import load_sequence

    search_paths = [
        Path.cwd() / "sequences",
        Path.cwd() / "demo" / "sequences",
    ]

    for seq_dir in search_paths:
        if not seq_dir.exists():
            continue
        for yaml_file in seq_dir.glob("*.yaml"):
            try:
                seq_file = load_sequence(yaml_file)
                if seq_file.sequence.id == sequence_id or yaml_file.stem == sequence_id:
                    return seq_file.model_dump()
            except Exception:
                continue
    return None


def save_sequence(sequence_id: str, sequence_data: dict, steps: list, dialogs: dict) -> bool:
    """Save sequence configuration to YAML file."""
    search_paths = [
        Path.cwd() / "sequences",
        Path.cwd() / "demo" / "sequences",
    ]

    target_file = None
    for seq_dir in search_paths:
        if seq_dir.exists():
            existing = seq_dir / f"{sequence_id}.yaml"
            if existing.exists():
                target_file = existing
                break

    if target_file is None:
        for seq_dir in search_paths:
            if seq_dir.exists():
                target_file = seq_dir / f"{sequence_id}.yaml"
                break

    if target_file is None:
        sequences_dir = Path.cwd() / "sequences"
        sequences_dir.mkdir(exist_ok=True)
        target_file = sequences_dir / f"{sequence_id}.yaml"

    yaml_data = {
        "sequence": sequence_data,
        "steps": steps,
    }
    if dialogs:
        yaml_data["dialogs"] = dialogs

    with open(target_file, "w") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False)

    return True


# -----------------------------------------------------------------------------
# Fixture Services
# -----------------------------------------------------------------------------


def discover_fixtures() -> list[dict]:
    """Discover fixture configurations from YAML files.

    Searches fixtures/ and demo/fixtures/ directories.
    """
    from litmus.loaders import load_fixture

    fixtures = []
    seen_ids = set()

    search_paths = [
        Path.cwd() / "fixtures",
        Path.cwd() / "demo" / "fixtures",
    ]

    for fixtures_dir in search_paths:
        if not fixtures_dir.exists():
            continue
        for yaml_file in fixtures_dir.glob("*.yaml"):
            try:
                fixture_file = load_fixture(yaml_file)
            except Exception:
                continue

            fixture_id = fixture_file.fixture.id

            if fixture_id in seen_ids:
                continue
            seen_ids.add(fixture_id)

            fixtures.append({
                "id": fixture_id,
                "name": fixture_file.fixture.name or yaml_file.stem,
                "description": fixture_file.fixture.description or "",
                "product_id": fixture_file.fixture.product_id,
                "product_family": fixture_file.fixture.product_family or "",
                "product_revision": fixture_file.fixture.product_revision,
                "points": {k: v.model_dump() for k, v in fixture_file.points.items()},
                "point_count": len(fixture_file.points),
                "file": str(yaml_file),
            })

    return fixtures


def load_fixture_config(fixture_id: str) -> dict | None:
    """Load fixture configuration by ID."""
    from litmus.loaders import load_fixture

    search_paths = [
        Path.cwd() / "fixtures",
        Path.cwd() / "demo" / "fixtures",
    ]

    for fixtures_dir in search_paths:
        if not fixtures_dir.exists():
            continue
        for yaml_file in fixtures_dir.glob("*.yaml"):
            try:
                fixture_file = load_fixture(yaml_file)
                if fixture_file.fixture.id == fixture_id or yaml_file.stem == fixture_id:
                    return fixture_file.model_dump()
            except Exception:
                continue
    return None


def create_fixture(
    fixture_id: str,
    name: str,
    product_id: str = "",
    product_revision: str = "",
    description: str = "",
) -> dict | None:
    """Create a new fixture configuration file.

    Args:
        fixture_id: Unique identifier for the fixture
        name: Human-readable fixture name
        product_id: Associated product ID
        product_revision: Optional product revision
        description: Optional description

    Returns:
        Dict with fixture info if successful, None if fixture already exists
    """
    fixtures_dir = Path.cwd() / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)

    fixture_file = fixtures_dir / f"{fixture_id}.yaml"
    if fixture_file.exists():
        return None

    fixture_data = {
        "id": fixture_id,
        "name": name,
    }
    if product_id:
        fixture_data["product_id"] = product_id
    if product_revision:
        fixture_data["product_revision"] = product_revision
    if description:
        fixture_data["description"] = description

    yaml_data = {
        "fixture": fixture_data,
        "points": {},
    }

    with open(fixture_file, "w") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False)

    return {
        "id": fixture_id,
        "name": name,
        "product_id": product_id,
        "file": str(fixture_file),
    }


def save_fixture(fixture_id: str, fixture_data: dict, points_data: dict) -> bool:
    """Save fixture configuration to YAML file."""
    search_paths = [
        Path.cwd() / "fixtures",
        Path.cwd() / "demo" / "fixtures",
    ]

    target_file = None
    for fixtures_dir in search_paths:
        if fixtures_dir.exists():
            existing = fixtures_dir / f"{fixture_id}.yaml"
            if existing.exists():
                target_file = existing
                break

    if target_file is None:
        for fixtures_dir in search_paths:
            if fixtures_dir.exists():
                target_file = fixtures_dir / f"{fixture_id}.yaml"
                break

    if target_file is None:
        fixtures_dir = Path.cwd() / "fixtures"
        fixtures_dir.mkdir(exist_ok=True)
        target_file = fixtures_dir / f"{fixture_id}.yaml"

    yaml_data = {
        "fixture": fixture_data,
        "points": points_data,
    }

    with open(target_file, "w") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, sort_keys=False)

    return True


# -----------------------------------------------------------------------------
# Station Type Services
# -----------------------------------------------------------------------------


def save_station_type(type_id: str, data: dict) -> bool:
    """Save station type YAML to stations/types/{type_id}.yaml."""
    types_dir = Path.cwd() / "stations" / "types"
    types_dir.mkdir(parents=True, exist_ok=True)

    target_file = types_dir / f"{type_id}.yaml"
    with open(target_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    return True


def load_station_type(type_id: str) -> dict | None:
    """Load station type by ID.

    Station types don't have a Pydantic model yet — pass through raw YAML.
    """
    from litmus.utils.loaders import load_yaml_file

    search_paths = [
        Path.cwd() / "stations" / "types",
        Path.cwd() / "demo" / "stations" / "types",
    ]
    for types_dir in search_paths:
        yaml_file = types_dir / f"{type_id}.yaml"
        data = load_yaml_file(yaml_file)
        if data is not None:
            return data
    return None


def get_instrument_channels_from_library(instrument_type: str) -> list[str]:
    """Get channel names from a catalog entry matching the given type.

    Falls back to ["1"] if no channels defined.
    """
    from litmus.catalog.loader import find_catalog_dirs, load_catalog_from_directory

    for cat_dir in find_catalog_dirs():
        for entry_id, entry in load_catalog_from_directory(cat_dir).items():
            if entry.type == instrument_type or entry.id == instrument_type:
                if entry.channels:
                    return list(entry.channels.keys())
                return ["1"]
    return ["1"]


def get_fixtures_for_product(product_family: str) -> list[dict]:
    """Get all fixtures for a product family."""
    all_fixtures = discover_fixtures()
    return [f for f in all_fixtures if f.get("product_family") == product_family]


def get_compatible_stations_for_fixture(fixture_id: str) -> list[dict]:
    """Get stations that have all instruments referenced by a fixture."""
    fixture_config = load_fixture_config(fixture_id)
    if not fixture_config:
        return []

    # Get instrument names referenced by fixture points
    points = fixture_config.get("points", {})
    required_instruments = {p.get("instrument") for p in points.values() if p.get("instrument")}

    # Check each station
    compatible = []
    for station in discover_stations():
        station_config = load_station_config(station["id"])
        if not station_config:
            continue

        station_instruments = set(station_config.get("instruments", {}).keys())
        if required_instruments <= station_instruments:
            compatible.append(station)

    return compatible
