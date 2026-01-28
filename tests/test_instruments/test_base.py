"""Tests for base instrument classes."""

import pytest

from litmus.instruments.base import Instrument, SimulatedBackend, VisaInstrument
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

    def test_subclass_with_simulated_flag(self):
        class ConcreteInstrument(Instrument):
            def connect(self):
                pass

            def disconnect(self):
                pass

        inst = ConcreteInstrument("SIM::TEST", simulated=True, sim_values={"voltage": 3.3})
        assert inst.resource == "SIM::TEST"
        assert inst.simulated is True
        assert inst.sim_values == {"voltage": 3.3}


class TestSimulatedBackend:
    """Tests for SimulatedBackend class."""

    def test_init_defaults(self):
        backend = SimulatedBackend("SIM::TEST")
        assert backend.resource == "SIM::TEST"
        assert backend._idn == "Litmus,Simulated,SN001,1.0"
        assert backend._responses == {}
        assert backend._connected is False

    def test_init_with_options(self):
        backend = SimulatedBackend(
            "SIM::TEST",
            idn="Test,Instrument,001,1.0",
            responses={"*IDN?": "Test"},
        )
        assert backend._idn == "Test,Instrument,001,1.0"
        assert backend._responses == {"*IDN?": "Test"}

    def test_connect_returns_idn(self):
        backend = SimulatedBackend("SIM::TEST", idn="Test,Instrument,001,1.0")
        idn = backend.connect()
        assert idn == "Test,Instrument,001,1.0"
        assert backend._connected is True

    def test_disconnect(self):
        backend = SimulatedBackend("SIM::TEST")
        backend.connect()
        assert backend._connected is True
        backend.disconnect()
        assert backend._connected is False

    def test_query_returns_configured_response(self):
        backend = SimulatedBackend(
            "SIM::TEST",
            responses={"MEAS:VOLT?": "5.0", "MEAS:CURR?": "0.1"},
        )
        backend.connect()
        assert backend.query("MEAS:VOLT?") == "5.0"
        assert backend.query("MEAS:CURR?") == "0.1"

    def test_query_returns_default_for_unknown(self):
        backend = SimulatedBackend("SIM::TEST", responses={})
        backend.connect()
        assert backend.query("UNKNOWN:CMD?") == "0"

    def test_query_not_connected_raises(self):
        backend = SimulatedBackend("SIM::TEST")
        with pytest.raises(RuntimeError, match="Not connected"):
            backend.query("*IDN?")

    def test_write_not_connected_raises(self):
        backend = SimulatedBackend("SIM::TEST")
        with pytest.raises(RuntimeError, match="Not connected"):
            backend.write("*RST")

    def test_write_connected_does_not_raise(self):
        backend = SimulatedBackend("SIM::TEST")
        backend.connect()
        # Write should succeed silently (simulation ignores writes)
        backend.write("CONF:VOLT:DC 10")

    def test_context_manager(self):
        with SimulatedBackend("SIM::TEST", idn="Test,IDN") as backend:
            assert backend._connected is True
            assert backend.query("*IDN?") == "0"  # No response configured
        assert backend._connected is False
