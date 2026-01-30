"""Data services for UI - loading, saving, and discovery functions."""

from pathlib import Path

import yaml

from litmus.matching import service as matching_service
from litmus.products.folder import ProductFolder

# -----------------------------------------------------------------------------
# Product Services
# -----------------------------------------------------------------------------


def discover_products() -> list[dict]:
    """Discover products from folders and spec files.

    Checks:
    1. products/ folder (new workflow structure)
    2. specs/ and demo/specs/ (legacy flat structure)
    """
    products = []
    seen_ids = set()

    # 1. Check products/ folder (new workflow structure)
    products_dir = Path.cwd() / "products"
    if products_dir.exists():
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
                    "pins": None,  # Not in Product model yet
                    "characteristics": {
                        name: char.model_dump() for name, char in spec.characteristics.items()
                    },
                    "test_requirements": {
                        name: req.model_dump() for name, req in spec.test_requirements.items()
                    },
                    "file": str(folder.path / "spec.yaml"),
                    "folder_path": str(folder.path),
                    "workflow_step": folder.current_step.value if folder.current_step else None,
                })
            else:
                # Folder exists but no spec yet (in progress)
                products.append({
                    "id": product_id,
                    "name": folder.name,
                    "description": folder.manifest.description or "",
                    "revision": "",
                    "pins": None,
                    "characteristics": {},
                    "test_requirements": {},
                    "file": None,
                    "folder_path": str(folder.path),
                    "workflow_step": folder.current_step.value if folder.current_step else None,
                })

    # 2. Check specs/ for backwards compat (legacy flat structure)
    search_paths = [
        Path.cwd() / "specs",
        Path.cwd() / "demo" / "specs",
    ]

    for specs_dir in search_paths:
        if not specs_dir.exists():
            continue
        for yaml_file in specs_dir.glob("*.yaml"):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data and "product" in data:
                    product_info = data["product"]
                    product_id = product_info.get("id", yaml_file.stem)

                    if product_id in seen_ids:
                        continue
                    seen_ids.add(product_id)

                    products.append({
                        "id": product_id,
                        "name": product_info.get("name", yaml_file.stem),
                        "description": product_info.get("description", ""),
                        "revision": product_info.get("revision", ""),
                        "pins": product_info.get("pins"),
                        "characteristics": data.get("characteristics", {}),
                        "test_requirements": data.get("test_requirements", {}),
                        "file": str(yaml_file),
                        "folder_path": None,
                        "workflow_step": None,
                    })

    return products


def load_product_model(product_id: str):
    """Load a Product model from specs directory."""
    return matching_service.load_product_by_id(product_id)


def get_required_capabilities(product) -> list[dict]:
    """Get required instrument capabilities for a product."""
    if not product:
        return []

    capabilities = []
    for char_name, char in product.characteristics.items():
        cap_req = char.to_capability_requirement()
        capabilities.append({
            "characteristic": char_name,
            "direction": cap_req.direction.value,
            "domain": cap_req.domain.value,
            "signal_types": (
                [st.value for st in cap_req.signal_types] if cap_req.signal_types else []
            ),
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


def save_product(product_id: str, product_data: dict) -> bool:
    """Save product specification to YAML file."""
    search_paths = [
        Path.cwd() / "specs",
        Path.cwd() / "demo" / "specs",
    ]

    target_file = None
    for specs_dir in search_paths:
        if specs_dir.exists():
            existing = specs_dir / f"{product_id}.yaml"
            if existing.exists():
                target_file = existing
                break

    if target_file is None:
        for specs_dir in search_paths:
            if specs_dir.exists():
                target_file = specs_dir / f"{product_id}.yaml"
                break

    if target_file is None:
        specs_dir = Path.cwd() / "specs"
        specs_dir.mkdir(exist_ok=True)
        target_file = specs_dir / f"{product_id}.yaml"

    yaml_data = {
        "product": {
            "id": product_data.get("id", product_id),
            "name": product_data.get("name", ""),
            "description": product_data.get("description", ""),
        },
        "characteristics": product_data.get("characteristics", {}),
        "test_requirements": product_data.get("test_requirements", {}),
    }

    if product_data.get("revision"):
        yaml_data["product"]["revision"] = product_data["revision"]

    if product_data.get("pins"):
        yaml_data["product"]["pins"] = product_data["pins"]

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

    capabilities = []
    instruments = config.get("instruments", {})

    for inst_name, inst_config in instruments.items():
        inst_type = inst_config.get("type")
        if not inst_type:
            continue

        inst_def = matching_service.load_instrument_library(inst_type)
        if not inst_def:
            continue

        for cap in inst_def.get("capabilities", []):
            capabilities.append({
                "instrument": inst_name,
                "name": cap.get("name", ""),
                "direction": cap.get("direction", ""),
                "domain": cap.get("domain", ""),
            })

    return capabilities


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
    """Discover available instrument types from YAML definitions.

    Searches user's instruments/ first, then built-in library.
    """
    instruments = []
    seen_types = set()

    # User instruments first, then built-in library
    search_paths = [
        Path.cwd() / "instruments",
        Path.cwd() / "demo" / "instruments",
        Path(__file__).parent.parent.parent / "instruments" / "library",
    ]

    for library_dir in search_paths:
        if not library_dir.exists():
            continue

        for yaml_file in library_dir.glob("*.yaml"):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data and "instrument" in data:
                    inst = data["instrument"]
                    inst_type = inst.get("type", yaml_file.stem)

                    # Skip if we already saw this type (user overrides built-in)
                    if inst_type in seen_types:
                        continue
                    seen_types.add(inst_type)

                    capabilities = data.get("capabilities", [])
                    instruments.append({
                        "type": inst_type,
                        "name": inst.get("name", yaml_file.stem),
                        "description": inst.get("description", ""),
                        "icon": inst.get("icon", "device_unknown"),
                        "capabilities": [c.get("name", "") for c in capabilities],
                        "capability_details": capabilities,
                        "source": str(library_dir),
                    })

    return instruments


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
    sequences = []
    search_paths = [
        Path.cwd() / "sequences",
        Path.cwd() / "demo" / "sequences",
    ]

    for seq_dir in search_paths:
        if not seq_dir.exists():
            continue
        for yaml_file in seq_dir.glob("*.yaml"):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data and "sequence" in data:
                    seq = data["sequence"]
                    sequences.append({
                        "id": seq.get("id", yaml_file.stem),
                        "name": seq.get("name", yaml_file.stem),
                        "description": seq.get("description", ""),
                        "product_family": seq.get("product_family"),
                        "test_phase": seq.get("test_phase", "validation"),
                        "steps": data.get("steps", []),
                    })
    return sequences
