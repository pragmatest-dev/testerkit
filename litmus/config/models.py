"""Pydantic models for Litmus configuration."""

from enum import StrEnum
from typing import Any, Literal

import warnings

from pydantic import BaseModel, Field, computed_field, model_validator

from litmus.utils.ranges import expand_numeric_range

# =============================================================================
# Capability Enums (shared vocabulary for products and instruments)
# =============================================================================


class Direction(StrEnum):
    """Direction of signal flow for a capability."""

    INPUT = "input"  # Signal/sense from DUT
    OUTPUT = "output"  # Source/drive to DUT
    BIDIR = "bidir"  # Both (SMU, VNA)
    TRANSFORM = "transform"  # Signal-path component (amplifier, filter, mixer)


class MeasurementFunction(StrEnum):
    """Named signal measurement/stimulus functions.

    Standards-grounded taxonomy derived from IVI Foundation instrument classes,
    IEEE 1641 signal primitives, and SCPI naming conventions. Designed for ALL
    electronics hardware test: DC, AC, RF, mixed-signal, digital, optical, thermal.

    Design principles:
    - One enum for instruments AND products (direction distinguishes measure vs source)
    - Functions describe WHAT, not HOW (dc_voltage not dmm_dc_volts)
    - Flat enum (grouped by comment only, no hierarchy)
    - Waveform shapes are parameters, not functions (use WaveformShape enum)
    - Instrument-class-neutral (both DMM and scope can measure dc_voltage)
    """

    # DMM functions (IVI-DMM)
    DC_VOLTAGE = "dc_voltage"
    AC_VOLTAGE = "ac_voltage"
    DC_CURRENT = "dc_current"
    AC_CURRENT = "ac_current"
    RESISTANCE = "resistance"
    RESISTANCE_4W = "resistance_4w"
    CAPACITANCE = "capacitance"
    INDUCTANCE = "inductance"
    IMPEDANCE = "impedance"
    FREQUENCY = "frequency"
    PERIOD = "period"
    TEMPERATURE = "temperature"

    # Oscilloscope / waveform capture (IVI-Scope)
    WAVEFORM = "waveform"

    # Power supply / load functions (IVI-DCPwr)
    DC_POWER = "dc_power"
    AC_POWER = "ac_power"

    # SMU functions (combined source-measure)
    # Use DC_VOLTAGE/DC_CURRENT with direction=bidir for SMU

    # RF functions (IVI-RFSigGen, IVI-PwrMeter, IVI-SpecAn)
    RF_POWER = "rf_power"
    RF_CW = "rf_cw"
    S_PARAMETERS = "s_parameters"
    SPECTRUM = "spectrum"
    PHASE_NOISE = "phase_noise"
    NOISE_FIGURE = "noise_figure"
    HARMONICS = "harmonics"

    # Digital / logic (IVI-Counter, digital I/O)
    DIGITAL_PATTERN = "digital_pattern"
    DIGITAL_IO = "digital_io"
    SERIAL_DATA = "serial_data"

    # DMM specialty functions
    DIODE = "diode"
    CONTINUITY = "continuity"

    # RLC meter functions (IVI-LCR)
    QUALITY_FACTOR = "quality_factor"
    DISSIPATION_FACTOR = "dissipation_factor"

    # Time/edge measurements (IVI-Counter, IVI-Scope)
    TIME_INTERVAL = "time_interval"
    PULSE_WIDTH = "pulse_width"
    DUTY_CYCLE = "duty_cycle"
    RISE_TIME = "rise_time"
    FALL_TIME = "fall_time"

    # Phase measurement
    PHASE = "phase"

    # Signal integrity
    POWER_QUALITY = "power_quality"
    JITTER = "jitter"
    EYE_DIAGRAM = "eye_diagram"

    # Signal quality metrics (product datasheet specs)
    THD = "thd"  # Total harmonic distortion
    SNR = "snr"  # Signal-to-noise ratio
    GAIN = "gain"  # Signal transfer ratio (RF amps, lock-in, signal chain)

    # RF network measurements (VNA-derived, but named product specs)
    RETURN_LOSS = "return_loss"  # S11 magnitude — "return loss > 20 dB"
    INSERTION_LOSS = "insertion_loss"  # S21 magnitude — "insertion loss < 0.5 dB"
    VSWR = "vswr"  # Voltage standing wave ratio — "VSWR < 1.5:1"
    GROUP_DELAY = "group_delay"  # Phase derivative — "group delay < 2 ns"

    # Optical (IVI-OpticalAttenuator, IVI-OpticalPowerMeter)
    OPTICAL_POWER = "optical_power"
    WAVELENGTH = "wavelength"

    # Environmental
    HUMIDITY = "humidity"  # Relative humidity measurement

    # Electrometer / charge measurement
    CHARGE = "charge"  # Accumulated charge (fC to µC)

    # Magnetic field (Gaussmeter)
    MAGNETIC_FIELD = "magnetic_field"

    # Position/motion (encoder, stage)
    POSITION = "position"


