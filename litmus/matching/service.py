"""Capability matching service.

Provides deterministic matching between product requirements and station capabilities.
This is the core service layer that the UI, API, and MCP tools all use.

Key concepts:
- Products define characteristics (what the DUT does: OUTPUT voltage, INPUT current)
- Characteristics derive capability requirements via direction pairing:
  - DUT OUTPUT -> Instrument INPUT (measure what DUT provides)
  - DUT INPUT -> Instrument OUTPUT (source what DUT needs)
- Stations have instruments with capabilities (what they can measure/source)
- Matching finds stations that satisfy all product requirements
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from litmus.capabilities.models import Capability, Direction, Domain, SignalType
from litmus.products.loader import load_product
from litmus.products.models import Product


class CapabilityRequirement(BaseModel):
    """A required instrument capability derived from a product characteristic."""

    direction: Direction
    domain: Domain
    signal_types: list[SignalType] = Field(default_factory=list)
    characteristic_name: str  # Which product characteristic this came from
    range_max: str | None = None  # Max range needed (with units)


class StationCapability(BaseModel):
    """A capability provided by a station instrument."""

    direction: Direction
    domain: Domain
    signal_types: list[SignalType] = Field(default_factory=list)
    name: str  # Capability name (e.g., "voltage_dc")
    instrument_type: str  # Which instrument provides this
    instrument_name: str  # Instance name in station config


class CapabilityMatch(BaseModel):
    """Result of matching a single requirement to a capability."""

    requirement: CapabilityRequirement
    matched_by: StationCapability | None = None
    satisfied: bool = False


class MatchResult(BaseModel):
    """Result of matching all requirements against available capabilities."""

    compatible: bool = False
    matches: list[CapabilityMatch] = Field(default_factory=list)
    missing: list[CapabilityRequirement] = Field(default_factory=list)
    unused: list[StationCapability] = Field(default_factory=list)


class StationMatch(BaseModel):
    """Summary of a station's compatibility with a product."""

    station_id: str
    station_name: str
    compatible: bool
    match_result: MatchResult


# -----------------------------------------------------------------------------
# Loaders
# -----------------------------------------------------------------------------


def _get_search_paths() -> tuple[list[Path], list[Path], Path]:
    """Get search paths for specs, stations, and instrument library."""
    cwd = Path.cwd()
    specs_paths = [cwd / "specs", cwd / "demo" / "specs"]
    stations_paths = [cwd / "stations", cwd / "demo" / "stations"]
    library_path = Path(__file__).parent.parent / "instruments" / "library"
    return specs_paths, stations_paths, library_path


def load_product_by_id(product_id: str) -> Product | None:
    """Load a Product model by ID from specs directories."""
    specs_paths, _, _ = _get_search_paths()

    for specs_dir in specs_paths:
        if not specs_dir.exists():
            continue
        for yaml_file in specs_dir.glob("*.yaml"):
            if yaml_file.name.startswith("_"):
                continue
            try:
                product = load_product(yaml_file)
                if product.id == product_id:
                    return product
            except Exception:
                continue
    return None


def list_products() -> list[dict[str, Any]]:
    """List all available products."""
    specs_paths, _, _ = _get_search_paths()
    products = []

    for specs_dir in specs_paths:
        if not specs_dir.exists():
            continue
        for yaml_file in specs_dir.glob("*.yaml"):
            if yaml_file.name.startswith("_"):
                continue
            try:
                product = load_product(yaml_file)
                products.append({
                    "id": product.id,
                    "name": product.name,
                    "description": product.description,
                    "revision": product.revision,
                    "characteristics_count": len(product.characteristics),
                    "test_requirements_count": len(product.test_requirements),
                })
            except Exception:
                continue
    return products


def load_instrument_library(instrument_type: str) -> dict | None:
    """Load instrument capabilities from library YAML."""
    _, _, library_path = _get_search_paths()
    yaml_file = library_path / f"{instrument_type}.yaml"

    if not yaml_file.exists():
        return None

    with open(yaml_file) as f:
        return yaml.safe_load(f)


def list_instrument_types() -> list[str]:
    """List available instrument types in the library."""
    _, _, library_path = _get_search_paths()
    if not library_path.exists():
        return []
    return [f.stem for f in library_path.glob("*.yaml")]


def load_station_config(station_id: str) -> dict | None:
    """Load station configuration by ID."""
    _, stations_paths, _ = _get_search_paths()

    for stations_dir in stations_paths:
        if not stations_dir.exists():
            continue
        for yaml_file in stations_dir.glob("*.yaml"):
            if yaml_file.name.startswith("_"):
                continue
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
                if data and "station" in data:
                    station_info = data["station"]
                    if station_info.get("id") == station_id:
                        return data
    return None


def list_stations() -> list[dict[str, Any]]:
    """List all available stations."""
    _, stations_paths, _ = _get_search_paths()
    stations = []

    for stations_dir in stations_paths:
        if not stations_dir.exists():
            continue
        for yaml_file in stations_dir.glob("*.yaml"):
            if yaml_file.name.startswith("_"):
                continue
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                    if data and "station" in data:
                        station_info = data["station"]
                        stations.append({
                            "id": station_info.get("id", yaml_file.stem),
                            "name": station_info.get("name", yaml_file.stem),
                            "location": station_info.get("location"),
                            "description": station_info.get("description"),
                        })
            except Exception:
                continue
    return stations


# -----------------------------------------------------------------------------
# Capability Extraction
# -----------------------------------------------------------------------------


