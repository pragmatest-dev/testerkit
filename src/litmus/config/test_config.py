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


class ConfigEntry(BaseModel):
    """One entry in a ``config:`` list — mirrors a pytest decorator.

    Five YAML shapes are parsed by :meth:`from_raw`:

    ============================================= =============================================
    YAML                                          Parsed to
    ============================================= =============================================
    ``- flaky``                                   ``ConfigEntry(name="flaky")``
    ``- skip: "reason"``                          ``ConfigEntry(name="skip", args=["reason"])``
    ``- litmus_limits: {v_rail: {...}}``          ``ConfigEntry(name="litmus_limits",
                                                       kwargs={"v_rail": {...}})``
    ``- litmus_sweeps: [{vin: [...]}, ...]``      ``ConfigEntry(name="litmus_sweeps",
                                                       args=[[{"vin": [...]}, ...]])``
    ``- litmus_mocks: [{target: "...", ...}, ...]`` ``ConfigEntry(name="litmus_mocks",
                                                       args=[[{"target": "...", ...}, ...]])``
    ============================================= =============================================

    Dict payloads become kwargs (named-entity markers like
    ``litmus_limits``, ``litmus_prompts``). For ``litmus_sweeps`` and
    ``litmus_mocks`` the YAML payload is itself a list of dicts —
    "enumerated entity" markers — so the list goes into ``args[0]``
    intact. Other list payloads (``parametrize``) flatten into
    positional args. String/number/bool payloads become a single
    positional arg; bare names have neither.

    Each entry maps to one pytest marker — the YAML key is ``config:``
    (runner-neutral vocabulary) but the entries are still pytest
    markers under the hood, so a reader who knows
    ``@pytest.mark.X(...)`` can read the YAML directly.
    """

    model_config = {"extra": "forbid"}

    name: str
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Any) -> Self:
        """Parse one YAML config-list entry into a :class:`ConfigEntry`."""
        if isinstance(raw, str):
            return cls(name=raw)
        if isinstance(raw, dict):
            if len(raw) != 1:
                raise ValueError(
                    "Config entry must be a bare name string or a single-key dict; "
                    f"got dict with {len(raw)} keys: {sorted(raw)}"
                )
            ((name, payload),) = raw.items()
            if not isinstance(name, str):
                raise TypeError(f"Config entry name must be a string; got {type(name).__name__}")
            if payload is None:
                return cls(name=name)
            # ``litmus_sweeps`` and ``litmus_mocks`` require a list of dicts
            # in YAML — list-of-dicts is the canonical shape for "enumerated
            # entities" markers. Single-entry case is still a list
            # (one-element). This forces a uniform schema (no dict-or-list
            # polymorphism) and lets Pydantic validate ``list[Dict]``
            # cleanly without special parsing.
            if name in ("litmus_sweeps", "litmus_mocks") and isinstance(payload, dict):
                shape = "sweep" if name == "litmus_sweeps" else "mock"
                example = (
                    "      - {<argname>: [<values>]}"
                    if name == "litmus_sweeps"
                    else "      - {target: <fixture.attr>, return_value: ...}"
                )
                raise ValueError(
                    f"{name} in YAML must be a list of {shape} dicts; "
                    f"got dict {payload!r}. Wrap your entries in a list:\n"
                    f"  - {name}:\n"
                    f"{example}"
                )
            if isinstance(payload, dict):
                return cls(name=name, kwargs=dict(payload))
            if isinstance(payload, list):
                # litmus_sweeps and litmus_mocks: payload IS the list, treat as
                # one positional arg. Other markers (parametrize, skip, etc.)
                # flatten the list into positional args.
                if name in ("litmus_sweeps", "litmus_mocks"):
                    return cls(name=name, args=[list(payload)])
                return cls(name=name, args=list(payload))
            return cls(name=name, args=[payload])
        raise TypeError(
            f"Config entry must be a string or single-key dict; got {type(raw).__name__}: {raw!r}"
        )

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        """Accept raw YAML shapes (str / single-key dict) during validation."""
        if isinstance(data, ConfigEntry):
            return data
        if (
            isinstance(data, dict)
            and "name" in data
            and set(data).issubset({"name", "args", "kwargs"})
        ):
            return data  # already structured — bare cls(name=...) or round-tripped
        return cls.from_raw(data).model_dump()


# Back-compat alias during the markers → config rename. Internal callers
# that still reference ``MarkerSpec`` (and the plugin) read it as the
# same class. The YAML user-facing surface is ``config:`` only.
MarkerSpec = ConfigEntry


class TestEntry(BaseModel):
    """Recursive node in a sidecar / profile ``tests:`` tree.

    Mirrors pytest's node-id structure: a class is a branch with its own
    ``config:`` (applied to every nested test) and a ``tests:`` dict
    holding its methods; a function is a leaf with config and an empty
    ``tests:``. The same shape composes recursively for nested classes.

    Example::

        tests:
          test_rail:                       # leaf
            config: [- flaky]
          TestRails:                       # branch
            config: [- litmus_vectors: {vin: [4.5, 5.0, 5.5]}]
            tests:
              test_rail:                   # nested leaf
                config: [- litmus_limits: {v_rail: {tolerance_pct: 1.0}}]
    """

    __test__ = False  # Prevent pytest collection (class name starts with "Test")

    model_config = {"extra": "forbid"}

    config: list[ConfigEntry] = Field(default_factory=list)
    tests: dict[str, TestEntry] = Field(default_factory=dict)


