"""Pydantic models for Litmus configuration."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class Limit(BaseModel):
    """A test limit with units and optional spec reference."""

    low: Decimal | None = None
    high: Decimal | None = None
    nominal: Decimal | None = None
    units: str
    spec_ref: str | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "low": 4.5,
                "high": 5.5,
                "nominal": 5.0,
                "units": "V",
                "spec_ref": "PWR-RAIL-5V",
            }
        }
    }


class Specification(BaseModel):
    """A product specification that limits are derived from."""

    id: str
    description: str
    nominal: Decimal
    tolerance_pct: Decimal | None = None
    tolerance_abs: Decimal | None = None
    units: str

    def to_limit(self, guardband_pct: Decimal = Decimal("0")) -> Limit:
        """Convert spec to test limit with optional guardbanding.

        Guardband tightens the limit relative to the specification.
        Formula: effective_tolerance = tolerance * (1 - guardband_pct / 100)

        Args:
            guardband_pct: Percentage to tighten the tolerance (0-100).
                          E.g., 10 means 10% guardband.

        Returns:
            Limit with low/high calculated from nominal and tolerance.
        """
        guardband_factor = Decimal("1") - guardband_pct / Decimal("100")

        if self.tolerance_pct is not None:
            tolerance = self.nominal * self.tolerance_pct / Decimal("100")
        elif self.tolerance_abs is not None:
            tolerance = self.tolerance_abs
        else:
            # No tolerance specified, return nominal only
            return Limit(nominal=self.nominal, units=self.units, spec_ref=self.id)

        effective_tolerance = tolerance * guardband_factor
        return Limit(
            low=self.nominal - effective_tolerance,
            high=self.nominal + effective_tolerance,
            nominal=self.nominal,
            units=self.units,
            spec_ref=self.id,
        )


class InstrumentConfig(BaseModel):
    """Configuration for a single instrument (template)."""

    type: str  # e.g., "dmm", "scope", "power_supply"
    driver: str  # e.g., "pyvisa", "serial", "custom"
    resource: str | None = None  # VISA resource string or COM port
    settings: dict = Field(default_factory=dict)  # Instrument-specific settings


class InstrumentInstance(BaseModel):
    """Physical instrument at a station."""

    type: str
    resource: str  # VISA address
    model: str | None = None  # Expected model (for validation)
    capabilities: list[str] = Field(default_factory=list)
    resolution: str | None = None
    bandwidth: str | None = None
    channels: int | None = None


class StationType(BaseModel):
    """Abstract station type (template)."""

    id: str
    description: str
    instruments: dict[str, InstrumentConfig]  # Instrument configs WITHOUT addresses
    capabilities: list[str] = Field(default_factory=list)


class StationInstance(BaseModel):
    """Concrete station instance (deployed)."""

    id: str
    station_type: str  # Reference to StationType
    location: str | None = None
    instruments: dict[str, InstrumentInstance] = Field(default_factory=dict)
    active_fixture: str | None = None  # May be detected at runtime


class FixtureChannel(BaseModel):
    """A single channel/pin on a test fixture."""

    name: str
    instrument: str  # Reference to instrument config
    instrument_channel: str | None = None
    description: str | None = None


class FixtureConfig(BaseModel):
    """Test fixture definition (DUT interface)."""

    id: str
    product_family: str
    channels: dict[str, FixtureChannel]


class DialogConfig(BaseModel):
    """Definition of an operator dialog."""

    id: str
    message: str
    dialog_type: Literal["confirm", "choice", "input", "image"]
    choices: list[str] | None = None
    image_path: str | None = None
    timeout_seconds: int | None = None


class RetryConfig(BaseModel):
    """Retry behavior configuration."""

    max_attempts: int = 1
    delay_seconds: float = 0
    strategy: Literal["always", "on_fail", "dialog", "custom"] = "on_fail"
    dialog_ref: str | None = None  # For strategy="dialog"


class TestStepConfig(BaseModel):
    """Configuration for a single test step."""

    id: str
    description: str
    measurement_name: str | None = None
    limit: Limit | None = None
    limit_ref: str | None = None  # Reference to spec -> derived limit
    pre_dialog: str | None = None  # Reference to DialogConfig
    post_dialog: str | None = None
    retry: RetryConfig | None = None
    skip_on: list[str] | None = None  # Skip if these tests failed


class TestSequenceConfig(BaseModel):
    """Configuration for a test sequence (maps to a pytest module)."""

    id: str
    description: str
    product_family: str
    test_phase: Literal["validation", "characterization", "production"]
    required_fixture: str  # Reference to FixtureConfig
    steps: list[TestStepConfig]
    dialogs: dict[str, DialogConfig] = Field(default_factory=dict)
