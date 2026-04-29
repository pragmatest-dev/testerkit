"""Tests for data models."""

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
        m = Measurement(name="voltage", value=5.0)
        assert m.name == "voltage"
        assert m.value == 5.0
        assert m.units is None
        assert m.outcome is None

    def test_measurement_with_limits(self):
        m = Measurement(
            name="voltage",
            value=5.0,
            units="V",
            limit_low=4.5,
            limit_high=5.5,
        )
        assert m.limit_low == 4.5
        assert m.limit_high == 5.5

    def test_check_limit_pass(self):
        m = Measurement(
            name="voltage",
            value=5.0,
            limit_low=4.5,
            limit_high=5.5,
        )
        result = m.check_limit()
        assert result == Outcome.PASS
        assert m.outcome == Outcome.PASS

    def test_check_limit_fail_low(self):
        m = Measurement(
            name="voltage",
            value=4.0,
            limit_low=4.5,
            limit_high=5.5,
        )
        result = m.check_limit()
        assert result == Outcome.FAIL
        assert m.outcome == Outcome.FAIL

    def test_check_limit_fail_high(self):
        m = Measurement(
            name="voltage",
            value=6.0,
            limit_low=4.5,
            limit_high=5.5,
        )
        result = m.check_limit()
        assert result == Outcome.FAIL
        assert m.outcome == Outcome.FAIL

    def test_check_limit_error_none_value(self):
        m = Measurement(
            name="voltage",
            value=None,
            limit_low=4.5,
            limit_high=5.5,
        )
        result = m.check_limit()
        assert result == Outcome.ERROR
        assert m.outcome == Outcome.ERROR

    def test_check_limit_no_limits_passes(self):
        m = Measurement(name="voltage", value=5.0)
        result = m.check_limit()
        assert result == Outcome.PASS

    def test_check_limit_only_low(self):
        m = Measurement(name="voltage", value=5.0, limit_low=4.5)
        result = m.check_limit()
        assert result == Outcome.PASS

        m2 = Measurement(name="voltage", value=4.0, limit_low=4.5)
        result2 = m2.check_limit()
        assert result2 == Outcome.FAIL

    def test_check_limit_only_high(self):
        m = Measurement(name="voltage", value=5.0, limit_high=5.5)
        result = m.check_limit()
        assert result == Outcome.PASS

        m2 = Measurement(name="voltage", value=6.0, limit_high=5.5)
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
        # Steps now contain vectors, which contain measurements
        assert step.vectors == []

    def test_step_with_vectors(self):
        m = Measurement(name="voltage", value=5.0)
        from litmus.data.models import TestVector

        vector = TestVector(measurements=[m])
        step = TestStep(name="measure_voltage", vectors=[vector])
        assert len(step.vectors) == 1
        assert len(step.vectors[0].measurements) == 1
        assert step.vectors[0].measurements[0].name == "voltage"


class TestTestRun:
    """Tests for TestRun model."""

    def test_basic_test_run(self):
        run = TestRun(
            dut=DUT(serial="SN001"),
            station_id="station_001",
        )
        assert run.dut.serial == "SN001"
        assert run.station_id == "station_001"
        assert run.outcome == Outcome.PASS
        assert run.steps == []

    def test_test_run_with_steps(self):
        step = TestStep(name="measure_voltage")
        run = TestRun(
            dut=DUT(serial="SN001"),
            station_id="station_001",
            steps=[step],
        )
        assert len(run.steps) == 1

    def test_test_run_bringup_tier_no_station(self):
        """Bringup tier: no station YAML loaded, station_id is None.

        ``station_hostname`` always populates from ``socket.gethostname()``
        downstream, so a bringup-tier run is still traceable to a
        machine — but the canonical station ``id`` doesn't exist.
        """
        run = TestRun(
            dut=DUT(serial="SN001"),
            station_id=None,
        )
        assert run.station_id is None

        # Round-trip through Pydantic to confirm None survives serialization.
        payload = run.model_dump()
        assert payload["station_id"] is None
        rehydrated = TestRun.model_validate(payload)
        assert rehydrated.station_id is None
