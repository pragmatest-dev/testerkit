"""Tests for mock instrument factory."""

from decimal import Decimal

from litmus.capabilities.interfaces import (
    CurrentInput,
    CurrentOutput,
    ResistanceInput,
    VoltageInput,
    VoltageOutput,
)
from litmus.instruments import DMM, ELoad, PSU
from litmus.instruments.mocks import Mock


class TestMockDMM:
    """Tests for Mock(DMM)."""

    def test_default_values(self):
        dmm = Mock(DMM)
        assert dmm.measure_voltage() == Decimal("0")
        assert dmm.measure_current() == Decimal("0")
        assert dmm.measure_resistance() == Decimal("0")
        assert dmm.measure_frequency() == Decimal("0")

    def test_custom_values(self):
        dmm = Mock(DMM, measure_voltage=3.3, measure_current=0.5, measure_resistance=470)
        assert dmm.measure_voltage() == Decimal("3.3")
        assert dmm.measure_current() == Decimal("0.5")
        assert dmm.measure_resistance() == Decimal("470")

    def test_set_mock_value(self):
        """Test set_mock_value method used by harness for per-vector config."""
        dmm = Mock(DMM, measure_voltage=3.3)
        assert dmm.measure_voltage() == Decimal("3.3")

        dmm.set_mock_value("measure_voltage", 5.0)
        assert dmm.measure_voltage() == Decimal("5.0")

        dmm.set_mock_value("measure_current", 0.5)
        assert dmm.measure_current() == Decimal("0.5")

    def test_period_from_frequency(self):
        # Note: With Mock, measure_period uses query() which returns the mocked value
        # We need to mock measure_period separately since it's a different SCPI command
        dmm = Mock(DMM, measure_frequency=1000, measure_period=0.001)
        assert dmm.measure_frequency() == Decimal("1000")
        assert dmm.measure_period() == Decimal("0.001")

    def test_context_manager(self):
        with Mock(DMM, measure_voltage=5.0) as dmm:
            assert dmm._connected
            assert dmm.measure_voltage() == Decimal("5.0")
        assert not dmm._connected

    def test_inherits_from_dmm(self):
        """Mock should inherit from DMM and implement capability interfaces."""
        dmm = Mock(DMM, measure_voltage=3.3)
        assert isinstance(dmm, DMM)
        assert isinstance(dmm, VoltageInput)
        assert isinstance(dmm, CurrentInput)
        assert isinstance(dmm, ResistanceInput)

    def test_raw_scpi_responses(self):
        """Can provide raw SCPI command responses."""
        dmm = Mock(DMM, responses={"MEAS:VOLT:DC?": "3.3", "MEAS:CURR:DC?": "0.1"})
        assert dmm.measure_voltage() == Decimal("3.3")
        assert dmm.measure_current() == Decimal("0.1")


class TestMockPSU:
    """Tests for Mock(PSU)."""

    def test_initial_state(self):
        psu = Mock(PSU)
        # Mock tracks state from write() calls
        assert psu.mock_state.get("output_enabled") is None

    def test_set_voltage_tracked(self):
        psu = Mock(PSU)
        psu.set_voltage(Decimal("5.0"))
        assert psu.mock_state["voltage_setpoint"] == "5.0"
        assert "VOLT 5.0" in psu.mock_write_log

    def test_enable_output(self):
        psu = Mock(PSU, measure_voltage=5.0, measure_current=1.0)
        psu.enable_output()

        assert psu.mock_state["output_enabled"] is True
        assert psu.measure_output_voltage() == Decimal("5.0")
        assert psu.measure_output_current() == Decimal("1.0")

    def test_disable_output(self):
        psu = Mock(PSU, measure_voltage=5.0)
        psu.enable_output()
        assert psu.measure_output_voltage() == Decimal("5.0")

        psu.disable_output()
        assert psu.mock_state["output_enabled"] is False
        # Values persist after disable (mock configured value)
        assert psu.measure_output_voltage() == Decimal("5.0")

    def test_context_manager(self):
        with Mock(PSU, measure_voltage=12.0) as psu:
            psu.enable_output()
            assert psu.measure_output_voltage() == Decimal("12.0")

    def test_set_mock_value(self):
        """Test set_mock_value method used by harness for per-vector config."""
        psu = Mock(PSU)
        psu.enable_output()

        psu.set_mock_value("measure_voltage", 5.0)
        assert psu.measure_output_voltage() == Decimal("5.0")

        psu.set_mock_value("measure_current", 0.25)
        assert psu.measure_output_current() == Decimal("0.25")

    def test_inherits_from_psu(self):
        """Mock should inherit from PSU and implement capability interfaces."""
        psu = Mock(PSU)
        assert isinstance(psu, PSU)
        assert isinstance(psu, VoltageOutput)
        assert isinstance(psu, CurrentOutput)

    def test_convenience_methods(self):
        """Convenience methods delegate to interface methods."""
        psu = Mock(PSU, measure_voltage=12.0, measure_current=0.5)
        assert psu.measure_voltage() == Decimal("12.0")
        assert psu.measure_current() == Decimal("0.5")