class WaveformShape(StrEnum):
    """Waveform shapes for function generator outputs.

    Used as a parameter value for capabilities with function=WAVEFORM,
    not as separate MeasurementFunction values. Per IEEE 1641, waveform
    shapes are characteristics of the signal, not distinct signal types.
    """

    SINE = "sine"
    SQUARE = "square"
    TRIANGLE = "triangle"
    RAMP = "ramp"
    PULSE = "pulse"
    ARBITRARY = "arbitrary"
    NOISE = "noise"
    DC = "dc"


class TerminalRole(StrEnum):
    """Physical terminal on an instrument channel (ATE/IVI standard names)."""

    HI = "hi"              # High-side force terminal (positive)
    LO = "lo"              # Low-side / return terminal (negative/ground)
    SENSE_HI = "sense_hi"  # Remote sense high (Kelvin connection)
    SENSE_LO = "sense_lo"  # Remote sense low
    GUARD = "guard"        # Guard terminal (triax center)
    SIGNAL = "signal"      # Single-ended signal (BNC center, probe tip)
    TRIGGER = "trigger"    # Trigger I/O


class GroundTopology(StrEnum):
    """How channel grounds relate to each other and earth."""

    FLOATING = "floating"  # Channels isolated from each other (typical PSU)
    SHARED = "shared"      # All channels share common ground (typical scope, DMM)
    EARTH = "earth"        # Referenced to earth ground


class ConnectorType(StrEnum):
    """Physical connector type on instrument."""

    BINDING_POST = "binding_post"
    BANANA = "banana"
    BNC = "bnc"
    TERMINAL_BLOCK = "terminal_block"
    PROBE = "probe"
    TRIAX = "triax"
    SMA = "sma"
    SMB = "smb"
    SPRING = "spring"
    PXI = "pxi"
    SCREW_TERMINAL = "screw_terminal"


class CompareMode(StrEnum):
    """Comparison direction for capability parameters.

    Determines how instrument and requirement values are compared:
    - CONTAINS: Instrument range must contain required range (default)
    - HIGHER_BETTER: Instrument value must be >= required (gain, bandwidth)
    - LOWER_BETTER: Instrument value must be <= required (noise, THD)
    """

    CONTAINS = "contains"
    HIGHER_BETTER = "higher_better"
    LOWER_BETTER = "lower_better"


class MatchDepth(StrEnum):
    """How deep to check when matching capabilities.

    Each level includes all checks from previous levels:
    - FUNCTION: MeasurementFunction match only
    - DIRECTION: + direction match
    - RANGE: + parameter range containment (current default)
    - ACCURACY: + accuracy comparison
    - RESOLUTION: + resolution comparison
    """

    FUNCTION = "function"
    DIRECTION = "direction"
    RANGE = "range"
    ACCURACY = "accuracy"
    RESOLUTION = "resolution"


class ParameterRole(StrEnum):
    """Role of a signal parameter in a capability.

    Describes how a parameter functions within a measurement or stimulus:
    - CONTROLLABLE: Instrument can set this value (e.g., output voltage)
    - MEASURABLE: Instrument can read this value (e.g., measured voltage)
    - CAPABILITY: Performance limit of the instrument (e.g., bandwidth)
    - CONDITION: Operating condition that affects other parameters (e.g., temperature)
    """

    CONTROLLABLE = "controllable"
    MEASURABLE = "measurable"
    CAPABILITY = "capability"
    CONDITION = "condition"


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


# =============================================================================
# Capability Models (for matching products to stations)
# =============================================================================


class RangeSpec(BaseModel):
    """Specification for measurement or output range."""

    min: float | None = None
    max: float | None = None
    units: str = ""


class AccuracySpec(BaseModel):
    """Specification for measurement accuracy."""

    pct_reading: float | None = None  # % of reading
    pct_range: float | None = None  # % of range
    absolute: float | None = None  # Fixed offset

    def total_uncertainty(self, value: float, range_max: float) -> float:
        """Calculate total uncertainty at a given value and range.

        Combines all applicable uncertainty components:
        - pct_reading: percentage of the measured value
        - pct_range: percentage of the full-scale range
        - absolute: fixed offset

        Returns the total uncertainty as an absolute value.
        """
        u = 0.0
        if self.pct_reading is not None:
            u += (self.pct_reading / 100) * abs(value)
        if self.pct_range is not None:
            u += (self.pct_range / 100) * abs(range_max)
        if self.absolute is not None:
            u += self.absolute
        return u


