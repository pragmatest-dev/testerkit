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

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litmus.models.catalog import InstrumentCatalogEntry
    from litmus.models.station import StationConfig

from pydantic import BaseModel, Field

from litmus.config.capability import band_matches
from litmus.models.config import (
    AccuracySpec,
    Attribute,
    Condition,
    Control,
    Direction,
    InstrumentCapability,
    MatchDepth,
    MeasurementFunction,
    RangeSpec,
    ResolutionSpec,
    Signal,
    SpecBand,
)
from litmus.models.product import Product, ProductCharacteristic
from litmus.store import (
    get_product,
    get_station,
    list_stations,
    resolve_catalog_ref,
)

logger = logging.getLogger(__name__)


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
    def signals(self) -> dict[str, Signal]:
        return self.capability.signals

    @property
    def conditions(self) -> dict[str, Condition]:
        return self.capability.conditions

    @property
    def attributes(self) -> dict[str, Attribute]:
        return self.capability.attributes


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
    def signals(self) -> dict[str, Signal]:
        return self.capability.signals

    @property
    def conditions(self) -> dict[str, Condition]:
        return self.capability.conditions

    @property
    def controls(self) -> dict[str, Control]:
        return self.capability.controls

    @property
    def attributes(self) -> dict[str, Attribute]:
        return self.capability.attributes

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
# Product/Station listing for API (summary dicts)
# -----------------------------------------------------------------------------


def list_products_summary() -> list[dict[str, Any]]:
    """List all available products as summary dicts (for API compatibility)."""
    from litmus.store import list_products

    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "revision": p.revision,
            "characteristics_count": len(p.characteristics),
        }
        for p in list_products()
    ]


# -----------------------------------------------------------------------------
# Capability Extraction
# -----------------------------------------------------------------------------


def _directions_compatible_direct(required_dir: Direction, instrument_dir: Direction) -> bool:
    """Check direct direction match (requirement specifies instrument direction).

    BIDIR on either side matches anything; otherwise directions must match exactly.
    """
    if instrument_dir == Direction.BIDIR or required_dir == Direction.BIDIR:
        return True
    return required_dir == instrument_dir


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


def get_station_capabilities(station_config: StationConfig) -> list[StationCapability]:
    """Extract all capabilities from a station's instruments.

    Iterates through the station's instruments, loads each instrument's
    library definition, and extracts capabilities. Each capability is
    expanded into per-channel entries so matching can allocate individual
    channels.

    Args:
        station_config: StationConfig model.
    """
    capabilities = []
    instruments = station_config.instruments or {}

    for inst_name, inst_config in instruments.items():
        inst_type = inst_config.type
        catalog_ref = inst_config.catalog_ref

        if not inst_type:
            continue

        if catalog_ref:
            _add_catalog_capabilities(catalog_ref, inst_type, inst_name, capabilities)
        else:
            logger.warning(
                "Instrument '%s' (type=%s) has no catalog_ref — skipping capability resolution",
                inst_name,
                inst_type,
            )

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
    entry = resolve_catalog_ref(catalog_ref)
    if entry:
        for cap in entry.capabilities:
            _expand_capability(cap, inst_type, inst_name, capabilities)


# -----------------------------------------------------------------------------
# Matching Logic
# -----------------------------------------------------------------------------


