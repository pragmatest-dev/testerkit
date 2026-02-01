"""Integration tests for the instrument architecture.

These tests verify:
- VISA simulation works without hardware
- Capability interfaces enable protocol-based testing
- Fixture manager routes pins to instruments correctly
- SpecContext integrates with fixtures
"""

import pytest

from litmus.capabilities.interfaces import (
    ConstantCurrentLoad,
    CurrentInput,
    CurrentOutput,
    ResistanceInput,
    VoltageInput,
    VoltageOutput,
)
from litmus.config.models import FixtureConfig, FixturePoint
from litmus.fixtures.manager import FixtureManager, PinAccessor
from litmus.instruments import DMM, PSU, ELoad, Mock, Scope


class TestCapabilityProtocols:
    """Test that drivers implement capability protocols correctly."""

    def test_dmm_implements_voltage_input(self):
        """DMM should implement VoltageInput protocol."""
        dmm = Mock(DMM,measure_voltage=5.0)
        assert isinstance(dmm, VoltageInput)

    def test_dmm_implements_current_input(self):
        """DMM should implement CurrentInput protocol."""
        dmm = Mock(DMM,measure_current=0.1)
        assert isinstance(dmm, CurrentInput)

    def test_dmm_implements_resistance_input(self):
        """DMM should implement ResistanceInput protocol."""
        dmm = Mock(DMM,measure_resistance=1000)
        assert isinstance(dmm, ResistanceInput)

    def test_psu_implements_voltage_output(self):
        """PSU should implement VoltageOutput protocol."""
        psu = Mock(PSU)
        assert isinstance(psu, VoltageOutput)

    def test_psu_implements_current_output(self):
        """PSU should implement CurrentOutput protocol."""
        psu = Mock(PSU)
        assert isinstance(psu, CurrentOutput)

    def test_eload_implements_constant_current_load(self):
        """ELoad should implement ConstantCurrentLoad protocol."""
        eload = Mock(ELoad,)
        assert isinstance(eload, ConstantCurrentLoad)


class TestCapabilityBasedTesting:
    """Test that capability-based testing patterns work."""

    def measure_voltage_portable(self, meter: VoltageInput) -> float:
        """A portable function that works with any VoltageInput."""
        return meter.measure_voltage()

    def test_mock_dmm_as_voltage_input(self):
        """Mock(DMM) should work as VoltageInput."""
        dmm = Mock(DMM,measure_voltage=3.3)
        voltage = self.measure_voltage_portable(dmm)
        assert float(voltage) == 3.3

    def test_mock_eload_as_voltage_input(self):
        """Mock(ELoad) should work as VoltageInput (via measure_voltage)."""
        eload = Mock(ELoad,measure_voltage=5.0)
        # ELoad has measure_voltage method
        assert float(eload.measure_voltage()) == 5.0


class TestVISASimulation:
    """Test VISA drivers with pyvisa-sim backend."""

    def test_dmm_simulates_without_hardware(self):
        """DMM should work with simulate=True."""
        with DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 3.3},
        ) as dmm:
            voltage = dmm.measure_voltage()
            assert float(voltage) == pytest.approx(3.3, abs=0.001)

    def test_psu_simulates_without_hardware(self):
        """PSU should work with simulate=True."""
        with PSU(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 5.0, "current": 0.1},
        ) as psu:
            psu.set_voltage(5.0)
            psu.set_current(1.0)
            psu.enable_output()
            voltage = psu.measure_output_voltage()
            assert float(voltage) == pytest.approx(5.0, abs=0.01)

    def test_scope_simulates_without_hardware(self):
        """Scope should work with simulate=True."""
        with Scope(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"frequency": 1000.0, "vpp": 2.0},
        ) as scope:
            scope.configure_acquisition(1e9, 1000)
            scope.initiate_acquisition()
            data, x_inc = scope.fetch_waveform("CH1")
            assert len(data) > 0
            assert x_inc > 0

    def test_eload_simulates_without_hardware(self):
        """ELoad should work with simulate=True."""
        with ELoad(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 5.0, "current": 1.0},
        ) as eload:
            eload.set_load_current(1.0)
            eload.enable_load()
            voltage = eload.measure_voltage()
            assert float(voltage) == pytest.approx(5.0, abs=0.01)