class TestMockELoad:
    """Tests for Mock(ELoad)."""

    def test_initial_state(self):
        eload = Mock(ELoad)
        assert eload.mock_state.get("load_enabled") is None

    def test_constant_current_mode(self):
        eload = Mock(ELoad, measure_voltage=5.0, measure_power=5.0)
        eload.set_load_current(Decimal("1.0"))
        eload.enable_load()

        assert eload.mock_state["mode"] == "CC"
        assert eload.mock_state["load_enabled"] is True
        assert eload.measure_voltage() == Decimal("5.0")
        assert eload.measure_power() == Decimal("5.0")

    def test_constant_power_mode(self):
        eload = Mock(ELoad, measure_voltage=5.0, measure_power=10.0)
        eload.set_load_power(Decimal("10.0"))
        eload.enable_load()

        assert eload.mock_state["mode"] == "CP"
        assert eload.measure_power() == Decimal("10.0")

    def test_constant_resistance_mode(self):
        eload = Mock(ELoad, measure_voltage=10.0, measure_power=1.0)
        eload.set_load_resistance(Decimal("100"))
        eload.enable_load()

        assert eload.mock_state["mode"] == "CR"
        assert eload.measure_power() == Decimal("1.0")

    def test_set_mock_value(self):
        """Test set_mock_value method used by harness for per-vector config."""
        eload = Mock(ELoad, measure_voltage=5.0)

        eload.set_mock_value("measure_voltage", 3.3)
        assert eload.measure_voltage() == Decimal("3.3")

        eload.set_mock_value("measure_power", 10.0)
        assert eload.measure_power() == Decimal("10.0")

    def test_inherits_from_eload(self):
        """Mock should inherit from ELoad and implement capability interfaces."""
        eload = Mock(ELoad)
        assert isinstance(eload, ELoad)

    def test_convenience_methods(self):
        """Convenience methods delegate to interface methods."""
        eload = Mock(ELoad, measure_voltage=5.0)
        eload.set_current(Decimal("1.0"))
        assert "CURR 1.0" in eload.mock_write_log
        eload.enable()
        assert eload.mock_state["load_enabled"] is True
        eload.disable()
        assert eload.mock_state["load_enabled"] is False


class TestMockWriteTracking:
    """Tests for SCPI write tracking."""

    def test_write_log_tracks_commands(self):
        psu = Mock(PSU)
        psu.set_voltage(Decimal("5.0"))
        psu.set_current(Decimal("1.0"))
        psu.enable_output()

        assert "VOLT 5.0" in psu.mock_write_log
        assert "CURR 1.0" in psu.mock_write_log
        assert "OUTP ON" in psu.mock_write_log

    def test_reset_clears_state(self):
        psu = Mock(PSU)
        psu.set_voltage(Decimal("5.0"))
        psu.enable_output()

        assert len(psu.mock_write_log) > 0
        assert len(psu.mock_state) > 0

        psu.reset_mock_state()

        assert len(psu.mock_write_log) == 0
        assert len(psu.mock_state) == 0