def capability_satisfies(
    station_cap: StationCapability,
    required: CapabilityRequirement,
    depth: MatchDepth = MatchDepth.RANGE,
    direct_direction: bool = False,
) -> bool:
    """Check if a station capability satisfies a requirement.

    Matching tiers (controlled by depth):
    1. FUNCTION: MeasurementFunction must match
    2. DIRECTION: Direction must match (or station capability is BIDIR)
    3. RANGE: Parameter ranges must contain required values/ranges
    4. ACCURACY: Instrument accuracy must be better than required
    5. RESOLUTION: Instrument resolution must be at least required

    Args:
        direct_direction: If True, requirement specifies the instrument
            capability directly (e.g. "I need an input instrument" matches
            input instruments). If False (default), uses product↔instrument
            pairing (OUTPUT↔INPUT, etc.).
    """
    # Tier 1: Function must match
    if station_cap.function != required.function:
        return False
    if depth == MatchDepth.FUNCTION:
        return True

    # Tier 2: Direction compatibility
    if direct_direction:
        # Direct: requirement specifies what the instrument must be
        # (same-direction match: INPUT→INPUT, OUTPUT→OUTPUT, BIDIR matches all)
        if not _directions_compatible_direct(required.direction, station_cap.direction):
            return False
    else:
        # Product↔instrument pairing (OUTPUT↔INPUT, etc.)
        if not _directions_compatible(required.direction, station_cap.direction):
            return False
    if depth == MatchDepth.DIRECTION:
        return True

    # Build operating point early — needed for condition-dependent range checks
    operating_point = _build_operating_point(
        required.signals, required.conditions, station_cap.controls
    )

    # Tier 3: Signal range containment (condition-aware via SpecBand.range)
    for measure_name, req_measure in required.signals.items():
        inst_measure = station_cap.signals.get(measure_name)
        if inst_measure is None:
            # Instrument doesn't have this measure
            if req_measure.range is not None or req_measure.value is not None:
                return False
            continue
        if not _signal_range_contains(inst_measure, req_measure, operating_point):
            return False

    if depth == MatchDepth.RANGE:
        return True

    # Tier 4: Accuracy check
    for measure_name, req_measure in required.signals.items():
        inst_measure = station_cap.signals.get(measure_name)
        if inst_measure is None:
            continue

        req_acc = _get_accuracy_at(req_measure, operating_point)
        inst_acc = _get_accuracy_at(inst_measure, operating_point)
        if req_acc is not None and inst_acc is not None:
            if not _accuracy_sufficient(inst_acc, req_acc):
                return False
    if depth == MatchDepth.ACCURACY:
        return True

    # Tier 5: Resolution check
    for measure_name, req_measure in required.signals.items():
        inst_measure = station_cap.signals.get(measure_name)
        if inst_measure is None:
            continue
        req_res = _get_resolution_at(req_measure, operating_point)
        inst_res = _get_resolution_at(inst_measure, operating_point)
        if req_res is not None and inst_res is not None:
            if not _resolution_sufficient(inst_res, req_res):
                return False

    return True


def _build_operating_point(
    signals: dict[str, Signal],
    conditions: dict[str, Condition] | None = None,
    controls: dict[str, Control] | None = None,
) -> dict[str, float | str | bool]:
    """Extract operating point values from requirement signals, conditions, and controls."""
    point: dict[str, float | str | bool] = {}
    for name, m in signals.items():
        if m.value is not None:
            point[name] = m.value
        elif m.range is not None:
            if m.range.min is not None and m.range.max is not None:
                point[name] = (m.range.min + m.range.max) / 2
            elif m.range.min is not None:
                point[name] = m.range.min
            elif m.range.max is not None:
                point[name] = m.range.max
    if conditions:
        for name, c in conditions.items():
            if c.range is not None:
                if c.range.min is not None and c.range.max is not None:
                    point[name] = (c.range.min + c.range.max) / 2
                elif c.range.min is not None:
                    point[name] = c.range.min
                elif c.range.max is not None:
                    point[name] = c.range.max
    if controls:
        for name, ctrl in controls.items():
            if name not in point and ctrl.default is not None:
                point[name] = ctrl.default
    return point


def get_spec_at(measure: Signal, operating_point: dict[str, float | str | bool]) -> SpecBand | None:
    """Find the SpecBand that applies at the given operating point.

    Returns None if no band matches (caller should use top-level defaults).
    Multiple ``when`` keys are ANDed — all must match.
    """
    if not measure.specs:
        return None
    for band in measure.specs:
        if band_matches(band, operating_point):
            return band
    return None


def _get_spec_field(
    measure: Signal,
    field: str,
    operating_point: dict[str, float | str | bool],
) -> Any:
    """Get a spec field at an operating point, falling back to top-level.

    Checks the matching SpecBand first; if the field is None or missing,
    returns the top-level value from the Signal itself.
    """
    band = get_spec_at(measure, operating_point)
    if band is not None:
        band_val = getattr(band, field, None)
        if band_val is not None:
            return band_val
    return getattr(measure, field, None)


def _get_accuracy_at(
    measure: Signal,
    operating_point: dict[str, float | str | bool],
) -> AccuracySpec | None:
    return _get_spec_field(measure, "accuracy", operating_point)


def _get_resolution_at(
    measure: Signal,
    operating_point: dict[str, float | str | bool],
) -> ResolutionSpec | None:
    return _get_spec_field(measure, "resolution", operating_point)


