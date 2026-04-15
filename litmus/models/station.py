"""Station configuration types.

Schema for ``stations/*.yaml`` files — flat, all fields at root.
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
        """Validate that resource is provided when not using mock mode."""
        if not self.mock and self.resource is None and self.driver is None:
            raise ValueError(
                "resource or driver is required when mock=False. Either set mock=True, "
                "provide a VISA resource string (e.g., 'GPIB::1::INSTR'), "
                "or provide a driver path (e.g., 'pymeasure.instruments.keithley:Keithley2400')."
            )
        return self


class StationConfig(BaseModel):
    """Schema for stations/*.yaml files — all fields at root."""

    model_config = {"extra": "forbid"}

    id: str
    name: str
    station_type: str | None = None
    location: str | None = None
    description: str | None = None
    instruments: dict[str, StationInstrumentConfig] = Field(default_factory=dict)
    supported_phases: list[str] = Field(default_factory=list)
