"""Pydantic models for Litmus configuration."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

from litmus.utils.ranges import expand_numeric_range, expand_range

# =============================================================================
# Capability Enums (shared vocabulary for products and instruments)
# =============================================================================


class Direction(StrEnum):
    """Direction of signal flow for a capability."""

    INPUT = "input"  # Measure/sense from DUT
    OUTPUT = "output"  # Source/drive to DUT
    BIDIR = "bidir"  # Both (SMU, VNA)


class MeasurementFunction(StrEnum):
    """Named signal measurement/stimulus functions (ATML/IEEE 1641 inspired).

    Replaces the Domain + SignalType pair with a single enum that describes
    what an instrument *does*. Grouped by IVI instrument class for clarity.
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

    # Oscilloscope functions (IVI-Scope)
    WAVEFORM = "waveform"

    # Power supply functions (IVI-DCPwr)
    DC_POWER = "dc_power"
    AC_POWER = "ac_power"

    # Function generator (IVI-FGen)
    SINE = "sine"
    SQUARE = "square"
    RAMP = "ramp"
    TRIANGLE = "triangle"
    PULSE = "pulse"
    ARBITRARY = "arbitrary"

    # SMU functions (combined source-measure)
    # Use DC_VOLTAGE/DC_CURRENT with direction=bidir for SMU

    # RF functions
    RF_POWER = "rf_power"
    RF_CW = "rf_cw"

    # Digital / logic
    LOGIC = "logic"
    COUNTER = "counter"

    # DMM specialty functions
    DIODE = "diode"
    CONTINUITY = "continuity"

    # Electronic load modes
    TRANSIENT = "transient"


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
    units: str


class AccuracySpec(BaseModel):
    """Specification for measurement accuracy."""

    pct_reading: float | None = None  # % of reading
    pct_range: float | None = None  # % of range
    absolute: float | None = None  # Fixed offset


class ResolutionSpec(BaseModel):
    """Specification for measurement resolution."""

    bits: int | None = None  # ADC resolution
    digits: float | None = None  # Display digits (e.g., 6.5)
    value: float | None = None  # Absolute resolution
    units: str | None = None


class InstrumentChannelSpec(BaseModel):
    """Specification for instrument channels.

    This describes the physical channels on an instrument (CH1, ai0, Output1),
    NOT fixture routing points or DUT pins.

    Supports multiple ways to specify channels:
    - count + naming: Generate names from pattern (count=4, naming="CH{n}" → CH1, CH2, CH3, CH4)
    - labels: Explicit list of names (["CH1", "CH2", "TRIG"])
    - range: Range syntax string ("CH[1:4]" → CH1, CH2, CH3, CH4)
    """

    count: int = 1
    simultaneous: bool = False  # Can measure/source all channels at once
    coupling: str | None = None  # single_ended, differential

    # Channel identity - multiple options
    naming: str | None = None  # Pattern: "CH{n}", "ai{n}", "{n}"
    labels: list[str] | None = None  # Explicit: ["CH1", "CH2", "CH3", "CH4"]
    range: str | None = None  # Range syntax: "CH[1:4]", "ai[0:15]", "1:4"

    def channel_names(self) -> list[str]:
        """Generate channel names.

        Priority: range > labels > count+naming

        Examples:
            range="CH[1:4]" → ["CH1", "CH2", "CH3", "CH4"]
            labels=["A", "B"] → ["A", "B"]
            count=4, naming="CH{n}" → ["CH1", "CH2", "CH3", "CH4"]
            count=4, no naming → ["1", "2", "3", "4"]
        """
        if self.range:
            return expand_range(self.range)
        if self.labels:
            return self.labels[: self.count]
        if self.naming:
            return [self.naming.format(n=i + 1) for i in range(self.count)]
        return [str(i + 1) for i in range(self.count)]


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


class SignalParameter(BaseModel):
    """A named parameter within a measurement function's capability.

    Each parameter describes a dimension of an instrument's capability
    (range, accuracy, resolution) or a fixed performance characteristic
    (bandwidth, sample rate).

    Example YAML:
        voltage:
          range: {min: 0.0001, max: 1000, units: V}
          accuracy: {pct_reading: 0.0035, pct_range: 0.0006}
          resolution: {digits: 6.5}
        bandwidth:
          value: 300000
          units: Hz
          role: capability
    """

    range: RangeSpec | None = None
    accuracy: AccuracySpec | None = None
    resolution: ResolutionSpec | None = None
    value: float | None = None  # Fixed value (for capability params like bandwidth)
    units: str | None = None
    role: ParameterRole = ParameterRole.CONTROLLABLE


class ConditionSpec(BaseModel):
    """Specification for matching against a condition value.

    Used in ParameterCondition to specify when a conditional override applies.

    Example: accuracy changes when voltage range exceeds 100V
        condition: {above: 100}
    """

    above: float | None = None
    below: float | None = None
    min: float | None = None
    max: float | None = None
    value: float | None = None  # Exact match


class ParameterCondition(BaseModel):
    """Conditional parameter override (accuracy varies with range, etc.).

    Describes how one parameter's specs change based on another parameter's value.
    Used for datasheet derating curves and range-dependent accuracy specs.

    Example YAML:
        conditions:
          - when:
              voltage: {above: 100}
            accuracy: {pct_reading: 0.015, pct_range: 0.001}
    """

    when: dict[str, ConditionSpec] = Field(default_factory=dict)
    accuracy: AccuracySpec | None = None
    range: RangeSpec | None = None
    resolution: ResolutionSpec | None = None


class FunctionCapability(BaseModel):
    """A single capability of an instrument (replaces old Capability).

    Describes what an instrument can measure or source using the ATML/IEEE 1641
    signal-parameter model: a measurement function with named parameters.

    The function field identifies *what kind of measurement or stimulus* this is.
    Parameters describe the ranges, accuracy, and resolution for each dimension.
    Direction indicates whether this measures (input) or sources (output).

    Example YAML:
        - function: dc_voltage
          direction: input
          parameters:
            voltage:
              range: {min: 0.0001, max: 1000, units: V}
              accuracy: {pct_reading: 0.0035, pct_range: 0.0006}
              resolution: {digits: 6.5}
          channels: ["1"]
          readback: false
    """

    function: MeasurementFunction
    direction: Direction
    parameters: dict[str, SignalParameter] = Field(default_factory=dict)
    channels: list[str] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)
    conditions: list[ParameterCondition] | None = None
    readback: bool = False  # Built-in meter, not primary measurement


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