class ResolutionSpec(BaseModel):
    """Specification for measurement resolution."""

    bits: int | None = None  # ADC resolution
    digits: float | None = None  # Display digits (e.g., 6.5)
    value: float | None = None  # Absolute resolution
    units: str | None = None


class ChannelTopology(BaseModel):
    """Physical topology of a single instrument channel.

    Describes the physical terminals, connector type, and ground topology
    for a channel. Used in catalog and instrument library entries to model
    how instruments physically connect to the DUT.

    Example YAML:
        "1":
          label: "6V/5A Output"
          terminals: [hi, lo, sense_hi, sense_lo]
          connector: binding_post
          ground: floating
    """

    label: str | None = None  # Display name, e.g., "6V/5A Output"
    terminals: list[TerminalRole] = Field(
        default_factory=lambda: [TerminalRole.HI, TerminalRole.LO]
    )
    connector: ConnectorType | None = None
    ground: GroundTopology = GroundTopology.SHARED


class SpecBand(BaseModel):
    """Condition-dependent specification override for a parameter.

    Each band says "at this operating point, here are the specs."
    The ``when`` keys reference sibling parameter names (signals,
    conditions, or controls); multiple keys are ANDed (all must match).
    Empty dict means unconditional (always applies).

    Any field that is ``None`` means "no override — use the top-level default."

    Example YAML (accuracy varies with frequency):
        specs:
          - when:
              frequency: {min: 3, max: 5, units: Hz}
            accuracy: {pct_reading: 0.35, pct_range: 0.03}

    Example YAML (range derated at high frequency):
        specs:
          - when:
              frequency: {min: 3e9, max: 6e9, units: Hz}
            range: {min: -130, max: 5, units: dBm}
            accuracy: {absolute: 0.8}
    """

    when: dict[str, RangeSpec] = Field(default_factory=dict)
    range: RangeSpec | None = None  # Derated range at this operating point
    value: float | None = None  # Nominal/typical at this operating point
    accuracy: AccuracySpec | None = None
    resolution: ResolutionSpec | None = None


class SignalParameter(BaseModel):
    """A named parameter within a measurement function's capability.

    .. deprecated::
        Use ``Signal``, ``Condition``, ``Control``, or ``Attribute`` instead.
        SignalParameter is kept temporarily for backward compatibility with
        instrument library YAML files and existing code.

    Each parameter describes a dimension of an instrument's capability
    (range, accuracy, resolution) or a fixed performance characteristic
    (bandwidth, sample rate).

    Top-level accuracy/resolution are defaults when no SpecBand matches.
    The ``specs`` list holds condition-dependent overrides.

    Example YAML:
        voltage:
          range: {min: 0.1, max: 750, units: V}
          accuracy: {pct_reading: 0.07, pct_range: 0.02}
          resolution: {digits: 6.5}
          specs:
            - when:
                frequency: {min: 3, max: 5, units: Hz}
              accuracy: {pct_reading: 0.35, pct_range: 0.03}
    """

    range: RangeSpec | None = None
    accuracy: AccuracySpec | None = None
    resolution: ResolutionSpec | None = None
    value: float | None = None  # Fixed value (for capability params like bandwidth)
    units: str | None = None
    role: ParameterRole = ParameterRole.CONTROLLABLE
    specs: list[SpecBand] | None = None  # Condition-dependent overrides
    compare: CompareMode | None = None  # Comparison direction for capability params


class Signal(BaseModel):
    """A measurable/sourceable parameter — the primary signal dimension.

    Used for what's being measured or sourced: range defines the operating
    envelope, accuracy/resolution define the quality of measurement.
    Top-level accuracy/resolution are defaults; ``specs`` holds condition-dependent
    overrides (e.g., accuracy varies with frequency).

    Example YAML (instrument):
        signals:
          voltage:
            range: {min: 0.1, max: 1000, units: V}
            accuracy: {pct_reading: 0.0035, pct_range: 0.0006}
            resolution: {digits: 6.5}
            specs:
              - when:
                  frequency: {min: 3, max: 5, units: Hz}
                accuracy: {pct_reading: 0.35, pct_range: 0.03}

    Example YAML (product):
        signals:
          voltage:
            value: 3.3
            units: V
    """

    range: RangeSpec | None = None
    accuracy: AccuracySpec | None = None
    resolution: ResolutionSpec | None = None
    value: float | None = None
    units: str | None = None
    specs: list[SpecBand] | None = None


class Condition(BaseModel):
    """An operating condition that affects accuracy of other parameters.

    Conditions don't have their own accuracy — they define the operating
    point used to look up SpecBand overrides on sibling signals.

    Example YAML:
        conditions:
          frequency:
            range: {min: 3, max: 300000, units: Hz}
    """

    range: RangeSpec | None = None


