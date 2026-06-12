"""Tests for part specification models."""

from litmus.models.capability import AccuracySpec, RangeSpec, SpecBand
from litmus.models.enums import Direction, MeasurementFunction
from litmus.models.part import (
    Part,
    PartCharacteristic,
)


class TestPartCharacteristic:
    """Tests for PartCharacteristic model."""

    def test_basic_characteristic(self):
        """Test creating a basic characteristic with function."""
        char = PartCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
        )
        assert char.direction == Direction.OUTPUT
        assert char.function == MeasurementFunction.DC_VOLTAGE
        assert char.units == "V"

    def test_characteristic_with_specs(self):
        """Test characteristic with SpecBand list."""
        char = PartCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            bands=[
                SpecBand(
                    when={"temperature": RangeSpec(min=25, max=25)},
                    value=3.3,
                    accuracy=AccuracySpec(pct_reading=3.0),
                ),
                SpecBand(
                    when={"temperature": RangeSpec(min=85, max=85)},
                    value=3.3,
                    accuracy=AccuracySpec(pct_reading=5.0),
                ),
            ],
        )
        assert len(char.bands) == 2

    def test_get_spec_at_match(self):
        """Test finding a spec band by parameters."""
        char = PartCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            bands=[
                SpecBand(
                    when={"temperature": RangeSpec(min=25, max=25)},
                    value=3.3,
                    accuracy=AccuracySpec(pct_reading=3.0),
                ),
                SpecBand(
                    when={"temperature": RangeSpec(min=85, max=85)},
                    value=3.35,
                    accuracy=AccuracySpec(pct_reading=5.0),
                ),
            ],
        )
        band = char.get_spec_at({"temperature": 85})
        assert band is not None
        assert band.value == 3.35

    def test_get_spec_at_no_match(self):
        """Test that no match returns None."""
        char = PartCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            bands=[
                SpecBand(
                    when={"temperature": RangeSpec(min=25, max=25)},
                    value=3.3,
                ),
            ],
        )
        band = char.get_spec_at({"temperature": -40})
        assert band is None

    def test_get_spec_at_unconditional(self):
        """Test that empty conditions matches anything."""
        char = PartCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            bands=[
                SpecBand(value=3.3),
            ],
        )
        band = char.get_spec_at({"temperature": 25})
        assert band is not None
        assert band.value == 3.3

    def test_physical_interface_required(self):
        """Test that characteristic requires physical interface."""
        import pytest

        with pytest.raises(ValueError, match="physical interface"):
            PartCharacteristic(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.OUTPUT,
                units="V",
            )

    def test_net_satisfies_physical_interface(self):
        """Test that net alone satisfies physical interface requirement."""
        char = PartCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            net="VOUT_3V3",
        )
        assert char.net == "VOUT_3V3"

    def test_signal_group_satisfies_physical_interface(self):
        """Test that signal_group alone satisfies physical interface requirement."""
        char = PartCharacteristic(
            function=MeasurementFunction.DIGITAL_IO,
            direction=Direction.BIDIR,
            units="V",
            signal_group="i2c_main",
        )
        assert char.signal_group == "i2c_main"


class TestPart:
    """Tests for Part model."""

    def test_basic_part(self):
        """Test creating a basic part."""
        part = Part(
            id="power_board_v1",
            name="DC-DC Power Board Rev A",
            description="5V to 3.3V buck converter",
            revision="A",
        )
        assert part.id == "power_board_v1"
        assert part.name == "DC-DC Power Board Rev A"

    def test_part_with_characteristics(self):
        """Test part with characteristics."""
        part = Part(
            id="power_board_v1",
            name="Power Board",
            characteristics={
                "rail_3v3_output": PartCharacteristic(
                    function=MeasurementFunction.DC_VOLTAGE,
                    direction=Direction.OUTPUT,
                    units="V",
                    pin="VOUT",
                    datasheet_ref="DS-001 Section 7.3",
                    bands=[
                        SpecBand(
                            when={
                                "temperature": RangeSpec(min=25, max=25),
                                "load": RangeSpec(min=0.1, max=0.1),
                            },
                            value=3.3,
                            accuracy=AccuracySpec(pct_reading=3.0),
                        ),
                    ],
                ),
            },
        )
        assert "rail_3v3_output" in part.characteristics