def _get_range_at(
    measure: Signal,
    operating_point: dict[str, float | str | bool],
) -> RangeSpec | None:
    return _get_spec_field(measure, "range", operating_point)


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


def _signal_range_contains(
    inst_measure: Signal,
    req_measure: Signal,
    operating_point: dict[str, float | str] | None = None,
) -> bool:
    """Check if instrument measure range contains the required value/range.

    When *operating_point* is provided, the instrument's effective range may
    come from a SpecBand override (derating).  Otherwise the top-level range
    is used.

    Handles both point values and range subsets:
    - req has value: inst range must contain that value
    - req has range: inst range must contain the entire required range
    - req has neither: always satisfied (no constraint)
    """
    inst_range = (
        _get_range_at(inst_measure, operating_point) if operating_point else inst_measure.range
    )

    # If requirement has a fixed value, check it's within instrument range
    if req_measure.value is not None and inst_range is not None:
        if inst_range.min is not None and req_measure.value < inst_range.min:
            return False
        if inst_range.max is not None and req_measure.value > inst_range.max:
            return False
        return True

    # If requirement has a range, check containment
    if req_measure.range is not None and inst_range is not None:
        if (
            req_measure.range.min is not None
            and inst_range.min is not None
            and req_measure.range.min < inst_range.min
        ):
            return False
        if (
            req_measure.range.max is not None
            and inst_range.max is not None
            and req_measure.range.max > inst_range.max
        ):
            return False
        return True

    # No range constraint on requirement, or instrument has no range defined —
    # treat as satisfied (instrument doesn't constrain this dimension)
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

    for station in stations_list:
        station_config = get_station(station.id)

        if not station_config:
            continue

        available = get_station_capabilities(station_config)
        match_result = match_capabilities(required, available)

        results.append(
            StationMatch(
                station_id=station.id,
                station_name=station.name,
                compatible=match_result.compatible,
                match_result=match_result,
            )
        )

    return results


def check_station_compatibility(
    product_id: str,
    station_id: str,
    project: str | Path | None = None,
) -> dict[str, Any] | None:
    """Check if a specific station can test a specific product.

    Returns detailed match report or None if product/station not found.
    """
    product = get_product(product_id)
    if not product:
        return None

    station_config = get_station(station_id)
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


def _evaluate_stations(
    required: list[CapabilityRequirement],
) -> list[tuple[Any, MatchResult, int, int, int]]:
    """Evaluate all stations against requirements.

    Returns list of (station, match_result, satisfied_count, total_count, coverage_pct)
    for each station that has a loadable config.
    """
    results = []
    for station in list_stations():
        station_config = get_station(station.id)
        if not station_config:
            continue

        available = get_station_capabilities(station_config)
        match_result = match_capabilities(required, available)

        satisfied_count = len([m for m in match_result.matches if m.satisfied])
        total_count = len(required)
        coverage_pct = int((satisfied_count / total_count) * 100) if total_count > 0 else 0

        results.append((station, match_result, satisfied_count, total_count, coverage_pct))
    return results


def find_partial_stations(product: Product) -> list[PartialStationMatch]:
    """Find stations with partial capability coverage for a product.

    Returns stations that have some but not all required capabilities.
    Useful for procurement planning - shows what's available and what to order.
    """
    required = get_required_capabilities(product)
    if not required:
        return []

    results = []
    for station, match_result, satisfied_count, total_count, coverage_pct in _evaluate_stations(
        required,
    ):
        if 0 < coverage_pct < 100:
            results.append(
                PartialStationMatch(
                    station_id=station.id,
                    station_name=station.name,
                    location=station.location,
                    coverage_pct=coverage_pct,
                    satisfied_count=satisfied_count,
                    total_count=total_count,
                    missing=[
                        f"{r.function.value} {r.direction.value}" for r in match_result.missing
                    ],
                )
            )

    results.sort(key=lambda x: x.coverage_pct, reverse=True)
    return results


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
    from litmus.store import find_catalog_dirs, load_catalog_from_directory  # noqa: F811

    old_cwd = os.getcwd() if project else None
    if project:
        os.chdir(project)

    try:
        # Load all catalog entries
        all_entries: dict[str, Any] = {}
        for cat_dir in find_catalog_dirs():
            all_entries.update(load_catalog_from_directory(cat_dir))
    finally:
        if old_cwd:
            os.chdir(old_cwd)

    # Convert simplified dicts to CapabilityRequirement objects
    cap_reqs, depths = _parse_requirements(requirements)

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
                if capability_satisfies(cap, req, depth=depths[req_idx], direct_direction=True):
                    satisfied_indices.append(req_idx)
                    break

        if satisfied_indices:
            recommendations.append(
                {
                    "catalog_id": entry.id,
                    "manufacturer": entry.manufacturer,
                    "model": entry.model,
                    "name": entry.name,
                    "type": entry.type,
                    "satisfies": satisfied_indices,
                    "channels": len(entry.channels) or 1,
                }
            )

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


