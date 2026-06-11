"""Capability matching helpers for the system designer.

Wraps litmus.matching.service to provide UI-specific matching:
- Pin -> characteristic reverse map
- Compatible channels for a selected pin
- Auto-suggest connections for bulk wiring (3-phase algorithm)

Phase 1: Signal/power pins via capability matching (exclusive channels, readback excluded)
Phase 2: Ground pins via bus wiring (fan-out to LO terminals of allocated channels)
Phase 3: Report unmatched pins
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, overload

from litmus.models.capability import Capability, InstrumentCapability, Signal
from litmus.models.enums import Direction, MeasurementFunction

if TYPE_CHECKING:
    from litmus.models.part import Part


def build_pin_characteristic_map(part: Part) -> dict[str, list[str]]:
    """Build reverse map: pin_key -> [characteristic names that reference it].

    This is used to look up which characteristics apply to a given pin,
    enabling capability matching when the user selects a pin.
    """
    result: dict[str, list[str]] = {}
    for char_name, char in part.characteristics.items():
        for pin_key in char.resolved_pins:
            if pin_key not in result:
                result[pin_key] = []
            result[pin_key].append(char_name)
    return result


@overload
def get_compatible_channels_for_pin(
    pin_key: str,
    char_by_pin: dict[str, list[str]],
    part: Part | None,
    instruments: dict[str, dict],
    dut_pins: dict[str, dict] | None = None,
    *,
    include_direction: Literal[True],
) -> dict[str, Direction]: ...


@overload
def get_compatible_channels_for_pin(
    pin_key: str,
    char_by_pin: dict[str, list[str]],
    part: Part | None,
    instruments: dict[str, dict],
    dut_pins: dict[str, dict] | None = None,
    *,
    include_direction: Literal[False] = False,
) -> set[str]: ...


def get_compatible_channels_for_pin(
    pin_key: str,
    char_by_pin: dict[str, list[str]],
    part: Part | None,
    instruments: dict[str, dict],
    dut_pins: dict[str, dict] | None = None,
    *,
    include_direction: bool = False,
) -> set[str] | dict[str, Direction]:
    """Get instrument channels compatible with a selected pin.

    Returns "role:channel" keys that can handle this pin's characteristics.
    Only channels whose capability parameters actually satisfy the requirement
    are included (not all channels on the instrument).

    For ground pins (role == "ground"), returns LO terminals of all channels
    instead of signal-matching channels.

    When no characteristic data is available, all channels are returned
    as compatible (manual wiring mode).

    Args:
        pin_key: The selected pin key.
        char_by_pin: Reverse map from build_pin_characteristic_map().
        part: Part model (may be None).
        instruments: Dict of role -> {type, driver, capabilities, channels}.
        dut_pins: Dict of pin_key -> pin data (with role field).
        include_direction: If True, return dict mapping channel to Direction.

    Returns:
        Set of "role:channel" strings, or dict of {channel: Direction} if
        include_direction=True.
    """
    # Check if this is a ground pin
    pin_role = _get_pin_role(pin_key, dut_pins)
    if pin_role == "ground":
        lo_channels = _get_lo_channels(instruments)
        if include_direction:
            # Ground wiring doesn't have a meaningful direction
            return {ch: Direction.INPUT for ch in lo_channels}
        return lo_channels

    # Get all channels across all instruments
    all_channels: set[str] = set()
    for role, inst in instruments.items():
        for ch in inst.get("channels", ["1"]):
            all_channels.add(f"{role}:{ch}")

    # If no characteristics for this pin, all channels are compatible
    char_names = char_by_pin.get(pin_key, [])
    if not char_names or not part:
        if include_direction:
            # No direction info available — default to INPUT (exclusive)
            return {ch: Direction.INPUT for ch in all_channels}
        return all_channels

    # Get required capabilities from characteristics
    requirements: list[Capability] = []
    for char_name in char_names:
        char = part.characteristics.get(char_name)
        if char:
            requirements.append(char)

    if not requirements:
        if include_direction:
            return {ch: Direction.INPUT for ch in all_channels}
        return all_channels

    # Check each instrument's capabilities against requirements
    compatible: dict[str, Direction] = {}
    for role, inst in instruments.items():
        caps = inst.get("capabilities", [])
        if not caps:
            # No capability data — treat all channels as compatible (INPUT)
            for ch in inst.get("channels", ["1"]):
                compatible[f"{role}:{ch}"] = Direction.INPUT
            continue

        # Check each requirement against each capability
        for req in requirements:
            matching = _get_channels_satisfying(caps, req)
            for ch, direction in matching:
                compatible[f"{role}:{ch}"] = direction

    if include_direction:
        return compatible
    return set(compatible.keys())


def _get_lo_channels(instruments: dict[str, dict]) -> set[str]:
    """Get all instrument channels that have LO terminals.

    For ground pin wiring — returns channels where we can wire to the LO terminal.
    """
    compatible: set[str] = set()
    for role, inst in instruments.items():
        for ch in inst.get("channels", ["1"]):
            # All channels have implicit LO terminals
            compatible.add(f"{role}:{ch}")
    return compatible


def _get_pin_role(pin_key: str, dut_pins: dict[str, dict] | None) -> str:
    """Get the role of a pin from the dut_pins dict."""
    if dut_pins and pin_key in dut_pins:
        return dut_pins[pin_key].get("role", "signal")
    return "signal"


def _get_pin_direction(
    pin_key: str,
    char_by_pin: dict[str, list[str]],
    part: Part | None,
) -> Direction | None:
    """Get the primary direction of a pin from its characteristics.

    Returns the direction of the first characteristic found, or None if
    no characteristics exist for this pin.
    """
    char_names = char_by_pin.get(pin_key, [])
    if not char_names or not part:
        return None

    for char_name in char_names:
        char = part.characteristics.get(char_name)
        if char and hasattr(char, "direction"):
            return char.direction
    return None


def _get_channels_satisfying(
    capabilities: list[InstrumentCapability], requirement: Capability
) -> list[tuple[str, Direction]]:
    """Get channels from capabilities that satisfy a requirement.

    Returns (channel, direction) tuples for capabilities that match function,
    direction, AND parameter ranges. Readback capabilities are excluded.
    If a matching capability has no channels field, returns ["1"] as default.
    """
    req_function = requirement.function
    req_direction = requirement.direction
    req_signals = requirement.signals or {}

    matching_channels: list[tuple[str, Direction]] = []

    for cap in capabilities:
        # Skip readback capabilities — not primary measurement
        if cap.readback:
            continue

        cap_function = cap.function
        cap_direction = cap.direction

        # Parse enums if they're strings
        try:
            if isinstance(cap_function, str):
                cap_function = MeasurementFunction(cap_function)
            if isinstance(cap_direction, str):
                cap_direction = Direction(cap_direction)
        except ValueError:
            continue

        # Function must match
        if cap_function != req_function:
            continue

        # Directions must be complementary:
        # - DUT input (receives) ↔ instrument output (sources)
        # - DUT output (provides) ↔ instrument input (measures)
        # BIDIR instruments match either direction
        if cap_direction == Direction.BIDIR:
            pass  # BIDIR matches anything
        elif req_direction == Direction.INPUT and cap_direction != Direction.OUTPUT:
            continue
        elif req_direction == Direction.OUTPUT and cap_direction != Direction.INPUT:
            continue
        elif req_direction == Direction.BIDIR:
            pass  # BIDIR requirement matches any capability

        # Signal range check
        if not _signals_satisfy(cap.signals or {}, req_signals):
            continue

        # This capability matches — extract its channels with capability direction
        raw_channels = cap.channels
        if raw_channels and isinstance(raw_channels, list):
            for ch in raw_channels:
                matching_channels.append((ch, cap_direction))
        elif raw_channels and isinstance(raw_channels, str):
            from litmus.utils.ranges import expand_range

            for ch in expand_range(raw_channels):
                matching_channels.append((ch, cap_direction))
        else:
            matching_channels.append(("1", cap_direction))

    return matching_channels


def _signals_satisfy(cap_measures: dict[str, Signal], req_measures: dict[str, Signal]) -> bool:
    """Check if capability signals satisfy required signals.

    Numeric comparisons are normalised to SI base units so that e.g.
    a 6 mA requirement is correctly compared against a 5 A capability.
    """
    for measure_name, req_measure in req_measures.items():
        cap_signal = cap_measures.get(measure_name)
        if cap_signal is None:
            if req_measure.range is not None or req_measure.value is not None:
                return False
            continue

        cap_range = cap_signal.range

        # Determine unit scale factor: convert requirement values into
        # the capability's unit scale so numbers are directly comparable.
        req_units = req_measure.units
        cap_units = cap_signal.units or (cap_range.units if cap_range else None)
        scale = _unit_scale_factor(req_units, cap_units)

        # Check value containment (scale requirement value to cap units)
        if req_measure.value is not None and cap_range:
            scaled_val = req_measure.value * scale
            if cap_range.min is not None and scaled_val < cap_range.min:
                return False
            if cap_range.max is not None and scaled_val > cap_range.max:
                return False

        # Check range containment (scale requirement range to cap units)
        if req_measure.range is not None and cap_range:
            if (
                req_measure.range.min is not None
                and cap_range.min is not None
                and req_measure.range.min * scale < cap_range.min
            ):
                return False
            if (
                req_measure.range.max is not None
                and cap_range.max is not None
                and req_measure.range.max * scale > cap_range.max
            ):
                return False

    return True


# SI prefix multipliers relative to the base unit
_SI_PREFIXES: dict[str, float] = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "": 1e0,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
}

# Base unit symbols recognised for prefix stripping
_BASE_UNITS: set[str] = {
    "V",
    "A",
    "W",
    "Hz",
    "F",
    "H",
    "s",
    "S",
    "ohm",
    "Ohm",
    "dB",
    "dBm",
    "K",
    "C",
}


def _parse_si_unit(unit_str: str | None) -> tuple[float, str]:
    """Parse a unit string into (multiplier, base_unit).

    Examples:
        "mA"  -> (1e-3, "A")
        "kHz" -> (1e3, "Hz")
        "V"   -> (1.0, "V")
        "%"   -> (1.0, "%")
        None  -> (1.0, "")
    """
    if not unit_str:
        return 1.0, ""

    # Try exact match first (e.g. "Hz", "ohm", "dBm", "%")
    if unit_str in _BASE_UNITS or unit_str == "%":
        return 1.0, unit_str

    # Try stripping a single-char prefix
    if len(unit_str) >= 2:
        prefix, rest = unit_str[0], unit_str[1:]
        if rest in _BASE_UNITS and prefix in _SI_PREFIXES:
            return _SI_PREFIXES[prefix], rest

    # Two-char base units with prefix (e.g. "kHz", "MHz")
    if len(unit_str) >= 3:
        prefix, rest = unit_str[0], unit_str[1:]
        if rest in _BASE_UNITS and prefix in _SI_PREFIXES:
            return _SI_PREFIXES[prefix], rest

    # Unrecognised — treat as base unit with scale 1
    return 1.0, unit_str


def _unit_scale_factor(req_units: str | None, cap_units: str | None) -> float:
    """Return the multiplier to convert a requirement value into capability units.

    If both units share the same base (e.g. mA and A), returns the ratio
    so that ``req_value * scale`` is in cap_units.  If units are absent or
    incompatible (different base), returns 1.0 (no scaling).
    """
    if not req_units or not cap_units:
        return 1.0
    if req_units == cap_units:
        return 1.0

    req_mult, req_base = _parse_si_unit(req_units)
    cap_mult, cap_base = _parse_si_unit(cap_units)

    if req_base != cap_base or not req_base:
        return 1.0  # Different physical quantities — no conversion

    return req_mult / cap_mult


def resolve_instrument_capabilities(station_config) -> dict:
    """Enrich station config instruments with capabilities from the instrument library.

    Converts StationConfig model to dict, then enriches each instrument
    with capabilities from the catalog. This is the designer's model→dict
    boundary — the designer uses mutable dicts internally for NiceGUI binding.
    """
    from litmus.models.station import StationConfig

    if isinstance(station_config, StationConfig):
        station_config = station_config.model_dump()
    instruments = station_config.get("instruments", {})
    for _role, inst in instruments.items():
        inst_type = inst.get("type", "")
        if not inst_type:
            continue
        # Skip if capabilities already populated
        existing_caps = inst.get("capabilities")
        if existing_caps:
            continue

        # catalog_ref takes priority (model-specific > generic library)
        catalog_ref = inst.get("catalog_ref")
        if catalog_ref:
            from litmus.store import resolve_catalog_ref

            entry = resolve_catalog_ref(catalog_ref)
            if entry:
                # Store InstrumentCapability objects directly (not dicts)
                inst["capabilities"] = list(entry.capabilities)
                if not inst.get("channels"):
                    inst["channels"] = entry.channel_names
                continue

        # No catalog_ref — skip with warning
        import logging

        logging.getLogger(__name__).warning(
            "Instrument '%s' (type=%s) has no catalog_ref — skipping capability resolution",
            _role,
            inst_type,
        )

    return station_config


def auto_suggest_connections(
    dut_pins: dict[str, dict],
    char_by_pin: dict[str, list[str]],
    part: Part | None,
    instruments: dict[str, dict],
    existing: dict[str, dict],
) -> list[dict]:
    """Suggest connections for unconnected pins using 3-phase algorithm.

    Phase 1: Signal/power pins via capability matching (exclusive channels, readback excluded)
    Phase 2: Ground pins via bus wiring (fan-out to LO terminals of allocated channels)
    Phase 3: Remaining pins reported as unmatched (no silent allocation)

    Args:
        dut_pins: Pin key -> pin data dict (includes 'role' field).
        char_by_pin: Pin key -> characteristic names.
        part: Part model.
        instruments: Role -> instrument data dict.
        existing: Existing connections (point_name -> connection dict).

    Returns:
        List of dicts with keys: point_name, dut_pin, instrument, channel, terminal, net.
    """
    # Track which channels are already used
    used_channels: set[str] = set()
    connected_pins: set[str] = set()
    for conn in existing.values():
        used_channels.add(f"{conn['instrument']}:{conn['channel']}")
        connected_pins.add(conn["dut_pin"])

    suggestions: list[dict] = []

    # Phase 1: Signal + Power pins — capability matching, exclusive channels.
    # Use "most constrained first" heuristic: assign pins with fewest
    # compatible channels first so they don't get starved by pins with
    # many options.
    pin_candidates: list[tuple[str, dict, dict[str, Direction]]] = []
    for pin_key, pin_data in dut_pins.items():
        if pin_key in connected_pins:
            continue
        pin_role = pin_data.get("role", "signal")
        if pin_role == "ground":
            continue  # Handled in phase 2

        # Must have characteristics to match
        if not char_by_pin.get(pin_key):
            continue

        compatible = get_compatible_channels_for_pin(
            pin_key,
            char_by_pin,
            part,
            instruments,
            dut_pins,
            include_direction=True,
        )
        assert isinstance(compatible, dict)  # Type narrowing
        if compatible:
            pin_candidates.append((pin_key, pin_data, compatible))

    # Sort: fewest compatible channels first (most constrained)
    pin_candidates.sort(key=lambda t: len(t[2]))

    for pin_key, pin_data, compatible in pin_candidates:
        # Get the pin's required direction from its characteristics
        pin_direction = _get_pin_direction(pin_key, char_by_pin, part)

        # Sort channels by preference:
        # - For INPUT pins: prefer OUTPUT channels (power sources)
        # - For OUTPUT pins: prefer INPUT channels (measurement)
        available = set(compatible.keys()) - used_channels

        def channel_priority(ch_key: str) -> int:
            ch_dir = compatible[ch_key]
            if pin_direction == Direction.INPUT:
                # INPUT pin wants OUTPUT source first
                if ch_dir == Direction.OUTPUT:
                    return 0
                elif ch_dir == Direction.BIDIR:
                    return 1
                else:
                    return 2
            elif pin_direction == Direction.OUTPUT:
                # OUTPUT pin wants INPUT measurement first
                if ch_dir == Direction.INPUT:
                    return 0
                elif ch_dir == Direction.BIDIR:
                    return 1
                else:
                    return 2
            return 1  # BIDIR or unknown

        for channel_key in sorted(available, key=lambda k: (channel_priority(k), k)):
            role, channel = channel_key.split(":", 1)
            direction = compatible[channel_key]
            point_name = _generate_point_name(pin_key, role, channel)
            suggestions.append(
                {
                    "point_name": point_name,
                    "dut_pin": pin_key,
                    "instrument": role,
                    "channel": channel,
                    "terminal": "hi",
                    "net": pin_data.get("net", ""),
                }
            )
            # OUTPUT/BIDIR channels can fan-out to multiple DUT inputs,
            # only INPUT channels (DMM, scope) are exclusive
            if direction == Direction.INPUT:
                used_channels.add(channel_key)
            break

    # Phase 2: Ground pins — bus wiring to LO terminals
    # For each ground pin, wire to the LO terminal of every instrument channel
    # that was allocated to a signal/power pin on the same net or same connector.
    allocated_channels: list[dict] = []
    for conn in existing.values():
        allocated_channels.append(conn)
    for s in suggestions:
        allocated_channels.append(s)

    for pin_key, pin_data in dut_pins.items():
        if pin_key in connected_pins:
            continue
        pin_role = pin_data.get("role", "signal")
        if pin_role != "ground":
            continue

        # Find instrument channels to wire GND to.
        # Strategy: wire to the LO terminal of each unique instrument
        # that has allocated channels (from phase 1 + existing connections).
        gnd_targets = _find_ground_targets(pin_key, pin_data, allocated_channels, dut_pins)

        for role, channel in gnd_targets:
            point_name = _generate_point_name(pin_key, role, channel, terminal="lo")
            suggestions.append(
                {
                    "point_name": point_name,
                    "dut_pin": pin_key,
                    "instrument": role,
                    "channel": channel,
                    "terminal": "lo",
                    "net": pin_data.get("net", ""),
                }
            )

    return suggestions


def _find_ground_targets(
    gnd_pin_key: str,
    gnd_pin_data: dict,
    allocated_channels: list[dict],
    dut_pins: dict[str, dict],
) -> list[tuple[str, str]]:
    """Find instrument channels that a ground pin should wire to.

    A ground pin wires to the LO terminal of instruments that serve
    signal/power pins on the same connector or the same net group.
    Returns a list of (role, channel) tuples.
    """
    gnd_net = gnd_pin_data.get("net", "")
    gnd_prefix = _connector_prefix(gnd_pin_key)

    # Collect unique instrument:channel pairs from allocated channels
    # that share the same connector prefix or serve the same net group
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()

    for conn in allocated_channels:
        conn_pin = conn.get("dut_pin", "")
        conn_pin_data = dut_pins.get(conn_pin, {})
        conn_role = conn.get("instrument", "")
        conn_channel = conn.get("channel", "")
        key = f"{conn_role}:{conn_channel}"

        if key in seen:
            continue

        # Match by connector prefix (J1_GND -> J1_VIN's instrument)
        conn_prefix = _connector_prefix(conn_pin)
        if gnd_prefix and conn_prefix and gnd_prefix == conn_prefix:
            targets.append((conn_role, conn_channel))
            seen.add(key)
            continue

        # Match by net group (GND net -> same net group instruments)
        conn_net = conn_pin_data.get("net", conn.get("net", ""))
        if gnd_net and conn_net and gnd_net == conn_net:
            targets.append((conn_role, conn_channel))
            seen.add(key)

    # If no targets found by prefix/net matching, wire to all allocated instruments
    if not targets:
        for conn in allocated_channels:
            conn_role = conn.get("instrument", "")
            conn_channel = conn.get("channel", "")
            key = f"{conn_role}:{conn_channel}"
            if key not in seen:
                targets.append((conn_role, conn_channel))
                seen.add(key)

    return targets


def _connector_prefix(pin_key: str) -> str | None:
    """Extract connector prefix from a pin key (e.g., 'J1' from 'J1_VIN').

    Returns None if no underscore separator is found.
    """
    parts = pin_key.split("_", 1)
    return parts[0] if len(parts) > 1 else None


def _generate_point_name(
    pin_key: str, role: str, channel: str = "1", terminal: str | None = None
) -> str:
    """Generate a fixture point name from pin key, role, and channel."""
    clean_pin = pin_key.lower().replace(" ", "_")
    suffix = f"_{terminal}" if terminal else ""
    return f"{clean_pin}_{role}_ch{channel}{suffix}"
