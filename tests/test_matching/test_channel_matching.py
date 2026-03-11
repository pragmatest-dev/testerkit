"""Tests for channel-aware capability matching."""

from litmus.config.models import (
    Direction,
    InstrumentCapability,
    MeasurementFunction,
    RangeSpec,
    Signal,
)
from litmus.matching.service import (
    CapabilityRequirement,
    StationCapability,
    capability_satisfies,
    match_capabilities,
)
from litmus.products.models import ProductCharacteristic


def _make_station_cap(
    function=MeasurementFunction.DC_VOLTAGE,
    direction=Direction.INPUT,
    signals=None,
    instrument_type="dmm",
    instrument_name="dmm_main",
    channel=None,
    readback=False,
    modes=None,
) -> StationCapability:
    return StationCapability(
        capability=InstrumentCapability(
            function=function,
            direction=direction,
            signals=signals or {},
            channels=[channel] if channel else [],
            modes=modes or [],
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
        capability=ProductCharacteristic(
            function=function,
            direction=direction,
            signals=signals or {},
            units=units,
            net=characteristic_name,
        ),
        characteristic_name=characteristic_name,
        pins=pins or [],
    )


class TestInstrumentCapabilityChannels:
    """Tests for InstrumentCapability.channels field (list[str])."""

    def test_list_channels(self):
        """Explicit list of channels is returned as-is."""
        cap = InstrumentCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            channels=["1", "2", "3"],
        )
        assert cap.channels == ["1", "2", "3"]

    def test_empty_channels(self):
        """Empty channels returns empty list."""
        cap = InstrumentCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
        )
        assert cap.channels == []


class TestPerChannelExpansion:
    """Tests for per-channel expansion in station capabilities."""

    def _make_e36312a_caps(self) -> list[StationCapability]:
        """Create E36312A-like per-channel capabilities.

        CH1: 0-6.18V, CH2+CH3: 0-25.75V
        """
        caps = []
        # CH1: 6V
        caps.append(
            _make_station_cap(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.OUTPUT,
                signals={
                    "voltage": Signal(
                        range=RangeSpec(min=0, max=6.18, units="V"),
                    ),
                },
                instrument_type="power_supply",
                instrument_name="psu",
                channel="1",
            )
        )
        # CH2: 25V
        caps.append(
            _make_station_cap(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.OUTPUT,
                signals={
                    "voltage": Signal(
                        range=RangeSpec(min=0, max=25.75, units="V"),
                    ),
                },
                instrument_type="power_supply",
                instrument_name="psu",
                channel="2",
            )
        )
        # CH3: 25V
        caps.append(
            _make_station_cap(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.OUTPUT,
                signals={
                    "voltage": Signal(
                        range=RangeSpec(min=0, max=25.75, units="V"),
                    ),
                },
                instrument_type="power_supply",
                instrument_name="psu",
                channel="3",
            )
        )
        return caps

    def test_12v_requirement_rejects_ch1_accepts_ch2(self):
        """12V requirement matches CH2/CH3 (25V max) but NOT CH1 (6V max)."""
        caps = self._make_e36312a_caps()

        req_12v = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=12, units="V"),
                ),
            },
            characteristic_name="input_12v",
        )

        # CH1 (6V max) should NOT satisfy 12V requirement
        assert capability_satisfies(caps[0], req_12v) is False

        # CH2 (25V max) should satisfy 12V requirement
        assert capability_satisfies(caps[1], req_12v) is True

        # CH3 (25V max) should satisfy 12V requirement
        assert capability_satisfies(caps[2], req_12v) is True

    def test_5v_requirement_matches_all_channels(self):
        """5V requirement matches all channels (6V, 25V, 25V)."""
        caps = self._make_e36312a_caps()

        req_5v = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=5, units="V"),
                ),
            },
            characteristic_name="input_5v",
        )

        for cap in caps:
            assert capability_satisfies(cap, req_5v) is True

    def test_channel_allocation_two_12v_requirements(self):
        """Two 12V requirements allocate different channels (CH2 and CH3)."""
        caps = self._make_e36312a_caps()

        req_12v_a = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=12, units="V"),
                ),
            },
            characteristic_name="input_12v_a",
        )
        req_12v_b = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=12, units="V"),
                ),
            },
            characteristic_name="input_12v_b",
        )

        result = match_capabilities([req_12v_a, req_12v_b], caps)

        assert result.compatible is True
        assert len(result.matches) == 2
        assert all(m.satisfied for m in result.matches)

        # Should be allocated to different channels
        channels = [m.matched_by.channel for m in result.matches]
        assert len(set(channels)) == 2  # Two different channels
        assert "1" not in channels  # CH1 (6V) should not be used

    def test_channel_exhaustion_three_12v_requirements(self):
        """Three 12V requirements -> third unmatched (only CH2 and CH3 qualify)."""
        caps = self._make_e36312a_caps()

        reqs = [
            _make_req(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.INPUT,
                signals={
                    "voltage": Signal(
                        range=RangeSpec(min=0, max=12, units="V"),
                    ),
                },
                characteristic_name=f"input_12v_{i}",
            )
            for i in range(3)
        ]

        result = match_capabilities(reqs, caps)

        assert result.compatible is False
        satisfied_count = sum(1 for m in result.matches if m.satisfied)
        assert satisfied_count == 2
        assert len(result.missing) == 1

    def test_mixed_requirements_5v_and_12v(self):
        """5V + 12V requirements: 5V gets CH1, 12V gets CH2."""
        caps = self._make_e36312a_caps()

        req_5v = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=5, units="V"),
                ),
            },
            characteristic_name="input_5v",
        )
        req_12v = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=12, units="V"),
                ),
            },
            characteristic_name="input_12v",
        )

        result = match_capabilities([req_5v, req_12v], caps)

        assert result.compatible is True
        assert all(m.satisfied for m in result.matches)

        # Both allocated to different channels
        channels = [m.matched_by.channel for m in result.matches]
        assert len(set(channels)) == 2


