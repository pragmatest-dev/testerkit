"""Tests for mock instrument factory."""

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

    def test_unconfigured_returns_none(self):
        """Unconfigured methods return None."""
        dmm = Mock(DMM)
        assert dmm.measure_voltage() is None
        assert dmm.measure_current() is None
        assert dmm.measure_resistance() is None

    def test_configured_returns_value(self):
        """Configured methods return configured values."""
        dmm = Mock(DMM, measure_voltage=3.3, measure_current=0.5, measure_resistance=470)
        assert dmm.measure_voltage() == 3.3
        assert dmm.measure_current() == 0.5
        assert dmm.measure_resistance() == 470.0

    def test_set_mock_value(self):
        """set_mock_value updates return value."""
        dmm = Mock(DMM, measure_voltage=3.3)
        assert dmm.measure_voltage() == 3.3

        dmm.set_mock_value("measure_voltage", 5.0)
        assert dmm.measure_voltage() == 5.0

    def test_context_manager(self):
        with Mock(DMM, measure_voltage=5.0) as dmm:
            assert dmm._connected
            assert dmm.measure_voltage() == 5.0
        assert not dmm._connected

    def test_inherits_from_dmm(self):
        """Mock should inherit from DMM and implement capability interfaces."""
        dmm = Mock(DMM, measure_voltage=3.3)
        assert isinstance(dmm, DMM)
        assert isinstance(dmm, VoltageInput)
        assert isinstance(dmm, CurrentInput)
        assert isinstance(dmm, ResistanceInput)

    def test_unconfigured_methods_are_noop(self):
        """Methods not configured are no-ops (return None)."""
        dmm = Mock(DMM, measure_voltage=3.3)
        # These should not crash - they're no-ops
        result = dmm.configure_voltage_range(10)
        assert result is None
        result = dmm.configure_voltage_nplc(1.0)
        assert result is None


class TestMockPSU:
    """Tests for Mock(PSU)."""

    def test_configured_values(self):
        psu = Mock(PSU, measure_voltage=5.0, measure_current=1.0)
        assert psu.measure_voltage() == 5.0
        assert psu.measure_current() == 1.0

    def test_unconfigured_methods_noop(self):
        """Unconfigured methods are no-ops."""
        psu = Mock(PSU, measure_voltage=5.0)
        # These should not crash
        psu.set_voltage(12.0)
        psu.set_current(2.0)
        psu.enable_output()
        psu.disable_output()
        # Configured value unchanged
        assert psu.measure_voltage() == 5.0

    def test_context_manager(self):
        with Mock(PSU, measure_voltage=12.0) as psu:
            assert psu.measure_voltage() == 12.0

    def test_set_mock_value(self):
        """set_mock_value updates return value."""
        psu = Mock(PSU, measure_voltage=5.0)
        assert psu.measure_voltage() == 5.0

        psu.set_mock_value("measure_voltage", 12.0)
        assert psu.measure_voltage() == 12.0

    def test_inherits_from_psu(self):
        """Mock should inherit from PSU and implement capability interfaces."""
        psu = Mock(PSU)
        assert isinstance(psu, PSU)
        assert isinstance(psu, VoltageOutput)
        assert isinstance(psu, CurrentOutput)


class TestMockELoad:
    """Tests for Mock(ELoad)."""

    def test_configured_values(self):
        eload = Mock(ELoad, measure_voltage=5.0, measure_power=10.0)
        assert eload.measure_voltage() == 5.0
        assert eload.measure_power() == 10.0

    def test_unconfigured_methods_noop(self):
        """Unconfigured methods are no-ops."""
        eload = Mock(ELoad, measure_voltage=5.0)
        # These should not crash
        eload.set_load_current(1.0)
        eload.set_load_power(10.0)
        eload.enable_load()
        eload.disable_load()
        # Configured value unchanged
        assert eload.measure_voltage() == 5.0

    def test_set_mock_value(self):
        """set_mock_value updates return value."""
        eload = Mock(ELoad, measure_voltage=5.0)
        assert eload.measure_voltage() == 5.0

        eload.set_mock_value("measure_voltage", 3.3)
        assert eload.measure_voltage() == 3.3

    def test_inherits_from_eload(self):
        """Mock should inherit from ELoad."""
        eload = Mock(ELoad)
        assert isinstance(eload, ELoad)


class TestMockPropertyBased:
    """Tests for property-based classes (PyMeasure style)."""

    def test_property_mock(self):
        """Mock works with property-based classes."""

        class FakeInstrument:
            def __init__(self, resource):
                pass

            @property
            def voltage(self):
                return self._voltage

            @voltage.setter
            def voltage(self, value):
                self._voltage = value

            @property
            def current(self):
                return self._current

        inst = Mock(FakeInstrument, voltage=5.0, current=0.1)
        assert inst.voltage == 5.0
        assert inst.current == 0.1
        assert isinstance(inst, FakeInstrument)

    def test_property_setter_stores_value(self):
        """Property setters store in mock_values."""

        class FakeInstrument:
            @property
            def voltage(self):
                return self._voltage

            @voltage.setter
            def voltage(self, value):
                self._voltage = value

        inst = Mock(FakeInstrument, voltage=5.0)
        assert inst.voltage == 5.0

        inst.voltage = 3.3
        assert inst.voltage == 3.3
        assert inst.mock_values["voltage"] == 3.3
