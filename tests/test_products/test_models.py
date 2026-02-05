"""Tests for product specification models."""

from litmus.config.models import (
    Capability,
    Comparator,
    Direction,
    Domain,
    SignalType,
)
from litmus.products.models import (
    Characteristic,
    ConditionPoint,
    Product,
    TestRequirement,
)


class TestConditionPoint:
    """Tests for ConditionPoint model."""

    def test_basic_condition_point(self):
        """Test creating a condition point with nominal and tolerance."""
        point = ConditionPoint(
            nominal=3.3,
            tolerance_pct=5.0,
        )
        assert point.nominal == 3.3
        assert point.tolerance_pct == 5.0
        assert point.comparator == Comparator.GELE

    def test_condition_params_via_extra(self):
        """Test that extra fields become condition parameters."""
        point = ConditionPoint(
            temperature=25,
            load=0.5,
            nominal=3.3,
            tolerance_pct=5.0,
        )
        assert point.condition_params == {"temperature": 25, "load": 0.5}

    def test_low_from_tolerance_pct(self):
        """Test low property calculation from percentage tolerance."""
        point = ConditionPoint(
            nominal=100.0,
            tolerance_pct=10.0,
        )
        # 100 * (1 - 10/100) = 90
        assert point.low == 90.0

    def test_high_from_tolerance_pct(self):
        """Test high property calculation from percentage tolerance."""
        import pytest
        point = ConditionPoint(
            nominal=100.0,
            tolerance_pct=10.0,
        )
        # 100 * (1 + 10/100) = 110
        assert point.high == pytest.approx(110.0)

    def test_low_from_tolerance_abs(self):
        """Test low property calculation from absolute tolerance."""
        point = ConditionPoint(
            nominal=5.0,
            tolerance_abs=0.5,
        )
        assert point.low == 4.5

    def test_high_from_tolerance_abs(self):
        """Test high property calculation from absolute tolerance."""
        point = ConditionPoint(
            nominal=5.0,
            tolerance_abs=0.5,
        )
        assert point.high == 5.5

    def test_explicit_limits_override_tolerance(self):
        """Test that explicit limit_low/high override tolerance calculation."""
        point = ConditionPoint(
            nominal=100.0,
            tolerance_pct=10.0,
            limit_low=85.0,
            limit_high=115.0,
        )
        # Should use explicit limits, not calculated
        assert point.low == 85.0
        assert point.high == 115.0

    def test_matches_exact(self):
        """Test matching with exact condition parameters."""
        point = ConditionPoint(
            temperature=25,
            load=0.5,
            nominal=3.3,
        )
        assert point.matches({"temperature": 25, "load": 0.5})

    def test_matches_subset_query_fails(self):
        """Test that subset query fails when condition has more params.

        The condition {temperature: 25, load: 0.5} requires BOTH params
        to be present in the query for a match.
        """
        point = ConditionPoint(
            temperature=25,
            load=0.5,
            nominal=3.3,
        )
        # Query missing 'load' should NOT match condition that has it
        assert not point.matches({"temperature": 25})

    def test_matches_superset_query_passes(self):
        """Test that query with extra params still matches.

        Extra params in the query are ignored - only condition params matter.
        This allows vector {temperature: 25, load: 0.5, vin: 5.0} to match
        condition {temperature: 25, load: 0.5} even though vin is extra.
        """
        point = ConditionPoint(
            temperature=25,
            nominal=3.3,
        )
        # Query has extra 'load' param but condition only needs 'temperature'
        assert point.matches({"temperature": 25, "load": 0.5})

    def test_matches_extra_query_params_ignored(self):
        """Test that extra query params don't affect matching."""
        point = ConditionPoint(
            temperature=25,
            nominal=3.3,
        )
        # Query has 'load' but point doesn't care - all condition params satisfied
        assert point.matches({"temperature": 25, "load": 0.5, "vin": 12.0})

    def test_matches_wrong_value(self):
        """Test that wrong value fails match."""
        point = ConditionPoint(
            temperature=25,
            nominal=3.3,
        )
        assert not point.matches({"temperature": 85})

    def test_matches_numeric_type_coercion(self):
        """Test that numeric comparison works across types."""
        point = ConditionPoint(
            temperature=25,  # int
            nominal=3.3,
        )
        # Should match with float comparison
        assert point.matches({"temperature": 25.0})
        assert point.matches({"temperature": 25})


