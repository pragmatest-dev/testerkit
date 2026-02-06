"""Capability matching service.

Provides deterministic matching between product requirements and station capabilities.
This is the core service layer that the UI, API, and MCP tools all use.

Key concepts:
- Products define characteristics with MeasurementFunction + Direction + parameters
- Characteristics derive capability requirements via direction pairing:
  - DUT OUTPUT -> Instrument INPUT (measure what DUT provides)
  - DUT INPUT -> Instrument OUTPUT (source what DUT needs)
- Stations have instruments with FunctionCapability entries
- Matching is 3-tier: function match -> direction match -> parameter range containment
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from litmus.config.models import (
    Direction,
    MeasurementFunction,
    SignalParameter,
)
from litmus.products.loader import load_product
from litmus.products.models import Product
from litmus.utils.loaders import find_yaml_files, load_yaml_file
from litmus.utils.paths import get_instrument_paths, get_station_paths


class CapabilityRequirement(BaseModel):
    """A required instrument capability derived from a product characteristic."""

    function: MeasurementFunction
    direction: Direction
    parameters: dict[str, SignalParameter] = Field(default_factory=dict)
    characteristic_name: str  # Which product characteristic this came from
    pins: list[str] = Field(default_factory=list)  # DUT pins for traceability


class StationCapability(BaseModel):
    """A capability provided by a station instrument."""

    function: MeasurementFunction
    direction: Direction
    parameters: dict[str, SignalParameter] = Field(default_factory=dict)
    name: str  # Capability name
    instrument_type: str  # Which instrument type provides this
    instrument_name: str  # Instance name in station config
    channel: str | None = None  # Specific channel this capability is on
    readback: bool = False  # Built-in meter, not primary measurement


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


class PartialStationMatch(BaseModel):
    """Summary of a station's partial compatibility with a product.

    Used for procurement planning - shows what's available and what's missing.
    """

    station_id: str
    station_name: str
    location: str | None = None
    coverage_pct: int  # Percentage of requirements satisfied (0-100)
    satisfied_count: int
    total_count: int
    missing: list[str] = Field(default_factory=list)  # Human-readable missing capabilities


# -----------------------------------------------------------------------------
# Loaders
# -----------------------------------------------------------------------------


def _get_product_paths() -> list[Path]:
    """Get search paths for product folders."""
    return [Path.cwd() / "products", Path.cwd() / "demo" / "products"]


def load_product_by_id(product_id: str) -> Product | None:
    """Load a Product model by ID from products directory.

    Products are stored in folder structure: products/{product_id}/spec.yaml
    """
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
        # Fallback: search all folders for matching product ID
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


def list_products() -> list[dict[str, Any]]:
    """List all available products.

    Products are stored in folder structure: products/{product_id}/spec.yaml
    """
    products = []
    seen_ids = set()

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
    """Load instrument capabilities from library YAML.

    Searches user's instruments/ first, then falls back to built-in library.
    """
    for library_path in get_instrument_paths():
        yaml_file = library_path / f"{instrument_type}.yaml"
        data = load_yaml_file(yaml_file)
        if data is not None:
            return data
    return None


def list_instrument_types() -> list[str]:
    """List available instrument types from all library locations.

    User-defined instruments appear alongside built-in ones.
    """
    seen = set()
    types = []

    for yaml_file, _ in find_yaml_files(get_instrument_paths(), prefix_skip=""):
        if yaml_file.stem not in seen:
            seen.add(yaml_file.stem)
            types.append(yaml_file.stem)

    return sorted(types)


def load_station_config(station_id: str) -> dict | None:
    """Load station configuration by ID."""
    for _, data in find_yaml_files(get_station_paths()):
        if data and "station" in data:
            station_info = data["station"]
            if station_info.get("id") == station_id:
                return data
    return None


def list_stations() -> list[dict[str, Any]]:
    """List all available stations."""
    stations = []

    for yaml_file, data in find_yaml_files(get_station_paths()):
        if data and "station" in data:
            station_info = data["station"]
            stations.append({
                "id": station_info.get("id", yaml_file.stem),
                "name": station_info.get("name", yaml_file.stem),
                "location": station_info.get("location"),
                "description": station_info.get("description"),
            })

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

        requirements.append(
            CapabilityRequirement(
                function=cap.function,
                direction=cap.direction,
                parameters=cap.parameters,
                characteristic_name=char_name,
                pins=char.resolved_pins,
            )
        )

    return requirements


def get_station_capabilities(station_config: dict) -> list[StationCapability]:
    """Extract all capabilities from a station's instruments.

    Iterates through the station's instruments, loads each instrument's
    library definition, and extracts capabilities. Each capability is
    expanded into per-channel entries so matching can allocate individual
    channels.
    """
    from litmus.catalog.loader import _normalize_channels

    capabilities = []
    # Instruments can be at root level or inside station block
    instruments = station_config.get("instruments", {})
    if not instruments and "station" in station_config:
        instruments = station_config["station"].get("instruments", {})

    for inst_name, inst_config in instruments.items():
        inst_type = inst_config.get("type")
        if not inst_type:
            continue

        # catalog_ref takes priority (model-specific data > generic library)
        catalog_ref = inst_config.get("catalog_ref")
        if catalog_ref:
            _add_catalog_capabilities(catalog_ref, inst_type, inst_name, capabilities)
            continue

        # Fall back to generic instrument library
        library = load_instrument_library(inst_type)
        if library and "capabilities" in library:
            for cap in library["capabilities"]:
                try:
                    function = MeasurementFunction(cap["function"])
                    direction = Direction(cap["direction"])
                except (ValueError, KeyError):
                    continue

                # Parse parameters
                params: dict[str, SignalParameter] = {}
                for param_name, param_data in cap.get("parameters", {}).items():
                    params[param_name] = _parse_signal_parameter(param_data)

                cap_name = cap.get("name", f"{function.value}_{direction.value}")
                readback = bool(cap.get("readback", False))

                # Expand per-channel
                channels = _normalize_channels(cap.get("channels"))

                if channels:
                    for ch in channels:
                        capabilities.append(
                            StationCapability(
                                function=function,
                                direction=direction,
                                parameters=params,
                                name=cap_name,
                                instrument_type=inst_type,
                                instrument_name=inst_name,
                                channel=ch,
                                readback=readback,
                            )
                        )
                else:
                    capabilities.append(
                        StationCapability(
                            function=function,
                            direction=direction,
                            parameters=params,
                            name=cap_name,
                            instrument_type=inst_type,
                            instrument_name=inst_name,
                            readback=readback,
                        )
                    )

    return capabilities


def _add_catalog_capabilities(
    catalog_ref: str,
    inst_type: str,
    inst_name: str,
    capabilities: list[StationCapability],
) -> None:
    """Add capabilities from a catalog reference, expanded per-channel."""
    from litmus.catalog.loader import resolve_catalog_ref

    entry = resolve_catalog_ref(catalog_ref)
    if entry:
        for cap in entry.capabilities:
            cap_name = f"{cap.function.value}_{cap.direction.value}"
            channels = cap.channels

            if channels:
                for ch in channels:
                    capabilities.append(
                        StationCapability(
                            function=cap.function,
                            direction=cap.direction,
                            parameters=cap.parameters,
                            name=cap_name,
                            instrument_type=inst_type,
                            instrument_name=inst_name,
                            channel=ch,
                            readback=cap.readback,
                        )
                    )
            else:
                capabilities.append(
                    StationCapability(
                        function=cap.function,
                        direction=cap.direction,
                        parameters=cap.parameters,
                        name=cap_name,
                        instrument_type=inst_type,
                        instrument_name=inst_name,
                        readback=cap.readback,
                    )
                )


def _parse_signal_parameter(data: dict[str, Any]) -> SignalParameter:
    """Parse a SignalParameter from dict data."""
    from litmus.config.models import (
        AccuracySpec,
        ParameterRole,
        RangeSpec,
        ResolutionSpec,
    )

    range_spec = None
    if "range" in data:
        r = data["range"]
        range_spec = RangeSpec(min=r.get("min"), max=r.get("max"), units=r.get("units", ""))

    accuracy_spec = None
    if "accuracy" in data:
        a = data["accuracy"]
        accuracy_spec = AccuracySpec(
            pct_reading=a.get("pct_reading"),
            pct_range=a.get("pct_range"),
            absolute=a.get("absolute"),
        )

    resolution_spec = None
    if "resolution" in data:
        r = data["resolution"]
        resolution_spec = ResolutionSpec(
            bits=r.get("bits"), digits=r.get("digits"), value=r.get("value"), units=r.get("units")
        )

    role = ParameterRole.CONTROLLABLE
    if "role" in data:
        role = ParameterRole(data["role"])

    return SignalParameter(
        range=range_spec,
        accuracy=accuracy_spec,
        resolution=resolution_spec,
        value=data.get("value"),
        units=data.get("units"),
        role=role,
    )


# -----------------------------------------------------------------------------
# Matching Logic
# -----------------------------------------------------------------------------


def capability_satisfies(
    station_cap: StationCapability, required: CapabilityRequirement
) -> bool:
    """Check if a station capability satisfies a requirement.

    3-tier matching:
    1. Function must match
    2. Direction must match (or station capability is BIDIR)
    3. Parameter ranges must contain required values/ranges
    """
    # Tier 1: Function must match
    if station_cap.function != required.function:
        return False

    # Tier 2: Direction must match (or station cap is bidir)
    if station_cap.direction != required.direction:
        if station_cap.direction != Direction.BIDIR:
            return False

    # Tier 3: Parameter range containment
    for param_name, req_param in required.parameters.items():
        inst_param = station_cap.parameters.get(param_name)
        if inst_param is None:
            # Instrument doesn't have this parameter
            if req_param.range is not None or req_param.value is not None:
                return False
            continue
        if not _range_contains(inst_param, req_param):
            return False

    return True


def _range_contains(inst_param: SignalParameter, req_param: SignalParameter) -> bool:
    """Check if instrument parameter range contains the required value/range.

    Handles both point values and range subsets:
    - req has value: inst range must contain that value
    - req has range: inst range must contain the entire required range
    - req has neither: always satisfied (no constraint)
    """
    # If requirement has a fixed value, check it's within instrument range
    if req_param.value is not None and inst_param.range is not None:
        if inst_param.range.min is not None and req_param.value < inst_param.range.min:
            return False
        if inst_param.range.max is not None and req_param.value > inst_param.range.max:
            return False
        return True

    # If requirement has a range, check containment
    if req_param.range is not None and inst_param.range is not None:
        if (
            req_param.range.min is not None
            and inst_param.range.min is not None
            and req_param.range.min < inst_param.range.min
        ):
            return False
        if (
            req_param.range.max is not None
            and inst_param.range.max is not None
            and req_param.range.max > inst_param.range.max
        ):
            return False
        return True

    # No range constraint on requirement — always satisfied
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

        # Find an unused capability that satisfies this requirement
        for i, avail in enumerate(available):
            if i in used_capabilities:
                continue
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
                "function": m.function.value,
                "direction": m.direction.value,
            }
            for m in match_result.missing
        ],
        "matches": [
            {
                "characteristic": m.requirement.characteristic_name,
                "function": m.requirement.function.value,
                "direction": m.requirement.direction.value,
                "matched_by": {
                    "instrument": m.matched_by.instrument_name,
                    "capability": m.matched_by.name,
                    "channel": m.matched_by.channel,
                }
                if m.matched_by
                else None,
            }
            for m in match_result.matches
        ],
    }


def find_partial_stations(product: Product) -> list[PartialStationMatch]:
    """Find stations with partial capability coverage for a product.

    Returns stations that have some but not all required capabilities.
    Useful for procurement planning - shows what's available and what to order.
    """
    required = get_required_capabilities(product)
    if not required:
        return []

    stations_list = list_stations()
    results = []

    for station_info in stations_list:
        station_id = station_info["id"]
        station_config = load_station_config(station_id)

        if not station_config:
            continue

        available = get_station_capabilities(station_config)
        match_result = match_capabilities(required, available)

        satisfied_count = len([m for m in match_result.matches if m.satisfied])
        total_count = len(required)
        coverage_pct = int((satisfied_count / total_count) * 100) if total_count > 0 else 0

        # Only include partial matches (not 0% and not 100%)
        if 0 < coverage_pct < 100:
            missing_readable = []
            for req in match_result.missing:
                missing_readable.append(f"{req.function.value} {req.direction.value}")

            results.append(
                PartialStationMatch(
                    station_id=station_id,
                    station_name=station_info.get("name", station_id),
                    location=station_info.get("location"),
                    coverage_pct=coverage_pct,
                    satisfied_count=satisfied_count,
                    total_count=total_count,
                    missing=missing_readable,
                )
            )

    # Sort by coverage (highest first)
    results.sort(key=lambda x: x.coverage_pct, reverse=True)
    return results


def find_all_station_matches(product: Product) -> dict[str, list]:
    """Find all stations categorized by compatibility level.

    Returns:
        Dict with keys:
        - "compatible": Fully compatible stations (100% coverage)
        - "partial": Partially compatible stations (0 < coverage < 100%)
        - "incompatible": Stations with 0% coverage
    """
    required = get_required_capabilities(product)
    if not required:
        return {"compatible": [], "partial": [], "incompatible": []}

    stations_list = list_stations()
    compatible = []
    partial = []
    incompatible = []

    for station_info in stations_list:
        station_id = station_info["id"]
        station_config = load_station_config(station_id)

        if not station_config:
            continue

        available = get_station_capabilities(station_config)
        match_result = match_capabilities(required, available)

        satisfied_count = len([m for m in match_result.matches if m.satisfied])
        total_count = len(required)
        coverage_pct = int((satisfied_count / total_count) * 100) if total_count > 0 else 0

        station_data = {
            "id": station_id,
            "name": station_info.get("name", station_id),
            "location": station_info.get("location"),
            "coverage": coverage_pct,
            "satisfied": satisfied_count,
            "total": total_count,
            "missing": [
                f"{r.function.value} {r.direction.value}" for r in match_result.missing
            ],
        }

        if coverage_pct == 100:
            compatible.append(station_data)
        elif coverage_pct > 0:
            partial.append(station_data)
        else:
            incompatible.append(station_data)

    # Sort partial by coverage (highest first)
    partial.sort(key=lambda x: x["coverage"], reverse=True)

    return {"compatible": compatible, "partial": partial, "incompatible": incompatible}
