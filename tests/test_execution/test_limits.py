"""Tests for limit derivation from product specifications."""

import pytest

from litmus.execution.limits import derive_limit
from litmus.models.config import (
    AccuracySpec,
    Comparator,
    Direction,
    Limit,
    MeasurementFunction,
    RangeSpec,
    SpecBand,
)
from litmus.models.product import ProductCharacteristic


class TestDeriveLimit:
    """Tests for derive_limit function."""

    @pytest.fixture
    def voltage_characteristic(self):
        """Create a voltage output characteristic with multiple conditions."""
        return ProductCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            datasheet_ref="DS-001 Section 7.3",
            specs=[
                SpecBand(
                    when={
                        "temperature": RangeSpec(min=25, max=25),
                        "load": RangeSpec(min=0.1, max=0.1),
                    },
                    value=3.3,
                    accuracy=AccuracySpec(pct_reading=3.0),
                ),
                SpecBand(
                    when={
                        "temperature": RangeSpec(min=25, max=25),
                        "load": RangeSpec(min=1.0, max=1.0),
                    },
                    value=3.3,
                    accuracy=AccuracySpec(pct_reading=5.0),
                ),
                SpecBand(
                    when={
                        "temperature": RangeSpec(min=85, max=85),
                        "load": RangeSpec(min=1.0, max=1.0),
                    },
                    value=3.3,
                    accuracy=AccuracySpec(pct_reading=6.0),
                ),
            ],
        )

    def test_basic_limit_derivation(self, voltage_characteristic):
        """Test deriving a limit with no guardband."""
        limit = derive_limit(
            voltage_characteristic,
            conditions={"temperature": 25, "load": 0.1},
        )

        assert isinstance(limit, Limit)
        assert limit.nominal == 3.3
        assert limit.units == "V"
        # 3% of reading: 3.3 * 0.03 = 0.099
        assert limit.low == pytest.approx(3.201)
        assert limit.high == pytest.approx(3.399)
        assert limit.comparator == Comparator.GELE

    def test_limit_with_guardband(self, voltage_characteristic):
        """Test that guardband tightens the limits."""
        limit = derive_limit(
            voltage_characteristic,
            conditions={"temperature": 25, "load": 0.1},
            guardband_pct=10.0,
        )

        # Spec range: 3.201 to 3.399 (0.198 total)
        # Guardband of 10% removes 0.0099 from each side
        expected_low = 3.201 + (0.198 * 0.10 / 2)
        expected_high = 3.399 - (0.198 * 0.10 / 2)
        assert limit.low == pytest.approx(expected_low)
        assert limit.high == pytest.approx(expected_high)

    def test_limit_different_conditions(self, voltage_characteristic):
        """Test deriving limit for different condition point."""
        limit = derive_limit(
            voltage_characteristic,
            conditions={"temperature": 85, "load": 1.0},
        )

        # 6% pct_reading: uncertainty = 3.3 * 0.06 = 0.198
        assert limit.low == pytest.approx(3.3 - 0.198)
        assert limit.high == pytest.approx(3.3 + 0.198)

    def test_limit_no_matching_condition(self, voltage_characteristic):
        """Test that missing condition raises ValueError."""
        with pytest.raises(ValueError, match="No spec band matches"):
            derive_limit(
                voltage_characteristic,
                conditions={"temperature": -40, "load": 0.5},
            )

    def test_limit_with_explicit_limits(self):
        """Test deriving limit from explicit limit_low/high."""
        char = ProductCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            specs=[
                SpecBand(
                    when={"temperature": RangeSpec(min=25, max=25)},
                    value=3.3,
                ),
            ],
        )

        limit = derive_limit(
            char,
            conditions={"temperature": 25},
            limit_low=3.0,
            limit_high=3.6,
        )

        assert limit.low == 3.0
        assert limit.high == 3.6
        assert limit.nominal == 3.3

    def test_limit_le_comparator(self):
        """Test LE comparator with explicit limit."""
        char = ProductCharacteristic(
            function=MeasurementFunction.DC_CURRENT,
            direction=Direction.INPUT,
            units="A",
            pin="VIN",
            specs=[
                SpecBand(
                    when={"temperature": RangeSpec(min=25, max=25)},
                    value=0.010,
                ),
            ],
        )

        limit = derive_limit(
            char,
            conditions={"temperature": 25},
            comparator=Comparator.LE,
            limit_high=0.015,
        )

        assert limit.comparator == Comparator.LE
        assert limit.high == 0.015
        assert limit.low is None

    def test_limit_spec_ref_traceability(self, voltage_characteristic):
        """Test that spec_ref includes condition info for traceability."""
        limit = derive_limit(
            voltage_characteristic,
            conditions={"temperature": 25, "load": 0.1},
        )

        assert limit.spec_ref is not None
        assert "DS-001 Section 7.3" in limit.spec_ref
        assert "temperature=25" in limit.spec_ref
        assert "load=0.1" in limit.spec_ref

    def test_limit_spec_id_from_char_id_param(self, voltage_characteristic):
        """Test that spec_id is set from explicit char_id parameter."""
        limit = derive_limit(
            voltage_characteristic,
            conditions={"temperature": 25, "load": 0.1},
            char_id="output_voltage",
        )

        assert limit.spec_id == "output_voltage"

    def test_limit_spec_id_none_without_char_id(self, voltage_characteristic):
        """Test that spec_id is None when no char_id provided."""
        limit = derive_limit(
            voltage_characteristic,
            conditions={"temperature": 25, "load": 0.1},
        )

        assert limit.spec_id is None

    def test_guardband_le_comparator(self):
        """Test guardband with single-sided LE comparator."""
        char = ProductCharacteristic(
            function=MeasurementFunction.DC_CURRENT,
            direction=Direction.INPUT,
            units="A",
            pin="VIN",
            specs=[
                SpecBand(
                    when={"temperature": RangeSpec(min=25, max=25)},
                    value=0.5,
                ),
            ],
        )

        limit = derive_limit(
            char,
            conditions={"temperature": 25},
            comparator=Comparator.LE,
            limit_high=1.0,
            guardband_pct=10.0,
        )

        # LE with 10% guardband: 1.0 - 0.1 = 0.9
        assert limit.high == 0.9
        assert limit.comparator == Comparator.LE

    def test_guardband_ge_comparator(self):
        """Test guardband with single-sided GE comparator."""
        char = ProductCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            specs=[
                SpecBand(
                    when={"temperature": RangeSpec(min=25, max=25)},
                    value=5.0,
                ),
            ],
        )

        limit = derive_limit(
            char,
            conditions={"temperature": 25},
            comparator=Comparator.GE,
            limit_low=3.0,
            guardband_pct=10.0,
        )

        # GE with 10% guardband: 3.0 + 0.3 = 3.3
        assert limit.low == 3.3
        assert limit.comparator == Comparator.GE

    def test_eq_comparator_no_guardband(self):
        """Test that EQ comparator ignores guardband."""
        char = ProductCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            specs=[
                SpecBand(
                    when={"temperature": RangeSpec(min=25, max=25)},
                    value=3.3,
                ),
            ],
        )

        limit = derive_limit(
            char,
            conditions={"temperature": 25},
            comparator=Comparator.EQ,
            guardband_pct=10.0,
        )

        assert limit.nominal == 3.3
        assert limit.comparator == Comparator.EQ


class TestGuardbandEdgeCases:
    """Tests for guardband edge cases."""

    def test_zero_guardband(self):
        """Test that zero guardband returns original limits."""
        char = ProductCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            specs=[
                SpecBand(
                    when={"temperature": RangeSpec(min=25, max=25)},
                    value=3.3,
                ),
            ],
        )

        limit = derive_limit(
            char,
            conditions={"temperature": 25},
            limit_low=3.0,
            limit_high=3.6,
        )

        assert limit.low == 3.0
        assert limit.high == 3.6

    def test_large_guardband(self):
        """Test that large guardband significantly tightens limits."""
        char = ProductCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            specs=[
                SpecBand(
                    when={"temperature": RangeSpec(min=25, max=25)},
                    value=3.5,
                ),
            ],
        )

        limit = derive_limit(
            char,
            conditions={"temperature": 25},
            limit_low=3.0,
            limit_high=4.0,
            guardband_pct=50.0,
        )

        # Original range: 1.0, guardband removes 0.25 from each side
        assert limit.low == 3.25
        assert limit.high == 3.75
