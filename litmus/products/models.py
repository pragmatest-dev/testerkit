"""Pydantic models for product specifications.

This module defines the information model for products (DUTs) such that specs
flow down to:
1. Required capabilities (what instruments are needed)
2. Station config (which instruments at which addresses)
3. Test limits (derived from specs with guardbands)
4. Test code (what to measure, what limits to apply)
5. Results (full traceability back to specs)

Key design principle: Product characteristics and instrument capabilities share
the same vocabulary (Direction, Domain, SignalType, Comparator). This enables
trivial capability matching - opposite directions pair.
"""

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, computed_field, model_validator

from litmus.config.models import (
    Capability,
    Comparator,
    Direction,
    Domain,
    RangeSpec,
    SignalType,
)
from litmus.utils.ranges import expand_range


class ConditionPoint(BaseModel):
    """Spec values at a specific operating condition (ATML-style).

    Uses a pure key-value model: condition parameters (temperature, load, etc.)
    are stored as extra fields alongside spec values (nominal, tolerance).

    Example YAML:
        - temperature: 25
          load: 0.5
          nominal: 3.3
          tolerance_pct: 5
          description: "Room temp, light load"

    The condition parameters (temperature, load) go into __pydantic_extra__,
    while the spec values and metadata are explicit fields.
    """

    # Spec values (what we're specifying)
    nominal: float | None = None
    tolerance_pct: float | None = None
    tolerance_abs: float | None = None
    limit_low: float | None = None
    limit_high: float | None = None
    comparator: Comparator = Comparator.GELE

    # Metadata (not used for condition matching)
    description: str | None = None

    # Condition parameters via extra="allow"
    model_config = {"extra": "allow"}

    @property
    def condition_params(self) -> dict[str, Any]:
        """Extract condition parameters (non-spec fields like temperature, load)."""
        return dict(self.__pydantic_extra__) if self.__pydantic_extra__ else {}

    @property
    def low(self) -> float | None:
        """Calculate lower bound from nominal - tolerance or explicit limit_low.

        Handles single-sided specs:
        - If limit_low is set → use it
        - If limit_high is set without limit_low → return None (one-sided: <= only)
        - Otherwise derive from nominal ± tolerance
        """
        if self.limit_low is not None:
            return self.limit_low
        if self.nominal is None:
            return None
        # If only limit_high is set, this is a one-sided spec (<=)
        if self.limit_high is not None:
            return None
        if self.tolerance_pct is not None:
            return self.nominal * (1.0 - self.tolerance_pct / 100.0)
        if self.tolerance_abs is not None:
            return self.nominal - self.tolerance_abs
        return self.nominal

    @property
    def high(self) -> float | None:
        """Calculate upper bound from nominal + tolerance or explicit limit_high.

        Handles single-sided specs:
        - If limit_high is set → use it
        - If limit_low is set without limit_high → return None (one-sided: >= only)
        - Otherwise derive from nominal ± tolerance
        """
        if self.limit_high is not None:
            return self.limit_high
        if self.nominal is None:
            return None
        # If only limit_low is set, this is a one-sided spec (>=)
        if self.limit_low is not None:
            return None
        if self.tolerance_pct is not None:
            return self.nominal * (1.0 + self.tolerance_pct / 100.0)
        if self.tolerance_abs is not None:
            return self.nominal + self.tolerance_abs
        return self.nominal

    def matches(self, params: dict[str, Any]) -> bool:
        """Check if this condition point matches the given parameters.

        Returns True if all keys in THIS CONDITION exist in params with matching
        values. Extra keys in params are ignored. This allows flexible matching:
        - Query {temperature: 25, load: 1.0, vin: 5.0}
        - Condition {temperature: 25, load: 1.0}
        - Result: MATCH (condition params satisfied, vin ignored)

        If this condition has no parameters, it matches any query (universal default).
        """
        my_params = self.condition_params

        # No condition params means this is a universal/default condition
        if not my_params:
            return True

        for key, point_value in my_params.items():
            if key not in params:
                return False
            query_value = params[key]
            # Compare with type coercion for numeric values
            if isinstance(query_value, (int, float)) or isinstance(point_value, (int, float)):
                try:
                    if float(query_value) != float(point_value):
                        return False
                except (ValueError, TypeError):
                    return False
            elif query_value != point_value:
                return False
        return True

    def satisfies(self, requirement: dict[str, Any]) -> bool:
        """Check if this condition point satisfies a requirement.

        Use this for test planning: finding which conditions match a partial
        requirement. All REQUIREMENT params must exist in this condition.

        Example:
        - Condition: {temperature: 25, load: 1.0}
        - Requirement: {temperature: 25}
        - Result: MATCH (requirement param satisfied, extra load is fine)

        Note: This is the inverse of matches(). Use matches() for vector
        execution, use satisfies() for test planning.
        """
        my_params = self.condition_params

        for key, req_value in requirement.items():
            if key not in my_params:
                return False
            point_value = my_params[key]
            # Compare with type coercion for numeric values
            if isinstance(req_value, (int, float)) or isinstance(point_value, (int, float)):
                try:
                    if float(req_value) != float(point_value):
                        return False
                except (ValueError, TypeError):
                    return False
            elif req_value != point_value:
                return False
        return True


