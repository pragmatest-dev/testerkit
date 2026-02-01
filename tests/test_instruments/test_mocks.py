"""Tests for mock instrument implementations."""

from decimal import Decimal

from litmus.instruments.mocks import MockDMM, MockELoad, MockPSU


class TestMockDMM:
    """Tests for MockDMM."""

    def test_default_values(self):
        dmm = MockDMM()
        assert dmm.measure_voltage() == Decimal("0")
        assert dmm.measure_current() == Decimal("0")
        assert dmm.measure_resistance() == Decimal("1000")
        assert dmm.measure_frequency() == Decimal("1000")

    def test_custom_values(self):
        dmm = MockDMM(voltage=3.3, current=0.5, resistance=470)
        assert dmm.measure_voltage() == Decimal("3.3")
        assert dmm.measure_current() == Decimal("0.5")
        assert dmm.measure_resistance() == Decimal("470")

    def test_dynamic_update(self):
        dmm = MockDMM(voltage=5.0)
        assert dmm.measure_voltage() == Decimal("5.0")

        dmm.set_value("voltage", 3.3)
        assert dmm.measure_voltage() == Decimal("3.3")

    def test_period_from_frequency(self):
        dmm = MockDMM(frequency=1000)
        assert dmm.measure_period() == Decimal("0.001")

    def test_context_manager(self):
        with MockDMM(voltage=5.0) as dmm:
            assert dmm._connected
            assert dmm.measure_voltage() == Decimal("5.0")
        assert not dmm._connected

    def test_extra_kwargs(self):
        dmm = MockDMM(voltage=5.0, temperature=25.0)
        assert dmm.get_value("temperature") == Decimal("25.0")


class TestMockPSU:
    """Tests for MockPSU."""

    def test_initial_state(self):
        psu = MockPSU()
        assert psu.voltage_setpoint == Decimal("0")
        assert psu.current_setpoint == Decimal("0")
        assert not psu.output_enabled

    def test_set_voltage(self):
        psu = MockPSU()
        psu.set_voltage(5.0)
        assert psu.voltage_setpoint == Decimal("5.0")
        # Readback still 0 until output enabled
        assert psu.measure_output_voltage() == Decimal("0")

    def test_enable_output(self):
        psu = MockPSU()
        psu.set_voltage(5.0)
        psu.set_current(1.0)
        psu.enable_output()

        assert psu.output_enabled
        assert psu.measure_output_voltage() == Decimal("5.0")
        assert psu.measure_output_current() == Decimal("1.0")

    def test_disable_output(self):
        psu = MockPSU()
        psu.set_voltage(5.0)
        psu.enable_output()
        assert psu.measure_output_voltage() == Decimal("5.0")

        psu.disable_output()
        assert not psu.output_enabled
        assert psu.measure_output_voltage() == Decimal("0")

    def test_context_manager(self):
        with MockPSU() as psu:
            psu.set_voltage(12.0)
            psu.enable_output()
            assert psu.measure_output_voltage() == Decimal("12.0")


class TestMockELoad:
    """Tests for MockELoad."""

    def test_initial_state(self):
        eload = MockELoad()
        assert not eload.enabled
        assert eload.mode == "CC"

    def test_constant_current_mode(self):
        eload = MockELoad(voltage=5.0)
        eload.set_load_current(Decimal("1.0"))
        eload.enable_load()

        assert eload.mode == "CC"
        assert eload.measure_voltage() == Decimal("5.0")
        assert eload.measure_power() == Decimal("5.0")  # 5V * 1A

    def test_constant_power_mode(self):
        eload = MockELoad(voltage=5.0)
        eload.set_load_power(Decimal("10.0"))
        eload.enable_load()

        assert eload.mode == "CP"
        assert eload.measure_power() == Decimal("10.0")

    def test_constant_resistance_mode(self):
        eload = MockELoad(voltage=10.0)
        eload.set_load_resistance(Decimal("100"))
        eload.enable_load()

        assert eload.mode == "CR"
        # P = V^2 / R = 100 / 100 = 1W
        assert eload.measure_power() == Decimal("1")

    def test_disabled_power_zero(self):
        eload = MockELoad(voltage=5.0)
        eload.set_load_current(Decimal("1.0"))
        # Don't enable
        assert eload.measure_power() == Decimal("0")

    def test_dynamic_voltage(self):
        eload = MockELoad(voltage=5.0)
        eload.set_load_current(Decimal("1.0"))
        eload.enable_load()
        assert eload.measure_power() == Decimal("5.0")

        eload.set_input_voltage(12.0)
        assert eload.measure_power() == Decimal("12.0")
