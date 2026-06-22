"""Tests for data models."""

from uuid import UUID

from litmus.data.models import UUT, Measurement, Outcome, TestRun, TestStep


class TestOutcomeEnum:
    """Tests for Outcome enum."""

    def test_pass_value(self):
        assert Outcome.PASSED.value == "passed"

    def test_fail_value(self):
        assert Outcome.FAILED.value == "failed"

    def test_error_value(self):
        assert Outcome.ERRORED.value == "errored"

    def test_skip_value(self):
        assert Outcome.SKIPPED.value == "skipped"

    def test_done_value(self):
        assert Outcome.DONE.value == "done"

    def test_aborted_value(self):
        assert Outcome.ABORTED.value == "aborted"

    def test_terminated_value(self):
        assert Outcome.TERMINATED.value == "terminated"


class TestMeasurement:
    """Tests for Measurement model."""

    def test_basic_measurement(self):
        m = Measurement(name="voltage", value=5.0)
        assert m.name == "voltage"
        assert m.value == 5.0
        assert m.unit is None
        assert m.outcome is None

    def test_measurement_with_limits(self):
        m = Measurement(
            name="voltage",
            value=5.0,
            unit="V",
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
        assert result == Outcome.PASSED
        assert m.outcome == Outcome.PASSED

    def test_check_limit_fail_low(self):
        m = Measurement(
            name="voltage",
            value=4.0,
            limit_low=4.5,
            limit_high=5.5,
        )
        result = m.check_limit()
        assert result == Outcome.FAILED
        assert m.outcome == Outcome.FAILED

    def test_check_limit_fail_high(self):
        m = Measurement(
            name="voltage",
            value=6.0,
            limit_low=4.5,
            limit_high=5.5,
        )
        result = m.check_limit()
        assert result == Outcome.FAILED
        assert m.outcome == Outcome.FAILED

    def test_check_limit_error_none_value(self):
        m = Measurement(
            name="voltage",
            value=None,
            limit_low=4.5,
            limit_high=5.5,
        )
        result = m.check_limit()
        assert result == Outcome.ERRORED
        assert m.outcome == Outcome.ERRORED

    def test_check_limit_no_limits_records_done(self):
        """``check_limit`` with no limit fields stamped → DONE.

        Recorder semantic ("ran, no judgment") matches what
        ``logger.measure`` produces for the same case.
        """
        m = Measurement(name="voltage", value=5.0)
        result = m.check_limit()
        assert result == Outcome.DONE

    def test_check_limit_only_low(self):
        m = Measurement(name="voltage", value=5.0, limit_low=4.5)
        result = m.check_limit()
        assert result == Outcome.PASSED

        m2 = Measurement(name="voltage", value=4.0, limit_low=4.5)
        result2 = m2.check_limit()
        assert result2 == Outcome.FAILED

    def test_check_limit_only_high(self):
        m = Measurement(name="voltage", value=5.0, limit_high=5.5)
        result = m.check_limit()
        assert result == Outcome.PASSED

        m2 = Measurement(name="voltage", value=6.0, limit_high=5.5)
        result2 = m2.check_limit()
        assert result2 == Outcome.FAILED


class TestUUT:
    """Tests for UUT model."""

    def test_basic_uut(self):
        uut = UUT(serial="SN001")
        assert uut.serial == "SN001"
        assert uut.part_number is None

    def test_uut_with_all_fields(self):
        uut = UUT(
            serial="SN001",
            part_number="PN-123",
            revision="A",
            lot_number="LOT001",
        )
        assert uut.serial == "SN001"
        assert uut.part_number == "PN-123"
        assert uut.revision == "A"
        assert uut.lot_number == "LOT001"


class TestTestStep:
    """Tests for TestStep model."""

    def test_basic_step(self):
        step = TestStep(name="measure_voltage")
        assert step.name == "measure_voltage"
        assert isinstance(step.id, UUID)
        # New steps have no outcome — it's stamped only when the
        # step actually runs (via measurement cascade or runner-side
        # stamping). ``None`` is the "never ran" signal at finalize.
        assert step.outcome is None
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
            uut=UUT(serial="SN001"),
            station_id="station_001",
        )
        assert run.uut.serial == "SN001"
        assert run.station_id == "station_001"
        # New runs have no outcome — see TestStep.test_basic_step.
        assert run.outcome is None
        assert run.steps == []

    def test_test_run_with_steps(self):
        step = TestStep(name="measure_voltage")
        run = TestRun(
            uut=UUT(serial="SN001"),
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
            uut=UUT(serial="SN001"),
            station_id=None,
        )
        assert run.station_id is None

        # Round-trip through Pydantic to confirm None survives serialization.
        payload = run.model_dump()
        assert payload["station_id"] is None
        rehydrated = TestRun.model_validate(payload)
        assert rehydrated.station_id is None
