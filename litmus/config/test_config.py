"""Test execution configuration models.

Models for test limits, specifications, fixtures, vectors,
sequences, and all test-runner configuration.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

from litmus.config.enums import Comparator
from litmus.utils.ranges import expand_numeric_range

# =============================================================================
# Limits & Specifications
# =============================================================================


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
        "extra": "forbid",
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
        },
    }


class Specification(BaseModel):
    """A product specification that limits are derived from."""

    model_config = {"extra": "forbid"}

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


# =============================================================================
# Fixture models
# =============================================================================


class SwitchRoute(BaseModel):
    """Switch routing for a fixture point.

    Declares which switch channels to close before this point's
    instrument can be used. The platform manages the lifecycle:
    lock acquisition, channel closure, settling, and cleanup.

    Example YAML:
        route:
          switch: matrix
          channels: ["r0c0", "r0c1"]
          settling_ms: 10
    """

    model_config = {"extra": "forbid"}

    switch: str  # Station instrument role for the switch
    channels: list[str]  # Crosspoints/channels to close
    settling_ms: float = 0  # ms to wait after closing


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

    Example YAML (direct wiring):
        vout_measure:
          name: vout_measure
          instrument: dmm
          instrument_channel: "1"
          instrument_terminal: hi
          dut_pin: VOUT
          net: "VOUT_3V3"

    Example YAML (switched routing):
        vout_measure:
          name: vout_measure
          instrument: dmm
          instrument_channel: "1"
          dut_pin: VOUT
          route:
            switch: matrix
            channels: ["r0c0"]
            settling_ms: 10
    """

    model_config = {"extra": "forbid"}

    name: str
    instrument: str  # Reference to instrument config
    instrument_channel: str | None = None
    instrument_terminal: str | None = None  # "hi", "lo", "signal", etc.
    description: str | None = None

    # DUT-side mapping (ATML: signal routing)
    dut_pin: str | None = None  # Reference to Product.pins key
    net: str | None = None  # Match by schematic net name

    # Switch routing (None = direct-wired, no switching needed)
    route: SwitchRoute | None = None


class FixtureSlot(BaseModel):
    """A DUT slot within a multi-DUT fixture.

    Each slot has its own FixturePoint mappings that route DUT pins
    to specific instrument channels. Slots share the same instrument
    roles but use different channels (or entirely different instruments).

    Example YAML:
        slot_1:
          dut_resource: /dev/ttyUSB0
          points:
            vout_measure:
              name: vout_measure
              instrument: dmm
              instrument_channel: "1"
              dut_pin: VOUT
    """

    model_config = {"extra": "forbid"}

    points: dict[str, FixturePoint] = Field(default_factory=dict)
    dut_resource: str | None = None  # Per-slot DUT connection string
    description: str | None = None


class FixtureConfig(BaseModel):
    """Test fixture definition (DUT interface).

    Fixtures define how product pins connect to station instruments.
    They can be scoped to:
    - A specific product (product_id)
    - A product family (product_family) - for shared fixtures
    - A specific revision (product_revision) - optional refinement

    Single-DUT fixtures use ``points`` directly. Multi-DUT fixtures
    use ``slots``, where each slot has its own ``points`` dict mapping
    DUT pins to instrument channels. The two are mutually exclusive.

    For simple setups without formal fixtures, tests can use:
    - Direct instrument access via fixtures (dmm, psu)
    - Ad-hoc pin mappings in test config
    """

    model_config = {"extra": "forbid"}

    id: str
    name: str | None = None

    # Product scope - use one or both
    product_id: str | None = None  # Specific product (preferred)
    product_family: str | None = None  # Product family (for shared fixtures)
    product_revision: str | None = None  # Optional: specific revision

    # DUT connection string (e.g., COM3, /dev/ttyUSB0)
    dut_resource: str | None = None

    # Pin-to-instrument mappings (single-DUT)
    points: dict[str, FixturePoint] = Field(default_factory=dict)
    # Multi-DUT slot mappings
    slots: dict[str, FixtureSlot] = Field(default_factory=dict)

    description: str | None = None

    def model_post_init(self, _: Any) -> None:
        """Validate fixture configuration."""
        if self.points and self.slots:
            raise ValueError(
                "FixtureConfig cannot have both 'points' and 'slots'. "
                "Use 'points' for single-DUT fixtures or 'slots' for multi-DUT."
            )
        for slot_id in self.slots:
            if not slot_id or not slot_id.strip():
                raise ValueError(f"Slot ID must be a non-empty string, got {slot_id!r}")

    @property
    def slot_count(self) -> int:
        """Number of DUT slots (1 for single-DUT fixtures)."""
        return len(self.slots) if self.slots else 1

    @property
    def is_multi_slot(self) -> bool:
        """True if this fixture has multiple DUT slots."""
        return len(self.slots) > 1

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

    model_config = {"extra": "forbid"}

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

    model_config = {"extra": "forbid"}

    start: float
    stop: float
    step: float | None = None
    count: int | None = None

    def model_post_init(self, _: Any) -> None:
        """Validate step/count constraints."""
        if (self.step is None) == (self.count is None):
            raise ValueError("Exactly one of 'step' or 'count' must be provided")
        if self.step is not None and self.step <= 0:
            raise ValueError("'step' must be positive")
        if self.count is not None and self.count < 1:
            raise ValueError("'count' must be >= 1")


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

    model_config = {"extra": "forbid"}

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
            if self.range.step is not None:
                step = self.range.step
                num_steps = int((self.range.stop - self.range.start) / step) + 1
                for i in range(num_steps):
                    result.append(self.range.start + i * step)
            elif self.range.count is not None:
                # Linear interpolation
                count = self.range.count
                if count == 1:
                    result.append(self.range.start)
                else:
                    span = self.range.stop - self.range.start
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

    model_config = {"extra": "forbid"}

    zip: list[LoopVariableConfig]


