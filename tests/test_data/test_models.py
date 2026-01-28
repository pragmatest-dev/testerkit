"""Tests for data models."""

from decimal import Decimal
from uuid import UUID

from litmus.data.models import DUT, Measurement, Outcome, TestRun, TestStep


class TestOutcomeEnum:
    """Tests for Outcome enum."""

    def test_pass_value(self):
        assert Outcome.PASS.value == "pass"

    def test_fail_value(self):
        assert Outcome.FAIL.value == "fail"

    def test_error_value(self):
        assert Outcome.ERROR.value == "error"

    def test_skip_value(self):
        assert Outcome.SKIP.value == "skip"


class TestMeasurement:
    """Tests for Measurement model."""

    def test_basic_measurement(self):
        m = Measurement(name="voltage", value=Decimal("5.0"))
        assert m.name == "voltage"
        assert m.value == Decimal("5.0")
        assert m.units is None
        assert m.outcome is None

    def test_measurement_with_limits(self):
        m = Measurement(
            name="voltage",
            value=Decimal("5.0"),
            units="V",
            low_limit=Decimal("4.5"),
            high_limit=Decimal("5.5"),
        )
        assert m.low_limit == Decimal("4.5")
        assert m.high_limit == Decimal("5.5")

    def test_check_limit_pass(self):
        m = Measurement(
            name="voltage",
            value=Decimal("5.0"),
            low_limit=Decimal("4.5"),
            high_limit=Decimal("5.5"),
        )
        result = m.check_limit()
        assert result == Outcome.PASS
        assert m.outcome == Outcome.PASS

    def test_check_limit_fail_low(self):
        m = Measurement(
            name="voltage",
            value=Decimal("4.0"),
            low_limit=Decimal("4.5"),
            high_limit=Decimal("5.5"),
        )
        result = m.check_limit()
        assert result == Outcome.FAIL
        assert m.outcome == Outcome.FAIL

    def test_check_limit_fail_high(self):
        m = Measurement(
            name="voltage",
            value=Decimal("6.0"),
            low_limit=Decimal("4.5"),
            high_limit=Decimal("5.5"),
        )
        result = m.check_limit()
        assert result == Outcome.FAIL
        assert m.outcome == Outcome.FAIL

    def test_check_limit_error_none_value(self):
        m = Measurement(
            name="voltage",
            value=None,
            low_limit=Decimal("4.5"),
            high_limit=Decimal("5.5"),
        )
        result = m.check_limit()
        assert result == Outcome.ERROR
        assert m.outcome == Outcome.ERROR

    def test_check_limit_no_limits_passes(self):
        m = Measurement(name="voltage", value=Decimal("5.0"))
        result = m.check_limit()
        assert result == Outcome.PASS

    def test_check_limit_only_low(self):
        m = Measurement(name="voltage", value=Decimal("5.0"), low_limit=Decimal("4.5"))
        result = m.check_limit()
        assert result == Outcome.PASS

        m2 = Measurement(name="voltage", value=Decimal("4.0"), low_limit=Decimal("4.5"))
        result2 = m2.check_limit()
        assert result2 == Outcome.FAIL

    def test_check_limit_only_high(self):
        m = Measurement(name="voltage", value=Decimal("5.0"), high_limit=Decimal("5.5"))
        result = m.check_limit()
        assert result == Outcome.PASS

        m2 = Measurement(name="voltage", value=Decimal("6.0"), high_limit=Decimal("5.5"))
        result2 = m2.check_limit()
        assert result2 == Outcome.FAIL


class TestDUT:
    """Tests for DUT model."""

    def test_basic_dut(self):
        dut = DUT(serial="SN001")
        assert dut.serial == "SN001"
        assert dut.part_number is None

    def test_dut_with_all_fields(self):
        dut = DUT(
            serial="SN001",
            part_number="PN-123",
            revision="A",
            lot_number="LOT001",
        )
        assert dut.serial == "SN001"
        assert dut.part_number == "PN-123"
        assert dut.revision == "A"
        assert dut.lot_number == "LOT001"


class TestTestStep:
    """Tests for TestStep model."""

    def test_basic_step(self):
        step = TestStep(name="measure_voltage")
        assert step.name == "measure_voltage"
        assert isinstance(step.id, UUID)
        assert step.outcome == Outcome.PASS
        assert step.measurements == []

    def test_step_with_measurements(self):
        m = Measurement(name="voltage", value=Decimal("5.0"))
        step = TestStep(name="measure_voltage", measurements=[m])
        assert len(step.measurements) == 1
        assert step.measurements[0].name == "voltage"


class TestTestRun:
    """Tests for TestRun model."""

    def test_basic_test_run(self):
        run = TestRun(
            dut=DUT(serial="SN001"),
            station_id="station_001",
            test_sequence_id="test_suite",
        )
        assert run.dut.serial == "SN001"
        assert run.station_id == "station_001"
        assert run.test_sequence_id == "test_suite"
        assert run.outcome == Outcome.PASS
        assert run.steps == []

    def test_test_run_with_steps(self):
        step = TestStep(name="measure_voltage")
        run = TestRun(
            dut=DUT(serial="SN001"),
            station_id="station_001",
            test_sequence_id="test_suite",
            steps=[step],
        )
        assert len(run.steps) == 1
