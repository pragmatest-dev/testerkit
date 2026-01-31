"""Tests for range expansion utilities."""

from decimal import Decimal

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
            "GPIO0", "GPIO1", "GPIO2", "GPIO5", "GPIO7", "GPIO8", "GPIO9"
        ]

    def test_expand_prefix_with_underscore(self):
        """ai_channel[0:3] → ai_channel0, ..."""
        assert expand_range("ai_channel[0:3]") == [
            "ai_channel0", "ai_channel1", "ai_channel2", "ai_channel3"
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
        """1:4 → [1, 2, 3, 4] as Decimals."""
        result = expand_numeric_range("1:4")
        assert result == [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]

    def test_range_with_step(self):
        """-40:125:55 → [-40, 15, 70, 125]."""
        result = expand_numeric_range("-40:125:55")
        assert result == [Decimal("-40"), Decimal("15"), Decimal("70"), Decimal("125")]

    def test_float_range_with_step(self):
        """0.1:0.5:0.1 → [0.1, 0.2, 0.3, 0.4, 0.5]."""
        result = expand_numeric_range("0.1:0.5:0.1")
        assert len(result) == 5
        assert result[0] == Decimal("0.1")
        assert result[4] == Decimal("0.5")

    def test_comma_separated(self):
        """3.3,5.0,12.0 → [3.3, 5.0, 12.0]."""
        result = expand_numeric_range("3.3,5.0,12.0")
        assert result == [Decimal("3.3"), Decimal("5.0"), Decimal("12.0")]

    def test_mixed_comma_and_range(self):
        """0,0.5:2:0.5,5 → [0, 0.5, 1.0, 1.5, 2.0, 5]."""
        result = expand_numeric_range("0,0.5:2:0.5,5")
        expected = [
            Decimal("0"), Decimal("0.5"), Decimal("1.0"),
            Decimal("1.5"), Decimal("2.0"), Decimal("5"),
        ]
        assert result == expected

    # === List pass-through ===

    def test_list_passthrough(self):
        """List input passes through with Decimal conversion."""
        result = expand_numeric_range([1, 2, 3])
        assert result == [Decimal("1"), Decimal("2"), Decimal("3")]

    def test_list_float_passthrough(self):
        """Float list passes through."""
        result = expand_numeric_range([0.1, 0.2, 0.3])
        assert result == [Decimal("0.1"), Decimal("0.2"), Decimal("0.3")]

    # === Single values ===

    def test_single_int(self):
        """Single int returns single-item list."""
        assert expand_numeric_range(5) == [Decimal("5")]

    def test_single_float(self):
        """Single float returns single-item list."""
        assert expand_numeric_range(3.14) == [Decimal("3.14")]

    # === Edge cases ===

    def test_negative_step(self):
        """125:-40:-55 → [125, 70, 15, -40] (descending)."""
        result = expand_numeric_range("125:-40:-55")
        assert result == [Decimal("125"), Decimal("70"), Decimal("15"), Decimal("-40")]

    def test_temperature_sweep(self):
        """Realistic temperature sweep: -40:85:25."""
        result = expand_numeric_range("-40:85:25")
        expected = [
            Decimal("-40"), Decimal("-15"), Decimal("10"),
            Decimal("35"), Decimal("60"), Decimal("85"),
        ]
        assert result == expected

    def test_voltage_sweep(self):
        """Realistic voltage sweep: 3.0:3.6:0.1."""
        result = expand_numeric_range("3.0:3.6:0.1")
        assert len(result) == 7
        assert result[0] == Decimal("3.0")
        assert result[-1] == Decimal("3.6")


class TestIntegrationWithModels:
    """Integration tests with Pydantic models."""

    def test_characteristic_resolved_pins_with_range(self):
        """Characteristic.resolved_pins expands range syntax."""
        from litmus.capabilities.models import Direction, Domain
        from litmus.products.models import Characteristic

        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pins="GPIO[0:3]",
        )
        assert char.resolved_pins == ["GPIO0", "GPIO1", "GPIO2", "GPIO3"]

    def test_characteristic_resolved_pins_with_list(self):
        """Characteristic.resolved_pins works with explicit list."""
        from litmus.capabilities.models import Direction, Domain
        from litmus.products.models import Characteristic

        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pins=["TP1", "TP2", "TP3"],
        )
        assert char.resolved_pins == ["TP1", "TP2", "TP3"]

    def test_characteristic_resolved_pins_with_single(self):
        """Characteristic.resolved_pins works with single pin."""
        from litmus.capabilities.models import Direction, Domain
        from litmus.products.models import Characteristic

        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            pin="TP_VOUT",
        )
        assert char.resolved_pins == ["TP_VOUT"]

    def test_characteristic_resolved_channels_with_range(self):
        """Characteristic.resolved_channels expands range syntax."""
        from litmus.capabilities.models import Direction, Domain
        from litmus.products.models import Characteristic

        char = Characteristic(
            direction=Direction.OUTPUT,
            domain=Domain.VOLTAGE,
            units="V",
            channels="CH[1:4]",
        )
        assert char.resolved_channels == ["CH1", "CH2", "CH3", "CH4"]

    def test_instrument_channel_spec_with_range(self):
        """InstrumentChannelSpec.channel_names() works with range."""
        from litmus.capabilities.models import InstrumentChannelSpec

        spec = InstrumentChannelSpec(range="ai[0:3]")
        assert spec.channel_names() == ["ai0", "ai1", "ai2", "ai3"]

    def test_instrument_channel_spec_with_labels(self):
        """InstrumentChannelSpec.channel_names() works with labels."""
        from litmus.capabilities.models import InstrumentChannelSpec

        # Note: count limits labels, so must specify count to match
        spec = InstrumentChannelSpec(count=3, labels=["A", "B", "C"])
        assert spec.channel_names() == ["A", "B", "C"]

    def test_instrument_channel_spec_with_count_naming(self):
        """InstrumentChannelSpec.channel_names() works with count+naming."""
        from litmus.capabilities.models import InstrumentChannelSpec

        spec = InstrumentChannelSpec(count=4, naming="CH{n}")
        assert spec.channel_names() == ["CH1", "CH2", "CH3", "CH4"]

    def test_loop_variable_config_with_range_string(self):
        """LoopVariableConfig.resolved_values expands range string."""
        from litmus.config.models import LoopVariableConfig

        config = LoopVariableConfig(name="temperature", values="-40:85:25")
        expected = [
            Decimal("-40"), Decimal("-15"), Decimal("10"),
            Decimal("35"), Decimal("60"), Decimal("85"),
        ]
        assert config.resolved_values == expected

    def test_loop_variable_config_with_list(self):
        """LoopVariableConfig.resolved_values works with list."""
        from litmus.config.models import LoopVariableConfig

        config = LoopVariableConfig(name="voltage", values=[3.3, 5.0, 12.0])
        assert config.resolved_values == [Decimal("3.3"), Decimal("5.0"), Decimal("12.0")]

    def test_loop_variable_config_with_range_object(self):
        """LoopVariableConfig.resolved_values works with RangeConfig."""
        from litmus.config.models import LoopVariableConfig, RangeConfig

        config = LoopVariableConfig(
            name="load",
            range=RangeConfig(start=Decimal("0"), stop=Decimal("1"), step=Decimal("0.5")),
        )
        assert config.resolved_values == [Decimal("0"), Decimal("0.5"), Decimal("1")]