class Control(BaseModel):
    """A user-configurable knob or setting.

    Controls are instrument settings the user can adjust, like motor position,
    temperature setpoint, or compliance limit. They have a range of valid
    values or a set of discrete options.

    Example YAML:
        controls:
          position:
            range: {min: 0, max: 300, units: mm}
          coupling:
            options: ["AC", "DC"]
            default: "DC"
    """

    range: RangeSpec | None = None
    options: list[float | str] | None = None
    units: str | None = None
    default: float | str | None = None


class Attribute(BaseModel):
    """A fixed hardware fact or performance characteristic.

    Attributes are not adjustable — they describe inherent instrument
    capabilities like bandwidth, sample rate, or input impedance.
    The ``compare`` field controls matching: higher_better for bandwidth,
    lower_better for noise floor, etc.

    Example YAML:
        attributes:
          bandwidth:
            value: 200000000
            units: Hz
            compare: higher_better
          sample_rate:
            value: 2000000000
            units: Sa/s
            compare: higher_better
    """

    value: float
    units: str | None = None


class ConditionKey(StrEnum):
    """Canonical keys for the ``conditions`` dict on a Capability.

    Not enforced at model level; used as a shared vocabulary so products
    and instruments use the same names.

    Derived from audit of 150+ instrument datasheets across 19 vendors and IVI
    Foundation class specifications (IVI-DMM, IVI-Scope, IVI-FGen, IVI-DCPwr).
    """

    # Universal operating conditions
    FREQUENCY = "frequency"  # AC measurement frequency band
    TEMPERATURE = "temperature"  # Ambient/operating temperature
    HUMIDITY = "humidity"  # Relative humidity (specs valid at < 80% RH)
    CALIBRATION_INTERVAL = "calibration_interval"  # Time since last cal (days)

    # Measurement configuration
    NPLC = "nplc"  # Integration time in power line cycles
    AUTO_ZERO = "auto_zero"  # Auto-zero ON/OFF state
    COUPLING = "coupling"  # AC/DC coupling mode
    IMPEDANCE = "impedance"  # Input impedance (50Ω vs 1MΩ)
    SENSE_MODE = "sense_mode"  # Local (2-wire) vs remote (4-wire) sense
    SAMPLE_RATE = "sample_rate"  # Digitizing sample rate
    BANDWIDTH = "bandwidth"  # Measurement bandwidth limit
    FILTER = "filter"  # Digital filter type/order (affects noise/accuracy)
    GATE_TIME = "gate_time"  # Counter/integrator gate period
    ACQUISITION_MODE = "acquisition_mode"  # Normal/average/peak-detect/hi-res
    TIME_CONSTANT = "time_constant"  # Lock-in amplifier tau, controller response

    # Signal characteristics
    SIGNAL_LEVEL = "signal_level"  # Signal amplitude relative to range
    CREST_FACTOR = "crest_factor"  # AC waveform peak-to-RMS ratio

    # Source/load conditions
    LOAD = "load"  # Output load current
    INPUT_VOLTAGE = "input_voltage"  # Input/line voltage
    VOLTAGE = "voltage"  # Operating voltage (derating)
    CURRENT = "current"  # Operating current (derating)
    DUTY_CYCLE = "duty_cycle"  # Pulsed operation duty cycle
    SLEW_RATE = "slew_rate"  # Programmable rise/fall rate
    SETTLING_TIME = "settling_time"  # Transient recovery time

    # Sensor/detector type
    SENSOR = "sensor"  # Sensor type (RTD/TC/diode, Si/InGaAs detector)
    WAVELENGTH = "wavelength"  # Optical wavelength (accuracy varies by λ)

    # RF/signal analysis
    OFFSET = "offset"  # Offset frequency (phase noise)


