"""Station configuration types.

Schema for ``stations/*.yaml`` (concrete station deployments) and
``stations/types/*.yaml`` (abstract station-type templates that
station files can declare compatibility with). Both are user-authored
and loaded through :mod:`litmus.store`.

The ``station_type`` field on :class:`StationConfig` is advisory today
— a label declaring which abstract :class:`StationType` template the
deployment implements. Profile-driven station-type matching is on the
roadmap; once that ships, this field becomes load-bearing for fixture
compatibility.
"""

from __future__ import annotations

from typing import Any

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
    def resource_required_for_real_hardware(self) -> StationInstrumentConfig:
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
    # Advisory label naming the StationType template this deployment
    # implements. Not enforced at load — profile-driven station-type
    # matching is on the roadmap (see ROADMAP.md).
    station_type: str | None = None
    location: str | None = None
    description: str | None = None
    instruments: dict[str, StationInstrumentConfig] = Field(default_factory=dict)
    supported_phases: list[str] = Field(default_factory=list)


class InstrumentConfig(BaseModel):
    """Configuration for a single instrument in a :class:`StationType` template."""

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

    id: str
    description: str
    instruments: dict[str, InstrumentConfig]
    capabilities: list[str] = Field(default_factory=list)