class SidecarConfig(BaseModel):
    """Top-level shape of a test-module sidecar YAML.

    File-level ``config:`` applies to every test in the module. Tests and
    classes both live under ``tests:`` — each value is a :class:`TestEntry`,
    so a class branch carries its own config plus a nested ``tests:``
    dict for its methods.

    Example::

        config:
          - litmus_limits: {v_rail: {tolerance_pct: 5.0}}
        tests:
          TestRails:
            config:
              - litmus_vectors: {vin: [4.5, 5.0, 5.5]}
            tests:
              test_rail:
                config:
                  - litmus_limits: {v_rail: {tolerance_pct: 1.0}}
          test_standalone:
            config:
              - skipif: "not os.getenv('HAS_BENCH')"
    """

    __test__ = False  # Prevent pytest collection

    model_config = {"extra": "forbid"}

    config: list[ConfigEntry] = Field(default_factory=list)
    tests: dict[str, TestEntry] = Field(default_factory=dict)


# =============================================================================
# Typed test config (forthcoming — replaces ConfigEntry list shape)
# =============================================================================
#
# These models define the dict-shaped ``config:`` schema that will replace
# the current ``list[ConfigEntry]`` shape. They are additive at this
# point — no caller imports them yet. Subsequent commits will wire them
# through sidecar.py / plugin.py / examples / docs.
#
# Schema shape matches the ROADMAP entry "YAML schema generalization":
#   * `config:` becomes a dict of typed Litmus-marker entries
#   * `runner:` is opaque dict[str, Any] validated by the active runner
#   * sidecar/profile/test-entry all carry both `config:` and `runner:`
#
# Per-Litmus-marker validation details (band resolution for limits,
# range expansion for sweeps, etc.) stay in the plugin layer — these
# Pydantic models enforce structural shape only, mirroring the
# Litmus-core/runner-plugin two-tier validation pattern.


class TestConfig(BaseModel):
    """Runner-neutral test config — one entry per Litmus concept.

    Key/value shapes match the marker family rule:
      * **Named entities** (user-typed identifiers) → dict-keyed-by-name:
        ``limits``, ``prompts``.
      * **Anonymous / positional entries** → list of dicts:
        ``sweeps``, ``mocks``.
      * **Single config / policy** → singleton dict (or ``None``):
        ``connections``, ``retry``.
      * **List of identifier strings** → list of strings: ``specs``.

    Detailed per-entry validation (limit-band parsing, mock-target
    extraction, etc.) happens in plugin handlers; this model enforces
    structural shape only. Top-level ``extra="forbid"`` catches typos
    in marker names before they get silently ignored.
    """

    __test__ = False  # Prevent pytest collection

    model_config = {"extra": "forbid"}

    limits: dict[str, dict[str, Any]] = Field(default_factory=dict)
    sweeps: list[dict[str, Any]] = Field(default_factory=list)
    mocks: list[dict[str, Any]] = Field(default_factory=list)
    specs: list[str] = Field(default_factory=list)
    connections: dict[str, Any] | None = None
    retry: dict[str, Any] | None = None
    prompts: dict[str, dict[str, Any]] = Field(default_factory=dict)


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


class FixtureConnection(BaseModel):
    """A named connection on a test fixture.

    Maps a DUT pin to an instrument channel and terminal, enabling
    complete signal routing traceability. Each named connection is the
    addressable unit; an instrument channel alone is ambiguous because
    channels span instruments.

    Terminology:
    - Pin: Physical DUT connection point (J1.1, TP5)
    - Net: Schematic signal name (VOUT_3V3)
    - FixtureConnection: Named DUT-pin ↔ instrument-channel pairing (vout_measure)
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

    Each slot has its own FixtureConnection mappings that route DUT pins
    to specific instrument channels. Slots share the same instrument
    roles but use different channels (or entirely different instruments).

    Example YAML:
        slot_1:
          dut_resource: /dev/ttyUSB0
          connections:
            vout_measure:
              name: vout_measure
              instrument: dmm
              instrument_channel: "1"
              dut_pin: VOUT
    """

    model_config = {"extra": "forbid"}

    connections: dict[str, FixtureConnection] = Field(default_factory=dict)
    dut_resource: str | None = None  # Per-slot DUT connection string
    description: str | None = None


class FixtureConfig(BaseModel):
    """Test fixture definition (DUT interface).

    Fixtures define how product pins connect to station instruments.
    They can be scoped to:
    - A specific product (product_id)
    - A product family (product_family) - for shared fixtures
    - A specific revision (product_revision) - optional refinement

    Single-DUT fixtures use ``connections`` directly. Multi-DUT fixtures
    use ``slots``, where each slot has its own ``connections`` dict mapping
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

    # DUT-pin ↔ instrument-channel pairings (single-DUT)
    connections: dict[str, FixtureConnection] = Field(default_factory=dict)
    # Multi-DUT slot mappings
    slots: dict[str, FixtureSlot] = Field(default_factory=dict)

    description: str | None = None

    @model_validator(mode="after")
    def _validate_connections_or_slots(self) -> Self:
        if self.connections and self.slots:
            raise ValueError(
                "FixtureConfig cannot have both 'connections' and 'slots'. "
                "Use 'connections' for single-DUT fixtures or 'slots' for multi-DUT."
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

    # Reference to a ProductCharacteristic id on the active product.
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