class Capability(BaseModel):
    """What a signal endpoint can do — shared by products and instruments.

    Base class for both product characteristics and instrument capabilities.
    Describes a measurement function with direction and typed parameter dicts.

    Parameter categories (ATML/IVI/IEEE 1641 lineage):
    - ``signals``: What's being measured/sourced (range + accuracy + resolution + specs)
    - ``conditions``: What affects accuracy (range only, feeds SpecBand lookup)
    - ``controls``: User-configurable knobs (range or options)
    - ``attributes``: Fixed hardware facts (value + units + compare)
    """

    function: MeasurementFunction
    direction: Direction
    signals: dict[str, Signal] = Field(default_factory=dict)
    conditions: dict[str, Condition] = Field(default_factory=dict)
    controls: dict[str, Control] = Field(default_factory=dict)
    attributes: dict[str, Attribute] = Field(default_factory=dict)
    units: str | None = None
    specs: list[SpecBand] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_spec_band_keys(self) -> "Capability":
        """Warn when SpecBand ``when`` keys don't reference known siblings.

        Every key in ``signal.specs[].when`` should match a name in
        either ``signals``, ``conditions``, or ``controls`` on the parent
        capability. Unknown keys indicate a typo or missing declaration.
        """
        # Enforce disjoint namespaces across signals/conditions/controls
        for a_name, a_keys, b_name, b_keys in [
            ("signals", set(self.signals), "conditions", set(self.conditions)),
            ("signals", set(self.signals), "controls", set(self.controls)),
            ("conditions", set(self.conditions), "controls", set(self.controls)),
        ]:
            overlap = a_keys & b_keys
            if overlap:
                raise ValueError(
                    f"{self.function.value}: keys {sorted(overlap)} appear in "
                    f"both {a_name} and {b_name} — each dimension must appear "
                    f"in exactly one"
                )

        known = set(self.signals) | set(self.conditions) | set(self.controls)
        if not known:
            return self
        for sig_name, sig in self.signals.items():
            if not sig.specs:
                continue
            for i, band in enumerate(sig.specs):
                for key in band.when:
                    if key not in known:
                        warnings.warn(
                            f"{self.function.value}: signal '{sig_name}' "
                            f"specs[{i}] references unknown condition key "
                            f"'{key}' (known: {sorted(known)})",
                            stacklevel=2,
                        )
        return self


class InstrumentCapability(Capability):
    """Instrument capability + channels + operational metadata.

    Example YAML:
        - function: dc_voltage
          direction: input
          signals:
            voltage:
              range: {min: 0.0001, max: 1000, units: V}
              accuracy: {pct_reading: 0.0035, pct_range: 0.0006}
              resolution: {digits: 6.5}
          conditions:
            frequency:
              range: {min: 3, max: 300000, units: Hz}
          channels: ["1"]
          readback: false
    """

    channels: str | list[str] = Field(default_factory=list)  # Range: "1:4", list, or int
    modes: list[str] = Field(default_factory=list)
    readback: bool = False  # Built-in meter, not primary measurement

    @computed_field
    @property
    def resolved_channels(self) -> list[str]:
        """Expand channels to list, handling range syntax.

        Supports:
        - Explicit list: ["1", "2", "3"] → ["1", "2", "3"]
        - Range string: "CH[1:4]" → ["CH1", "CH2", "CH3", "CH4"]
        - Numeric range: "1:4" → ["1", "2", "3", "4"]
        - Single string: "1" → ["1"]
        """
        from litmus.utils.ranges import expand_range

        if isinstance(self.channels, list):
            return [str(ch) for ch in self.channels]
        return expand_range(self.channels)


class Limit(BaseModel):
    """A test limit with units and optional spec reference.

    The comparator field (per ATML/IEEE 1671) defines how the measured
    value is compared against the limits:
        - GELE (default): low <= value <= high (inclusive range)
        - EQ: value == nominal
        - LE: value <= high
        - GE: value >= low
        - etc.

    Traceability fields:
        - spec_id: Structured identifier of the characteristic (e.g., "output_voltage")
        - spec_ref: Human-readable reference with conditions (e.g., "Table 4.2 @ temp=25")
    """

    low: float | None = None
    high: float | None = None
    nominal: float | None = None
    units: str
    spec_id: str | None = None  # Characteristic ID for structured traceability
    spec_ref: str | None = None  # Human-readable spec reference with conditions
    comparator: Comparator = Comparator.GELE

    model_config = {
        "json_schema_extra": {
            "example": {
                "low": 4.5,
                "high": 5.5,
                "nominal": 5.0,
                "units": "V",
                "spec_id": "output_voltage",
                "spec_ref": "Table 4.2 @ temp=25, load=0.8",
                "comparator": "GELE",
            }
        }
    }


class Specification(BaseModel):
    """A product specification that limits are derived from."""

    id: str
    description: str
    nominal: float
    tolerance_pct: float | None = None
    tolerance_abs: float | None = None
    units: str

    def to_limit(self, guardband_pct: float = 0.0) -> Limit:
        """Convert spec to test limit with optional guardbanding.

        Guardband tightens the limit relative to the specification.
        Formula: effective_tolerance = tolerance * (1 - guardband_pct / 100)

        Args:
            guardband_pct: Percentage to tighten the tolerance (0-100).
                          E.g., 10 means 10% guardband.

        Returns:
            Limit with low/high calculated from nominal and tolerance.
        """
        guardband_factor = 1.0 - guardband_pct / 100.0

        if self.tolerance_pct is not None:
            tolerance = self.nominal * self.tolerance_pct / 100.0
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


