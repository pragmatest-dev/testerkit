"""Tests for base instrument classes."""

import pytest

from litmus.instruments.base import Instrument, VisaInstrument
from litmus.instruments.simulated import get_sim_resource_manager, get_simulated_resource


class TestVisaInstrument:
    """Tests for VisaInstrument class."""

    def test_init(self):
        vi = VisaInstrument("TCPIP::192.168.1.100::INSTR")
        assert vi.resource == "TCPIP::192.168.1.100::INSTR"
        assert vi.visa_library == ""
        assert vi.timeout_ms == 5000

    def test_init_with_options(self):
        vi = VisaInstrument(
            "GPIB::1::INSTR",
            visa_library="/path/to/visa.so",
            timeout_ms=10000,
        )
        assert vi.resource == "GPIB::1::INSTR"
        assert vi.visa_library == "/path/to/visa.so"
        assert vi.timeout_ms == 10000

    def test_connect_simulated(self):
        visa_lib = get_sim_resource_manager()
        resource = get_simulated_resource()

        vi = VisaInstrument(resource, visa_library=visa_lib)
        idn = vi.connect()

        assert idn == "Litmus,SimDMM,SN001,1.0"
        vi.disconnect()

    def test_context_manager_simulated(self):
        visa_lib = get_sim_resource_manager()
        resource = get_simulated_resource()

        with VisaInstrument(resource, visa_library=visa_lib) as vi:
            response = vi.query("*IDN?")
            assert response == "Litmus,SimDMM,SN001,1.0"

    def test_query_simulated(self):
        visa_lib = get_sim_resource_manager()
        resource = get_simulated_resource()

        with VisaInstrument(resource, visa_library=visa_lib) as vi:
            voltage = vi.query("MEAS:VOLT:DC?")
            assert voltage == "5.0012"

    def test_query_not_connected_raises(self):
        vi = VisaInstrument("TCPIP::192.168.1.100::INSTR")
        with pytest.raises(RuntimeError, match="Not connected"):
            vi.query("*IDN?")

    def test_write_not_connected_raises(self):
        vi = VisaInstrument("TCPIP::192.168.1.100::INSTR")
        with pytest.raises(RuntimeError, match="Not connected"):
            vi.write("*RST")


class TestInstrumentBase:
    """Tests for Instrument abstract base class."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            Instrument("TCPIP::192.168.1.100::INSTR")  # type: ignore

    def test_subclass_implementation(self):
        class ConcreteInstrument(Instrument):
            def connect(self):
                pass

            def disconnect(self):
                pass

        inst = ConcreteInstrument("TCPIP::192.168.1.100::INSTR")
        assert inst.resource == "TCPIP::192.168.1.100::INSTR"
