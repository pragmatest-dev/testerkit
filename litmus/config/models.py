"""Pydantic models for Litmus configuration."""

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from litmus.capabilities.models import Comparator


class Limit(BaseModel):
    """A test limit with units and optional spec reference.

    The comparator field (per ATML/IEEE 1671) defines how the measured
    value is compared against the limits:
        - GELE (default): low <= value <= high (inclusive range)
        - EQ: value == nominal
        - LE: value <= high
        - GE: value >= low
        - etc.
    """

    low: Decimal | None = None
    high: Decimal | None = None
    nominal: Decimal | None = None
    units: str
    spec_ref: str | None = None
    comparator: Comparator = Comparator.GELE

    model_config = {
        "json_schema_extra": {
            "example": {
                "low": 4.5,
                "high": 5.5,
                "nominal": 5.0,
                "units": "V",
                "spec_ref": "PWR-RAIL-5V",
                "comparator": "GELE",
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

    start: Decimal
    stop: Decimal
    step: Decimal | None = None
    count: int | None = None

    def model_post_init(self, __context: Any) -> None:
        """Validate that exactly one of step or count is provided."""
        if (self.step is None) == (self.count is None):
            raise ValueError("Exactly one of 'step' or 'count' must be provided")


class LoopVariableConfig(BaseModel):
    """Configuration for a single loop variable.

    Example YAML (explicit values):
        - name: voltage
          values: [3.3, 5.0, 12.0]

    Example YAML (range):
        - name: temperature
          range:
            start: -40
            stop: 85
            step: 25
    """

    name: str
    values: list[Any] | None = None
    range: RangeConfig | None = None
    prompt: "PromptConfig | None" = None  # Prompt shown when this variable changes

    def model_post_init(self, __context: Any) -> None:
        """Validate that exactly one of values or range is provided."""
        if (self.values is None) == (self.range is None):
            raise ValueError("Exactly one of 'values' or 'range' must be provided")


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
    guardband_pct: Decimal = Decimal("0")


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
    tolerance_pct: Decimal | None = None
    tolerance_abs: Decimal | None = None
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
    low: Decimal | None = None
    high: Decimal | None = None
    nominal: Decimal | None = None
    units: str | None = None

    # Spec reference
    ref: str | None = None
    guardband_pct: Decimal | None = None

    # Expression
    expr: str | None = None
    tolerance_pct: Decimal | None = None
    tolerance_abs: Decimal | None = None

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

    id: str
    test: str | None = None  # pytest node ID, e.g. "tests/test_power.py::test_5v"
    sequence: str | None = None  # Reference another sequence by ID
    description: str | None = None
    measurement_name: str | None = None
    limit: Limit | None = None
    limit_ref: str | None = None  # Reference to spec -> derived limit
    pre_dialog: str | None = None  # Reference to DialogConfig
    post_dialog: str | None = None
    retry: RetryConfig | None = None
    skip_on: list[str] | None = None  # Skip if these tests failed

    def model_post_init(self, __context: Any) -> None:
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

    id: str
    name: str | None = None  # Display name (defaults to id)
    description: str
    product_family: str | None = None  # Optional for composable sequences
    test_phase: Literal["validation", "characterization", "production"] | None = None
    required_fixture: str | None = None  # Reference to FixtureConfig
    required_station_type: str | None = None  # Station type required
    steps: list[TestStepConfig] = Field(default_factory=list)
    dialogs: dict[str, DialogConfig] = Field(default_factory=dict)
    # pytest customization
    pytest_args: list[str] = Field(default_factory=list)  # Extra pytest arguments
    timeout_seconds: int | None = None  # Overall sequence timeout
