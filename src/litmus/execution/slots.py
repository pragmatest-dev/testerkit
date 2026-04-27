"""Fixture slot resolution for multi-DUT testing.

Resolves fixture slots against a station config to validate that all
referenced instrument roles exist. Single-DUT fixtures (using
``connections`` instead of ``slots``) are normalized to a single
implicit slot.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

from litmus.models.test_config import FixtureConfig, FixtureConnection


class ResolvedSlot(BaseModel):
    """A fixture slot with validated instrument references.

    Attributes:
        slot_id: Unique slot identifier (e.g., "slot_1").
        connections: FixtureConnection mappings for this slot's DUT.
        instrument_roles: Set of station instrument roles this slot needs.
        dut_resource: Per-slot DUT connection string (e.g., COM3, /dev/ttyUSB0).
    """

    slot_id: str
    connections: dict[str, FixtureConnection] = Field(default_factory=dict)
    instrument_roles: set[str] = Field(default_factory=set)
    dut_resource: str | None = None


# Default slot ID for single-DUT fixtures
DEFAULT_SLOT_ID = "default"


def resolve_fixture_slots(
    fixture_config: FixtureConfig,
    station_instruments: set[str] | None = None,
) -> dict[str, ResolvedSlot]:
    """Resolve fixture slots and validate instrument references.

    For single-DUT fixtures (``connections``), returns one slot with id
    "default". For multi-DUT fixtures (``slots``), returns one slot per
    entry.

    Args:
        fixture_config: Fixture configuration with connections or slots.
        station_instruments: Set of instrument role names from station config.
            If provided, validates that all fixture connection instrument
            references exist in the station.

    Returns:
        Dict mapping slot_id → ResolvedSlot.

    Raises:
        ValueError: If a fixture connection references an instrument
            role not present in the station config.
    """
    if fixture_config.slots:
        slots = {
            slot_id: _build_resolved_slot(
                slot_id,
                slot.connections,
                dut_resource=slot.dut_resource,
            )
            for slot_id, slot in fixture_config.slots.items()
        }
    else:
        slots = {
            DEFAULT_SLOT_ID: _build_resolved_slot(
                DEFAULT_SLOT_ID,
                fixture_config.connections,
                dut_resource=fixture_config.dut_resource,
            )
        }

    if station_instruments is not None:
        _validate_instrument_refs(slots, station_instruments, fixture_config.id)

    return slots


def _build_resolved_slot(
    slot_id: str,
    connections: dict[str, FixtureConnection],
    *,
    dut_resource: str | None = None,
) -> ResolvedSlot:
    """Build a ResolvedSlot from fixture connections."""
    roles = {conn.instrument for conn in connections.values()}
    # Include switch roles from route configs so station validation
    # catches missing switch instruments
    for conn in connections.values():
        if conn.route is not None:
            roles.add(conn.route.switch)
    return ResolvedSlot(
        slot_id=slot_id,
        connections=connections,
        instrument_roles=roles,
        dut_resource=dut_resource,
    )


def detect_shared_instruments(slots: dict[str, ResolvedSlot]) -> set[str]:
    """Detect instrument roles shared by multiple slots.

    An instrument role is "shared" when two or more slots reference it.

    Args:
        slots: Resolved fixture slots.

    Returns:
        Set of instrument role names that appear in 2+ slots.
    """
    counts: Counter[str] = Counter()
    for slot in slots.values():
        counts.update(slot.instrument_roles)
    return {role for role, count in counts.items() if count >= 2}


def _validate_instrument_refs(
    slots: dict[str, ResolvedSlot],
    station_instruments: set[str],
    fixture_id: str,
) -> None:
    """Validate that all fixture connection instrument refs exist in station."""
    for slot_id, slot in slots.items():
        missing = slot.instrument_roles - station_instruments
        if missing:
            raise ValueError(
                f"Fixture '{fixture_id}' slot '{slot_id}' references instruments "
                f"not in station config: {', '.join(sorted(missing))}"
            )