def _infer_depth(req_dict: dict) -> MatchDepth:
    """Determine the deepest match tier from provided fields."""
    if "resolution" in req_dict:
        return MatchDepth.RESOLUTION
    if "accuracy" in req_dict:
        return MatchDepth.ACCURACY
    if "range_min" in req_dict or "range_max" in req_dict:
        return MatchDepth.RANGE
    return MatchDepth.DIRECTION


def _parse_requirements(
    raw: list[dict[str, Any]],
) -> tuple[list[CapabilityRequirement], list[MatchDepth]]:
    """Convert simplified requirement dicts to CapabilityRequirement objects.

    Returns:
        Tuple of (requirements, depths) where depths[i] is the auto-inferred
        match depth for requirements[i].
    """
    reqs = []
    depths = []
    for i, r in enumerate(raw):
        function = MeasurementFunction(r["function"])
        direction = Direction(r["direction"])

        signals: dict[str, Signal] = {}

        # Build a range measure from range_min/range_max if provided
        range_min = r.get("range_min")
        range_max = r.get("range_max")
        units = r.get("units", "")

        measure_name = function.value.replace("dc_", "").replace("ac_", "")

        if range_min is not None or range_max is not None:
            signals[measure_name] = Signal(
                range=RangeSpec(min=range_min, max=range_max, units=units),
            )

        # Apply accuracy to the signal if provided
        if "accuracy" in r:
            if measure_name not in signals:
                signals[measure_name] = Signal()
            signals[measure_name].accuracy = AccuracySpec(**r["accuracy"])

        # Apply resolution to the signal if provided
        if "resolution" in r:
            if measure_name not in signals:
                signals[measure_name] = Signal()
            signals[measure_name].resolution = ResolutionSpec(**r["resolution"])

        # Parse conditions
        conditions: dict[str, Condition] = {}
        if "conditions" in r:
            for cond_name, cond_spec in r["conditions"].items():
                conditions[cond_name] = Condition(range=RangeSpec(**cond_spec))

        # Build a synthetic ProductCharacteristic for the wrapper
        char = ProductCharacteristic(
            function=function,
            direction=direction,
            signals=signals,
            conditions=conditions,
            units=units or None,
            net=f"req_{i}",  # Synthetic physical interface
        )

        reqs.append(
            CapabilityRequirement(
                capability=char,
                characteristic_name=f"req_{i}",
            )
        )
        depths.append(_infer_depth(r))
    return reqs, depths


def _catalog_entry_to_capabilities(entry: InstrumentCatalogEntry) -> list[StationCapability]:
    """Convert a catalog entry's capabilities to StationCapability objects."""
    caps: list[StationCapability] = []
    for cap in entry.capabilities:
        _expand_capability(cap, entry.type or "", entry.id or "", caps)
    return caps


def find_all_station_matches(product: Product) -> dict[str, list[dict[str, Any]]]:
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

    compatible = []
    partial = []
    incompatible = []

    for station, match_result, satisfied_count, total_count, coverage_pct in _evaluate_stations(
        required,
    ):
        station_data = {
            "id": station.id,
            "name": station.name,
            "location": station.location,
            "coverage": coverage_pct,
            "satisfied": satisfied_count,
            "total": total_count,
            "missing": [f"{r.function.value} {r.direction.value}" for r in match_result.missing],
        }

        if coverage_pct == 100:
            compatible.append(station_data)
        elif coverage_pct > 0:
            partial.append(station_data)
        else:
            incompatible.append(station_data)

    partial.sort(key=lambda x: x["coverage"], reverse=True)

    return {"compatible": compatible, "partial": partial, "incompatible": incompatible}
