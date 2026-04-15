"""Tests for range expansion utilities."""

from litmus.utils.ranges import expand_numeric_range, expand_range


class TestExpandRange:
    """Tests for expand_range() function."""

    # === Named ranges (prefix[range]) ===

    def test_expand_prefix_range_simple(self):
        """GPIO[0:2] → GPIO0, GPIO1, GPIO2."""
        assert expand_range("GPIO[0:2]") == ["GPIO0", "GPIO1", "GPIO2"]

    def test_expand_prefix_range_larger(self):
        """GPIO[0:7] → 8 pins."""
        result = expand_range("GPIO[0:7]")
        assert len(result) == 8
        assert result[0] == "GPIO0"
        assert result[7] == "GPIO7"

    def test_expand_prefix_range_non_zero_start(self):
        """CH[1:4] → CH1, CH2, CH3, CH4."""
        assert expand_range("CH[1:4]") == ["CH1", "CH2", "CH3", "CH4"]

    def test_expand_prefix_range_non_contiguous(self):
        """GPIO[0,2,4:6] → GPIO0, GPIO2, GPIO4, GPIO5, GPIO6."""
        assert expand_range("GPIO[0,2,4:6]") == ["GPIO0", "GPIO2", "GPIO4", "GPIO5", "GPIO6"]

    def test_expand_prefix_range_mixed(self):
        """GPIO[0:2,5,7:9] → GPIO0, GPIO1, GPIO2, GPIO5, GPIO7, GPIO8, GPIO9."""
        assert expand_range("GPIO[0:2,5,7:9]") == [
            "GPIO0",
            "GPIO1",
            "GPIO2",
            "GPIO5",
            "GPIO7",
            "GPIO8",
            "GPIO9",
        ]

    def test_expand_prefix_with_underscore(self):
        """ai_channel[0:3] → ai_channel0, ..."""
        assert expand_range("ai_channel[0:3]") == [
            "ai_channel0",
            "ai_channel1",
            "ai_channel2",
            "ai_channel3",
        ]

    def test_expand_prefix_lowercase(self):
        """ai[0:2] → ai0, ai1, ai2."""
        assert expand_range("ai[0:2]") == ["ai0", "ai1", "ai2"]

    # === Numeric ranges ===

    def test_expand_simple_numeric_range(self):
        """1:4 → 1, 2, 3, 4 (inclusive)."""
        assert expand_range("1:4") == ["1", "2", "3", "4"]

    def test_expand_numeric_range_zero_start(self):
        """0:3 → 0, 1, 2, 3."""
        assert expand_range("0:3") == ["0", "1", "2", "3"]

    def test_expand_numeric_comma_separated(self):
        """1,3,5 → 1, 3, 5."""
        assert expand_range("1,3,5") == ["1", "3", "5"]

    def test_expand_numeric_mixed(self):
        """1,3:5,8 → 1, 3, 4, 5, 8."""
        assert expand_range("1,3:5,8") == ["1", "3", "4", "5", "8"]

    # === Pass-through cases ===

    def test_expand_list_passthrough(self):
        """List input passes through with string conversion."""
        assert expand_range(["A", "B", "C"]) == ["A", "B", "C"]

    def test_expand_list_numeric_passthrough(self):
        """Numeric list passes through with string conversion."""
        assert expand_range([1, 2, 3]) == ["1", "2", "3"]

    def test_expand_single_string(self):
        """Single string without range pattern returns as-is."""
        assert expand_range("TP_VOUT") == ["TP_VOUT"]

    def test_expand_single_int(self):
        """Single int returns as string list."""
        assert expand_range(1) == ["1"]

    def test_expand_empty_list(self):
        """Empty list returns empty list."""
        assert expand_range([]) == []

    # === Edge cases ===

    def test_expand_single_item_brackets(self):
        """GPIO[5] → GPIO5."""
        assert expand_range("GPIO[5]") == ["GPIO5"]

    def test_expand_reverse_range(self):
        """7:4 → 7, 6, 5, 4 (reverse)."""
        # Note: this may or may not be supported depending on implementation
        result = expand_range("7:4")
        assert result == ["7", "6", "5", "4"]