class TestStationCapabilityChannel:
    """Tests for channel field on StationCapability."""

    def test_channel_is_preserved_in_match(self):
        """Channel info is preserved through matching."""
        available = [
            _make_station_cap(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.INPUT,
                instrument_type="dmm",
                instrument_name="dmm_main",
                channel="1",
            ),
        ]
        required = [
            _make_req(
                function=MeasurementFunction.DC_VOLTAGE,
                direction=Direction.OUTPUT,
                characteristic_name="rail_3v3",
            ),
        ]

        result = match_capabilities(required, available)

        assert result.compatible is True
        assert result.matches[0].matched_by.channel == "1"

    def test_none_channel_default(self):
        """Channel defaults to None when not specified."""
        cap = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            instrument_type="dmm",
            instrument_name="dmm_main",
        )
        assert cap.channel is None


class TestCapabilityRequirementPins:
    """Tests for pins field on CapabilityRequirement."""

    def test_pins_default_empty(self):
        """Pins defaults to empty list."""
        req = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            characteristic_name="rail_3v3",
        )
        assert req.pins == []

    def test_pins_populated(self):
        """Pins can be populated."""
        req = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            characteristic_name="rail_3v3",
            pins=["VOUT", "GND"],
        )
        assert req.pins == ["VOUT", "GND"]


class TestCatalogChannelParsing:
    """Tests for channel parsing from catalog entries."""

    def test_e36312a_catalog_channels(self):
        """E36312A catalog entry has correct per-channel capabilities."""
        from pathlib import Path

        from litmus.store import load_catalog_entry

        catalog_path = Path("catalog/keysight_e36312a.yaml")
        if not catalog_path.exists():
            return  # Skip if catalog not available

        entry = load_catalog_entry(catalog_path)

        assert entry.channel_names == ["1", "2"]

        # Find the CH1 dc_voltage output capability
        ch1_caps = [
            c for c in entry.capabilities
            if c.function == MeasurementFunction.DC_VOLTAGE
            and c.direction == Direction.OUTPUT
            and "1" in c.resolved_channels
            and "2" not in c.resolved_channels
        ]
        assert len(ch1_caps) == 1
        assert ch1_caps[0].signals["voltage"].range.max == 6

        # Find the CH2 dc_voltage output capability
        ch2_caps = [
            c for c in entry.capabilities
            if c.function == MeasurementFunction.DC_VOLTAGE
            and c.direction == Direction.OUTPUT
            and "2" in c.resolved_channels
        ]
        assert len(ch2_caps) == 1
        assert ch2_caps[0].signals["voltage"].range.max == 25


