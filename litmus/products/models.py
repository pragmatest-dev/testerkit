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

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from litmus.capabilities.models import (
    Capability,
    Comparator,
    Direction,
    Domain,
    RangeSpec,
    SignalType,
)


class ConditionPoint(BaseModel):
    """Spec values at a specific operating condition (ATML-style).

    Uses a pure key-value model: condition parameters (temperature, load, etc.)
    are stored as extra fields alongside spec values (nominal, tolerance).

    Example YAML:
        - temperature: 25
          load: 0.5
          nominal: 3.3
          tolerance_pct: 5

    The condition parameters (temperature, load) go into __pydantic_extra__,
    while the spec values are explicit fields.
    """

    # Spec values (what we're specifying)
    nominal: Decimal | None = None
    tolerance_pct: Decimal | None = None
    tolerance_abs: Decimal | None = None
    limit_low: Decimal | None = None
    limit_high: Decimal | None = None
    comparator: Comparator = Comparator.GELE

    # Condition parameters via extra="allow"
    model_config = {"extra": "allow"}

    @property
    def condition_params(self) -> dict[str, Any]:
        """Extract condition parameters (non-spec fields like temperature, load)."""
        return dict(self.__pydantic_extra__) if self.__pydantic_extra__ else {}

    @property
    def low(self) -> Decimal | None:
        """Calculate lower bound from nominal - tolerance or explicit limit_low."""
        if self.limit_low is not None:
            return self.limit_low
        if self.nominal is None:
            return None
        if self.tolerance_pct is not None:
            return self.nominal * (Decimal("1") - self.tolerance_pct / Decimal("100"))
        if self.tolerance_abs is not None:
            return self.nominal - self.tolerance_abs
        return self.nominal

    @property
    def high(self) -> Decimal | None:
        """Calculate upper bound from nominal + tolerance or explicit limit_high."""
        if self.limit_high is not None:
            return self.limit_high
        if self.nominal is None:
            return None
        if self.tolerance_pct is not None:
            return self.nominal * (Decimal("1") + self.tolerance_pct / Decimal("100"))
        if self.tolerance_abs is not None:
            return self.nominal + self.tolerance_abs
        return self.nominal

    def matches(self, params: dict[str, Any]) -> bool:
        """Check if this condition point matches the given parameters.

        Returns True if all keys in params exist in this condition point
        with matching values. This allows partial matching - querying with
        {temperature: 25} will match a point with {temperature: 25, load: 0.5}.
        """
        my_params = self.condition_params
        for key, query_value in params.items():
            if key not in my_params:
                return False
            point_value = my_params[key]
            # Compare with type coercion for numeric values
            if isinstance(query_value, (int, float, Decimal)) or isinstance(
                point_value, (int, float, Decimal)
            ):
                try:
                    if Decimal(str(query_value)) != Decimal(str(point_value)):
                        return False
                except (ValueError, TypeError):
                    return False
            elif query_value != point_value:
                return False
        return True


class Characteristic(BaseModel):
    """A product characteristic (ATML: UUT Characteristic).

    REUSES Direction, Domain, SignalType from the capabilities module.
    This is the product-side mirror of instrument Capability.

    The direction indicates whether the DUT provides (OUTPUT) or consumes (INPUT)
    this signal. Capability matching pairs OPPOSITE directions:
    - DUT OUTPUT -> Instrument INPUT (measure what DUT provides)
    - DUT INPUT -> Instrument OUTPUT (source what DUT needs)

    Example YAML:
        rail_3v3_output:
          direction: output         # DUT provides this voltage
          domain: voltage
          signal_types: [dc]
          units: V
          datasheet_ref: "DS-001 Section 7.3"
          schematic_ref: "VOUT_3V3"
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

    # Traceability
    datasheet_ref: str | None = None
    schematic_ref: str | None = None  # Net name for fixture mapping

    # Spec values at conditions (ATML-style key-value)
    conditions: list[ConditionPoint] = Field(default_factory=list)

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
            range_max = max_nominal * Decimal("1.2")
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

    characteristic_ref: str | None = None
    conditions: dict[str, Any] = Field(default_factory=dict)
    guardband_pct: Decimal = Decimal("0")
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
    characteristics: dict[str, Characteristic] = Field(default_factory=dict)
    test_requirements: dict[str, TestRequirement] = Field(default_factory=dict)
