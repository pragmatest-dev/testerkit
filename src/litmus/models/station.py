"""Station configuration types.

Schema for ``stations/*.yaml`` (concrete station deployments) and
``stations/types/*.yaml`` (abstract station-type templates that
station files can declare compatibility with). Both are user-authored
and loaded through :mod:`litmus.store`.

The ``station_type`` field on :class:`StationConfig` is load-bearing:
when set, the resolver checks at session start that the station's
declared instruments cover the roles its named :class:`StationType`
requires (see :func:`validate_station_against_type`), and that the
active profile's `station_type` matches.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, Field, model_validator


class StationInstrumentConfig(BaseModel):
    """Single instrument entry in a station file."""

    model_config = {"extra": "forbid"}

    type: str
    driver: str | None = None  # Optional for mock-only instruments
    resource: str | None = None
    catalog_ref: str | None = None
    mock: bool = False
    channels: dict[str, str] = Field(default_factory=dict)
    description: str | None = None
    mock_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def resource_required_for_real_hardware(self) -> Self:
        """Validate that resource or driver is provided when not in mock mode."""
        if not self.mock and self.resource is None and self.driver is None:
            raise ValueError(
                "Real-hardware instrument requires resource and/or driver. "
                "Set one of:\n"
                "  - resource: 'GPIB::1::INSTR' (PyVISA-discovered driver)\n"
                "  - driver:   'pymeasure.instruments.keithley:Keithley2400'\n"
                "  - mock:     true  (simulated instrument; resource/driver optional)"
            )
        return self


class StationConfig(BaseModel):
    """Schema for stations/*.yaml files — all fields at root."""

    model_config = {"extra": "forbid"}

    id: str
    name: str
    # Names the StationType template this deployment implements.
    # Load-bearing at session start: the resolver runs
    # ``validate_station_against_type`` to ensure the declared
    # instruments cover the type's required roles.
    station_type: str | None = None
    # Hostname of the bench machine running this station. When set,
    # the session-start resolver auto-matches against
    # ``socket.gethostname()`` so operators don't need to pass
    # ``--station=<id>`` on the matching machine.
    hostname: str | None = None
    location: str | None = None
    description: str | None = None
    instruments: dict[str, StationInstrumentConfig] = Field(default_factory=dict)
    supported_phases: list[str] = Field(default_factory=list)


class InstrumentConfig(BaseModel):
    """Configuration for a single instrument in a :class:`StationType` template."""

    model_config = {"extra": "forbid"}

    type: str
    driver: str
    resource: str | None = None
    settings: dict = Field(default_factory=dict)


class StationType(BaseModel):
    """Abstract station-type template (``stations/types/*.yaml``).

    Declares the instruments and capabilities a station of this type
    provides. Concrete deployments (:class:`StationConfig`) can name the
    type they implement via ``station_type``.
    """

    model_config = {"extra": "forbid"}

    id: str
    description: str
    instruments: dict[str, InstrumentConfig]
    capabilities: list[str] = Field(default_factory=list)


def validate_station_against_type(station: StationConfig, station_type: StationType) -> list[str]:
    """Return human-readable role mismatches between station and type.

    A concrete station "comports with" its declared :class:`StationType`
    when, for every role the type requires (``station_type.instruments``
    keys), the station declares an instrument under the same role with
    a matching ``type:`` value. Returns an empty list when fully
    compliant; otherwise returns one entry per problem describing the
    role and the mismatch.

    The check is data-only (no I/O, no driver / resource validation)
    and crosses two YAML-loaded models — written as a free function
    rather than a Pydantic ``model_validator`` because the
    :class:`StationType` template lives in a separate file that may not
    be loaded when the :class:`StationConfig` is parsed.

    Args:
        station: concrete station configuration.
        station_type: the abstract type template the station declares
            via its ``station_type`` field.

    Returns:
        A list of mismatch descriptions; empty when compliant.
    """
    mismatches: list[str] = []
    for role, type_inst in station_type.instruments.items():
        station_inst = station.instruments.get(role)
        if station_inst is None:
            mismatches.append(
                f"role {role!r} required by station_type "
                f"{station_type.id!r} but not declared on station "
                f"{station.id!r}"
            )
            continue
        if station_inst.type != type_inst.type:
            mismatches.append(
                f"role {role!r} on station {station.id!r} declares "
                f"type={station_inst.type!r}, but station_type "
                f"{station_type.id!r} requires type={type_inst.type!r}"
            )
    return mismatches
