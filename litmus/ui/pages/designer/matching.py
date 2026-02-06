"""Capability matching helpers for the system designer.

Wraps litmus.matching.service to provide UI-specific matching:
- Pin -> characteristic reverse map
- Compatible channels for a selected pin
- Auto-suggest connections for bulk wiring
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from litmus.config.models import Direction, Domain

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
) -> set[str]:
    """Get instrument channels compatible with a selected pin.

    Returns a set of "role:channel" keys that can handle this pin's
    characteristics. When no characteristic data is available, all
    channels are returned as compatible (manual wiring mode).

    Args:
        pin_key: The selected pin key.
        char_by_pin: Reverse map from build_pin_characteristic_map().
        product: Product model (may be None).
        instruments: Dict of role -> {type, driver, capabilities, channels}.

    Returns:
        Set of "role:channel" strings for compatible channels.
    """
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
        cap = char.to_capability_requirement()
        requirements.append(
            {
                "direction": cap.direction,
                "domain": cap.domain,
                "signal_types": cap.signal_types,
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

        # Check if any instrument capability satisfies any requirement
        for req in requirements:
            if _instrument_satisfies(caps, req):
                for ch in inst.get("channels", ["1"]):
                    compatible.add(f"{role}:{ch}")
                break

    return compatible


def _instrument_satisfies(capabilities: list[dict], requirement: dict[str, Any]) -> bool:
    """Check if any instrument capability satisfies a requirement.

    Matches on direction (with bidir support) and domain.
    Signal types must overlap if both sides specify them.
    """
    req_direction = requirement["direction"]
    req_domain = requirement["domain"]
    req_signals = set(requirement.get("signal_types", []))

    for cap in capabilities:
        cap_direction = cap.get("direction", "")
        cap_domain = cap.get("domain", "")

        # Parse enums if they're strings
        try:
            if isinstance(cap_direction, str):
                cap_direction = Direction(cap_direction)
            if isinstance(cap_domain, str):
                cap_domain = Domain(cap_domain)
        except ValueError:
            continue

        # Direction must match (or instrument is bidir)
        if cap_direction != req_direction and cap_direction != Direction.BIDIR:
            continue

        # Domain must match
        if cap_domain != req_domain:
            continue

        # Signal types must overlap if both specify
        if req_signals:
            cap_signals = set(cap.get("signal_types", []))
            if cap_signals and not cap_signals.intersection(req_signals):
                continue

        return True

    return False


def resolve_instrument_capabilities(station_config: dict) -> dict:
    """Enrich station config instruments with capabilities from the instrument library.

    For each instrument that has a ``type`` field but no ``capabilities``,
    looks up the instrument library YAML and copies capabilities in.
    Modifies the config dict in-place and returns it for convenience.
    """
    from litmus.matching import service as matching_service

    instruments = station_config.get("instruments", {})
    for _role, inst in instruments.items():
        inst_type = inst.get("type", "")
        if not inst_type:
            continue
        # Skip if capabilities already populated
        existing_caps = inst.get("capabilities")
        if existing_caps:
            continue
        library = matching_service.load_instrument_library(inst_type)
        if library and "capabilities" in library:
            inst["capabilities"] = library["capabilities"]
    return station_config


def auto_suggest_connections(
    dut_pins: dict[str, dict],
    char_by_pin: dict[str, list[str]],
    product: Product | None,
    instruments: dict[str, dict],
    existing: dict[str, dict],
) -> list[dict]:
    """Suggest connections for unconnected pins.

    For each unconnected pin that has characteristics, finds the best
    available (unused) channel. Returns a list of suggested connections.

    Args:
        dut_pins: Pin key -> pin data dict.
        char_by_pin: Pin key -> characteristic names.
        product: Product model.
        instruments: Role -> instrument data dict.
        existing: Existing connections (point_name -> connection dict).

    Returns:
        List of dicts with keys: point_name, dut_pin, instrument, channel, net.
    """
    # Track which channels are already used
    used_channels: set[str] = set()
    connected_pins: set[str] = set()
    for conn in existing.values():
        used_channels.add(f"{conn['instrument']}:{conn['channel']}")
        connected_pins.add(conn["dut_pin"])

    suggestions: list[dict] = []

    # Phase 1: Match signal pins (those with characteristics) — exclusive channels
    for pin_key, pin_data in dut_pins.items():
        if pin_key in connected_pins:
            continue
        if not char_by_pin.get(pin_key):
            continue  # Ground/characterless pins handled in phase 2

        compatible = get_compatible_channels_for_pin(pin_key, char_by_pin, product, instruments)

        for channel_key in sorted(compatible):
            if channel_key not in used_channels:
                role, channel = channel_key.split(":", 1)
                point_name = _generate_point_name(pin_key, role, channel)
                suggestions.append(
                    {
                        "point_name": point_name,
                        "dut_pin": pin_key,
                        "instrument": role,
                        "channel": channel,
                        "net": pin_data.get("net", ""),
                    }
                )
                used_channels.add(channel_key)
                break

    # Phase 2: Match ground pins — share channel with related signal pin.
    # A ground pin's current return pairs with the signal pin on the same
    # connector (e.g., J1_GND shares PSU CH1 with J1_VIN).
    # Build lookup: connector prefix -> (instrument, channel) from phase 1 + existing
    prefix_to_channel: dict[str, tuple[str, str]] = {}
    for conn in existing.values():
        pfx = _connector_prefix(conn["dut_pin"])
        if pfx:
            prefix_to_channel.setdefault(pfx, (conn["instrument"], conn["channel"]))
    for s in suggestions:
        pfx = _connector_prefix(s["dut_pin"])
        if pfx:
            prefix_to_channel.setdefault(pfx, (s["instrument"], s["channel"]))

    for pin_key, pin_data in dut_pins.items():
        if pin_key in connected_pins:
            continue
        if char_by_pin.get(pin_key):
            continue  # Has characteristics — already handled in phase 1

        pfx = _connector_prefix(pin_key)
        target = prefix_to_channel.get(pfx) if pfx else None
        if target:
            role, channel = target
            point_name = _generate_point_name(pin_key, role, channel)
            suggestions.append(
                {
                    "point_name": point_name,
                    "dut_pin": pin_key,
                    "instrument": role,
                    "channel": channel,
                    "net": pin_data.get("net", ""),
                }
            )

    return suggestions


def _connector_prefix(pin_key: str) -> str | None:
    """Extract connector prefix from a pin key (e.g., 'J1' from 'J1_VIN').

    Returns None if no underscore separator is found.
    """
    parts = pin_key.split("_", 1)
    return parts[0] if len(parts) > 1 else None


def _generate_point_name(pin_key: str, role: str, channel: str = "1") -> str:
    """Generate a fixture point name from pin key, role, and channel."""
    clean_pin = pin_key.lower().replace(" ", "_")
    return f"{clean_pin}_{role}_ch{channel}"