class FixturePoint(BaseModel):
    """A named routing junction on a test fixture.

    Maps a DUT connection point to an instrument channel and terminal,
    enabling complete signal routing traceability. Called "Point" rather
    than "Channel" to avoid confusion with instrument channels.

    Terminology:
    - Pin: Physical DUT connection point (J1.1, TP5)
    - Net: Schematic signal name (VOUT_3V3)
    - FixturePoint: Named routing junction (vout_measure)
    - InstrumentChannel: Physical channel on instrument (CH1, ai0)
    - InstrumentTerminal: Physical terminal (hi, lo, signal)

    Example YAML:
        vout_measure:
          name: vout_measure
          instrument: dmm
          instrument_channel: "1"
          instrument_terminal: hi
          dut_pin: VOUT
          net: "VOUT_3V3"
    """

    name: str
    instrument: str  # Reference to instrument config
    instrument_channel: str | None = None
    instrument_terminal: str | None = None  # "hi", "lo", "signal", etc.
    description: str | None = None

    # DUT-side mapping (ATML: signal routing)
    dut_pin: str | None = None  # Reference to Product.pins key
    net: str | None = None  # Match by schematic net name




class FixtureConfig(BaseModel):
    """Test fixture definition (DUT interface).

    Fixtures define how product pins connect to station instruments.
    They can be scoped to:
    - A specific product (product_id)
    - A product family (product_family) - for shared fixtures
    - A specific revision (product_revision) - optional refinement

    For simple setups without formal fixtures, tests can use:
    - Direct instrument access via fixtures (dmm, psu)
    - Ad-hoc pin mappings in test config
    """

    id: str
    name: str | None = None

    # Product scope - use one or both
    product_id: str | None = None  # Specific product (preferred)
    product_family: str | None = None  # Product family (for shared fixtures)
    product_revision: str | None = None  # Optional: specific revision

    # Pin-to-instrument mappings
    points: dict[str, FixturePoint] = Field(default_factory=dict)

    description: str | None = None

    def matches_product(self, product_id: str, revision: str | None = None) -> bool:
        """Check if this fixture matches a product."""
        # Exact product match
        if self.product_id and self.product_id == product_id:
            if self.product_revision and revision:
                return self.product_revision == revision
            return True

        # Family match (less specific)
        if self.product_family:
            # Would need product lookup to check family membership
            # For now, direct ID comparison as fallback
            return self.product_family == product_id

        return False


class RetryConfig(BaseModel):
    """Retry behavior configuration."""

    max_attempts: int = 1
    delay_seconds: float = 0
    strategy: Literal["always", "on_fail", "dialog", "custom"] = "on_fail"
    dialog_ref: str | None = None  # For strategy="dialog"


# =============================================================================
# Vector Configuration Models
# =============================================================================


class RangeConfig(BaseModel):
    """Configuration for a numeric range of values.

    Example YAML:
        range:
          start: 0.0
          stop: 5.0
          step: 0.5
    """

    start: float
    stop: float
    step: float | None = None
    count: int | None = None

    def model_post_init(self, _: Any) -> None:
        """Validate that exactly one of step or count is provided."""
        if (self.step is None) == (self.count is None):
            raise ValueError("Exactly one of 'step' or 'count' must be provided")


class LoopVariableConfig(BaseModel):
    """Configuration for a single loop variable.

    Supports three input formats:
    1. Explicit list: values=[3.3, 5.0, 12.0]
    2. Range object: range={start: -40, stop: 85, step: 25}
    3. Range string: values="-40:125:25" (compact SCPI-style syntax)

    Example YAML (explicit values):
        - name: voltage
          values: [3.3, 5.0, 12.0]

    Example YAML (range object):
        - name: temperature
          range:
            start: -40
            stop: 85
            step: 25

    Example YAML (range string - NEW):
        - name: temperature
          values: "-40:125:25"    # start:stop:step → -40, -15, 10, ...
        - name: load
          values: "0.1:0.5:0.1"  # → 0.1, 0.2, 0.3, 0.4, 0.5
    """

    name: str
    values: list[Any] | str | None = None  # List, or range string like "-40:125:25"
    range: RangeConfig | None = None
    prompt: "PromptConfig | None" = None  # Prompt shown when this variable changes

    def model_post_init(self, _: Any) -> None:
        """Validate that exactly one of values or range is provided."""
        if (self.values is None) == (self.range is None):
            raise ValueError("Exactly one of 'values' or 'range' must be provided")

    @computed_field
    @property
    def resolved_values(self) -> list[float]:
        """Expand values to list, handling range syntax.

        Returns:
            List of float values ready for iteration.

        Examples:
            values=[1, 2, 3] → [1.0, 2.0, 3.0]
            values="-40:125:55" → [-40.0, 15.0, 70.0, 125.0]
            range={start: 0, stop: 1, step: 0.5} → [0.0, 0.5, 1.0]
        """
        if self.values is not None:
            return expand_numeric_range(self.values)

        if self.range is not None:
            # Generate from RangeConfig
            result: list[float] = []
            current = self.range.start
            if self.range.step is not None:
                step = self.range.step
                while current <= self.range.stop:
                    result.append(current)
                    current += step
            elif self.range.count is not None:
                # Linear interpolation
                span = self.range.stop - self.range.start
                count = self.range.count
                for i in range(count):
                    result.append(self.range.start + span * i / (count - 1))
            return result

        return []


