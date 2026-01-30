"""Tests for base instrument classes."""

import pytest

from litmus.instruments.base import Instrument
from litmus.instruments.visa import VisaInstrument
from litmus.instruments.dmm import DMM


class TestInstrumentBase:
    """Tests for Instrument abstract base class."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            Instrument()  # type: ignore

    def test_subclass_implementation(self):
        class ConcreteInstrument(Instrument):
            def connect(self):
                self._connected = True

            def disconnect(self):
                self._connected = False

        inst = ConcreteInstrument()
        assert inst.simulate is False
        assert inst.sim_config == {}
        assert inst._connected is False

    def test_subclass_with_simulation(self):
        class ConcreteInstrument(Instrument):
            def connect(self):
                self._connected = True

            def disconnect(self):
                self._connected = False

        inst = ConcreteInstrument(simulate=True, sim_config={"voltage": 3.3})
        assert inst.simulate is True
        assert inst.sim_config == {"voltage": 3.3}

    def test_context_manager(self):
        class ConcreteInstrument(Instrument):
            def connect(self):
                self._connected = True

            def disconnect(self):
                self._connected = False

        with ConcreteInstrument() as inst:
            assert inst._connected is True
        assert inst._connected is False


class TestVisaInstrument:
    """Tests for VisaInstrument class."""

    def test_init(self):
        vi = VisaInstrument("TCPIP::192.168.1.100::INSTR")
        assert vi.resource == "TCPIP::192.168.1.100::INSTR"
        assert vi.simulate is False
        assert vi.timeout_ms == 5000

    def test_init_with_simulation(self):
        vi = VisaInstrument(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"responses": {"*IDN?": "Test,Sim,001,1.0"}},
            timeout_ms=10000,
        )
        assert vi.resource == "TCPIP::192.168.1.100::INSTR"
        assert vi.simulate is True
        assert vi.timeout_ms == 10000

    def test_query_not_connected_raises(self):
        vi = VisaInstrument("TCPIP::192.168.1.100::INSTR")
        with pytest.raises(RuntimeError, match="Not connected"):
            vi.query("*IDN?")

    def test_write_not_connected_raises(self):
        vi = VisaInstrument("TCPIP::192.168.1.100::INSTR")
        with pytest.raises(RuntimeError, match="Not connected"):
            vi.write("*RST")

    def test_simulated_connection(self):
        """Test that simulate=True creates a pyvisa-sim connection."""
        vi = VisaInstrument(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
        )
        vi.connect()
        try:
            # Should be able to query *IDN? on simulated instrument
            response = vi.query("*IDN?")
            assert response is not None
        finally:
            vi.disconnect()

    def test_simulated_context_manager(self):
        """Test context manager with simulation."""
        with VisaInstrument(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"idn": "Test,Simulated,001,1.0"},
        ) as vi:
            response = vi.query("*IDN?")
            assert response == "Test,Simulated,001,1.0"


class TestDMM:
    """Tests for DMM driver."""

    def test_init(self):
        dmm = DMM("TCPIP::192.168.1.100::INSTR")
        assert dmm.resource == "TCPIP::192.168.1.100::INSTR"
        assert dmm.simulate is False

    def test_simulated_measurement(self):
        """Test DMM simulation with pyvisa-sim."""
        dmm = DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 3.3},
        )
        with dmm:
            voltage = dmm.measure_voltage()
            # Should return configured voltage
            assert float(voltage) == pytest.approx(3.3, abs=0.001)

    def test_simulated_current_measurement(self):
        """Test DMM current simulation."""
        dmm = DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"current": 0.5},
        )
        with dmm:
            current = dmm.measure_current()
            assert float(current) == pytest.approx(0.5, abs=0.001)

    def test_simulated_resistance_measurement(self):
        """Test DMM resistance simulation."""
        dmm = DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"resistance": 1000.0},
        )
        with dmm:
            resistance = dmm.measure_resistance()
            assert float(resistance) == pytest.approx(1000.0, abs=0.1)

    def test_idn_after_connect(self):
        """Test that IDN is read on connect."""
        dmm = DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
        )
        dmm.connect()
        try:
            assert dmm.idn is not None
            assert "Litmus,SimDMM" in dmm.idn
        finally:
            dmm.disconnect()
