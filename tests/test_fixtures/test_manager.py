"""Tests for FixtureManager and PinAccessor."""

import pytest

from litmus.config.models import FixtureConfig, FixturePoint
from litmus.fixtures.manager import FixtureManager, PinAccessor
from litmus.instruments import DMM, PSU


class TestFixtureManager:
    """Tests for FixtureManager routing."""

    @pytest.fixture
    def fixture_config(self):
        """Fixture providing a test fixture configuration."""
        return FixtureConfig(
            id="test_fixture",
            product_family="test_product",
            points={
                "vout_measure": FixturePoint(
                    name="vout_measure",
                    instrument="dmm_main",
                    instrument_channel="1",
                    dut_pin="VOUT",
                    net="VOUT_3V3",
                ),
                "vin_supply": FixturePoint(
                    name="vin_supply",
                    instrument="psu_main",
                    instrument_channel="CH1",
                    dut_pin="VIN",
                    net="VIN_5V",
                ),
            },
        )

    @pytest.fixture
    def instruments(self):
        """Fixture providing simulated instruments."""
        dmm = DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 3.3},
        )
        psu = PSU(
            "TCPIP::192.168.1.101::INSTR",
            simulate=True,
            sim_config={"voltage": 5.0},
        )
        dmm.connect()
        psu.connect()
        yield {"dmm_main": dmm, "psu_main": psu}
        dmm.disconnect()
        psu.disconnect()

    def test_get_point(self, fixture_config, instruments):
        manager = FixtureManager(fixture_config, instruments)
        point = manager.get_point("vout_measure")
        assert point.name == "vout_measure"
        assert point.instrument == "dmm_main"

    def test_get_point_not_found(self, fixture_config, instruments):
        manager = FixtureManager(fixture_config, instruments)
        with pytest.raises(KeyError, match="nonexistent"):
            manager.get_point("nonexistent")

    def test_get_point_for_pin(self, fixture_config, instruments):
        manager = FixtureManager(fixture_config, instruments)
        point = manager.get_point_for_pin("VOUT")
        assert point.name == "vout_measure"
        assert point.dut_pin == "VOUT"

    def test_get_point_for_net(self, fixture_config, instruments):
        manager = FixtureManager(fixture_config, instruments)
        point = manager.get_point_for_net("VIN_5V")
        assert point.name == "vin_supply"
        assert point.net == "VIN_5V"

    def test_get_instrument_for_point(self, fixture_config, instruments):
        manager = FixtureManager(fixture_config, instruments)
        inst = manager.get_instrument_for_point("vout_measure")
        assert isinstance(inst, DMM)

    def test_get_instrument_for_pin(self, fixture_config, instruments):
        manager = FixtureManager(fixture_config, instruments)
        inst = manager.get_instrument_for_pin("VOUT")
        assert isinstance(inst, DMM)

    def test_get_channel_for_point(self, fixture_config, instruments):
        manager = FixtureManager(fixture_config, instruments)
        channel = manager.get_channel_for_point("vout_measure")
        assert channel == "1"

    def test_list_pins(self, fixture_config, instruments):
        manager = FixtureManager(fixture_config, instruments)
        pins = manager.list_pins()
        assert "VOUT" in pins
        assert "VIN" in pins
        assert len(pins) == 2

    def test_list_points(self, fixture_config, instruments):
        manager = FixtureManager(fixture_config, instruments)
        points = manager.list_points()
        assert "vout_measure" in points
        assert "vin_supply" in points


class TestPinAccessor:
    """Tests for PinAccessor dict-like interface."""

    @pytest.fixture
    def pin_accessor(self):
        """Fixture providing a PinAccessor with simulated instruments."""
        fixture_config = FixtureConfig(
            id="test_fixture",
            product_family="test_product",
            points={
                "vout_measure": FixturePoint(
                    name="vout_measure",
                    instrument="dmm_main",
                    dut_pin="VOUT",
                ),
                "vin_supply": FixturePoint(
                    name="vin_supply",
                    instrument="psu_main",
                    dut_pin="VIN",
                ),
            },
        )
        dmm = DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 3.3},
        )
        psu = PSU(
            "TCPIP::192.168.1.101::INSTR",
            simulate=True,
        )
        dmm.connect()
        psu.connect()
        instruments = {"dmm_main": dmm, "psu_main": psu}
        manager = FixtureManager(fixture_config, instruments)
        yield PinAccessor(manager)
        dmm.disconnect()
        psu.disconnect()

    def test_getitem(self, pin_accessor):
        dmm = pin_accessor["VOUT"]
        assert isinstance(dmm, DMM)

    def test_getitem_not_found(self, pin_accessor):
        with pytest.raises(KeyError):
            _ = pin_accessor["NONEXISTENT"]

    def test_contains(self, pin_accessor):
        assert "VOUT" in pin_accessor
        assert "VIN" in pin_accessor
        assert "NONEXISTENT" not in pin_accessor

    def test_keys(self, pin_accessor):
        keys = pin_accessor.keys()
        assert "VOUT" in keys
        assert "VIN" in keys

    def test_get_with_default(self, pin_accessor):
        dmm = pin_accessor.get("VOUT")
        assert isinstance(dmm, DMM)

        default = pin_accessor.get("NONEXISTENT", "default")
        assert default == "default"

    def test_measure_via_accessor(self, pin_accessor):
        """Test the full UUT-centric flow."""
        voltage = pin_accessor["VOUT"].measure_voltage()
        assert float(voltage) == pytest.approx(3.3, abs=0.001)

    def test_supply_via_accessor(self, pin_accessor):
        """Test setting voltage via accessor."""
        psu = pin_accessor["VIN"]
        psu.set_voltage(5.0)
        psu.enable_output()
        # PSU is simulated so just verify no exceptions
        psu.disable_output()