class ZippedLoopConfig(BaseModel):
    """Configuration for zipped variables that iterate together.

    Example YAML:
        - zip:
            - name: voltage
              values: [3.3, 5.0, 12.0]
            - name: expected
              values: [3.2, 4.9, 11.8]
    """

    zip: list[LoopVariableConfig]


class PromptConfig(BaseModel):
    """Configuration for operator prompts.

    Example YAML:
        prompt:
          message: "Set chamber to {temperature}C"
          type: confirm
    """

    message: str
    prompt_type: Literal["confirm", "choice", "input"] = "confirm"
    choices: list[str] | None = None
    timeout_seconds: int | None = None


class LimitRefConfig(BaseModel):
    """Configuration for a limit reference to a spec.

    Example YAML:
        limits:
          output_voltage:
            ref: specs.power_board.rail_3v3
            guardband_pct: 10
    """

    ref: str
    guardband_pct: float = 0.0


class LimitExprConfig(BaseModel):
    """Configuration for expression-based limits.

    Example YAML:
        limits:
          output_voltage:
            expr: "0.66 * vector.input_voltage"
            tolerance_pct: 5
            units: V
    """

    expr: str
    tolerance_pct: float | None = None
    tolerance_abs: float | None = None
    units: str


class LimitLookupConfig(BaseModel):
    """Configuration for lookup-table based limits.

    Example YAML:
        limits:
          output_voltage:
            lookup:
              key: temperature
              table:
                -40: { low: 3.0, high: 3.6 }
                25: { low: 3.1, high: 3.5 }
    """

    key: str
    table: dict[str, Limit]
    units: str | None = None


class LimitStepConfig(BaseModel):
    """Configuration for step-function limits.

    Example YAML:
        limits:
          output_voltage:
            steps:
              param: load_current
              ranges:
                - below: 0.5
                  limit: { low: 3.2, high: 3.4, units: V }
                - below: 1.0
                  limit: { low: 3.1, high: 3.5, units: V }
                - default:
                  limit: { low: 3.0, high: 3.6, units: V }
    """

    param: str
    ranges: list[dict[str, Any]]  # List of {below: X, limit: {...}} or {default: ..., limit: {...}}


class LimitCallableConfig(BaseModel):
    """Configuration for Python callable limits.

    Example YAML:
        limits:
          output_voltage:
            callable: "myproject.limits.output_voltage_limit"
    """

    callable: str  # Dotted path to Python function


class MeasurementLimitConfig(BaseModel):
    """Configuration for a measurement's limit (union of limit types).

    Supports multiple limit strategies:
    - Direct limit: low/high/nominal values
    - Spec reference: ref to specification with optional guardband
    - Expression: formula based on vector params
    - Lookup: table keyed by vector param
    - Step function: ranges based on param value
    - Callable: Python function for complex logic
    """

    # Direct limit values
    low: float | None = None
    high: float | None = None
    nominal: float | None = None
    units: str | None = None

    # Spec reference
    ref: str | None = None
    guardband_pct: float | None = None
    comparator: Comparator | None = None

    # Expression
    expr: str | None = None
    tolerance_pct: float | None = None
    tolerance_abs: float | None = None

    # Lookup table
    lookup: LimitLookupConfig | None = None

    # Step function
    steps: LimitStepConfig | None = None

    # Python callable
    callable: str | None = None

    def to_limit(self) -> Limit | None:
        """Convert direct limit values to a Limit object.

        Returns None if this is not a direct limit configuration.
        """
        if self.low is not None or self.high is not None or self.nominal is not None:
            return Limit(
                low=self.low,
                high=self.high,
                nominal=self.nominal,
                units=self.units or "",
            )
        return None


class VectorConfig(BaseModel):
    """Configuration for test vector expansion.

    Supports multiple expansion modes:
    - Explicit list: Just a list of parameter dicts
    - product: Cartesian product of parameters
    - zip: Parallel iteration of parameters
    - nested: Nested loops with fine-grained control

    Example YAML (product):
        vectors:
          expand: product
          voltage: [3.3, 5.0, 12.0]
          current: [0.1, 0.5, 1.0]

    Example YAML (nested):
        vectors:
          expand: nested
          loops:
            - name: temperature
              values: [-40, 25, 85]
            - name: voltage
              range: { start: 3.0, stop: 3.6, step: 0.1 }
    """

    expand: Literal["product", "zip", "nested"] | None = None
    loops: list[LoopVariableConfig | ZippedLoopConfig] | None = None
    # For product/zip modes, parameters are stored as extra fields


