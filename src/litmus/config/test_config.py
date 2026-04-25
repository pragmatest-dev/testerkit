"""Test execution configuration models.

Models for test limits, specifications, fixtures, markers,
sequences, and all test-runner configuration.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from litmus.config.enums import Comparator

# =============================================================================
# Markers — the single vocabulary across inline / sidecar / profile
# =============================================================================


class MarkerSpec(BaseModel):
    """One entry in a ``markers:`` list — mirrors a pytest decorator.

    Four YAML shapes are parsed by :meth:`from_raw`:

    ==================================== =============================================
    YAML                                 Parsed to
    ==================================== =============================================
    ``- flaky``                          ``MarkerSpec(name="flaky")``
    ``- skip: "reason"``                 ``MarkerSpec(name="skip", args=["reason"])``
    ``- parametrize: ["vin", [1, 2]]``   ``MarkerSpec(name="parametrize",
                                             args=["vin", [1, 2]])``
    ``- litmus_limits: {v_rail: {...}}`` ``MarkerSpec(name="litmus_limits",
                                             kwargs={"v_rail": {...}})``
    ==================================== =============================================

    List payloads expand to positional args; dict payloads are keyword
    args; string/number/bool payloads become a single positional arg;
    bare names have neither. Mirrors how pytest decorators are called,
    so a reader who knows ``@pytest.mark.parametrize(...)`` can read the
    YAML directly.
    """

    model_config = {"extra": "forbid"}

    name: str
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Any) -> Self:
        """Parse one YAML markers-list entry into a :class:`MarkerSpec`."""
        if isinstance(raw, str):
            return cls(name=raw)
        if isinstance(raw, dict):
            if len(raw) != 1:
                raise ValueError(
                    "Marker spec must be a bare name string or a single-key dict; "
                    f"got dict with {len(raw)} keys: {sorted(raw)}"
                )
            ((name, payload),) = raw.items()
            if not isinstance(name, str):
                raise TypeError(f"Marker name must be a string; got {type(name).__name__}")
            if payload is None:
                return cls(name=name)
            if isinstance(payload, dict):
                return cls(name=name, kwargs=dict(payload))
            if isinstance(payload, list):
                return cls(name=name, args=list(payload))
            return cls(name=name, args=[payload])
        raise TypeError(
            f"Marker entry must be a string or single-key dict; got {type(raw).__name__}: {raw!r}"
        )

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        """Accept raw YAML shapes (str / single-key dict) during validation."""
        if isinstance(data, MarkerSpec):
            return data
        if (
            isinstance(data, dict)
            and "name" in data
            and set(data).issubset({"name", "args", "kwargs"})
        ):
            return data  # already structured — bare cls(name=...) or round-tripped
        return cls.from_raw(data).model_dump()


class TestMarkers(BaseModel):
    """Container for a test / class entry in sidecars and profiles."""

    __test__ = False  # Prevent pytest collection (class name starts with "Test")

    model_config = {"extra": "forbid"}

    markers: list[MarkerSpec] = Field(default_factory=list)


class ClassMarkers(BaseModel):
    """Container for a class entry in sidecars.

    Class-scoped markers apply to every method of the class. Per-method
    overrides go under ``tests.<ClassName>.<method>`` (qualified form)
    rather than nesting inside the class block.
    """

    model_config = {"extra": "forbid"}

    markers: list[MarkerSpec] = Field(default_factory=list)


class SidecarConfig(BaseModel):
    """Top-level shape of a test-module sidecar YAML.

    Three scopes: file-root ``markers``, ``classes.<ClassName>``, and
    ``tests.<name>``. Every entry is a list of :class:`MarkerSpec`.

    Example::

        markers:
          - litmus_limits: {v_rail: {tolerance_pct: 5.0}}
        classes:
          TestRails:
            markers:
              - parametrize: ["vin", [4.5, 5.0, 5.5]]
        tests:
          TestRails.test_rail:
            markers:
              - litmus_limits: {v_rail: {tolerance_pct: 1.0}}
          test_standalone:
            markers:
              - skipif: "not os.getenv('HAS_BENCH')"
    """

    __test__ = False  # Prevent pytest collection

    model_config = {"extra": "forbid"}

    markers: list[MarkerSpec] = Field(default_factory=list)
    classes: dict[str, ClassMarkers] = Field(default_factory=dict)
    tests: dict[str, TestMarkers] = Field(default_factory=dict)


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

    def __contains__(self, value: object) -> bool:
        """Return True iff ``value`` satisfies this limit's comparator.

        Supports ``value in limit`` — the pythonic assert form test
        authors use for ad-hoc checks:

            assert measured in limits["vout"]

        Pytest's assertion rewriter renders the failure via
        :meth:`__repr__` so failures include the limit fields inline.
        """
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        v = float(value)
        cmp = self.comparator
        if cmp == Comparator.EQ:
            return self.nominal is not None and v == self.nominal
        if cmp == Comparator.NE:
            return self.nominal is not None and v != self.nominal
        if cmp == Comparator.LT:
            return self.high is None or v < self.high
        if cmp == Comparator.LE:
            return self.high is None or v <= self.high
        if cmp == Comparator.GT:
            return self.low is None or v > self.low
        if cmp == Comparator.GE:
            return self.low is None or v >= self.low
        if cmp == Comparator.GELE:
            return (self.low is None or v >= self.low) and (self.high is None or v <= self.high)
        if cmp == Comparator.GELT:
            return (self.low is None or v >= self.low) and (self.high is None or v < self.high)
        if cmp == Comparator.GTLE:
            return (self.low is None or v > self.low) and (self.high is None or v <= self.high)
        if cmp == Comparator.GTLT:
            return (self.low is None or v > self.low) and (self.high is None or v < self.high)
        return False

    def __repr__(self) -> str:
        parts: list[str] = []
        if self.low is not None:
            parts.append(f"low={self.low}")
        if self.high is not None:
            parts.append(f"high={self.high}")
        if self.nominal is not None:
            parts.append(f"nominal={self.nominal}")
        if self.units:
            parts.append(f"units={self.units!r}")
        parts.append(f"comparator={self.comparator.value!r}")
        return f"Limit({', '.join(parts)})"


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

    @model_validator(mode="after")
    def _validate_points_or_slots(self) -> Self:
        if self.points and self.slots:
            raise ValueError(
                "FixtureConfig cannot have both 'points' and 'slots'. "
                "Use 'points' for single-DUT fixtures or 'slots' for multi-DUT."
            )
        for slot_id in self.slots:
            if not slot_id or not slot_id.strip():
                raise ValueError(f"Slot ID must be a non-empty string, got {slot_id!r}")
        return self

    @property
    def slot_count(self) -> int:
        """Number of DUT slots (1 for single-DUT fixtures)."""
        return len(self.slots) if self.slots else 1

    @property
    def is_multi_slot(self) -> bool:
        """True if this fixture has multiple DUT slots."""
        return len(self.slots) > 1


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

    @model_validator(mode="after")
    def _validate_step_or_count(self) -> Self:
        if (self.step is None) == (self.count is None):
            raise ValueError("Exactly one of 'step' or 'count' must be provided")
        if self.step is not None and self.step <= 0:
            raise ValueError("'step' must be positive")
        if self.count is not None and self.count < 1:
            raise ValueError("'count' must be >= 1")
        return self


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

    Can also appear as one band in a condition-indexed list — in that shape
    ``when:`` names the active-vector-param values at which this band's
    policy applies. See ``TestConfig.limits`` for the list form.
    """

    model_config = {"extra": "forbid"}

    # Condition-indexed match keys (list-of-bands shape). An empty dict
    # matches any active-vector-params (unconditional band). Mirrors the
    # semantics of ``SpecBand.when`` on product characteristics.
    when: dict[str, Any] = Field(default_factory=dict)

    # Direct limit values
    low: float | None = None
    high: float | None = None
    nominal: float | None = None
    units: str | None = None
    # spec_id = characteristic id (structured traceability, stamped on Limit)
    # spec_ref = human-readable note about limit origin (documentation)
    spec_id: str | None = None
    spec_ref: str | None = None

    # Binding to a ProductCharacteristic id on the active product.
    # When set, the resolver reads product.characteristics[characteristic]
    # .get_spec_at(active_vector_params) → SpecBand, using .value as the
    # nominal against which tolerance_pct / tolerance_abs / guardband_pct
    # are applied. Overrides the test-level characteristic only if the
    # test-level one is absent — one characteristic per test (see plan).
    characteristic: str | None = None

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
    raise_on_fail: bool | None = None  # None = inherit from sequence/decorator
    skip_on: list[str] | None = None  # Skip if these tests failed

    @model_validator(mode="after")
    def _validate_exactly_one_action(self) -> Self:
        action_count = sum(1 for x in (self.test, self.sequence, self.sync) if x)
        if action_count == 0:
            raise ValueError("Step must have one of 'test', 'sequence', or 'sync'")
        if action_count > 1:
            raise ValueError("Step must have only one of 'test', 'sequence', or 'sync'")
        return self


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
    # Sequence-level defaults (step overrides these)
    raise_on_fail: bool | None = None
    retry: RetryConfig | None = None
