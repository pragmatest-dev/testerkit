"""Pydantic models for instrument capabilities."""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class Direction(StrEnum):
    """Direction of signal flow for a capability."""

    INPUT = "input"  # Measure/sense from DUT
    OUTPUT = "output"  # Source/drive to DUT
    BIDIR = "bidir"  # Both (SMU, VNA)


class Domain(StrEnum):
    """Physical domain of measurement or stimulus."""

    # Electrical - basic
    VOLTAGE = "voltage"
    CURRENT = "current"
    RESISTANCE = "resistance"
    POWER = "power"
    # Electrical - reactive
    CAPACITANCE = "capacitance"
    INDUCTANCE = "inductance"
    IMPEDANCE = "impedance"
    # Electrical - frequency
    FREQUENCY = "frequency"
    PHASE = "phase"
    # Time domain
    TIME = "time"
    LOGIC = "logic"
    # Physical
    TEMPERATURE = "temperature"


class SignalType(StrEnum):
    """Type of signal being measured or sourced."""

    DC = "dc"
    AC = "ac"
    PULSED = "pulsed"
    TRANSIENT = "transient"


class Comparator(StrEnum):
    """Limit comparators per ATML/IEEE 1671.

    Used for limit checking in both instrument capabilities and product specs.
    The comparator defines how a measured value is compared against limits.

    Single-bound comparators:
        EQ: value == nominal (exact match)
        NE: value != nominal (not equal)
        LT: value < high (less than)
        LE: value <= high (less than or equal)
        GT: value > low (greater than)
        GE: value >= low (greater than or equal)

    Range comparators (two bounds):
        GELE: low <= value <= high (inclusive range, most common)
        GELT: low <= value < high (inclusive low, exclusive high)
        GTLE: low < value <= high (exclusive low, inclusive high)
        GTLT: low < value < high (exclusive range)
    """

    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LE = "LE"
    GT = "GT"
    GE = "GE"
    GELE = "GELE"
    GELT = "GELT"
    GTLE = "GTLE"
    GTLT = "GTLT"


class RangeSpec(BaseModel):
    """Specification for measurement or output range."""

    min: Decimal | None = None
    max: Decimal | None = None
    units: str


class AccuracySpec(BaseModel):
    """Specification for measurement accuracy."""

    pct_reading: Decimal | None = None  # % of reading
    pct_range: Decimal | None = None  # % of range
    absolute: Decimal | None = None  # Fixed offset


class ResolutionSpec(BaseModel):
    """Specification for measurement resolution."""

    bits: int | None = None  # ADC resolution
    digits: float | None = None  # Display digits (e.g., 6.5)
    value: Decimal | None = None  # Absolute resolution
    units: str | None = None


class ChannelSpec(BaseModel):
    """Specification for instrument channels."""

    count: int = 1
    simultaneous: bool = False  # Can measure/source all channels at once
    coupling: str | None = None  # single_ended, differential


class Capability(BaseModel):
    """A single capability of an instrument.

    Describes what an instrument can measure or source, including
    the physical domain, signal type, range, accuracy, and features.
    """

    direction: Direction
    domain: Domain
    signal_types: list[SignalType] = Field(default_factory=list)
    channels: ChannelSpec = Field(default_factory=ChannelSpec)
    range: RangeSpec | None = None
    accuracy: AccuracySpec | None = None
    resolution: ResolutionSpec | None = None
    features: list[str] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)
