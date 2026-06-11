"""Tests for the capability matching service."""

from litmus.matching.service import (
    CapabilityRequirement,
    StationCapability,
    _directions_compatible,
    capability_satisfies,
    get_required_capabilities,
    get_station_capabilities,
    match_capabilities,
)
from litmus.models.capability import AccuracySpec, InstrumentCapability, RangeSpec, Signal, SpecBand
from litmus.models.enums import Direction, MeasurementFunction
from litmus.models.part import Part, PartCharacteristic

# ---------------------------------------------------------------------------
# Helpers to build test objects with new wrapper API
# ---------------------------------------------------------------------------


def _make_station_cap(
    function=MeasurementFunction.DC_VOLTAGE,
    direction=Direction.INPUT,
    signals=None,
    instrument_type="dmm",
    instrument_name="dmm_main",
    channel=None,
    readback=False,
) -> StationCapability:
    return StationCapability(
        capability=InstrumentCapability(
            function=function,
            direction=direction,
            signals=signals or {},
            channels=[channel] if channel else [],
            readback=readback,
        ),
        instrument_type=instrument_type,
        instrument_name=instrument_name,
        channel=channel,
    )


def _make_req(
    function=MeasurementFunction.DC_VOLTAGE,
    direction=Direction.OUTPUT,
    signals=None,
    characteristic_name="test_char",
    pins=None,
    units="V",
) -> CapabilityRequirement:
    return CapabilityRequirement(
        capability=PartCharacteristic(
            function=function,
            direction=direction,
            signals=signals or {},
            units=units,
            net=characteristic_name,  # Use net as synthetic physical interface
        ),
        characteristic_name=characteristic_name,
        pins=pins or [],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDirectionsCompatible:
    """Tests for _directions_compatible()."""

    def test_output_matches_input(self):
        assert _directions_compatible(Direction.OUTPUT, Direction.INPUT) is True

    def test_input_matches_output(self):
        assert _directions_compatible(Direction.INPUT, Direction.OUTPUT) is True

    def test_bidir_instrument_matches_anything(self):
        assert _directions_compatible(Direction.OUTPUT, Direction.BIDIR) is True
        assert _directions_compatible(Direction.INPUT, Direction.BIDIR) is True
        assert _directions_compatible(Direction.BIDIR, Direction.BIDIR) is True

    def test_bidir_part_requires_bidir_instrument(self):
        assert _directions_compatible(Direction.BIDIR, Direction.INPUT) is False
        assert _directions_compatible(Direction.BIDIR, Direction.OUTPUT) is False

    def test_same_direction_does_not_match(self):
        assert _directions_compatible(Direction.OUTPUT, Direction.OUTPUT) is False
        assert _directions_compatible(Direction.INPUT, Direction.INPUT) is False


class TestCapabilitySatisfies:
    """Tests for the capability_satisfies function."""

    def test_exact_function_and_direction_match(self):
        """Station capability matches on function and direction."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
        )
        required = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,  # DUT output → needs instrument input
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is True

    def test_bidir_satisfies_input(self):
        """Station BIDIR satisfies required OUTPUT (needs instrument INPUT)."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.BIDIR,
            instrument_type="smu",
            instrument_name="smu_main",
        )
        required = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is True

    def test_bidir_satisfies_output(self):
        """Station BIDIR satisfies required INPUT (needs instrument OUTPUT)."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.BIDIR,
            instrument_type="smu",
            instrument_name="smu_main",
        )
        required = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            characteristic_name="input_voltage",
        )
        assert capability_satisfies(station, required) is True

    def test_direction_mismatch(self):
        """Station INPUT does not satisfy DUT INPUT (both same direction)."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
        )
        required = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,  # DUT input → needs instrument output, not input
            characteristic_name="input_voltage",
        )
        assert capability_satisfies(station, required) is False

    def test_function_mismatch(self):
        """dc_voltage capability does not satisfy dc_current requirement."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
        )
        required = _make_req(
            function=MeasurementFunction.DC_CURRENT,
            direction=Direction.OUTPUT,
            characteristic_name="output_current",
            units="A",
        )
        assert capability_satisfies(station, required) is False

    def test_dmm_does_not_match_waveform(self):
        """DMM dc_voltage does not satisfy oscilloscope waveform requirement."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
        )
        required = _make_req(
            function=MeasurementFunction.WAVEFORM,
            direction=Direction.OUTPUT,
            characteristic_name="output_ripple",
        )
        assert capability_satisfies(station, required) is False

    def test_parameter_range_containment_value_within(self):
        """Required value within instrument range passes."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0.0001, max=1000, units="V"),
                )
            },
        )
        required = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            signals={
                "voltage": Signal(value=3.3, units="V"),
            },
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is True

    def test_parameter_range_containment_value_outside(self):
        """Required value outside instrument range fails."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=10, units="V"),
                )
            },
        )
        required = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            signals={
                "voltage": Signal(value=48.0, units="V"),
            },
            characteristic_name="rail_48v",
        )
        assert capability_satisfies(station, required) is False

    def test_parameter_range_subset_containment(self):
        """Required range within instrument range passes."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=1000, units="V"),
                )
            },
        )
        required = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=50, units="V"),
                ),
            },
            characteristic_name="rail_48v",
        )
        assert capability_satisfies(station, required) is True

    def test_parameter_range_not_contained(self):
        """Required range exceeding instrument range fails."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=10, units="V"),
                )
            },
        )
        required = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=50, units="V"),
                ),
            },
            characteristic_name="rail_48v",
        )
        assert capability_satisfies(station, required) is False

    def test_missing_parameter_with_requirement_fails(self):
        """Instrument missing a required parameter with range fails."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={},
        )
        required = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            signals={
                "voltage": Signal(value=3.3, units="V"),
            },
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is False

    def test_no_parameter_requirements_always_passes(self):
        """No parameter requirements always satisfied."""
        station = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
        )
        required = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(station, required) is True


class TestMatchCapabilities:
    """Tests for the match_capabilities function."""

    def test_all_requirements_satisfied(self):
        """All requirements satisfied returns compatible=True."""
        required = [
            _make_req(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.OUTPUT,
                characteristic_name="rail_3v3",
            ),
            _make_req(
                function=MeasurementFunction.DC_CURRENT,
                direction=Direction.OUTPUT,
                characteristic_name="output_current",
                units="A",
            ),
        ]
        available = [
            _make_station_cap(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.INPUT,
            ),
            _make_station_cap(
                function=MeasurementFunction.DC_CURRENT,
                direction=Direction.INPUT,
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
            _make_req(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.OUTPUT,
                characteristic_name="rail_3v3",
            ),
            _make_req(
                function=MeasurementFunction.DC_CURRENT,
                direction=Direction.INPUT,
                characteristic_name="input_current",
                units="A",
            ),
        ]
        available = [
            _make_station_cap(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.INPUT,
            ),
            # No current OUTPUT capability
        ]

        result = match_capabilities(required, available)

        assert result.compatible is False
        assert len(result.missing) == 1
        assert result.missing[0].characteristic_name == "input_current"
        assert result.missing[0].direction == Direction.INPUT

    def test_unused_capabilities_tracked(self):
        """Unused station capabilities are tracked."""
        required = [
            _make_req(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.OUTPUT,
                characteristic_name="rail_3v3",
            ),
        ]
        available = [
            _make_station_cap(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.INPUT,
            ),
            _make_station_cap(
                function=MeasurementFunction.DC_CURRENT,
                direction=Direction.INPUT,
            ),
            _make_station_cap(
                function=MeasurementFunction.RESISTANCE,
                direction=Direction.INPUT,
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

    def test_preserves_direction(self):
        """DUT OUTPUT characteristic preserves direction in requirement."""
        part = Part(
            id="test_part",
            name="Test Part",
            characteristics={
                "rail_3v3": PartCharacteristic(
                    function=MeasurementFunction.DC_VOLTAGE,
                    direction=Direction.OUTPUT,
                    units="V",
                    pin="VOUT",
                    bands=[
                        SpecBand(
                            value=3.3,
                            accuracy=AccuracySpec(pct_reading=3.0),
                        )
                    ],
                ),
            },
        )

        requirements = get_required_capabilities(part)

        assert len(requirements) == 1
        req = requirements[0]
        # Direction is preserved (pairing happens in capability_satisfies)
        assert req.direction == Direction.OUTPUT
        assert req.function == MeasurementFunction.DC_VOLTAGE
        assert req.characteristic_name == "rail_3v3"

    def test_multiple_characteristics(self):
        """Multiple characteristics generate multiple requirements."""
        part = Part(
            id="test_part",
            name="Test Part",
            characteristics={
                "rail_3v3": PartCharacteristic(
                    function=MeasurementFunction.DC_VOLTAGE,
                    direction=Direction.OUTPUT,
                    units="V",
                    pin="VOUT_3V3",
                ),
                "rail_5v": PartCharacteristic(
                    function=MeasurementFunction.DC_VOLTAGE,
                    direction=Direction.OUTPUT,
                    units="V",
                    pin="VOUT_5V",
                ),
                "input_current": PartCharacteristic(
                    function=MeasurementFunction.DC_CURRENT,
                    direction=Direction.INPUT,
                    units="A",
                    pin="VIN",
                ),
            },
        )

        requirements = get_required_capabilities(part)

        assert len(requirements) == 3
        char_names = [r.characteristic_name for r in requirements]
        assert "rail_3v3" in char_names
        assert "rail_5v" in char_names
        assert "input_current" in char_names


class TestGetStationCapabilities:
    """Tests for the get_station_capabilities function."""

    def test_extracts_capabilities_from_catalog_ref(self, monkeypatch):
        """Capabilities are extracted from catalog_ref on station instruments."""
        from litmus.models.capability import InstrumentCapability
        from litmus.models.catalog import InstrumentCatalogEntry

        mock_entry = InstrumentCatalogEntry(
            id="test_dmm",
            manufacturer="Test",
            model="DMM-1000",
            type="dmm",
            capabilities=[
                InstrumentCapability.model_validate(
                    {
                        "function": "dc_voltage",
                        "direction": "input",
                        "signals": {
                            "voltage": {"range": {"min": 0, "max": 1000, "units": "V"}},
                        },
                    }
                ),
                InstrumentCapability.model_validate(
                    {
                        "function": "dc_current",
                        "direction": "input",
                        "signals": {
                            "current": {"range": {"min": 0, "max": 10, "units": "A"}},
                        },
                    }
                ),
            ],
        )

        import litmus.matching.service as matching_svc

        monkeypatch.setattr(
            matching_svc,
            "resolve_catalog_ref",
            lambda ref: mock_entry if ref == "test_dmm" else None,
        )

        from litmus.models.station import StationConfig, StationInstrumentConfig

        station_config = StationConfig(
            id="test_station",
            name="Test Station",
            instruments={
                "dmm_main": StationInstrumentConfig(
                    type="dmm",
                    driver="test.driver",
                    catalog_ref="test_dmm",
                    resource="GPIB::1",
                ),
            },
        )

        capabilities = get_station_capabilities(station_config)

        assert len(capabilities) == 2
        functions = [c.function for c in capabilities]
        assert MeasurementFunction.DC_VOLTAGE in functions
        assert MeasurementFunction.DC_CURRENT in functions
        assert all(c.instrument_name == "dmm_main" for c in capabilities)
        assert all(c.instrument_type == "dmm" for c in capabilities)
