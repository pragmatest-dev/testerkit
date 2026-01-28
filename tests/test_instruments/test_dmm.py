"""Tests for DMM driver."""

from decimal import Decimal

import pytest

from litmus.instruments.dmm import DMM
from litmus.instruments.simulated import get_sim_resource_manager, get_simulated_resource


class TestDMM:
    """Tests for DMM driver using pyvisa-sim backend."""

    @pytest.fixture
    def dmm(self):
        """Fixture providing a connected simulated DMM."""
        visa_lib = get_sim_resource_manager()
        resource = get_simulated_resource()
        dmm = DMM(resource, visa_library=visa_lib)
        dmm.connect()
        yield dmm
        dmm.disconnect()

    def test_connect_and_idn(self, dmm):
        assert dmm.idn == "Litmus,SimDMM,SN001,1.0"

    def test_context_manager(self):
        visa_lib = get_sim_resource_manager()
        resource = get_simulated_resource()

        with DMM(resource, visa_library=visa_lib) as dmm:
            assert dmm.idn == "Litmus,SimDMM,SN001,1.0"

    def test_measure_dc_voltage(self, dmm):
        voltage = dmm.measure_dc_voltage()
        assert voltage == Decimal("5.0012")

    def test_measure_dc_current(self, dmm):
        current = dmm.measure_dc_current()
        assert current == Decimal("0.1003")

    def test_measure_resistance_2wire(self, dmm):
        resistance = dmm.measure_resistance(four_wire=False)
        assert resistance == Decimal("1000.5")

    def test_measure_resistance_4wire(self, dmm):
        resistance = dmm.measure_resistance(four_wire=True)
        assert resistance == Decimal("999.8")

    def test_measure_not_connected_raises(self):
        visa_lib = get_sim_resource_manager()
        resource = get_simulated_resource()
        dmm = DMM(resource, visa_library=visa_lib)

        with pytest.raises(RuntimeError, match="Not connected"):
            dmm.measure_dc_voltage()


class TestDMMInit:
    """Tests for DMM initialization."""

    def test_init_defaults(self):
        dmm = DMM("TCPIP::192.168.1.100::INSTR")
        assert dmm.resource == "TCPIP::192.168.1.100::INSTR"
        assert dmm.visa_library == ""
        assert dmm.idn is None

    def test_init_with_visa_library(self):
        dmm = DMM("GPIB::1::INSTR", visa_library="/path/to/visa.so")
        assert dmm.resource == "GPIB::1::INSTR"
        assert dmm.visa_library == "/path/to/visa.so"

    def test_init_simulated(self):
        dmm = DMM("SIM::DMM", simulated=True)
        assert dmm.resource == "SIM::DMM"
        assert dmm.simulated is True
        assert dmm.sim_values == {}

    def test_init_simulated_with_values(self):
        dmm = DMM("SIM::DMM", simulated=True, sim_values={"voltage": 3.3})
        assert dmm.simulated is True
        assert dmm.sim_values == {"voltage": 3.3}


class TestDMMSimulated:
    """Tests for DMM driver using built-in simulation (no pyvisa required)."""

    def test_simulated_connect_and_idn(self):
        with DMM("SIM::DMM", simulated=True) as dmm:
            assert dmm.idn == "Litmus,SimDMM,SN001,1.0"

    def test_simulated_measure_voltage_default(self):
        with DMM("SIM::DMM", simulated=True) as dmm:
            voltage = dmm.measure_dc_voltage()
            assert voltage == Decimal("5.0")

    def test_simulated_measure_voltage_custom(self):
        with DMM("SIM::DMM", simulated=True, sim_values={"voltage": 3.3}) as dmm:
            voltage = dmm.measure_dc_voltage()
            assert voltage == Decimal("3.3")

    def test_simulated_measure_current_default(self):
        with DMM("SIM::DMM", simulated=True) as dmm:
            current = dmm.measure_dc_current()
            assert current == Decimal("0.1")

    def test_simulated_measure_current_custom(self):
        with DMM("SIM::DMM", simulated=True, sim_values={"current": 2.5}) as dmm:
            current = dmm.measure_dc_current()
            assert current == Decimal("2.5")

    def test_simulated_measure_resistance_default(self):
        with DMM("SIM::DMM", simulated=True) as dmm:
            resistance = dmm.measure_resistance()
            assert resistance == Decimal("1000.0")

    def test_simulated_measure_resistance_custom(self):
        with DMM("SIM::DMM", simulated=True, sim_values={"resistance": 470}) as dmm:
            resistance = dmm.measure_resistance()
            assert resistance == Decimal("470")

    def test_simulated_measure_resistance_4wire(self):
        with DMM("SIM::DMM", simulated=True, sim_values={"resistance": 100}) as dmm:
            resistance = dmm.measure_resistance(four_wire=True)
            assert resistance == Decimal("100")

    def test_simulated_multiple_values(self):
        with DMM(
            "SIM::DMM",
            simulated=True,
            sim_values={"voltage": 12.0, "current": 0.5, "resistance": 24},
        ) as dmm:
            assert dmm.measure_dc_voltage() == Decimal("12.0")
            assert dmm.measure_dc_current() == Decimal("0.5")
            assert dmm.measure_resistance() == Decimal("24")

    def test_simulated_not_connected_raises(self):
        dmm = DMM("SIM::DMM", simulated=True)
        with pytest.raises(RuntimeError, match="Not connected"):
            dmm.measure_dc_voltage()

    def test_simulated_disconnect_reconnect(self):
        dmm = DMM("SIM::DMM", simulated=True, sim_values={"voltage": 9.0})
        dmm.connect()
        assert dmm.measure_dc_voltage() == Decimal("9.0")
        dmm.disconnect()
        with pytest.raises(RuntimeError, match="Not connected"):
            dmm.measure_dc_voltage()
        dmm.connect()
        assert dmm.measure_dc_voltage() == Decimal("9.0")
        dmm.disconnect()
