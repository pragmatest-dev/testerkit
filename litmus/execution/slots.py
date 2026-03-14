"""Fixture slot resolution for multi-DUT testing.

Resolves fixture slots against a station config to validate that all
referenced instrument roles exist. Single-DUT fixtures (using ``points``
instead of ``slots``) are normalized to a single implicit slot.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

from litmus.config.test_config import FixtureConfig, FixturePoint
from litmus.schemas import StationInstrumentConfig


class ResolvedSlot(BaseModel):
    """A fixture slot with validated instrument references.

    Attributes:
        slot_id: Unique slot identifier (e.g., "slot_1").
        points: FixturePoint mappings for this slot's DUT.
        instrument_roles: Set of station instrument roles this slot needs.
        dut_resource: Per-slot DUT connection string (e.g., COM3, /dev/ttyUSB0).
    """

    slot_id: str
    points: dict[str, FixturePoint] = Field(default_factory=dict)
    instrument_roles: set[str] = Field(default_factory=set)
    dut_resource: str | None = None


# Default slot ID for single-DUT fixtures
DEFAULT_SLOT_ID = "default"


def resolve_fixture_slots(
    fixture_config: FixtureConfig,
    station_instruments: set[str] | None = None,
) -> dict[str, ResolvedSlot]:
    """Resolve fixture slots and validate instrument references.

    For single-DUT fixtures (``points``), returns one slot with id "default".
    For multi-DUT fixtures (``slots``), returns one slot per entry.

    Args:
        fixture_config: Fixture configuration with points or slots.
        station_instruments: Set of instrument role names from station config.
            If provided, validates that all fixture point instrument references
            exist in the station.

    Returns:
        Dict mapping slot_id → ResolvedSlot.

    Raises:
        ValueError: If a fixture point references an instrument role not
            present in the station config.
    """
    if fixture_config.slots:
        slots = {
            slot_id: _build_resolved_slot(
                slot_id, slot.points, dut_resource=slot.dut_resource,
            )
            for slot_id, slot in fixture_config.slots.items()
        }
    else:
        slots = {
            DEFAULT_SLOT_ID: _build_resolved_slot(
                DEFAULT_SLOT_ID, fixture_config.points,
                dut_resource=fixture_config.dut_resource,
            )
        }

    if station_instruments is not None:
        _validate_instrument_refs(slots, station_instruments, fixture_config.id)

    return slots


def _build_resolved_slot(
    slot_id: str,
    points: dict[str, FixturePoint],
    *,
    dut_resource: str | None = None,
) -> ResolvedSlot:
    """Build a ResolvedSlot from fixture points."""
    roles = {point.instrument for point in points.values()}
    # Include switch roles from route configs so station validation
    # catches missing switch instruments
    for point in points.values():
        if point.route is not None:
            roles.add(point.route.switch)
    return ResolvedSlot(
        slot_id=slot_id,
        points=points,
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


def needs_thread_mode(
    shared_roles: set[str],
    station_instruments: dict[str, StationInstrumentConfig],
) -> bool:
    """Determine whether thread-per-slot mode is required.

    Thread mode is forced when any shared instrument has ``persistent=True``
    in station config. Persistent instruments require a long-lived connection
    that cannot be repeatedly connected/disconnected per measurement.

    Args:
        shared_roles: Instrument roles referenced by multiple slots.
        station_instruments: Station instrument configs keyed by role.

    Returns:
        True if any shared instrument is persistent.
    """
    for role in shared_roles:
        cfg = station_instruments.get(role)
        if cfg is not None and cfg.persistent:
            return True
    return False


def _validate_instrument_refs(
    slots: dict[str, ResolvedSlot],
    station_instruments: set[str],
    fixture_id: str,
) -> None:
    """Validate that all fixture point instrument refs exist in station."""
    for slot_id, slot in slots.items():
        missing = slot.instrument_roles - station_instruments
        if missing:
            raise ValueError(
                f"Fixture '{fixture_id}' slot '{slot_id}' references instruments "
                f"not in station config: {', '.join(sorted(missing))}"
            )