class Pin(BaseModel):
    """Physical pin/pad on the DUT (ATML: Port).

    Represents a single connection point that can be routed through
    a fixture to an instrument. Pin behaviour (power input, signal output,
    ground reference, etc.) is described entirely by the characteristics
    that reference it — not by a separate "type" field.

    Example YAML:
        pins:
          VIN:
            name: "J1.1"
            net: "VIN_5V"
            description: "5V power input from bench supply"
          VOUT:
            name: "J1.3"
            net: "VOUT_3V3"
            description: "3.3V regulated output"
    """

    name: str  # Pin designator: "J1.1", "TP5", "U3.14"
    net: str | None = None  # Schematic net name
    description: str | None = None


class BusSignal(BaseModel):
    """A signal within a bus group.

    Example YAML:
        signals:
          - pin: SDA
            role: data
          - pin: SCL
            role: clock
    """

    pin: str  # Reference to Pin key
    role: str  # "clock", "data", "chip_select", "strobe", etc.
    index: int | None = None  # For multi-bit: DATA[0], DATA[1]


class SignalGroup(BaseModel):
    """Grouped signals forming a bus interface (ATML: Bus).

    Used for protocols like I2C, SPI, UART where multiple signals
    must be treated as a unit for routing and testing.

    Example YAML:
        signal_groups:
          i2c_main:
            protocol: i2c
            signals:
              - pin: SDA
                role: data
              - pin: SCL
                role: clock
            parameters:
              frequency: 400000
    """

    protocol: str  # "i2c", "spi", "uart", "parallel", "custom"
    signals: list[BusSignal] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None


class Characteristic(BaseModel):
    """A product characteristic tied to physical DUT interface (ATML: UUT Characteristic).

    REUSES Direction, Domain, SignalType from the capabilities module.
    This is the product-side mirror of instrument Capability.

    The direction indicates whether the DUT provides (OUTPUT) or consumes (INPUT)
    this signal. Capability matching pairs OPPOSITE directions:
    - DUT OUTPUT -> Instrument INPUT (measure what DUT provides)
    - DUT INPUT -> Instrument OUTPUT (source what DUT needs)

    REQUIRES physical interface: Every characteristic must be tied to at least one
    physical connection point (pin, net, or signal_group). This enables:
    - Fixture mapping (characteristic → pin → fixture point → instrument channel)
    - Traceability from measurement back to schematic
    - Clear routing for test execution

    Example YAML:
        rail_3v3_output:
          pin: VOUT               # Physical connection point (REQUIRED)
          direction: output       # DUT provides this voltage
          domain: voltage
          signal_types: [dc]
          units: V
          channel: "1"            # For multi-output DUTs (like instrument channels)
          datasheet_ref: "DS-001 Section 7.3"
          conditions:
            - temperature: 25
              load: 0.1
              nominal: 3.3
              tolerance_pct: 3
    """

    # Shared vocabulary with instrument capabilities
    direction: Direction
    domain: Domain
    signal_types: list[SignalType] = Field(default_factory=lambda: [SignalType.DC])
    units: str

    # Physical interface - AT LEAST ONE REQUIRED
    # These mirror how instruments have `channels` - characteristics need connection points
    pin: str | None = None  # Single pin reference (Product.pins key)
    pins: str | list[str] = Field(default_factory=list)  # Multiple pins (range: "GPIO[0:7]")
    net: str | None = None  # Schematic net name (matches fixture routing)
    signal_group: str | None = None  # Reference to Product.signal_groups key

    # For multi-channel DUT outputs (like instrument's `channels`)
    # E.g., a 4-channel power supply has characteristics for CH1, CH2, CH3, CH4
    channel: str | None = None  # Single channel
    channels: str | list[str] = Field(default_factory=list)  # Multiple channels (range: "CH[1:4]")

    # Traceability
    datasheet_ref: str | None = None

    # Spec values at conditions (ATML-style key-value)
    conditions: list[ConditionPoint] = Field(default_factory=list)

    @computed_field
    @property
    def resolved_pins(self) -> list[str]:
        """Expand pins to list, handling range syntax.

        Supports:
        - Single pin: pin="TP_VOUT" → ["TP_VOUT"]
        - Explicit list: pins=["GPIO0", "GPIO1"] → ["GPIO0", "GPIO1"]
        - Range string: pins="GPIO[0:7]" → ["GPIO0", "GPIO1", ..., "GPIO7"]
        - Non-contiguous: pins="GPIO[0,2,4:6]" → ["GPIO0", "GPIO2", "GPIO4", "GPIO5", "GPIO6"]
        """
        if self.pin:
            return [self.pin]
        if self.pins:
            return expand_range(self.pins)
        return []

    @computed_field
    @property
    def resolved_channels(self) -> list[str]:
        """Expand channels to list, handling range syntax.

        Supports:
        - Single channel: channel="1" → ["1"]
        - Explicit list: channels=["CH1", "CH2"] → ["CH1", "CH2"]
        - Range string: channels="CH[1:4]" → ["CH1", "CH2", "CH3", "CH4"]
        - Numeric range: channels="1:4" → ["1", "2", "3", "4"]
        """
        if self.channel:
            return [self.channel]
        if self.channels:
            return expand_range(self.channels)
        return []

    @model_validator(mode="after")
    def validate_physical_interface(self) -> Self:
        """Ensure characteristic is tied to physical interface.

        Every characteristic must specify WHERE on the DUT it applies.
        This enables fixture mapping and signal routing.

        Accepts:
        - pin/pins: Reference to Product.pins keys (pins supports range syntax)
        - channel/channels: For multi-channel DUTs (channels supports range syntax)
        - net: Schematic net name
        - signal_group: Reference to Product.signal_groups key

        For calculated characteristics (efficiency, regulation) that involve
        multiple measurement points, list all involved pins in `pins`.
        """
        has_interface = any([
            self.pin,
            self.pins,
            self.channel,
            self.channels,
            self.net,
            self.signal_group,
        ])
        if not has_interface:
            raise ValueError(
                "Characteristic must specify physical interface: "
                "pin, pins, channel, channels, net, or signal_group"
            )
        return self

    def get_at_conditions(self, params: dict[str, Any]) -> ConditionPoint | None:
        """Find the condition point matching the given parameters.

        Args:
            params: Dictionary of condition parameters (e.g., temperature=25, load=0.5)

        Returns:
            The matching ConditionPoint, or None if no match found.
        """
        for point in self.conditions:
            if point.matches(params):
                return point
        return None

    def to_capability_requirement(self) -> Capability:
        """Derive instrument capability requirement from this characteristic.

        Pairs OPPOSITE directions:
        - DUT OUTPUT -> instrument INPUT (measure what DUT provides)
        - DUT INPUT -> instrument OUTPUT (source what DUT needs)
        - DUT BIDIR -> instrument BIDIR (need both)

        Returns:
            A Capability describing what instrument capability is needed.
        """
        # Direction pairing: opposite directions match
        if self.direction == Direction.OUTPUT:
            inst_direction = Direction.INPUT
        elif self.direction == Direction.INPUT:
            inst_direction = Direction.OUTPUT
        else:  # BIDIR
            inst_direction = Direction.BIDIR

        # Derive range from conditions (with headroom)
        all_nominals = [c.nominal for c in self.conditions if c.nominal is not None]
        range_spec = None
        if all_nominals:
            max_nominal = max(all_nominals)
            # Include 20% headroom for range coverage
            range_max = max_nominal * 1.2
            range_spec = RangeSpec(max=range_max, units=self.units)

        return Capability(
            direction=inst_direction,
            domain=self.domain,
            signal_types=self.signal_types,
            range=range_spec,
        )


