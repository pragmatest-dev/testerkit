"""Tests for FixtureManager and PinAccessor."""

import pytest

from litmus.fixtures.manager import FixtureManager, PinAccessor
from litmus.instruments import Mock
from litmus.models.config import FixtureConfig, FixturePoint


class FakeDMM:
    """Fake DMM class for testing."""

    def __init__(self, resource: str = ""):
        self.resource = resource
        self._connected = False

    def connect(self):
        self._connected = True

    def disconnect(self):
        self._connected = False

    def measure_voltage(self):
        pass


class FakePSU:
    """Fake PSU class for testing."""

    def __init__(self, resource: str = ""):
        self.resource = resource
        self._connected = False

    def connect(self):
        self._connected = True

    def disconnect(self):
        self._connected = False

    def set_voltage(self, voltage: float):
        pass

    def enable_output(self):
        pass

    def disable_output(self):
        pass


class TestFixtureManager:
    """Tests for FixtureManager routing."""

    @pytest.fixture
    def fx_config(self):
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
    def inst_map(self):
        """Fixture providing mock instruments."""
        dmm = Mock(FakeDMM, measure_voltage=3.3)
        psu = Mock(FakePSU)
        dmm.connect()
        psu.connect()
        yield {"dmm_main": dmm, "psu_main": psu}
        dmm.disconnect()
        psu.disconnect()

    def test_get_point(self, fx_config, inst_map):
        manager = FixtureManager(fx_config, inst_map)
        point = manager.get_point("vout_measure")
        assert point.name == "vout_measure"
        assert point.instrument == "dmm_main"

    def test_get_point_not_found(self, fx_config, inst_map):
        manager = FixtureManager(fx_config, inst_map)
        with pytest.raises(KeyError, match="nonexistent"):
            manager.get_point("nonexistent")

    def test_get_point_for_pin(self, fx_config, inst_map):
        manager = FixtureManager(fx_config, inst_map)
        point = manager.get_point_for_pin("VOUT")
        assert point.name == "vout_measure"
        assert point.dut_pin == "VOUT"

    def test_get_point_for_net(self, fx_config, inst_map):
        manager = FixtureManager(fx_config, inst_map)
        point = manager.get_point_for_net("VIN_5V")
        assert point.name == "vin_supply"
        assert point.net == "VIN_5V"

    def test_get_instrument_for_point(self, fx_config, inst_map):
        manager = FixtureManager(fx_config, inst_map)
        inst = manager.get_instrument_for_point("vout_measure")
        assert isinstance(inst, FakeDMM)

    def test_get_instrument_for_pin(self, fx_config, inst_map):
        manager = FixtureManager(fx_config, inst_map)
        inst = manager.get_instrument_for_pin("VOUT")
        assert isinstance(inst, FakeDMM)

    def test_get_channel_for_point(self, fx_config, inst_map):
        manager = FixtureManager(fx_config, inst_map)
        channel = manager.get_channel_for_point("vout_measure")
        assert channel == "1"

    def test_list_pins(self, fx_config, inst_map):
        manager = FixtureManager(fx_config, inst_map)
        pins = manager.list_pins()
        assert "VOUT" in pins
        assert "VIN" in pins
        assert len(pins) == 2

    def test_list_points(self, fx_config, inst_map):
        manager = FixtureManager(fx_config, inst_map)
        points = manager.list_points()
        assert "vout_measure" in points
        assert "vin_supply" in points


class TestPinAccessor:
    """Tests for PinAccessor dict-like interface."""

    @pytest.fixture
    def pin_accessor(self):
        """Fixture providing a PinAccessor with mock instruments."""
        fc = FixtureConfig(
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
        dmm = Mock(FakeDMM, measure_voltage=3.3)
        psu = Mock(FakePSU)
        dmm.connect()
        psu.connect()
        inst_map = {"dmm_main": dmm, "psu_main": psu}
        manager = FixtureManager(fc, inst_map)
        yield PinAccessor(manager)
        dmm.disconnect()
        psu.disconnect()

    def test_getitem(self, pin_accessor):
        dmm = pin_accessor["VOUT"]
        assert isinstance(dmm, FakeDMM)

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
        assert isinstance(dmm, FakeDMM)

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
        # PSU is mock so just verify no exceptions
        psu.disable_output()
