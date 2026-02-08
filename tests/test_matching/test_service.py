"""Tests for the capability matching service."""

from litmus.config.models import (
    AccuracySpec,
    Direction,
    MeasurementFunction,
    RangeSpec,
    SignalParameter,
)
from litmus.matching.service import (
    CapabilityRequirement,
    StationCapability,
    capability_satisfies,
    get_required_capabilities,
    get_station_capabilities,
    match_capabilities,
)
from litmus.config.models import SpecBand
from litmus.products.models import Characteristic, Product


class TestCapabilitySatisfies:
    """Tests for the capability_satisfies function."""

    def test_exact_function_and_direction_match(self):
        """Station capability matches on function and direction."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            name="dc_voltage_input",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is True

    def test_bidir_satisfies_input(self):
        """Station BIDIR satisfies required INPUT."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.BIDIR,
            name="voltage",
            instrument_type="smu",
            instrument_name="smu_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is True

    def test_bidir_satisfies_output(self):
        """Station BIDIR satisfies required OUTPUT."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.BIDIR,
            name="voltage",
            instrument_type="smu",
            instrument_name="smu_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            characteristic_name="input_voltage",
        )
        assert capability_satisfies(station, required) is True

    def test_direction_mismatch(self):
        """Station INPUT does not satisfy required OUTPUT."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            name="dc_voltage_input",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            characteristic_name="input_voltage",
        )
        assert capability_satisfies(station, required) is False

    def test_function_mismatch(self):
        """dc_voltage capability does not satisfy dc_current requirement."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            name="dc_voltage_input",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.DC_CURRENT,
            direction=Direction.INPUT,
            characteristic_name="output_current",
        )
        assert capability_satisfies(station, required) is False

    def test_dmm_does_not_match_waveform(self):
        """DMM dc_voltage does not satisfy oscilloscope waveform requirement."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            name="dc_voltage_input",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.WAVEFORM,
            direction=Direction.INPUT,
            characteristic_name="output_ripple",
        )
        assert capability_satisfies(station, required) is False

    def test_parameter_range_containment_value_within(self):
        """Required value within instrument range passes."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            parameters={
                "voltage": SignalParameter(
                    range=RangeSpec(min=0.0001, max=1000, units="V"),
                )
            },
            name="dc_voltage_input",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            parameters={
                "voltage": SignalParameter(value=3.3, units="V"),
            },
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is True

    def test_parameter_range_containment_value_outside(self):
        """Required value outside instrument range fails."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            parameters={
                "voltage": SignalParameter(
                    range=RangeSpec(min=0, max=10, units="V"),
                )
            },
            name="dc_voltage_input",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            parameters={
                "voltage": SignalParameter(value=48.0, units="V"),
            },
            characteristic_name="rail_48v",
        )
        assert capability_satisfies(station, required) is False

    def test_parameter_range_subset_containment(self):
        """Required range within instrument range passes."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            parameters={
                "voltage": SignalParameter(
                    range=RangeSpec(min=0, max=1000, units="V"),
                )
            },
            name="dc_voltage_input",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            parameters={
                "voltage": SignalParameter(
                    range=RangeSpec(min=0, max=50, units="V"),
                ),
            },
            characteristic_name="rail_48v",
        )
        assert capability_satisfies(station, required) is True

    def test_parameter_range_not_contained(self):
        """Required range exceeding instrument range fails."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            parameters={
                "voltage": SignalParameter(
                    range=RangeSpec(min=0, max=10, units="V"),
                )
            },
            name="dc_voltage_input",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            parameters={
                "voltage": SignalParameter(
                    range=RangeSpec(min=0, max=50, units="V"),
                ),
            },
            characteristic_name="rail_48v",
        )
        assert capability_satisfies(station, required) is False

    def test_missing_parameter_with_requirement_fails(self):
        """Instrument missing a required parameter with range fails."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            parameters={},  # No voltage parameter
            name="dc_voltage_input",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            parameters={
                "voltage": SignalParameter(value=3.3, units="V"),
            },
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is False

    def test_no_parameter_requirements_always_passes(self):
        """No parameter requirements always satisfied."""
        station = StationCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            name="dc_voltage_input",
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        required = CapabilityRequirement(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is True


class TestMatchCapabilities:
    """Tests for the match_capabilities function."""

    def test_all_requirements_satisfied(self):
        """All requirements satisfied returns compatible=True."""
        required = [
            CapabilityRequirement(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.INPUT,
                characteristic_name="rail_3v3",
            ),
            CapabilityRequirement(
                function=MeasurementFunction.DC_CURRENT,
                direction=Direction.INPUT,
                characteristic_name="output_current",
            ),
        ]
        available = [
            StationCapability(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.INPUT,
                name="dc_voltage_input",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
            StationCapability(
                function=MeasurementFunction.DC_CURRENT,
                direction=Direction.INPUT,
                name="dc_current_input",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
        ]

        result = match_capabilities(required, available)

        assert result.compatible is True
        assert len(result.missing) == 0
        assert len(result.matches) == 2
        assert all(m.satisfied for m in result.matches)

    def test_missing_requirement(self):
        """Missing requirement returns compatible=False."""
        required = [
            CapabilityRequirement(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.INPUT,
                characteristic_name="rail_3v3",
            ),
            CapabilityRequirement(
                function=MeasurementFunction.DC_CURRENT,
                direction=Direction.OUTPUT,
                characteristic_name="input_current",
            ),
        ]
        available = [
            StationCapability(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.INPUT,
                name="dc_voltage_input",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
            # No current OUTPUT capability
        ]

        result = match_capabilities(required, available)

        assert result.compatible is False
        assert len(result.missing) == 1
        assert result.missing[0].characteristic_name == "input_current"
        assert result.missing[0].direction == Direction.OUTPUT

    def test_unused_capabilities_tracked(self):
        """Unused station capabilities are tracked."""
        required = [
            CapabilityRequirement(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.INPUT,
                characteristic_name="rail_3v3",
            ),
        ]
        available = [
            StationCapability(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.INPUT,
                name="dc_voltage_input",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
            StationCapability(
                function=MeasurementFunction.DC_CURRENT,
                direction=Direction.INPUT,
                name="dc_current_input",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
            StationCapability(
                function=MeasurementFunction.RESISTANCE,
                direction=Direction.INPUT,
                name="resistance_input",
                instrument_type="dmm",
                instrument_name="dmm_main",
            ),
        ]

        result = match_capabilities(required, available)

        assert result.compatible is True
        assert len(result.unused) == 2
        unused_names = [u.name for u in result.unused]
        assert "dc_current_input" in unused_names
        assert "resistance_input" in unused_names


class TestGetRequiredCapabilities:
    """Tests for the get_required_capabilities function."""

    def test_direction_flipping_output_to_input(self):
        """DUT OUTPUT characteristic requires instrument INPUT."""
        product = Product(
            id="test_product",
            name="Test Product",
            characteristics={
                "rail_3v3": Characteristic(
                    function=MeasurementFunction.DC_VOLTAGE,
                    direction=Direction.OUTPUT,
                    units="V",
                    pin="VOUT",
                    specs=[
                        SpecBand(
                            value=3.3,
                            accuracy=AccuracySpec(pct_reading=3.0),
                        )
                    ],
                ),
            },
        )

        requirements = get_required_capabilities(product)

        assert len(requirements) == 1
        req = requirements[0]
        assert req.direction == Direction.INPUT  # Flipped to instrument INPUT
        assert req.function == MeasurementFunction.DC_VOLTAGE
        assert req.characteristic_name == "rail_3v3"

    def test_direction_flipping_input_to_output(self):
        """DUT INPUT characteristic requires instrument OUTPUT."""
        product = Product(
            id="test_product",
            name="Test Product",
            characteristics={
                "input_voltage": Characteristic(
                    function=MeasurementFunction.DC_VOLTAGE,
                    direction=Direction.INPUT,
                    units="V",
                    pin="VIN",
                    specs=[
                        SpecBand(
                            value=12.0,
                            accuracy=AccuracySpec(pct_reading=5.0),
                        )
                    ],
                ),
            },
        )

        requirements = get_required_capabilities(product)

        assert len(requirements) == 1
        req = requirements[0]
        assert req.direction == Direction.OUTPUT  # Flipped to instrument OUTPUT
        assert req.function == MeasurementFunction.DC_VOLTAGE

    def test_bidir_stays_bidir(self):
        """DUT BIDIR characteristic requires instrument BIDIR."""
        product = Product(
            id="test_product",
            name="Test Product",
            characteristics={
                "data_line": Characteristic(
                    function=MeasurementFunction.DC_VOLTAGE,
                    direction=Direction.BIDIR,
                    units="V",
                    pin="DATA",
                ),
            },
        )

        requirements = get_required_capabilities(product)

        assert len(requirements) == 1
        assert requirements[0].direction == Direction.BIDIR

    def test_multiple_characteristics(self):
        """Multiple characteristics generate multiple requirements."""
        product = Product(
            id="test_product",
            name="Test Product",
            characteristics={
                "rail_3v3": Characteristic(
                    function=MeasurementFunction.DC_VOLTAGE,
                    direction=Direction.OUTPUT,
                    units="V",
                    pin="VOUT_3V3",
                ),
                "rail_5v": Characteristic(
                    function=MeasurementFunction.DC_VOLTAGE,
                    direction=Direction.OUTPUT,
                    units="V",
                    pin="VOUT_5V",
                ),
                "input_current": Characteristic(
                    function=MeasurementFunction.DC_CURRENT,
                    direction=Direction.INPUT,
                    units="A",
                    pin="VIN",
                ),
            },
        )

        requirements = get_required_capabilities(product)

        assert len(requirements) == 3
        char_names = [r.characteristic_name for r in requirements]
        assert "rail_3v3" in char_names
        assert "rail_5v" in char_names
        assert "input_current" in char_names


class TestGetStationCapabilities:
    """Tests for the get_station_capabilities function."""

    def test_extracts_capabilities_from_instruments(self, monkeypatch):
        """Capabilities are extracted from station's instruments."""
        mock_library = {
            "instrument": {"type": "dmm", "name": "DMM"},
            "capabilities": [
                {
                    "function": "dc_voltage",
                    "direction": "input",
                    "parameters": {
                        "voltage": {"range": {"min": 0, "max": 1000, "units": "V"}},
                    },
                },
                {
                    "function": "dc_current",
                    "direction": "input",
                    "parameters": {
                        "current": {"range": {"min": 0, "max": 10, "units": "A"}},
                    },
                },
            ],
        }

        def mock_load_instrument_library(inst_type):
            if inst_type == "dmm":
                return mock_library
            return None

        import litmus.matching.service as service_module

        monkeypatch.setattr(
            service_module, "load_instrument_library", mock_load_instrument_library
        )

        station_config = {
            "station": {
                "id": "test_station",
            },
            "instruments": {
                "dmm_main": {"type": "dmm", "resource": "GPIB::1"},
            },
        }

        capabilities = get_station_capabilities(station_config)

        assert len(capabilities) == 2
        functions = [c.function for c in capabilities]
        assert MeasurementFunction.DC_VOLTAGE in functions
        assert MeasurementFunction.DC_CURRENT in functions
        assert all(c.instrument_name == "dmm_main" for c in capabilities)
        assert all(c.instrument_type == "dmm" for c in capabilities)
