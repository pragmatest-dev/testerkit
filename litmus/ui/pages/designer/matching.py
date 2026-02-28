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

from typing import TYPE_CHECKING, Any

from litmus.config.models import Direction, MeasurementFunction, Signal

if TYPE_CHECKING:
    from litmus.products.models import Product


def build_pin_characteristic_map(product: Product) -> dict[str, list[str]]:
    """Build reverse map: pin_key -> [characteristic names that reference it].

    This is used to look up which characteristics apply to a given pin,
    enabling capability matching when the user selects a pin.
    """
    result: dict[str, list[str]] = {}
    for char_name, char in product.characteristics.items():
        for pin_key in char.resolved_pins:
            if pin_key not in result:
                result[pin_key] = []
            result[pin_key].append(char_name)
    return result


def get_compatible_channels_for_pin(
    pin_key: str,
    char_by_pin: dict[str, list[str]],
    product: Product | None,
    instruments: dict[str, dict],
    dut_pins: dict[str, dict] | None = None,
) -> set[str]:
    """Get instrument channels compatible with a selected pin.

    Returns a set of "role:channel" keys that can handle this pin's
    characteristics. Only channels whose capability parameters actually
    satisfy the requirement are included (not all channels on the instrument).

    For ground pins (role == "ground"), returns LO terminals of all channels
    instead of signal-matching channels.

    When no characteristic data is available, all channels are returned
    as compatible (manual wiring mode).

    Args:
        pin_key: The selected pin key.
        char_by_pin: Reverse map from build_pin_characteristic_map().
        product: Product model (may be None).
        instruments: Dict of role -> {type, driver, capabilities, channels}.
        dut_pins: Dict of pin_key -> pin data (with role field).

    Returns:
        Set of "role:channel" strings for compatible channels.
    """
    # Check if this is a ground pin
    pin_role = _get_pin_role(pin_key, dut_pins)
    if pin_role == "ground":
        return _get_lo_channels(instruments)

    # Get all channels across all instruments
    all_channels: set[str] = set()
    for role, inst in instruments.items():
        for ch in inst.get("channels", ["1"]):
            all_channels.add(f"{role}:{ch}")

    # If no characteristics for this pin, all channels are compatible
    char_names = char_by_pin.get(pin_key, [])
    if not char_names or not product:
        return all_channels

    # Build required capabilities from characteristics
    requirements: list[dict[str, Any]] = []
    for char_name in char_names:
        char = product.characteristics.get(char_name)
        if not char:
            continue
        requirements.append(
            {
                "function": char.function,
                "direction": char.direction,
                "signals": char.signals,
            }
        )

    if not requirements:
        return all_channels

    # Check each instrument's capabilities against requirements
    compatible: set[str] = set()
    for role, inst in instruments.items():
        caps = inst.get("capabilities", [])
        if not caps:
            # No capability data — treat all channels as compatible
            for ch in inst.get("channels", ["1"]):
                compatible.add(f"{role}:{ch}")
            continue

        # Check each requirement against each capability
        for req in requirements:
            matching_channels = _get_channels_satisfying(caps, req)
            for ch in matching_channels:
                compatible.add(f"{role}:{ch}")

    return compatible


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


def _get_channels_satisfying(
    capabilities: list[dict], requirement: dict[str, Any]
) -> list[str]:
    """Get channels from capabilities that satisfy a requirement.

    Returns only the channels on capabilities that match function, direction,
    AND parameter ranges. Readback capabilities are excluded.
    If a matching capability has no channels field, returns ["1"] as default.
    """
    req_function = requirement["function"]
    req_direction = requirement["direction"]
    req_measures = requirement.get("signals", {})

    matching_channels: list[str] = []

    for cap in capabilities:
        # Skip readback capabilities — not primary measurement
        if cap.get("readback", False):
            continue

        cap_function = cap.get("function", "")
        cap_direction = cap.get("direction", "")

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

        # Direction must match (or instrument is bidir)
        if cap_direction != req_direction and cap_direction != Direction.BIDIR:
            continue

        # Signal range check
        if not _signals_satisfy(cap.get("signals", {}), req_measures):
            continue

        # This capability matches — extract its channels
        raw_channels = cap.get("channels")
        if raw_channels and isinstance(raw_channels, list):
            matching_channels.extend(raw_channels)
        elif raw_channels and isinstance(raw_channels, str):
            from litmus.utils.ranges import expand_range
            matching_channels.extend(expand_range(raw_channels))
        else:
            matching_channels.append("1")

    return matching_channels


