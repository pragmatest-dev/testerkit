"""Tests for mock instrument factory."""

from litmus.instruments.mocks import Mock


class FakeDMM:
    """Fake DMM class for testing."""

    def __init__(self, resource: str = ""):
        self.resource = resource

    def measure_voltage(self):
        pass

    def measure_current(self):
        pass

    def measure_resistance(self):
        pass

    def configure_voltage_range(self, range_val):
        pass

    def configure_voltage_nplc(self, nplc):
        pass


class FakePSU:
    """Fake PSU class for testing."""

    def __init__(self, resource: str = ""):
        self.resource = resource

    def measure_voltage(self):
        pass

    def measure_current(self):
        pass

    def set_voltage(self, voltage):
        pass

    def set_current(self, current):
        pass

    def enable_output(self):
        pass

    def disable_output(self):
        pass


class FakeELoad:
    """Fake electronic load class for testing."""

    def __init__(self, resource: str = ""):
        self.resource = resource

    def measure_voltage(self):
        pass

    def measure_power(self):
        pass

    def set_load_current(self, current):
        pass

    def set_load_power(self, power):
        pass

    def enable_load(self):
        pass

    def disable_load(self):
        pass


class TestMockDMM:
    """Tests for Mock(FakeDMM)."""

    def test_unconfigured_returns_none(self):
        """Unconfigured methods return None."""
        dmm = Mock(FakeDMM)
        assert dmm.measure_voltage() is None
        assert dmm.measure_current() is None
        assert dmm.measure_resistance() is None

    def test_configured_returns_value(self):
        """Configured methods return configured values."""
        dmm = Mock(FakeDMM, measure_voltage=3.3, measure_current=0.5, measure_resistance=470)
        assert dmm.measure_voltage() == 3.3
        assert dmm.measure_current() == 0.5
        assert dmm.measure_resistance() == 470.0

    def test_set_mock_value(self):
        """set_mock_value updates return value."""
        dmm = Mock(FakeDMM, measure_voltage=3.3)
        assert dmm.measure_voltage() == 3.3

        dmm.set_mock_value("measure_voltage", 5.0)
        assert dmm.measure_voltage() == 5.0

    def test_context_manager(self):
        with Mock(FakeDMM, measure_voltage=5.0) as dmm:
            assert dmm._connected
            assert dmm.measure_voltage() == 5.0
        assert not dmm._connected

    def test_inherits_from_class(self):
        """Mock should inherit from the original class."""
        dmm = Mock(FakeDMM, measure_voltage=3.3)
        assert isinstance(dmm, FakeDMM)

    def test_unconfigured_methods_are_noop(self):
        """Methods not configured are no-ops (return None)."""
        dmm = Mock(FakeDMM, measure_voltage=3.3)
        # These should not crash - they're no-ops
        result = dmm.configure_voltage_range(10)
        assert result is None
        result = dmm.configure_voltage_nplc(1.0)
        assert result is None


class TestMockPSU:
    """Tests for Mock(FakePSU)."""

    def test_configured_values(self):
        psu = Mock(FakePSU, measure_voltage=5.0, measure_current=1.0)
        assert psu.measure_voltage() == 5.0
        assert psu.measure_current() == 1.0

    def test_unconfigured_methods_noop(self):
        """Unconfigured methods are no-ops."""
        psu = Mock(FakePSU, measure_voltage=5.0)
        # These should not crash
        psu.set_voltage(12.0)
        psu.set_current(2.0)
        psu.enable_output()
        psu.disable_output()
        # Configured value unchanged
        assert psu.measure_voltage() == 5.0

    def test_context_manager(self):
        with Mock(FakePSU, measure_voltage=12.0) as psu:
            assert psu.measure_voltage() == 12.0

    def test_set_mock_value(self):
        """set_mock_value updates return value."""
        psu = Mock(FakePSU, measure_voltage=5.0)
        assert psu.measure_voltage() == 5.0

        psu.set_mock_value("measure_voltage", 12.0)
        assert psu.measure_voltage() == 12.0

    def test_inherits_from_class(self):
        """Mock should inherit from the original class."""
        psu = Mock(FakePSU)
        assert isinstance(psu, FakePSU)


class TestMockELoad:
    """Tests for Mock(FakeELoad)."""

    def test_configured_values(self):
        eload = Mock(FakeELoad, measure_voltage=5.0, measure_power=10.0)
        assert eload.measure_voltage() == 5.0
        assert eload.measure_power() == 10.0

    def test_unconfigured_methods_noop(self):
        """Unconfigured methods are no-ops."""
        eload = Mock(FakeELoad, measure_voltage=5.0)
        # These should not crash
        eload.set_load_current(1.0)
        eload.set_load_power(10.0)
        eload.enable_load()
        eload.disable_load()
        # Configured value unchanged
        assert eload.measure_voltage() == 5.0

    def test_set_mock_value(self):
        """set_mock_value updates return value."""
        eload = Mock(FakeELoad, measure_voltage=5.0)
        assert eload.measure_voltage() == 5.0

        eload.set_mock_value("measure_voltage", 3.3)
        assert eload.measure_voltage() == 3.3

    def test_inherits_from_class(self):
        """Mock should inherit from the original class."""
        eload = Mock(FakeELoad)
        assert isinstance(eload, FakeELoad)


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


class TestMockDictValues:
    """Tests for dict-based argument matching (SCPI mocking)."""

    def test_dict_value_lookup(self):
        """Dict values use first argument as lookup key."""

        class FakeVisa:
            def query(self, cmd):
                pass

        inst = Mock(FakeVisa, query={
            "MEAS:VOLT:DC?": "3.3",
            "MEAS:CURR:DC?": "0.1",
            "*IDN?": "Keithley,2400,SN123,1.0",
        })

        assert inst.query("MEAS:VOLT:DC?") == "3.3"
        assert inst.query("MEAS:CURR:DC?") == "0.1"
        assert inst.query("*IDN?") == "Keithley,2400,SN123,1.0"
        assert inst.query("UNKNOWN?") is None

    def test_dict_value_with_set_mock_value(self):
        """Dict values can be updated via set_mock_value."""

        class FakeVisa:
            def query(self, cmd):
                pass

        inst = Mock(FakeVisa, query={"MEAS:VOLT?": "3.3"})
        assert inst.query("MEAS:VOLT?") == "3.3"

        inst.set_mock_value("query", {"MEAS:VOLT?": "5.0", "MEAS:CURR?": "0.5"})
        assert inst.query("MEAS:VOLT?") == "5.0"
        assert inst.query("MEAS:CURR?") == "0.5"


class TestMockCallableValues:
    """Tests for callable mock values."""

    def test_callable_value(self):
        """Callable values are called with method arguments."""

        class FakeVisa:
            def query(self, cmd):
                pass

        inst = Mock(FakeVisa, query=lambda cmd: "3.3" if "VOLT" in cmd else "0.1")

        assert inst.query("MEAS:VOLT:DC?") == "3.3"
        assert inst.query("MEAS:CURR:DC?") == "0.1"

    def test_callable_with_multiple_args(self):
        """Callable receives all arguments."""

        class FakeInstrument:
            def configure(self, param, value):
                pass

        calls = []
        def track_calls(param, value):
            calls.append((param, value))
            return "OK"

        inst = Mock(FakeInstrument, configure=track_calls)
        result = inst.configure("voltage", 5.0)

        assert result == "OK"
        assert calls == [("voltage", 5.0)]
