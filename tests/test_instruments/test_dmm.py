"""Tests for DMM driver."""

from decimal import Decimal

import pytest

from litmus.instruments.dmm import DMM
from litmus.instruments.simulated import get_sim_resource_manager, get_simulated_resource


class TestDMM:
    """Tests for DMM driver using simulated backend."""

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