class TestExpandNumericRange:
    """Tests for expand_numeric_range() function."""

    # === Simple ranges ===

    def test_simple_range(self):
        """1:4 → [1, 2, 3, 4] as floats."""
        result = expand_numeric_range("1:4")
        assert result == [1.0, 2.0, 3.0, 4.0]

    def test_range_with_step(self):
        """-40:125:55 → [-40, 15, 70, 125]."""
        result = expand_numeric_range("-40:125:55")
        assert result == [-40.0, 15.0, 70.0, 125.0]

    def test_float_range_with_step(self):
        """0.1:0.5:0.1 → [0.1, 0.2, 0.3, 0.4, 0.5]."""
        result = expand_numeric_range("0.1:0.5:0.1")
        assert len(result) == 5
        assert result[0] == 0.1
        assert result[4] == 0.5

    def test_comma_separated(self):
        """3.3,5.0,12.0 → [3.3, 5.0, 12.0]."""
        result = expand_numeric_range("3.3,5.0,12.0")
        assert result == [3.3, 5.0, 12.0]

    def test_mixed_comma_and_range(self):
        """0,0.5:2:0.5,5 → [0, 0.5, 1.0, 1.5, 2.0, 5]."""
        result = expand_numeric_range("0,0.5:2:0.5,5")
        expected = [
            0.0,
            0.5,
            1.0,
            1.5,
            2.0,
            5.0,
        ]
        assert result == expected

    # === List pass-through ===

    def test_list_passthrough(self):
        """List input passes through with float conversion."""
        result = expand_numeric_range([1, 2, 3])
        assert result == [1.0, 2.0, 3.0]

    def test_list_float_passthrough(self):
        """Float list passes through."""
        result = expand_numeric_range([0.1, 0.2, 0.3])
        assert result == [0.1, 0.2, 0.3]

    # === Single values ===

    def test_single_int(self):
        """Single int returns single-item list."""
        assert expand_numeric_range(5) == [5.0]

    def test_single_float(self):
        """Single float returns single-item list."""
        assert expand_numeric_range(3.14) == [3.14]

    # === Edge cases ===

    def test_negative_step(self):
        """125:-40:-55 → [125, 70, 15, -40] (descending)."""
        result = expand_numeric_range("125:-40:-55")
        assert result == [125.0, 70.0, 15.0, -40.0]

    def test_temperature_sweep(self):
        """Realistic temperature sweep: -40:85:25."""
        result = expand_numeric_range("-40:85:25")
        expected = [
            -40.0,
            -15.0,
            10.0,
            35.0,
            60.0,
            85.0,
        ]
        assert result == expected

    def test_voltage_sweep(self):
        """Realistic voltage sweep: 3.0:3.6:0.1."""
        import pytest

        result = expand_numeric_range("3.0:3.6:0.1")
        assert len(result) == 7
        assert result[0] == 3.0
        assert result[-1] == pytest.approx(3.6)


class TestIntegrationWithModels:
    """Integration tests with Pydantic models."""

    def test_characteristic_resolved_pins_with_range(self):
        """Characteristic.resolved_pins expands range syntax."""
        from litmus.models.config import Direction, MeasurementFunction
        from litmus.models.product import ProductCharacteristic

        char = ProductCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pins="GPIO[0:3]",
        )
        assert char.resolved_pins == ["GPIO0", "GPIO1", "GPIO2", "GPIO3"]

    def test_characteristic_resolved_pins_with_list(self):
        """Characteristic.resolved_pins works with explicit list."""
        from litmus.models.config import Direction, MeasurementFunction
        from litmus.models.product import ProductCharacteristic

        char = ProductCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pins=["TP1", "TP2", "TP3"],
        )
        assert char.resolved_pins == ["TP1", "TP2", "TP3"]

    def test_characteristic_resolved_pins_with_single(self):
        """Characteristic.resolved_pins works with single pin."""
        from litmus.models.config import Direction, MeasurementFunction
        from litmus.models.product import ProductCharacteristic

        char = ProductCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="TP_VOUT",
        )
        assert char.resolved_pins == ["TP_VOUT"]