class TestRequirement(BaseModel):
    """A requirement to verify a characteristic (ATML: TestRequirement).

    Links a characteristic to test execution by specifying:
    - Which characteristic to test (characteristic_ref)
    - Which operating conditions to use
    - Manufacturing margin via guardband (guardband_pct)
    - Test priority for coverage planning

    Example YAML:
        verify_output_voltage_room:
          characteristic_ref: rail_3v3_output
          conditions:
            temperature: 25
            load: 0.1
          guardband_pct: 5
          priority: critical
    """

    __test__ = False  # Prevent pytest collection

    characteristic_ref: str | None = None
    conditions: dict[str, Any] = Field(default_factory=dict)
    guardband_pct: float = 0.0
    priority: Literal["critical", "standard", "optional"] = "standard"
    description: str | None = None


class Product(BaseModel):
    """Product definition (ATML: UUT Description).

    Top-level model that ties together:
    - Product identification (id, name, revision)
    - Documentation references (datasheet, schematic)
    - Characteristics with condition matrices
    - Test requirements linking characteristics to tests

    Example YAML:
        product:
          id: power_board_v1
          name: "DC-DC Power Board Rev A"
          revision: "A"
          datasheet: "docs/DS-001.pdf"

        characteristics:
          rail_3v3_output:
            direction: output
            domain: voltage
            ...

        test_requirements:
          verify_output_voltage:
            characteristic_ref: rail_3v3_output
            ...
    """

    id: str
    name: str
    description: str | None = None
    revision: str | None = None
    datasheet: str | None = None
    schematic: str | None = None

    # Physical interface (ATML: UUT Ports)
    pins: dict[str, Pin] = Field(default_factory=dict)
    signal_groups: dict[str, SignalGroup] = Field(default_factory=dict)

    # Electrical characteristics and test requirements
    characteristics: dict[str, Characteristic] = Field(default_factory=dict)
    test_requirements: dict[str, TestRequirement] = Field(default_factory=dict)
