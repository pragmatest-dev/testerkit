"""Tests for product specification models."""

from litmus.config.models import (
    AccuracySpec,
    Direction,
    FunctionCapability,
    MeasurementFunction,
    RangeSpec,
    SpecBand,
)
from litmus.products.models import (
    Characteristic,
    Product,
)


class TestCharacteristic:
    """Tests for Characteristic model."""

    def test_basic_characteristic(self):
        """Test creating a basic characteristic with function."""
        char = Characteristic(
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
        char = Characteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            specs=[
                SpecBand(
                    conditions={"temperature": RangeSpec(min=25, max=25)},
                    value=3.3,
                    accuracy=AccuracySpec(pct_reading=3.0),
                ),
                SpecBand(
                    conditions={"temperature": RangeSpec(min=85, max=85)},
                    value=3.3,
                    accuracy=AccuracySpec(pct_reading=5.0),
                ),
            ],
        )
        assert len(char.specs) == 2

    def test_get_spec_at_match(self):
        """Test finding a spec band by parameters."""
        char = Characteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            specs=[
                SpecBand(
                    conditions={"temperature": RangeSpec(min=25, max=25)},
                    value=3.3,
                    accuracy=AccuracySpec(pct_reading=3.0),
                ),
                SpecBand(
                    conditions={"temperature": RangeSpec(min=85, max=85)},
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
        char = Characteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            specs=[
                SpecBand(
                    conditions={"temperature": RangeSpec(min=25, max=25)},
                    value=3.3,
                ),
            ],
        )
        band = char.get_spec_at({"temperature": -40})
        assert band is None

    def test_get_spec_at_unconditional(self):
        """Test that empty conditions matches anything."""
        char = Characteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            specs=[
                SpecBand(value=3.3),
            ],
        )
        band = char.get_spec_at({"temperature": 25})
        assert band is not None
        assert band.value == 3.3

    def test_to_capability_requirement_output(self):
        """Test that DUT OUTPUT maps to instrument INPUT."""
        char = Characteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            specs=[
                SpecBand(value=3.3),
            ],
        )
        cap = char.to_capability_requirement()
        assert isinstance(cap, FunctionCapability)
        assert cap.direction == Direction.INPUT
        assert cap.function == MeasurementFunction.DC_VOLTAGE

    def test_to_capability_requirement_input(self):
        """Test that DUT INPUT maps to instrument OUTPUT."""
        char = Characteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            units="V",
            pin="VIN",
            specs=[
                SpecBand(value=5.0),
            ],
        )
        cap = char.to_capability_requirement()
        assert cap.direction == Direction.OUTPUT

    def test_to_capability_requirement_bidir(self):
        """Test that DUT BIDIR maps to instrument BIDIR."""
        char = Characteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.BIDIR,
            units="V",
            pin="DATA",
        )
        cap = char.to_capability_requirement()
        assert cap.direction == Direction.BIDIR

    def test_to_capability_requirement_derives_parameters(self):
        """Test that capability derives parameter range from specs."""
        import pytest
        char = Characteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            units="V",
            pin="VOUT",
            specs=[
                SpecBand(value=3.3),
                SpecBand(value=5.0),
                SpecBand(value=12.0),
            ],
        )
        cap = char.to_capability_requirement()
        assert "voltage" in cap.parameters
        voltage_param = cap.parameters["voltage"]
        assert voltage_param.range is not None
        assert float(voltage_param.range.max) == pytest.approx(14.4)
        assert voltage_param.units == "V"

    def test_to_capability_requirement_function_preserved(self):
        """Test that the MeasurementFunction is preserved in capability."""
        char = Characteristic(
            function=MeasurementFunction.DC_CURRENT,
            direction=Direction.INPUT,
            units="A",
            pin="IIN",
            specs=[
                SpecBand(value=0.015),
            ],
        )
        cap = char.to_capability_requirement()
        assert cap.function == MeasurementFunction.DC_CURRENT
        assert "current" in cap.parameters


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
        """Test product with characteristics."""
        product = Product(
            id="power_board_v1",
            name="Power Board",
            characteristics={
                "rail_3v3_output": Characteristic(
                    function=MeasurementFunction.DC_VOLTAGE,
                    direction=Direction.OUTPUT,
                    units="V",
                    pin="VOUT",
                    datasheet_ref="DS-001 Section 7.3",
                    specs=[
                        SpecBand(
                            conditions={
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
        assert "rail_3v3_output" in product.characteristics