class TestReadbackFiltering:
    """Tests for readback flag on capabilities."""

    def test_readback_default_false(self):
        """Readback defaults to False."""
        cap = InstrumentCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            channels=["1", "2"],
        )
        assert cap.readback is False

    def test_readback_set_true(self):
        """Readback can be set to True."""
        cap = InstrumentCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            channels=["1"],
            readback=True,
        )
        assert cap.readback is True

    def test_readback_on_station_capability(self):
        """StationCapability has readback field."""
        cap = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            instrument_type="power_supply",
            instrument_name="psu",
            channel="1",
            readback=True,
        )
        assert cap.readback is True

    def test_readback_capability_not_used_in_match(self):
        """Readback capability should still match in capability_satisfies.

        Filtering is at auto-suggest level.
        """
        available = _make_station_cap(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            instrument_type="power_supply",
            instrument_name="psu",
            channel="1",
            readback=True,
        )
        required = _make_req(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            characteristic_name="rail_3v3",
        )
        assert capability_satisfies(available, required) is True


class TestPinRole:
    """Tests for PinRole enum on product Pin model."""

    def test_pin_role_default_signal(self):
        """Pin role defaults to signal."""
        from litmus.products.models import Pin
        pin = Pin(name="TP1")
        assert pin.role == "signal"

    def test_pin_role_ground(self):
        """Pin role can be set to ground."""
        from litmus.products.models import Pin, PinRole
        pin = Pin(name="J1.2", role=PinRole.GROUND)
        assert pin.role == "ground"

    def test_pin_role_power(self):
        """Pin role can be set to power."""
        from litmus.products.models import Pin, PinRole
        pin = Pin(name="J1.1", role=PinRole.POWER, net="VIN_5V")
        assert pin.role == "power"
        assert pin.net == "VIN_5V"


class TestChannelTopology:
    """Tests for structured channel topology."""

    def test_channel_topology_defaults(self):
        """ChannelTopology has sensible defaults."""
        from litmus.config.models import ChannelTopology, GroundTopology
        ct = ChannelTopology()
        assert ct.terminals == []
        assert ct.ground == GroundTopology.SHARED
        assert ct.connector is None
        assert ct.label is None

    def test_channel_topology_custom(self):
        """ChannelTopology with custom values."""
        from litmus.config.models import (
            ChannelTopology,
            ConnectorType,
            GroundTopology,
            TerminalRole,
        )
        ct = ChannelTopology(
            label="6V/5A Output",
            terminals=[
                TerminalRole.HI, TerminalRole.LO,
                TerminalRole.SENSE_HI, TerminalRole.SENSE_LO,
            ],
            connector=ConnectorType.BINDING_POST,
            ground=GroundTopology.FLOATING,
        )
        assert ct.label == "6V/5A Output"
        assert len(ct.terminals) == 4
        assert ct.connector == ConnectorType.BINDING_POST
        assert ct.ground == GroundTopology.FLOATING

    def test_catalog_entry_structured_channels(self):
        """InstrumentCatalogEntry with structured channel dict."""
        from litmus.catalog.models import InstrumentCatalogEntry
        from litmus.config.models import (
            ChannelTopology,
            ConnectorType,
            GroundTopology,
            TerminalRole,
        )
        entry = InstrumentCatalogEntry(
            id="test_psu",
            manufacturer="Test",
            model="PSU-1",
            name="Test PSU",
            type="psu",
            channels={
                "1": ChannelTopology(
                    terminals=[TerminalRole.HI, TerminalRole.LO],
                    connector=ConnectorType.BINDING_POST,
                    ground=GroundTopology.FLOATING,
                ),
                "2": ChannelTopology(
                    terminals=[TerminalRole.HI, TerminalRole.LO],
                    connector=ConnectorType.BINDING_POST,
                    ground=GroundTopology.FLOATING,
                ),
            },
        )
        assert entry.channel_names == ["1", "2"]

    def test_catalog_entry_empty_channels(self):
        """InstrumentCatalogEntry with no channels."""
        from litmus.catalog.models import InstrumentCatalogEntry
        entry = InstrumentCatalogEntry(
            id="test_dmm",
            manufacturer="Test",
            model="DMM-1",
            name="Test DMM",
            type="dmm",
        )
        assert entry.channel_names == []


