"""Capability matching service.

Provides deterministic matching between product requirements and station capabilities.
This is the core service layer that the UI, API, and MCP tools all use.

Key concepts:
- Products define characteristics (ProductCharacteristic extends Capability)
- Instruments define capabilities (InstrumentCapability extends Capability)
- Both share the same base: function + direction + parameters + specs
- Direction pairing happens here: DUT OUTPUT ↔ Instrument INPUT
- Matching is multi-tier: function → direction → range → accuracy → resolution
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from litmus.config.models import (
    AccuracySpec,
    CompareMode,
    Direction,
    InstrumentCapability,
    MatchDepth,
    MeasurementFunction,
    ResolutionSpec,
    SignalParameter,
    SpecBand,
)
from litmus.products.loader import load_product
from litmus.products.models import Product, ProductCharacteristic
from litmus.utils.loaders import find_yaml_files, load_yaml_file
from litmus.utils.paths import get_instrument_paths, get_station_paths


class CapabilityRequirement(BaseModel):
    """A required instrument capability derived from a product characteristic."""

    capability: ProductCharacteristic
    characteristic_name: str  # Which product characteristic this came from
    pins: list[str] = Field(default_factory=list)  # DUT pins for traceability

    # Convenience accessors
    @property
    def function(self) -> MeasurementFunction:
        return self.capability.function

    @property
    def direction(self) -> Direction:
        return self.capability.direction

    @property
    def parameters(self) -> dict[str, SignalParameter]:
        return self.capability.parameters


class StationCapability(BaseModel):
    """A capability provided by a station instrument."""

    capability: InstrumentCapability
    instrument_type: str  # Which instrument type provides this
    instrument_name: str  # Instance name in station config
    channel: str | None = None  # Specific channel this capability is on

    # Convenience accessors
    @property
    def function(self) -> MeasurementFunction:
        return self.capability.function

    @property
    def direction(self) -> Direction:
        return self.capability.direction

    @property
    def parameters(self) -> dict[str, SignalParameter]:
        return self.capability.parameters

    @property
    def name(self) -> str:
        return f"{self.capability.function.value}_{self.capability.direction.value}"

    @property
    def readback(self) -> bool:
        return self.capability.readback


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


def _get_product_paths(project: str | Path | None = None) -> list[Path]:
    """Get search paths for product folders."""
    root = Path(project) if project else Path.cwd()
    return [root / "products", root / "demo" / "products"]


def load_product_by_id(
    product_id: str, project: str | Path | None = None,
) -> Product | None:
    """Load a Product model by ID from products directory.

    Products are stored in folder structure: products/{product_id}/spec.yaml
    """
    for products_dir in _get_product_paths(project):
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
                    "characteristics_count_total": len(product.characteristics),
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


def _directions_compatible(product_dir: Direction, instrument_dir: Direction) -> bool:
    """Check if product and instrument directions are compatible for matching.

    Direction pairing rules:
    - DUT OUTPUT → Instrument INPUT (measure what DUT provides)
    - DUT INPUT → Instrument OUTPUT (source what DUT needs)
    - DUT BIDIR → Instrument BIDIR only
    - Instrument BIDIR satisfies any product direction
    """
    if instrument_dir == Direction.BIDIR:
        return True
    if product_dir == Direction.OUTPUT:
        return instrument_dir == Direction.INPUT
    if product_dir == Direction.INPUT:
        return instrument_dir == Direction.OUTPUT
    if product_dir == Direction.BIDIR:
        return instrument_dir == Direction.BIDIR
    if product_dir == Direction.TRANSFORM:
        return instrument_dir == Direction.TRANSFORM
    return False


def get_required_capabilities(product: Product) -> list[CapabilityRequirement]:
    """Derive required instrument capabilities from product characteristics.

    Wraps each ProductCharacteristic directly — no lossy conversion.
    Direction pairing happens in capability_satisfies() via _directions_compatible().
    """
    requirements = []

    for char_name, char in product.characteristics.items():
        requirements.append(
            CapabilityRequirement(
                capability=char,
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
            for cap_data in library["capabilities"]:
                try:
                    function = MeasurementFunction(cap_data["function"])
                    direction = Direction(cap_data["direction"])
                except (ValueError, KeyError):
                    continue

                # Parse parameters
                params: dict[str, SignalParameter] = {}
                for param_name, param_data in cap_data.get("parameters", {}).items():
                    params[param_name] = _parse_signal_parameter(param_data)

                readback = bool(cap_data.get("readback", False))
                channels = _normalize_channels(cap_data.get("channels"))

                inst_cap = InstrumentCapability(
                    function=function,
                    direction=direction,
                    parameters=params,
                    channels=channels,
                    modes=cap_data.get("modes", []),
                    readback=readback,
                )

                _expand_capability(inst_cap, inst_type, inst_name, capabilities)

    return capabilities


def _expand_capability(
    cap: InstrumentCapability,
    inst_type: str,
    inst_name: str,
    capabilities: list[StationCapability],
) -> None:
    """Expand an InstrumentCapability into per-channel StationCapability entries."""
    if cap.resolved_channels:
        for ch in cap.resolved_channels:
            capabilities.append(
                StationCapability(
                    capability=cap,
                    instrument_type=inst_type,
                    instrument_name=inst_name,
                    channel=ch,
                )
            )
    else:
        capabilities.append(
            StationCapability(
                capability=cap,
                instrument_type=inst_type,
                instrument_name=inst_name,
            )
        )


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
            _expand_capability(cap, inst_type, inst_name, capabilities)


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
    station_cap: StationCapability,
    required: CapabilityRequirement,
    depth: MatchDepth = MatchDepth.RANGE,
) -> bool:
    """Check if a station capability satisfies a requirement.

    Matching tiers (controlled by depth):
    1. FUNCTION: MeasurementFunction must match
    2. DIRECTION: Direction must match (or station capability is BIDIR)
    3. RANGE: Parameter ranges must contain required values/ranges
    4. ACCURACY: Instrument accuracy must be better than required
    5. RESOLUTION: Instrument resolution must be at least required
    """
    # Tier 1: Function must match
    if station_cap.function != required.function:
        return False
    if depth == MatchDepth.FUNCTION:
        return True

    # Tier 2: Direction compatibility (product OUTPUT ↔ instrument INPUT, etc.)
    if not _directions_compatible(required.direction, station_cap.direction):
        return False
    if depth == MatchDepth.DIRECTION:
        return True

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
    if depth == MatchDepth.RANGE:
        return True

    # Build operating point from required parameters for spec band lookup
    operating_point = _build_operating_point(required.parameters)

    # Tier 4: Accuracy check
    for param_name, req_param in required.parameters.items():
        inst_param = station_cap.parameters.get(param_name)
        if inst_param is None:
            continue

        # Check capability value comparison (higher_better / lower_better)
        if not _capability_value_sufficient(inst_param, req_param, operating_point):
            return False

        req_acc = _get_accuracy_at(req_param, operating_point)
        inst_acc = _get_accuracy_at(inst_param, operating_point)
        if req_acc is not None and inst_acc is not None:
            if not _accuracy_sufficient(inst_acc, req_acc):
                return False
    if depth == MatchDepth.ACCURACY:
        return True

    # Tier 5: Resolution check
    for param_name, req_param in required.parameters.items():
        inst_param = station_cap.parameters.get(param_name)
        if inst_param is None:
            continue
        req_res = _get_resolution_at(req_param, operating_point)
        inst_res = _get_resolution_at(inst_param, operating_point)
        if req_res is not None and inst_res is not None:
            if not _resolution_sufficient(inst_res, req_res):
                return False

    return True


def _build_operating_point(parameters: dict[str, SignalParameter]) -> dict[str, float]:
    """Extract operating point values from requirement parameters."""
    point: dict[str, float] = {}
    for name, param in parameters.items():
        if param.value is not None:
            point[name] = param.value
        elif param.range is not None:
            # Use midpoint of range as operating point
            if param.range.min is not None and param.range.max is not None:
                point[name] = (param.range.min + param.range.max) / 2
            elif param.range.min is not None:
                point[name] = param.range.min
            elif param.range.max is not None:
                point[name] = param.range.max
    return point


def get_spec_at(
    param: SignalParameter, operating_point: dict[str, float]
) -> SpecBand | None:
    """Find the SpecBand that applies at the given operating point.

    Returns None if no band matches (caller should use top-level defaults).
    Multiple ``when`` keys are ANDed — all must match.
    """
    if not param.specs:
        return None
    for band in param.specs:
        if _band_matches(band, operating_point):
            return band
    return None


def _band_matches(band: SpecBand, operating_point: dict[str, float]) -> bool:
    """Check if all conditions in a SpecBand match the operating point."""
    for key, range_spec in band.conditions.items():
        val = operating_point.get(key)
        if val is None:
            return False
        if range_spec.min is not None and val < range_spec.min:
            return False
        if range_spec.max is not None and val > range_spec.max:
            return False
    return True


def _get_accuracy_at(
    param: SignalParameter, operating_point: dict[str, float]
) -> AccuracySpec | None:
    """Get the applicable accuracy for a parameter at an operating point."""
    band = get_spec_at(param, operating_point)
    if band is not None and band.accuracy is not None:
        return band.accuracy
    return param.accuracy


def _get_resolution_at(
    param: SignalParameter, operating_point: dict[str, float]
) -> ResolutionSpec | None:
    """Get the applicable resolution for a parameter at an operating point."""
    band = get_spec_at(param, operating_point)
    if band is not None and band.resolution is not None:
        return band.resolution
    return param.resolution


def _get_value_at(
    param: SignalParameter, operating_point: dict[str, float]
) -> float | None:
    """Get the applicable value for a parameter at an operating point."""
    band = get_spec_at(param, operating_point)
    if band is not None and band.value is not None:
        return band.value
    return param.value


def _accuracy_sufficient(inst: AccuracySpec, req: AccuracySpec) -> bool:
    """Check if instrument accuracy is better than (<=) required.

    Lower values = better accuracy. Each component checked independently;
    instrument must be better on every specified component.
    """
    if req.pct_reading is not None and inst.pct_reading is not None:
        if inst.pct_reading > req.pct_reading:
            return False
    if req.pct_range is not None and inst.pct_range is not None:
        if inst.pct_range > req.pct_range:
            return False
    if req.absolute is not None and inst.absolute is not None:
        if inst.absolute > req.absolute:
            return False
    return True


def _resolution_sufficient(inst: ResolutionSpec, req: ResolutionSpec) -> bool:
    """Check if instrument resolution meets or exceeds required.

    Higher bits/digits = better. Lower absolute value = better.
    """
    if req.bits is not None and inst.bits is not None:
        if inst.bits < req.bits:
            return False
    if req.digits is not None and inst.digits is not None:
        if inst.digits < req.digits:
            return False
    if req.value is not None and inst.value is not None:
        # Lower absolute resolution value = finer resolution = better
        if inst.value > req.value:
            return False
    return True


def _capability_value_sufficient(
    inst_param: SignalParameter,
    req_param: SignalParameter,
    operating_point: dict[str, float],
) -> bool:
    """Check capability value using compare mode (higher_better / lower_better).

    Only applies to parameters with a compare mode set. For CONTAINS mode
    (default), range containment already handles it.
    """
    compare = inst_param.compare or req_param.compare
    if compare is None or compare == CompareMode.CONTAINS:
        return True

    inst_val = _get_value_at(inst_param, operating_point)
    req_val = _get_value_at(req_param, operating_point)
    if inst_val is None or req_val is None:
        return True  # Can't compare without values

    if compare == CompareMode.HIGHER_BETTER:
        return inst_val >= req_val
    if compare == CompareMode.LOWER_BETTER:
        return inst_val <= req_val
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
    product_id: str, station_id: str, project: str | Path | None = None,
) -> dict[str, Any] | None:
    """Check if a specific station can test a specific product.

    Returns detailed match report or None if product/station not found.
    """
    product = load_product_by_id(product_id, project)
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


def _catalog_cap_matches(
    cap: StationCapability,
    req: CapabilityRequirement,
) -> bool:
    """Direct capability matching for catalog search (no direction pairing).

    Unlike capability_satisfies() which pairs product↔instrument directions
    (OUTPUT↔INPUT), this does direct matching: the requirement specifies the
    instrument capability needed, so directions must match directly.
    """
    if cap.function != req.function:
        return False
    # Direct direction match (not paired)
    if req.direction != Direction.BIDIR and cap.direction != req.direction:
        if cap.direction != Direction.BIDIR:
            return False
    # Range containment
    for param_name, req_param in req.parameters.items():
        inst_param = cap.parameters.get(param_name)
        if inst_param is None:
            if req_param.range is not None or req_param.value is not None:
                return False
            continue
        if not _range_contains(inst_param, req_param):
            return False
    return True


def recommend_from_catalog(
    requirements: list[dict[str, Any]],
    project: Path | None = None,
) -> dict[str, Any]:
    """Recommend catalog instruments that satisfy ad-hoc capability requirements.

    Takes simplified requirement dicts (as an AI agent would construct) and
    searches the catalog for instruments that can satisfy them.

    Args:
        requirements: List of dicts with keys: function, direction,
            range_max, range_min, units (all optional except function/direction).
        project: Project root for locating catalog dirs. Defaults to cwd.

    Returns:
        Dict with requirements, recommendations (sorted by coverage), and
        coverage summary.
    """
    from litmus.catalog.loader import find_catalog_dirs, load_catalog_from_directory

    if project:
        import os

        old_cwd = os.getcwd()
        os.chdir(project)

    try:
        # Load all catalog entries
        all_entries: dict[str, Any] = {}
        for cat_dir in find_catalog_dirs():
            all_entries.update(load_catalog_from_directory(cat_dir))
    finally:
        if project:
            os.chdir(old_cwd)

    # Convert simplified dicts to CapabilityRequirement objects
    cap_reqs = _parse_requirements(requirements)

    # For each catalog entry, check which requirements it satisfies
    recommendations = []
    coverage: dict[str, list[str]] = {}

    for req_idx, req in enumerate(cap_reqs):
        key = f"{req_idx}:{req.function.value}:{req.direction.value}"
        coverage[key] = []

    for entry_id, entry in all_entries.items():
        # Expand capabilities per-channel into StationCapability objects
        caps = _catalog_entry_to_capabilities(entry)
        satisfied_indices: list[int] = []

        for req_idx, req in enumerate(cap_reqs):
            for cap in caps:
                if _catalog_cap_matches(cap, req):
                    satisfied_indices.append(req_idx)
                    break

        if satisfied_indices:
            recommendations.append({
                "catalog_id": entry.id,
                "manufacturer": entry.manufacturer,
                "model": entry.model,
                "name": entry.name,
                "type": entry.type,
                "satisfies": satisfied_indices,
                "channels": len(entry.channels) or 1,
            })

            for idx in satisfied_indices:
                req = cap_reqs[idx]
                key = f"{idx}:{req.function.value}:{req.direction.value}"
                coverage[key].append(entry.id)

    # Sort by coverage (most requirements satisfied first), then alphabetically
    recommendations.sort(key=lambda r: (-len(r["satisfies"]), r["catalog_id"]))

    return {
        "requirements": requirements,
        "recommendations": recommendations,
        "coverage": coverage,
    }


def _parse_requirements(raw: list[dict[str, Any]]) -> list[CapabilityRequirement]:
    """Convert simplified requirement dicts to CapabilityRequirement objects."""
    from litmus.config.models import RangeSpec

    reqs = []
    for i, r in enumerate(raw):
        function = MeasurementFunction(r["function"])
        direction = Direction(r["direction"])

        parameters: dict[str, SignalParameter] = {}

        # Build a range parameter from range_min/range_max if provided
        range_min = r.get("range_min")
        range_max = r.get("range_max")
        units = r.get("units", "")

        if range_min is not None or range_max is not None:
            # Derive parameter name from function (e.g., dc_voltage -> voltage)
            param_name = function.value.replace("dc_", "").replace("ac_", "")
            parameters[param_name] = SignalParameter(
                range=RangeSpec(min=range_min, max=range_max, units=units),
            )

        # Build a synthetic ProductCharacteristic for the wrapper
        char = ProductCharacteristic(
            function=function,
            direction=direction,
            parameters=parameters,
            units=units or None,
            net=f"req_{i}",  # Synthetic physical interface
        )

        reqs.append(CapabilityRequirement(
            capability=char,
            characteristic_name=f"req_{i}",
        ))
    return reqs


def _catalog_entry_to_capabilities(entry: Any) -> list[StationCapability]:
    """Convert a catalog entry's capabilities to StationCapability objects."""
    caps: list[StationCapability] = []
    for cap in entry.capabilities:
        _expand_capability(cap, entry.type, entry.id, caps)
    return caps


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