class TestFixtureManager:
    """Test fixture manager pin routing."""

    @pytest.fixture
    def simple_fixture_config(self):
        """Create a simple fixture configuration."""
        return FixtureConfig(
            id="test_fixture",
            name="Test Fixture",
            product_family="test_product",
            points={
                "vin_source": FixturePoint(
                    name="vin_source",
                    instrument="psu_main",
                    instrument_channel="1",
                    dut_pin="VIN",
                    net="VIN_5V",
                ),
                "vout_measure": FixturePoint(
                    name="vout_measure",
                    instrument="dmm_main",
                    instrument_channel="1",
                    dut_pin="VOUT",
                    net="VOUT_3V3",
                ),
                "load_sink": FixturePoint(
                    name="load_sink",
                    instrument="eload_main",
                    dut_pin="LOAD",
                ),
            },
        )

    @pytest.fixture
    def mock_instruments(self):
        """Create mock instruments dictionary."""
        return {
            "psu_main": Mock(PSU),
            "dmm_main": Mock(DMM,measure_voltage=3.3),
            "eload_main": Mock(ELoad,measure_voltage=3.3),
        }

    def test_get_instrument_for_point(self, simple_fixture_config, mock_instruments):
        """Should resolve point name to instrument."""
        manager = FixtureManager(simple_fixture_config, mock_instruments)
        dmm = manager.get_instrument_for_point("vout_measure")
        assert dmm is mock_instruments["dmm_main"]

    def test_get_instrument_for_pin(self, simple_fixture_config, mock_instruments):
        """Should resolve DUT pin name to instrument."""
        manager = FixtureManager(simple_fixture_config, mock_instruments)
        psu = manager.get_instrument_for_pin("VIN")
        assert psu is mock_instruments["psu_main"]

    def test_get_channel_for_pin(self, simple_fixture_config, mock_instruments):
        """Should return instrument channel for a pin."""
        manager = FixtureManager(simple_fixture_config, mock_instruments)
        channel = manager.get_channel_for_pin("VOUT")
        assert channel == "1"

    def test_get_channel_returns_none_when_not_set(
        self, simple_fixture_config, mock_instruments
    ):
        """Should return None when channel not specified."""
        manager = FixtureManager(simple_fixture_config, mock_instruments)
        channel = manager.get_channel_for_pin("LOAD")
        assert channel is None

    def test_list_pins(self, simple_fixture_config, mock_instruments):
        """Should list all DUT pins with fixture connections."""
        manager = FixtureManager(simple_fixture_config, mock_instruments)
        pins = manager.list_pins()
        assert set(pins) == {"VIN", "VOUT", "LOAD"}

    def test_missing_point_raises_keyerror(self, simple_fixture_config, mock_instruments):
        """Should raise KeyError for unknown point."""
        manager = FixtureManager(simple_fixture_config, mock_instruments)
        with pytest.raises(KeyError, match="nonexistent"):
            manager.get_point("nonexistent")

    def test_missing_pin_raises_keyerror(self, simple_fixture_config, mock_instruments):
        """Should raise KeyError for unknown pin."""
        manager = FixtureManager(simple_fixture_config, mock_instruments)
        with pytest.raises(KeyError, match="GND"):
            manager.get_instrument_for_pin("GND")


