"""Tests for TestRunLogger."""

from uuid import uuid4

from litmus.data.models import Measurement, Outcome, TestStep, TestVector
from litmus.execution._state import (
    get_current_step,
    get_current_vector,
    push_current_step,
    push_current_vector,
    reset_current_step,
    reset_current_vector,
)
from litmus.execution.logger import TestRunLogger


class TestTestRunLogger:
    """Tests for TestRunLogger."""

    def test_init(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        assert logger.test_run.dut.serial == "SN001"
        assert logger.test_run.station_id == "station_001"
        assert logger.test_run.outcome is None

    def test_init_with_all_options(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            station_type="production",
            operator_id="John Doe",
            test_phase="debug",
        )
        assert logger.test_run.station_type == "production"
        assert logger.test_run.operator_id == "John Doe"
        assert logger.test_run.test_phase == "debug"

    def test_start_step(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        logger.start_step("measure_voltage", description="Signal 5V rail")

        assert len(logger.test_run.steps) == 1
        assert logger.test_run.steps[0].name == "measure_voltage"
        assert logger.test_run.steps[0].description == "Signal 5V rail"
        assert get_current_step() is not None

    def test_log_measurement(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        logger.start_step("test_step")

        m = Measurement(name="voltage", value=5.0, outcome=Outcome.PASSED)
        logger.log_measurement(m)

        # Measurements are stored in vectors within the step
        step = get_current_step()
        assert step is not None
        assert len(step.vectors) == 1
        assert len(step.vectors[0].measurements) == 1
        assert step.vectors[0].measurements[0].name == "voltage"

    def test_log_measurement_auto_creates_step(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )

        m = Measurement(name="voltage", value=5.0, outcome=Outcome.PASSED)
        logger.log_measurement(m)

        assert len(logger.test_run.steps) == 1
        assert logger.test_run.steps[0].name == "voltage"

    def test_log_measurement_fail_propagates(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        logger.start_step("test_step")

        m = Measurement(name="voltage", value=6.0, outcome=Outcome.FAILED)
        logger.log_measurement(m)

        step_1 = get_current_step()
        assert step_1 is not None
        assert step_1.outcome == Outcome.FAILED
        assert logger.test_run.outcome == Outcome.FAILED

    def test_log_measurement_error_propagates(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        logger.start_step("test_step")

        m = Measurement(name="voltage", value=None, outcome=Outcome.ERRORED)
        logger.log_measurement(m)

        step_2 = get_current_step()
        assert step_2 is not None
        assert step_2.outcome == Outcome.ERRORED
        assert logger.test_run.outcome == Outcome.ERRORED

    def test_error_overrides_fail(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        logger.start_step("test_step")

        m1 = Measurement(name="current", value=6.0, outcome=Outcome.FAILED)
        m2 = Measurement(name="voltage", value=None, outcome=Outcome.ERRORED)
        logger.log_measurement(m1)
        logger.log_measurement(m2)

        # ERROR overrides FAIL — can't trust results from untrusted state
        step_3 = get_current_step()
        assert step_3 is not None
        assert step_3.outcome == Outcome.ERRORED
        assert logger.test_run.outcome == Outcome.ERRORED

    def test_end_step(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        logger.start_step("test_step")
        logger.end_step()

        assert get_current_step() is None
        assert logger.test_run.steps[0].ended_at is not None

    def test_finalize(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        logger.start_step("test_step")
        logger.end_step()

        test_run = logger.finalize()

        assert test_run.ended_at is not None
        assert test_run is logger.test_run

    def test_multiple_steps(self):
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )

        logger.start_step("step1")
        m1 = Measurement(name="voltage", value=5.0, outcome=Outcome.PASSED)
        logger.log_measurement(m1)
        logger.end_step()

        logger.start_step("step2")
        m2 = Measurement(name="current", value=0.1, outcome=Outcome.PASSED)
        logger.log_measurement(m2)
        logger.end_step()

        assert len(logger.test_run.steps) == 2
        assert logger.test_run.steps[0].name == "step1"
        assert logger.test_run.steps[1].name == "step2"

    def test_start_step_sets_contextvars(self):
        """start_step() sets module-level contextvars."""
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        # Before start_step, contextvars should be None (default)
        assert get_current_step() is None

        logger.start_step("cv_step")
        assert get_current_step() is logger.test_run.steps[0]
        assert get_current_vector() is logger.test_run.steps[0].vectors[0]

        logger.end_step()
        assert get_current_step() is None
        assert get_current_vector() is None

    def test_log_measurement_resolves_from_contextvar(self):
        """log_measurement() uses contextvar step when instance state is None."""
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        # Create a step externally and set via contextvar
        step = TestStep(name="external_step")
        logger.test_run.steps.append(step)
        vector = TestVector()
        step.vectors.append(vector)

        step_token = push_current_step(step)
        vector_token = push_current_vector(vector)
        try:
            m = Measurement(name="voltage", value=5.0, outcome=Outcome.PASSED)
            logger.log_measurement(m)

            assert len(vector.measurements) == 1
            assert vector.measurements[0].name == "voltage"
            assert step.outcome == Outcome.PASSED
        finally:
            reset_current_step(step_token)
            reset_current_vector(vector_token)

    def test_register_step(self):
        """register_step() adds step to test_run and returns index."""
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        step = TestStep(name="registered_step")
        idx = logger.register_step(step)

        assert idx == 0
        assert logger.test_run.steps[0] is step

        step2 = TestStep(name="registered_step_2")
        idx2 = logger.register_step(step2)
        assert idx2 == 1

    def test_log_measurement_no_double_append(self):
        """log_measurement() doesn't double-append if measurement already in vector."""
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        logger.start_step("test_step")
        vector = get_current_vector()
        assert vector is not None

        m = Measurement(name="voltage", value=5.0, outcome=Outcome.PASSED)
        # Pre-append (simulating what harness.measure() does)
        vector.measurements.append(m)
        # Now call log_measurement — should NOT double-append
        logger.log_measurement(m)

        assert len(vector.measurements) == 1


class TestEventLogIntegration:
    """Tests for EventLog integration in TestRunLogger."""

    def test_event_log_emits_events(self, tmp_path):
        """Logger emits StepStarted, MeasurementRecorded, StepEnded, RunEnded."""

        from litmus.data.event_log import EventLog

        run_id = uuid4()
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            run_id=run_id,
        )
        event_log = EventLog(tmp_path / "events", run_id)
        logger._event_log = event_log
        logger._session_id = uuid4()

        logger.start_step("step1")
        m = Measurement(name="voltage", value=5.0, outcome=Outcome.PASSED)
        logger.log_measurement(m)
        logger.end_step()
        logger.finalize()

        event_log.close()
        events = event_log.events()
        types = [e["event_type"] for e in events]
        assert types == [
            "test.step_started",
            "test.measurement",
            "test.step_ended",
            "run.ended",
        ]

    def test_measurement_event_is_normalized(self, tmp_path):
        """MeasurementRecorded should NOT contain run-level metadata."""

        from litmus.data.event_log import EventLog

        run_id = uuid4()
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
            run_id=run_id,
        )
        event_log = EventLog(tmp_path / "events", run_id)
        logger._event_log = event_log
        logger._session_id = uuid4()

        logger.start_step("step1")
        logger.log_measurement(Measurement(name="v", value=3.3, outcome=Outcome.PASSED))
        logger.end_step()
        logger.finalize()

        event_log.close()
        events = event_log.events()
        for data in events:
            if data["event_type"] == "test.measurement":
                # These fields should NOT be on the normalized event
                assert "station_id" not in data
                assert "dut_serial" not in data
                assert "instruments" not in data
                assert "vector_started_at" not in data
                assert "step_started_at" not in data
                # These should be present
                assert data["measurement_name"] == "v"
                assert data["value"] == 3.3
                assert data["step_name"] == "step1"
                break
        else:
            raise AssertionError("No test.measurement event found")

    def test_start_step_code_identity(self):
        """start_step() stores code identity on TestStep."""
        logger = TestRunLogger(
            dut_serial="SN001",
            station_id="station_001",
        )
        logger.start_step(
            "test_5v_rail",
            node_id="tests/test_power.py::TestPower::test_5v_rail",
            file="tests/test_power.py",
            module="tests.test_power",
            class_name="TestPower",
            function="test_5v_rail",
            markers="parametrize",
        )

        step = logger.test_run.steps[0]
        assert step.node_id == "tests/test_power.py::TestPower::test_5v_rail"
        assert step.file == "tests/test_power.py"
        assert step.module == "tests.test_power"
        assert step.class_name == "TestPower"
        assert step.function == "test_5v_rail"
        assert step.markers == "parametrize"