class TestCharacteristic:
    """Tests for Characteristic model."""

    def test_basic_characteristic(self):
        """Test creating a basic characteristic."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            units="V",
            pin="VOUT",
        )
        assert char.direction == Direction.OUTPUT
        assert char.domain == Domain.VOLTAGE
        assert char.units == "V"

    def test_characteristic_with_conditions(self):
        """Test characteristic with condition points."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    nominal=3.3,
                    tolerance_pct=3.0,
                ),
                ConditionPoint(
                    temperature=85,
                    nominal=3.3,
                    tolerance_pct=5.0,
                ),
            ],
        )
        assert len(char.conditions) == 2

    def test_get_at_conditions_match(self):
        """Test finding a condition point by parameters."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    nominal=3.3,
                    tolerance_pct=3.0,
                ),
                ConditionPoint(
                    temperature=85,
                    nominal=3.35,
                    tolerance_pct=5.0,
                ),
            ],
        )
        point = char.get_at_conditions({"temperature": 85})
        assert point is not None
        assert point.nominal == 3.35

    def test_get_at_conditions_no_match(self):
        """Test that no match returns None."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(temperature=25, nominal=3.3),
            ],
        )
        point = char.get_at_conditions({"temperature": -40})
        assert point is None

    def test_to_capability_requirement_output(self):
        """Test that DUT OUTPUT maps to instrument INPUT."""
        char = Characteristic(
            direction=Direction.OUTPUT,  # DUT provides voltage
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(nominal=3.3),
            ],
        )
        cap = char.to_capability_requirement()
        assert isinstance(cap, Capability)
        # DUT OUTPUT -> instrument INPUT (to measure)
        assert cap.direction == Direction.INPUT
        assert cap.domain == Domain.VOLTAGE

    def test_to_capability_requirement_input(self):
        """Test that DUT INPUT maps to instrument OUTPUT."""
        char = Characteristic(
            direction=Direction.INPUT,  # DUT consumes power
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            units="V",
            pin="VIN",
            conditions=[
                ConditionPoint(nominal=5.0),
            ],
        )
        cap = char.to_capability_requirement()
        # DUT INPUT -> instrument OUTPUT (to source)
        assert cap.direction == Direction.OUTPUT

    def test_to_capability_requirement_bidir(self):
        """Test that DUT BIDIR maps to instrument BIDIR."""
        char = Characteristic(
            direction=Direction.BIDIR,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            units="V",
            pin="DATA",
        )
        cap = char.to_capability_requirement()
        assert cap.direction == Direction.BIDIR

    def test_to_capability_requirement_range(self):
        """Test that capability range is derived from conditions."""
        import pytest
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(nominal=3.3),
                ConditionPoint(nominal=5.0),
                ConditionPoint(nominal=12.0),
            ],
        )
        cap = char.to_capability_requirement()
        # Max nominal is 12.0, with 20% headroom = 14.4
        assert cap.range is not None
        assert float(cap.range.max) == pytest.approx(14.4)
        assert cap.range.units == "V"


class TestTestRequirement:
    """Tests for TestRequirement model."""

    def test_basic_requirement(self):
        """Test creating a basic test requirement."""
        req = TestRequirement(
            characteristic_ref="rail_3v3_output",
            conditions={"temperature": 25},
            guardband_pct=10.0,
            priority="critical",
        )
        assert req.characteristic_ref == "rail_3v3_output"
        assert req.guardband_pct == 10.0
        assert req.priority == "critical"

    def test_requirement_defaults(self):
        """Test default values for test requirement."""
        req = TestRequirement()
        assert req.conditions == {}
        assert req.guardband_pct == 0.0
        assert req.priority == "standard"


class TestProduct:
    """Tests for Product model."""

    def test_basic_product(self):
        """Test creating a basic product."""
        product = Product(
            id="power_board_v1",
            name="DC-DC Power Board Rev A",
            description="5V to 3.3V buck converter",
            revision="A",
        )
        assert product.id == "power_board_v1"
        assert product.name == "DC-DC Power Board Rev A"

    def test_product_with_characteristics(self):
        """Test product with characteristics and requirements."""
        product = Product(
            id="power_board_v1",
            name="Power Board",
            characteristics={
                "rail_3v3_output": Characteristic(
                    direction=Direction.OUTPUT,
                    domain=Domain.VOLTAGE,
                    units="V",
                    pin="VOUT",
                    datasheet_ref="DS-001 Section 7.3",
                    conditions=[
                        ConditionPoint(
                            temperature=25,
                            load=0.1,
                            nominal=3.3,
                            tolerance_pct=3.0,
                        ),
                    ],
                ),
            },
            test_requirements={
                "verify_output_voltage": TestRequirement(
                    characteristic_ref="rail_3v3_output",
                    conditions={"temperature": 25, "load": 0.1},
                    guardband_pct=5.0,
                    priority="critical",
                ),
            },
        )
        assert "rail_3v3_output" in product.characteristics
        assert "verify_output_voltage" in product.test_requirements