class TestPinAccessor:
    """Test PinAccessor for UUT-centric tests."""

    @pytest.fixture
    def pin_accessor(self):
        """Create a pin accessor with mock instruments."""
        config = FixtureConfig(
            id="test_fixture",
            name="Test Fixture",
            product_family="test_product",
            points={
                "vin_source": FixturePoint(
                    name="vin_source",
                    instrument="psu_main",
                    dut_pin="VIN",
                ),
                "vout_measure": FixturePoint(
                    name="vout_measure",
                    instrument="dmm_main",
                    dut_pin="VOUT",
                ),
            },
        )
        instruments = {
            "psu_main": Mock(PSU),
            "dmm_main": Mock(DMM,measure_voltage=3.3),
        }
        manager = FixtureManager(config, instruments)
        return PinAccessor(manager)

    def test_getitem_returns_instrument(self, pin_accessor):
        """pins[name] should return the instrument."""
        dmm = pin_accessor["VOUT"]
        assert isinstance(dmm, DMM)

    def test_contains_check(self, pin_accessor):
        """'pin in pins' should work."""
        assert "VOUT" in pin_accessor
        assert "VIN" in pin_accessor
        assert "GND" not in pin_accessor

    def test_keys_returns_pin_names(self, pin_accessor):
        """pins.keys() should return all pin names."""
        assert set(pin_accessor.keys()) == {"VIN", "VOUT"}

    def test_get_with_default(self, pin_accessor):
        """pins.get(name, default) should return default for missing pins."""
        assert pin_accessor.get("GND") is None
        assert pin_accessor.get("GND", "missing") == "missing"

    def test_uut_centric_workflow(self, pin_accessor):
        """Demonstrate the UUT-centric test pattern."""
        # Apply input voltage
        psu = pin_accessor["VIN"]
        psu.set_voltage(5.0)
        psu.enable_output()

        # Measure output
        dmm = pin_accessor["VOUT"]
        voltage = dmm.measure_voltage()

        assert float(voltage) == 3.3


class TestEndToEndWorkflow:
    """Test complete hardware test workflows."""

    def test_power_supply_characterization(self):
        """Simulate a power supply characterization test."""
        # Setup instruments (all mocked)
        psu = Mock(PSU)
        dmm = Mock(DMM,measure_voltage=3.3)
        eload = Mock(ELoad,measure_voltage=3.3)

        # Connect instruments
        psu.connect()
        dmm.connect()
        eload.connect()

        try:
            # Apply input voltage
            psu.set_voltage(5.0)
            psu.set_current_limit(2.0)
            psu.enable_output()

            # Measure no-load output
            no_load_voltage = dmm.measure_voltage()
            assert float(no_load_voltage) == 3.3

            # Apply load
            eload.set_load_current(0.5)
            eload.enable_load()

            # Measure under load
            loaded_voltage = dmm.measure_voltage()
            assert float(loaded_voltage) == 3.3  # Mock doesn't change

            # Update mock to simulate load regulation
            dmm.set_mock_value("measure_voltage", 3.25)
            loaded_voltage = dmm.measure_voltage()
            assert float(loaded_voltage) == 3.25

        finally:
            # Cleanup
            eload.disable_load()
            psu.disable_output()
            eload.disconnect()
            dmm.disconnect()
            psu.disconnect()

    def test_multi_point_sweep(self):
        """Simulate a sweep across multiple test points."""
        psu = Mock(PSU)
        dmm = Mock(DMM)
        results = []

        psu.connect()
        dmm.connect()

        try:
            # Sweep input voltages
            for vin in [3.3, 5.0, 12.0]:
                # Simulate output voltage based on input (mock behavior)
                expected_vout = vin * 0.66  # ~66% of input
                dmm.set_mock_value("measure_voltage", expected_vout)

                # Set and measure
                psu.set_voltage(float(vin))
                psu.enable_output()
                vout = dmm.measure_voltage()

                results.append({"vin": vin, "vout": float(vout)})
                psu.disable_output()

            # Verify sweep collected data
            assert len(results) == 3
            assert results[0]["vin"] == 3.3
            assert results[1]["vin"] == 5.0
            assert results[2]["vin"] == 12.0

        finally:
            psu.disconnect()
            dmm.disconnect()
