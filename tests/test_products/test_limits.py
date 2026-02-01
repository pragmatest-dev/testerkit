"""Tests for limit derivation from product specifications."""

from decimal import Decimal

import pytest

from litmus.capabilities.models import Comparator, Direction, Domain, SignalType
from litmus.config.models import Limit
from litmus.products.limits import derive_limit, derive_limits_for_requirement
from litmus.products.models import Characteristic, ConditionPoint, TestRequirement


class TestDeriveLimit:
    """Tests for derive_limit function."""

    @pytest.fixture
    def voltage_characteristic(self):
        """Create a voltage output characteristic with multiple conditions."""
        return Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            signal_types=[SignalType.DC],
            units="V",
            pin="VOUT",  # Physical interface required
            datasheet_ref="DS-001 Section 7.3",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    load=Decimal("0.1"),
                    nominal=Decimal("3.3"),
                    tolerance_pct=Decimal("3"),
                ),
                ConditionPoint(
                    temperature=25,
                    load=Decimal("1.0"),
                    nominal=Decimal("3.3"),
                    tolerance_pct=Decimal("5"),
                ),
                ConditionPoint(
                    temperature=85,
                    load=Decimal("1.0"),
                    nominal=Decimal("3.3"),
                    tolerance_pct=Decimal("6"),
                ),
            ],
        )

    def test_basic_limit_derivation(self, voltage_characteristic):
        """Test deriving a limit with no guardband."""
        req = TestRequirement(
            characteristic_ref="rail_3v3_output",
            conditions={"temperature": 25, "load": Decimal("0.1")},
            guardband_pct=Decimal("0"),
        )

        limit = derive_limit(voltage_characteristic, req)

        assert isinstance(limit, Limit)
        assert limit.nominal == Decimal("3.3")
        assert limit.units == "V"
        # 3.3 * (1 - 0.03) = 3.201, 3.3 * (1 + 0.03) = 3.399
        assert limit.low == Decimal("3.201")
        assert limit.high == Decimal("3.399")
        assert limit.comparator == Comparator.GELE

    def test_limit_with_guardband(self, voltage_characteristic):
        """Test that guardband tightens the limits."""
        req = TestRequirement(
            characteristic_ref="rail_3v3_output",
            conditions={"temperature": 25, "load": Decimal("0.1")},
            guardband_pct=Decimal("10"),
        )

        limit = derive_limit(voltage_characteristic, req)

        # Spec range: 3.201 to 3.399 (0.198 total)
        # Guardband of 10% removes 0.0198 from each side
        # New range: 3.2109 to 3.3891
        expected_low = Decimal("3.201") + (Decimal("0.198") * Decimal("0.10") / 2)
        expected_high = Decimal("3.399") - (Decimal("0.198") * Decimal("0.10") / 2)
        assert limit.low == expected_low
        assert limit.high == expected_high

    def test_limit_different_conditions(self, voltage_characteristic):
        """Test deriving limit for different condition point."""
        req = TestRequirement(
            conditions={"temperature": 85, "load": Decimal("1.0")},
            guardband_pct=Decimal("0"),
        )

        limit = derive_limit(voltage_characteristic, req)

        # 6% tolerance at 85C, 1.0A load
        assert limit.low == Decimal("3.3") * (1 - Decimal("0.06"))
        assert limit.high == Decimal("3.3") * (1 + Decimal("0.06"))

    def test_limit_no_matching_condition(self, voltage_characteristic):
        """Test that missing condition raises ValueError."""
        req = TestRequirement(
            conditions={"temperature": -40, "load": Decimal("0.5")},
        )

        with pytest.raises(ValueError, match="No condition point matches"):
            derive_limit(voltage_characteristic, req)

    def test_limit_preserves_comparator(self):
        """Test that comparator is preserved from spec."""
        char = Characteristic(
            direction=Direction.INPUT,
            domain=Domain.CURRENT,
            units="A",
            pin="VIN",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    comparator=Comparator.LE,
                    limit_high=Decimal("0.015"),
                ),
            ],
        )
        req = TestRequirement(conditions={"temperature": 25})

        limit = derive_limit(char, req)

        assert limit.comparator == Comparator.LE
        assert limit.high == Decimal("0.015")
        assert limit.low is None  # LE comparator only needs high

    def test_limit_with_explicit_limits(self):
        """Test deriving limit from explicit limit_low/high values."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    nominal=Decimal("3.3"),
                    limit_low=Decimal("3.0"),
                    limit_high=Decimal("3.6"),
                ),
            ],
        )
        req = TestRequirement(conditions={"temperature": 25})

        limit = derive_limit(char, req)

        assert limit.low == Decimal("3.0")
        assert limit.high == Decimal("3.6")
        assert limit.nominal == Decimal("3.3")

    def test_limit_spec_ref_traceability(self, voltage_characteristic):
        """Test that spec_ref includes condition info for traceability."""
        req = TestRequirement(
            conditions={"temperature": 25, "load": Decimal("0.1")},
        )

        limit = derive_limit(voltage_characteristic, req)

        assert "DS-001 Section 7.3" in limit.spec_ref
        assert "temperature=25" in limit.spec_ref
        assert "load=0.1" in limit.spec_ref

    def test_limit_spec_id_from_char_id_param(self, voltage_characteristic):
        """Test that spec_id is set from explicit char_id parameter."""
        req = TestRequirement(
            characteristic_ref="other_ref",
            conditions={"temperature": 25, "load": Decimal("0.1")},
        )

        limit = derive_limit(voltage_characteristic, req, char_id="output_voltage")

        assert limit.spec_id == "output_voltage"

    def test_limit_spec_id_falls_back_to_characteristic_ref(self, voltage_characteristic):
        """Test that spec_id falls back to characteristic_ref if no char_id given."""
        req = TestRequirement(
            characteristic_ref="rail_3v3_output",
            conditions={"temperature": 25, "load": Decimal("0.1")},
        )

        limit = derive_limit(voltage_characteristic, req)

        assert limit.spec_id == "rail_3v3_output"

    def test_limit_spec_id_is_none_when_no_ref(self, voltage_characteristic):
        """Test that spec_id can be None if no reference is provided."""
        req = TestRequirement(
            conditions={"temperature": 25, "load": Decimal("0.1")},
        )

        limit = derive_limit(voltage_characteristic, req)

        # characteristic_ref is None by default, so spec_id should also be None
        assert limit.spec_id is None

    def test_guardband_le_comparator(self):
        """Test guardband with single-sided LE comparator."""
        char = Characteristic(
            direction=Direction.INPUT,
            domain=Domain.CURRENT,
            units="A",
            pin="VIN",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    comparator=Comparator.LE,
                    limit_high=Decimal("1.0"),
                ),
            ],
        )
        req = TestRequirement(
            conditions={"temperature": 25},
            guardband_pct=Decimal("10"),
        )

        limit = derive_limit(char, req)

        # LE with 10% guardband: 1.0 - 0.1 = 0.9
        assert limit.high == Decimal("0.9")
        assert limit.comparator == Comparator.LE

    def test_guardband_ge_comparator(self):
        """Test guardband with single-sided GE comparator."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    comparator=Comparator.GE,
                    limit_low=Decimal("3.0"),
                ),
            ],
        )
        req = TestRequirement(
            conditions={"temperature": 25},
            guardband_pct=Decimal("10"),
        )

        limit = derive_limit(char, req)

        # GE with 10% guardband: 3.0 + 0.3 = 3.3
        assert limit.low == Decimal("3.3")
        assert limit.comparator == Comparator.GE