class PromptConfig(BaseModel):
    """Configuration for operator prompts.

    Example YAML:
        prompt:
          message: "Set chamber to {temperature}C"
          type: confirm
    """

    model_config = {"extra": "forbid"}

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

    model_config = {"extra": "forbid"}

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

    model_config = {"extra": "forbid"}

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

    model_config = {"extra": "forbid"}

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

    model_config = {"extra": "forbid"}

    param: str
    ranges: list[dict[str, Any]]  # List of {below: X, limit: {...}} or {default: ..., limit: {...}}


class LimitCallableConfig(BaseModel):
    """Configuration for Python callable limits.

    Example YAML:
        limits:
          output_voltage:
            callable: "myproject.limits.output_voltage_limit"
    """

    model_config = {"extra": "forbid"}

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

    model_config = {"extra": "forbid"}

    # Direct limit values
    low: float | None = None
    high: float | None = None
    nominal: float | None = None
    units: str | None = None
    # TODO: review naming — ref vs spec_ref vs spec_id are confusingly similar.
    # ref = "derive limits from this Specification" (functional, config-time lookup key)
    # spec_id = "this measurement traces to this characteristic" (traceability)
    # spec_ref = "human-readable note about limit origin" (documentation)
    spec_id: str | None = None
    spec_ref: str | None = None

    # Spec reference (dotted path to a Specification, e.g. "specs.power_board.rail_3v3")
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
                spec_id=self.spec_id,
                spec_ref=self.spec_ref,
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
    model_config = {"extra": "forbid"}

    description: str | None = None
    vectors: VectorConfig | list[dict[str, Any]] | None = None
    retry: RetryConfig | None = None
    limits: dict[str, MeasurementLimitConfig | Limit] = Field(default_factory=dict)
    prompt_before_all: PromptConfig | None = None
    prompt_before_each: bool = False
    prompt: PromptConfig | None = None  # Template for prompt_before_each


class TestStepConfig(BaseModel):
    """Configuration for a single test step.

    A step references either a test, a sequence, or a sync point (mutually exclusive).

    Example with test:
        - id: measure_5v
          test: tests/test_power.py::test_5v
          description: "Verify 5V rail"

    Example with sequence (composition):
        - id: run_smoke
          sequence: power_board_smoke
          description: "Run smoke tests first"

    Example with sync point (multi-DUT):
        - id: wait_thermal
          sync: thermal_soak
          timeout: 300
          description: "Wait for all slots to reach thermal soak"
    """

    __test__ = False  # Prevent pytest collection
    model_config = {"extra": "forbid"}

    id: str
    test: str | None = None  # pytest node ID, e.g. "tests/test_power.py::test_5v"
    sequence: str | None = None  # Reference another sequence by ID
    sync: str | None = None  # Sync point name for multi-DUT coordination
    timeout: float | None = None  # Timeout for sync point (seconds)
    description: str | None = None
    measurement_name: str | None = None
    limit: Limit | None = None  # Inline limit (highest precedence)
    limit_ref: str | None = None  # Spec-derived limit (second precedence)
    pre_dialog: str | None = None  # Reference to DialogConfig
    post_dialog: str | None = None
    aliases: dict[str, str] = Field(
        default_factory=dict,
        description="Maps alias names (test fixture params) to station instrument roles",
    )
    vectors: list[dict[str, Any]] | dict[str, Any] | None = None
    limits: dict[str, Any] | None = None  # Bulk limits dict (lowest precedence)
    mocks: dict[str, Any] | None = None
    retry: RetryConfig | None = None
    skip_on: list[str] | None = None  # Skip if these tests failed

    def model_post_init(self, _: Any) -> None:
        """Validate that step has exactly one of test, sequence, or sync."""
        action_count = sum(1 for x in (self.test, self.sequence, self.sync) if x)
        if action_count == 0:
            raise ValueError("Step must have one of 'test', 'sequence', or 'sync'")
        if action_count > 1:
            raise ValueError("Step must have only one of 'test', 'sequence', or 'sync'")


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
    model_config = {"extra": "forbid"}

    id: str
    name: str | None = None  # Display name (defaults to id)
    description: str
    product_family: str | None = None  # Optional for composable sequences
    test_phase: Literal["development", "validation", "characterization", "production"] | None = None
    required_fixture: str | None = None  # Reference to FixtureConfig
    required_station_type: str | None = None  # Station type required
    steps: list[TestStepConfig] = Field(default_factory=list)
    dialogs: dict[str, Any] = Field(default_factory=dict)  # Inline dialog definitions
    # pytest customization
    pytest_args: list[str] = Field(default_factory=list)  # Extra pytest arguments
    timeout_seconds: int | None = None  # Overall sequence timeout
