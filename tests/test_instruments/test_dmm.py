"""Tests for DMM driver."""

from decimal import Decimal

import pytest

from litmus.instruments.dmm import DMM


class TestDMM:
    """Tests for DMM driver using pyvisa-sim backend."""

    @pytest.fixture
    def dmm(self):
        """Fixture providing a connected simulated DMM."""
        dmm = DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 5.0012, "current": 0.1003, "resistance": 1000.5},
        )
        dmm.connect()
        yield dmm
        dmm.disconnect()

    def test_connect_and_idn(self, dmm):
        assert "Litmus,SimDMM" in dmm.idn

    def test_context_manager(self):
        with DMM("TCPIP::192.168.1.100::INSTR", simulate=True) as dmm:
            assert dmm.idn is not None

    def test_measure_dc_voltage(self, dmm):
        voltage = dmm.measure_voltage()
        assert float(voltage) == pytest.approx(5.0012, abs=0.001)

    def test_measure_dc_current(self, dmm):
        current = dmm.measure_current()
        assert float(current) == pytest.approx(0.1003, abs=0.001)

    def test_measure_resistance_2wire(self, dmm):
        resistance = dmm.measure_resistance(four_wire=False)
        assert float(resistance) == pytest.approx(1000.5, abs=0.1)

    def test_measure_resistance_4wire(self):
        # 4-wire needs separate sim_config since it uses MEAS:FRES?
        with DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"resistance": 999.8},
        ) as dmm:
            resistance = dmm.measure_resistance(four_wire=True)
            assert float(resistance) == pytest.approx(999.8, abs=0.1)

    def test_measure_not_connected_raises(self):
        dmm = DMM("TCPIP::192.168.1.100::INSTR", simulate=True)

        with pytest.raises(RuntimeError, match="Not connected"):
            dmm.measure_voltage()


class TestDMMInit:
    """Tests for DMM initialization."""

    def test_init_defaults(self):
        dmm = DMM("TCPIP::192.168.1.100::INSTR")
        assert dmm.resource == "TCPIP::192.168.1.100::INSTR"
        assert dmm.simulate is False
        assert dmm.idn is None

    def test_init_with_simulation(self):
        dmm = DMM("TCPIP::192.168.1.100::INSTR", simulate=True)
        assert dmm.resource == "TCPIP::192.168.1.100::INSTR"
        assert dmm.simulate is True
        assert dmm.sim_config == {}

    def test_init_simulated_with_config(self):
        dmm = DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 3.3},
        )
        assert dmm.simulate is True


class TestDMMSimulated:
    """Tests for DMM driver using built-in pyvisa-sim simulation."""

    def test_simulated_connect_and_idn(self):
        with DMM("TCPIP::192.168.1.100::INSTR", simulate=True) as dmm:
            assert "Litmus,SimDMM" in dmm.idn

    def test_simulated_measure_voltage_default(self):
        with DMM("TCPIP::192.168.1.100::INSTR", simulate=True) as dmm:
            voltage = dmm.measure_voltage()
            # Default is 0.0 from _sim_responses
            assert float(voltage) == pytest.approx(0.0, abs=0.001)

    def test_simulated_measure_voltage_custom(self):
        with DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 3.3},
        ) as dmm:
            voltage = dmm.measure_voltage()
            assert float(voltage) == pytest.approx(3.3, abs=0.001)

    def test_simulated_measure_current_default(self):
        with DMM("TCPIP::192.168.1.100::INSTR", simulate=True) as dmm:
            current = dmm.measure_current()
            assert float(current) == pytest.approx(0.0, abs=0.001)

    def test_simulated_measure_current_custom(self):
        with DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"current": 2.5},
        ) as dmm:
            current = dmm.measure_current()
            assert float(current) == pytest.approx(2.5, abs=0.001)

    def test_simulated_measure_resistance_default(self):
        with DMM("TCPIP::192.168.1.100::INSTR", simulate=True) as dmm:
            resistance = dmm.measure_resistance()
            # Default is 1000.0 from _sim_responses
            assert float(resistance) == pytest.approx(1000.0, abs=0.1)

    def test_simulated_measure_resistance_custom(self):
        with DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"resistance": 470},
        ) as dmm:
            resistance = dmm.measure_resistance()
            assert float(resistance) == pytest.approx(470.0, abs=0.1)

    def test_simulated_measure_resistance_4wire(self):
        with DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"resistance": 100},
        ) as dmm:
            resistance = dmm.measure_resistance(four_wire=True)
            assert float(resistance) == pytest.approx(100.0, abs=0.1)

    def test_simulated_multiple_values(self):
        with DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 12.0, "current": 0.5, "resistance": 24},
        ) as dmm:
            assert float(dmm.measure_voltage()) == pytest.approx(12.0, abs=0.001)
            assert float(dmm.measure_current()) == pytest.approx(0.5, abs=0.001)
            assert float(dmm.measure_resistance()) == pytest.approx(24.0, abs=0.1)

    def test_simulated_not_connected_raises(self):
        dmm = DMM("TCPIP::192.168.1.100::INSTR", simulate=True)
        with pytest.raises(RuntimeError, match="Not connected"):
            dmm.measure_voltage()

    def test_simulated_disconnect_reconnect(self):
        dmm = DMM(
            "TCPIP::192.168.1.100::INSTR",
            simulate=True,
            sim_config={"voltage": 9.0},
        )
        dmm.connect()
        assert float(dmm.measure_voltage()) == pytest.approx(9.0, abs=0.001)
        dmm.disconnect()
        with pytest.raises(RuntimeError, match="Not connected"):
            dmm.measure_voltage()
        dmm.connect()
        assert float(dmm.measure_voltage()) == pytest.approx(9.0, abs=0.001)
        dmm.disconnect()