class TestFixturePointTerminal:
    """Tests for instrument_terminal field on FixturePoint."""

    def test_fixture_point_no_terminal(self):
        """FixturePoint without terminal (backward compat)."""
        from litmus.config.models import FixturePoint
        fp = FixturePoint(name="vin_psu", instrument="psu")
        assert fp.instrument_terminal is None

    def test_fixture_point_with_terminal(self):
        """FixturePoint with instrument_terminal."""
        from litmus.config.models import FixturePoint
        fp = FixturePoint(
            name="gnd_psu_lo",
            dut_pin="J1_GND",
            instrument="psu",
            instrument_channel="1",
            instrument_terminal="lo",
        )
        assert fp.instrument_terminal == "lo"


class TestDesignerAutoSuggest:
    """Tests for 3-phase auto-suggest algorithm."""

    def _make_instruments(self):
        """Create test instrument set: PSU + DMM."""
        return {
            "psu": {
                "type": "power_supply",
                "driver": "drivers.PSU",
                "capabilities": [
                    {
                        "function": "dc_voltage",
                        "direction": "output",
                        "channels": ["1"],
                        "signals": {
                            "voltage": {"range": {"min": 0, "max": 30, "units": "V"}},
                        },
                    },
                    {
                        "function": "dc_voltage",
                        "direction": "input",
                        "readback": True,  # PSU readback
                        "channels": ["1"],
                        "signals": {
                            "voltage": {"range": {"min": 0, "max": 30, "units": "V"}},
                        },
                    },
                ],
                "channels": ["1"],
            },
            "dmm": {
                "type": "dmm",
                "driver": "drivers.DMM",
                "capabilities": [
                    {
                        "function": "dc_voltage",
                        "direction": "input",
                        "channels": ["1"],
                        "signals": {
                            "voltage": {"range": {"min": 0, "max": 1000, "units": "V"}},
                        },
                    },
                ],
                "channels": ["1"],
            },
        }

    def test_readback_excluded_from_matching_hints(self):
        """PSU readback channels should not appear as compatible for input measurements."""
        from litmus.ui.pages.designer.matching import get_compatible_channels_for_pin
        instruments = self._make_instruments()

        compatible = get_compatible_channels_for_pin(
            pin_key="TP_VOUT",
            char_by_pin={},
            product=None,
            instruments=instruments,
            dut_pins={"TP_VOUT": {"name": "TP2", "net": "VOUT", "role": "signal"}},
        )
        # Without characteristics, all channels are returned
        assert "psu:1" in compatible
        assert "dmm:1" in compatible

    def test_ground_pin_gets_lo_channels(self):
        """Ground pins should get all channels as compatible (for LO terminal wiring)."""
        from litmus.ui.pages.designer.matching import get_compatible_channels_for_pin
        instruments = self._make_instruments()

        compatible = get_compatible_channels_for_pin(
            pin_key="J1_GND",
            char_by_pin={},
            product=None,
            instruments=instruments,
            dut_pins={"J1_GND": {"name": "J1.2", "net": "GND", "role": "ground"}},
        )
        assert "psu:1" in compatible
        assert "dmm:1" in compatible