def _signals_satisfy(
    cap_measures: dict[str, Any], req_measures: dict[str, Signal]
) -> bool:
    """Check if capability signals satisfy required signals.

    Numeric comparisons are normalised to SI base units so that e.g.
    a 6 mA requirement is correctly compared against a 5 A capability.
    """
    for measure_name, req_measure in req_measures.items():
        cap_measure_data = cap_measures.get(measure_name)
        if cap_measure_data is None:
            if req_measure.range is not None or req_measure.value is not None:
                return False
            continue

        # Parse cap_measure into range data for comparison
        cap_range = cap_measure_data.get("range") if isinstance(cap_measure_data, dict) else None

        # Determine unit scale factor: convert requirement values into
        # the capability's unit scale so numbers are directly comparable.
        req_units = req_measure.units
        cap_units = None
        if isinstance(cap_measure_data, dict):
            cap_units = cap_measure_data.get("units")
            if not cap_units and cap_range:
                cap_units = cap_range.get("units")
        scale = _unit_scale_factor(req_units, cap_units)

        # Check value containment (scale requirement value to cap units)
        if req_measure.value is not None and cap_range:
            scaled_val = req_measure.value * scale
            if cap_range.get("min") is not None and scaled_val < cap_range["min"]:
                return False
            if cap_range.get("max") is not None and scaled_val > cap_range["max"]:
                return False

        # Check range containment (scale requirement range to cap units)
        if req_measure.range is not None and cap_range:
            if (
                req_measure.range.min is not None
                and cap_range.get("min") is not None
                and req_measure.range.min * scale < cap_range["min"]
            ):
                return False
            if (
                req_measure.range.max is not None
                and cap_range.get("max") is not None
                and req_measure.range.max * scale > cap_range["max"]
            ):
                return False

    return True


# SI prefix multipliers relative to the base unit
_SI_PREFIXES: dict[str, float] = {
    "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6,
    "m": 1e-3, "": 1e0, "k": 1e3, "K": 1e3,
    "M": 1e6, "G": 1e9, "T": 1e12,
}

# Base unit symbols recognised for prefix stripping
_BASE_UNITS: set[str] = {
    "V", "A", "W", "Hz", "F", "H", "s", "S",
    "ohm", "Ohm", "dB", "dBm", "K", "C",
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


def resolve_instrument_capabilities(station_config: dict) -> dict:
    """Enrich station config instruments with capabilities from the instrument library.

    For each instrument that has a ``type`` field but no ``capabilities``,
    looks up the instrument library YAML and copies capabilities in.
    Also populates ``channels`` from the library if not already present.
    Modifies the config dict in-place and returns it for convenience.
    """
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
            from litmus.catalog.loader import resolve_catalog_ref

            entry = resolve_catalog_ref(catalog_ref)
            if entry:
                inst["capabilities"] = [
                    {
                        "function": cap.function.value,
                        "direction": cap.direction.value,
                        "channels": cap.resolved_channels,
                        "readback": cap.readback,
                        "signals": {
                            name: m.model_dump(exclude_none=True)
                            for name, m in cap.signals.items()
                        },
                    }
                    for cap in entry.capabilities
                ]
                if not inst.get("channels"):
                    inst["channels"] = entry.channel_names
                continue

        # No catalog_ref — skip with warning
        import logging
        logging.getLogger(__name__).warning(
            "Instrument '%s' (type=%s) has no catalog_ref — skipping capability resolution",
            _role, inst_type,
        )

    return station_config


def auto_suggest_connections(
    dut_pins: dict[str, dict],
    char_by_pin: dict[str, list[str]],
    product: Product | None,
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
        product: Product model.
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
    pin_candidates: list[tuple[str, dict, set[str]]] = []
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
            pin_key, char_by_pin, product, instruments, dut_pins
        )
        if compatible:
            pin_candidates.append((pin_key, pin_data, compatible))

    # Sort: fewest compatible channels first (most constrained)
    pin_candidates.sort(key=lambda t: len(t[2]))

    for pin_key, pin_data, compatible in pin_candidates:
        for channel_key in sorted(compatible - used_channels):
            role, channel = channel_key.split(":", 1)
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