class TestDeriveLimitsForRequirement:
    """Tests for derive_limits_for_requirement function."""

    def test_single_match(self):
        """Test deriving limits with single matching condition."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    load=Decimal("0.5"),
                    nominal=Decimal("3.3"),
                    tolerance_pct=Decimal("3"),
                ),
            ],
        )
        req = TestRequirement(
            conditions={"temperature": 25, "load": Decimal("0.5")},
        )

        results = derive_limits_for_requirement(char, req)

        assert len(results) == 1
        conditions, limit = results[0]
        assert conditions == {"temperature": 25, "load": Decimal("0.5")}
        assert limit.nominal == Decimal("3.3")

    def test_partial_condition_match(self):
        """Test deriving limits with partial condition match."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    load=Decimal("0.1"),
                    nominal=Decimal("3.3"),
                    tolerance_pct=Decimal("3"),
                ),
                ConditionPoint(
                    temperature=25,
                    load=Decimal("0.5"),
                    nominal=Decimal("3.3"),
                    tolerance_pct=Decimal("4"),
                ),
                ConditionPoint(
                    temperature=85,
                    load=Decimal("0.5"),
                    nominal=Decimal("3.3"),
                    tolerance_pct=Decimal("5"),
                ),
            ],
        )
        # Requirement only specifies temperature=25
        req = TestRequirement(
            conditions={"temperature": 25},
        )

        results = derive_limits_for_requirement(char, req)

        # Should match both temperature=25 conditions
        assert len(results) == 2
        loads = [c["load"] for c, _ in results]
        assert Decimal("0.1") in loads
        assert Decimal("0.5") in loads

    def test_no_matches(self):
        """Test that no matches returns empty list."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    nominal=Decimal("3.3"),
                ),
            ],
        )
        req = TestRequirement(
            conditions={"temperature": 85},
        )

        results = derive_limits_for_requirement(char, req)

        assert len(results) == 0


class TestGuardbandEdgeCases:
    """Tests for guardband edge cases."""

    def test_zero_guardband(self):
        """Test that zero guardband returns original limits."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    limit_low=Decimal("3.0"),
                    limit_high=Decimal("3.6"),
                ),
            ],
        )
        req = TestRequirement(
            conditions={"temperature": 25},
            guardband_pct=Decimal("0"),
        )

        limit = derive_limit(char, req)

        assert limit.low == Decimal("3.0")
        assert limit.high == Decimal("3.6")

    def test_large_guardband(self):
        """Test that large guardband significantly tightens limits."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    limit_low=Decimal("3.0"),
                    limit_high=Decimal("4.0"),
                ),
            ],
        )
        req = TestRequirement(
            conditions={"temperature": 25},
            guardband_pct=Decimal("50"),  # 50% guardband
        )

        limit = derive_limit(char, req)

        # Original range: 1.0, guardband removes 0.25 from each side
        assert limit.low == Decimal("3.25")
        assert limit.high == Decimal("3.75")

    def test_eq_comparator_no_guardband(self):
        """Test that EQ comparator ignores guardband."""
        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="VOUT",
            conditions=[
                ConditionPoint(
                    temperature=25,
                    nominal=Decimal("3.3"),
                    comparator=Comparator.EQ,
                ),
            ],
        )
        req = TestRequirement(
            conditions={"temperature": 25},
            guardband_pct=Decimal("10"),
        )

        limit = derive_limit(char, req)

        # EQ comparator - guardband not applicable
        assert limit.nominal == Decimal("3.3")
        assert limit.comparator == Comparator.EQ