def get_required_capabilities(product: Product) -> list[CapabilityRequirement]:
    """Derive required instrument capabilities from product characteristics.

    Uses Characteristic.to_capability_requirement() for direction flipping:
    - DUT OUTPUT -> Instrument INPUT (measure what DUT provides)
    - DUT INPUT -> Instrument OUTPUT (source what DUT needs)
    """
    requirements = []

    for char_name, char in product.characteristics.items():
        cap = char.to_capability_requirement()
        range_max = None
        if cap.range:
            range_max = f"{cap.range.max} {cap.range.units}" if cap.range.max else None

        requirements.append(
            CapabilityRequirement(
                direction=cap.direction,
                domain=cap.domain,
                signal_types=cap.signal_types,
                characteristic_name=char_name,
                range_max=range_max,
            )
        )

    return requirements


def get_station_capabilities(station_config: dict) -> list[StationCapability]:
    """Extract all capabilities from a station's instruments.

    Iterates through the station's instruments, loads each instrument's
    library definition, and extracts capabilities.
    """
    capabilities = []
    station_data = station_config.get("station", station_config)
    instruments = station_data.get("instruments", {})

    for inst_name, inst_config in instruments.items():
        inst_type = inst_config.get("type")
        if not inst_type:
            continue

        library = load_instrument_library(inst_type)
        if library and "capabilities" in library:
            for cap in library["capabilities"]:
                # Parse direction and domain as enums
                direction_str = cap.get("direction", "input")
                domain_str = cap.get("domain", "voltage")
                signal_types_raw = cap.get("signal_types", [])

                try:
                    direction = Direction(direction_str.lower())
                    domain = Domain(domain_str.lower())
                    signal_types = [SignalType(st.lower()) for st in signal_types_raw]
                except ValueError:
                    # Skip capabilities with unknown enum values
                    continue

                capabilities.append(
                    StationCapability(
                        direction=direction,
                        domain=domain,
                        signal_types=signal_types,
                        name=cap.get("name", ""),
                        instrument_type=inst_type,
                        instrument_name=inst_name,
                    )
                )

    return capabilities


# -----------------------------------------------------------------------------
# Matching Logic
# -----------------------------------------------------------------------------


def capability_satisfies(
    station_cap: StationCapability, required: CapabilityRequirement
) -> bool:
    """Check if a station capability satisfies a requirement.

    Match criteria:
    - Direction must match (or station capability is BIDIR)
    - Domain must match
    - Signal types must overlap (at least one common type)
    """
    # Direction must match (or station cap is bidir)
    if station_cap.direction != required.direction:
        if station_cap.direction != Direction.BIDIR:
            return False

    # Domain must match
    if station_cap.domain != required.domain:
        return False

    # Signal types must overlap (if both specify types)
    if required.signal_types and station_cap.signal_types:
        station_signals = set(station_cap.signal_types)
        required_signals = set(required.signal_types)
        if not station_signals.intersection(required_signals):
            return False

    return True


def match_capabilities(
    required: list[CapabilityRequirement], available: list[StationCapability]
) -> MatchResult:
    """Match required capabilities against available station capabilities.

    Returns a detailed result showing which requirements are satisfied,
    which are missing, and which station capabilities are unused.
    """
    matches = []
    used_capabilities = set()

    for req in required:
        match = CapabilityMatch(requirement=req)

        # Find a capability that satisfies this requirement
        for i, avail in enumerate(available):
            if capability_satisfies(avail, req):
                match.matched_by = avail
                match.satisfied = True
                used_capabilities.add(i)
                break

        matches.append(match)

    # Find missing requirements
    missing = [m.requirement for m in matches if not m.satisfied]

    # Find unused capabilities
    unused = [cap for i, cap in enumerate(available) if i not in used_capabilities]

    compatible = len(missing) == 0

    return MatchResult(
        compatible=compatible,
        matches=matches,
        missing=missing,
        unused=unused,
    )


def find_compatible_stations(product: Product) -> list[StationMatch]:
    """Find all stations that can test the given product.

    Returns a list of StationMatch objects with compatibility details.
    """
    required = get_required_capabilities(product)
    stations_list = list_stations()
    results = []

    for station_info in stations_list:
        station_id = station_info["id"]
        station_config = load_station_config(station_id)

        if not station_config:
            continue

        available = get_station_capabilities(station_config)
        match_result = match_capabilities(required, available)

        results.append(
            StationMatch(
                station_id=station_id,
                station_name=station_info.get("name", station_id),
                compatible=match_result.compatible,
                match_result=match_result,
            )
        )

    return results


def check_station_compatibility(
    product_id: str, station_id: str
) -> dict[str, Any] | None:
    """Check if a specific station can test a specific product.

    Returns detailed match report or None if product/station not found.
    """
    product = load_product_by_id(product_id)
    if not product:
        return None

    station_config = load_station_config(station_id)
    if not station_config:
        return None

    required = get_required_capabilities(product)
    available = get_station_capabilities(station_config)
    match_result = match_capabilities(required, available)

    return {
        "product_id": product_id,
        "station_id": station_id,
        "compatible": match_result.compatible,
        "requirements_count": len(required),
        "satisfied_count": len([m for m in match_result.matches if m.satisfied]),
        "missing_count": len(match_result.missing),
        "missing": [
            {
                "characteristic": m.characteristic_name,
                "direction": m.direction.value,
                "domain": m.domain.value,
                "signal_types": [st.value for st in m.signal_types],
            }
            for m in match_result.missing
        ],
        "matches": [
            {
                "characteristic": m.requirement.characteristic_name,
                "direction": m.requirement.direction.value,
                "domain": m.requirement.domain.value,
                "matched_by": {
                    "instrument": m.matched_by.instrument_name,
                    "capability": m.matched_by.name,
                }
                if m.matched_by
                else None,
            }
            for m in match_result.matches
        ],
    }