class TestConfig(BaseModel):
    """Configuration for a test function with vectors.

    This is the top-level config for a pytest test function that
    uses vector expansion.

    Example YAML:
        test_voltage_sweep:
          description: "Sweep input voltage and measure output"
          vectors:
            expand: product
            voltage: [3.3, 5.0, 12.0]
            load: [0.1, 0.5, 1.0]
          retry:
            max_attempts: 3
            delay_seconds: 0.5
          limits:
            output_voltage:
              ref: specs.power_board.rail_3v3
              guardband_pct: 10
    """

    __test__ = False  # Prevent pytest collection

    description: str | None = None
    vectors: VectorConfig | list[dict[str, Any]] | None = None
    retry: RetryConfig | None = None
    limits: dict[str, MeasurementLimitConfig | Limit] = Field(default_factory=dict)
    prompt_before_all: PromptConfig | None = None
    prompt_before_each: bool = False
    prompt: PromptConfig | None = None  # Template for prompt_before_each


class TestStepConfig(BaseModel):
    """Configuration for a single test step.

    A step references either a test OR another sequence (mutually exclusive).

    Example with test:
        - id: measure_5v
          test: tests/test_power.py::test_5v
          description: "Verify 5V rail"

    Example with sequence (composition):
        - id: run_smoke
          sequence: power_board_smoke
          description: "Run smoke tests first"
    """

    __test__ = False  # Prevent pytest collection

    id: str
    test: str | None = None  # pytest node ID, e.g. "tests/test_power.py::test_5v"
    sequence: str | None = None  # Reference another sequence by ID
    description: str | None = None
    measurement_name: str | None = None
    limit: Limit | None = None
    limit_ref: str | None = None  # Reference to spec -> derived limit
    pre_dialog: str | None = None  # Reference to DialogConfig
    post_dialog: str | None = None
    aliases: dict[str, str] = Field(
        default_factory=dict,
        description="Maps alias names (test fixture params) to station instrument roles",
    )
    vectors: list[dict[str, Any]] | dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    mocks: dict[str, Any] | None = None
    retry: RetryConfig | None = None
    skip_on: list[str] | None = None  # Skip if these tests failed

    def model_post_init(self, _: Any) -> None:
        """Validate that step has either test or sequence, not both."""
        if not self.test and not self.sequence:
            raise ValueError("Step must have either 'test' or 'sequence'")
        if self.test and self.sequence:
            raise ValueError("Step cannot have both 'test' and 'sequence'")


class TestSequenceConfig(BaseModel):
    """Configuration for a test sequence.

    A test sequence is a named, ordered collection of test steps that an
    operator can select and run. Steps reference pytest node IDs explicitly,
    making the sequence the source of truth for test execution order.

    Sequences can compose other sequences via step references:
        - id: run_smoke
          sequence: power_board_smoke  # Expands to all tests in that sequence

    Example YAML (sequences/power_board_smoke.yaml):
        sequence:
          id: power_board_smoke
          name: "Power Board - Smoke Test"
          description: "Quick power-up verification"

          steps:
            - id: measure_5v_rail
              test: tests/test_power_board.py::test_measure_5v_rail
              description: "Verify 5V rail present"

            - id: measure_3v3_rail
              test: tests/test_power_board.py::test_measure_3v3_rail
              description: "Verify 3.3V rail present"
              skip_on: [measure_5v_rail]

    Example YAML (sequences/power_board_full.yaml):
        sequence:
          id: power_board_full
          name: "Power Board - Full Test"
          description: "Complete functional test"
          product_family: power_board
          test_phase: production

          steps:
            - id: smoke_tests
              sequence: power_board_smoke  # Compose smoke as first step

            - id: load_test
              test: tests/test_power_board.py::test_load_5v
              pre_dialog: confirm_load_connected

          dialogs:
            confirm_load_connected:
              id: confirm_load_connected
              message: "Connect electronic load to 5V output"
              dialog_type: confirm
    """

    __test__ = False  # Prevent pytest collection

    id: str
    name: str | None = None  # Display name (defaults to id)
    description: str
    product_family: str | None = None  # Optional for composable sequences
    test_phase: Literal["validation", "characterization", "production"] | None = None
    required_fixture: str | None = None  # Reference to FixtureConfig
    required_station_type: str | None = None  # Station type required
    steps: list[TestStepConfig] = Field(default_factory=list)
    dialogs: dict[str, Any] = Field(default_factory=dict)  # Inline dialog definitions
    # pytest customization
    pytest_args: list[str] = Field(default_factory=list)  # Extra pytest arguments
    timeout_seconds: int | None = None  # Overall sequence timeout
