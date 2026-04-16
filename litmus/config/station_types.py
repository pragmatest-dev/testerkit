"""Station/instrument infrastructure models.

These are Pydantic BaseModels for station type templates and instances,
not enums. Separated from enums.py for clarity.
"""

from pydantic import BaseModel, Field


class InstrumentConfig(BaseModel):
    """Configuration for a single instrument (template)."""

    type: str
    driver: str
    resource: str | None = None
    settings: dict = Field(default_factory=dict)


class InstrumentInstance(BaseModel):
    """Physical instrument at a station."""

    type: str
    resource: str


class StationType(BaseModel):
    """Abstract station type (template)."""

    id: str
    description: str
    instruments: dict[str, InstrumentConfig]
    capabilities: list[str] = Field(default_factory=list)


class StationInstance(BaseModel):
    """Concrete station instance (deployed)."""

    id: str
    station_type: str
    location: str | None = None
    instruments: dict[str, InstrumentInstance] = Field(default_factory=dict)
