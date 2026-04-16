"""Tests for TestHarness class."""

import pytest

from litmus.data.models import Outcome, TestVector
from litmus.execution.harness import Context, TestHarness
from litmus.execution.vectors import Vector
from litmus.models.config import Limit, RetryConfig


# Fake instrument classes for testing Mock factory
class FakeDMM:
    """Fake DMM for testing."""

    def __init__(self, resource: str = ""):
        self.resource = resource

    def connect(self):
        pass

    def disconnect(self):
        pass

    def measure_voltage(self) -> float:
        return 0.0

    def query(self, cmd: str) -> str:
        return ""


class FakePSU:
    """Fake PSU for testing."""

    def __init__(self, resource: str = ""):
        self.resource = resource

    def connect(self):
        pass

    def disconnect(self):
        pass

    def measure_current(self) -> float:
        return 0.0


class TestHarnessInit:
    """Tests for TestHarness initialization."""

    def test_basic_init(self):
        harness = TestHarness()
        assert len(harness.vectors) == 1  # Single empty vector
        assert harness.vectors[0].params() == {}

    def test_init_with_explicit_vectors(self):
        config = {"vectors": [{"voltage": 3.3}, {"voltage": 5.0}, {"voltage": 12.0}]}
        harness = TestHarness(config=config)
        assert len(harness.vectors) == 3
        assert harness.vectors[0]["voltage"] == 3.3

    def test_init_with_product_expansion(self):
        config = {
            "vectors": {
                "expand": "product",
                "voltage": [3.3, 5.0],
                "current": [0.1, 0.5],
            }
        }
        harness = TestHarness(config=config)
        assert len(harness.vectors) == 4

    def test_init_with_retry_config(self):
        config = {"retry": {"max_attempts": 3, "delay_seconds": 0.5}}
        harness = TestHarness(config=config)
        assert harness.retry_config.max_attempts == 3
        assert harness.retry_config.delay_seconds == 0.5

    def test_init_with_retry_override(self):
        config = {"retry": {"max_attempts": 3}}
        override = RetryConfig(max_attempts=5, delay_seconds=1.0)
        harness = TestHarness(config=config, retry=override)
        assert harness.retry_config.max_attempts == 5

    def test_init_with_limits(self):
        config = {"limits": {"voltage": {"low": 3.0, "high": 3.6, "units": "V"}}}
        harness = TestHarness(config=config)
        assert "voltage" in harness._limits


class TestHarnessMeasure:
    """Tests for TestHarness.measure method."""

    def test_measure_basic(self):
        harness = TestHarness()
        with harness.step():
            with harness.run_vector(Vector(voltage=3.3, _index=0)):
                m = harness.measure("output", 3.28)
                assert m.name == "output"
                assert m.value == 3.28
                assert m.outcome == Outcome.PASS

    def test_measure_with_explicit_limit(self):
        harness = TestHarness()
        limit = Limit(low=3.0, high=3.6, units="V")

        with harness.step():
            with harness.run_vector(Vector(_index=0)):
                m = harness.measure("voltage", 3.3, limit=limit)
                assert m.low_limit == 3.0
                assert m.high_limit == 3.6
                assert m.units == "V"
                assert m.outcome == Outcome.PASS

    def test_measure_fail_updates_vector_outcome(self):
        harness = TestHarness()
        limit = Limit(low=3.0, high=3.6, units="V")

        with harness.step():
            with harness.run_vector(Vector(_index=0)) as tv:
                harness.measure("voltage", 4.0, limit=limit)  # Out of range

        assert tv.outcome == Outcome.FAIL

    def test_measure_error_updates_vector_outcome_no_logger(self):
        """Without a logger, harness updates vector outcome on ERROR."""
        harness = TestHarness()
        limit = Limit(low=3.0, high=3.6, units="V")

        with harness.step():
            with harness.run_vector(Vector(_index=0)) as tv:
                # value=None with limits → ERROR via check_limit()
                harness.measure("voltage", None, limit=limit)

        assert tv.outcome == Outcome.ERROR

    def test_measure_error_overrides_fail_no_logger(self):
        """Without a logger, ERROR overrides FAIL — can't trust untrusted state."""
        harness = TestHarness()
        limit = Limit(low=3.0, high=3.6, units="V")

        with harness.step():
            with harness.run_vector(Vector(_index=0)) as tv:
                harness.measure("voltage_bad", 4.0, limit=limit)  # FAIL
                assert tv.outcome == Outcome.FAIL
                harness.measure("voltage_err", None, limit=limit)  # ERROR
                assert tv.outcome == Outcome.ERROR

        assert tv.outcome == Outcome.ERROR

    def test_measure_from_config_limits(self):
        config = {"limits": {"voltage": {"low": 3.0, "high": 3.6, "units": "V"}}}
        harness = TestHarness(config=config)

        with harness.step():
            with harness.run_vector(Vector(_index=0)):
                m = harness.measure("voltage", 3.3)
                assert m.low_limit == 3.0
                assert m.outcome == Outcome.PASS

    def test_measure_no_limit_passes(self):
        harness = TestHarness()
        with harness.step():
            with harness.run_vector(Vector(_index=0)):
                m = harness.measure("voltage", 999.0)  # Any value
                assert m.outcome == Outcome.PASS


class TestHarnessRunVector:
    """Tests for TestHarness.run_vector context manager."""

    def test_run_vector_creates_test_vector(self):
        harness = TestHarness()
        vector = Vector(voltage=3.3, current=0.1, _index=0)

        with harness.step():
            with harness.run_vector(vector) as tv:
                assert isinstance(tv, TestVector)
                assert tv.params == {"voltage": 3.3, "current": 0.1}
                assert tv.index == 0

    def test_run_vector_sets_timing(self):
        harness = TestHarness()
        vector = Vector(_index=0)

        with harness.step():
            with harness.run_vector(vector) as tv:
                assert tv.started_at is not None
                assert tv.ended_at is None

        assert tv.ended_at is not None

    def test_run_vector_handles_exception(self):
        harness = TestHarness()
        vector = Vector(_index=0)

        captured_tv = None
        with harness.step():
            with pytest.raises(ValueError):
                with harness.run_vector(vector) as tv:
                    captured_tv = tv
                    raise ValueError("Test error")

        assert captured_tv is not None
        assert captured_tv.outcome == Outcome.ERROR
        assert captured_tv.error_message == "Test error"

    def test_run_vector_added_to_step(self):
        harness = TestHarness()

        with harness.step() as step:
            with harness.run_vector(Vector(_index=0)):
                pass
            with harness.run_vector(Vector(_index=1)):
                pass

        assert len(step.vectors) == 2


class TestHarnessRunWithRetry:
    """Tests for TestHarness.run_with_retry method."""

    def test_retry_on_failure(self):
        config = {"retry": {"max_attempts": 3, "delay_seconds": 0}}
        harness = TestHarness(config=config)

        call_count = 0

        def test_fn(vector):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Fail")
            return 3.3

        with harness.step():
            tv = harness.run_with_retry(Vector(_index=0), test_fn)

        assert call_count == 3
        assert tv.attempt == 3
        assert tv.outcome == Outcome.PASS

    def test_no_retry_on_pass(self):
        config = {"retry": {"max_attempts": 3, "delay_seconds": 0}}
        harness = TestHarness(config=config)

        call_count = 0

        def test_fn(vector):
            nonlocal call_count
            call_count += 1
            return 3.3

        with harness.step():
            tv = harness.run_with_retry(Vector(_index=0), test_fn)

        assert call_count == 1
        assert tv.attempt == 1
        assert tv.outcome == Outcome.PASS

    def test_retry_exhausted_returns_fail(self):
        config = {"retry": {"max_attempts": 2, "delay_seconds": 0}}
        harness = TestHarness(config=config)

        def test_fn(vector):
            raise ValueError("Always fail")

        with harness.step():
            tv = harness.run_with_retry(Vector(_index=0), test_fn)

        assert tv.attempt == 2
        assert tv.outcome == Outcome.ERROR

    def test_retry_with_generator(self):
        """Test that yield pattern works with retry."""
        config = {"retry": {"max_attempts": 2, "delay_seconds": 0}}
        harness = TestHarness(config=config)

        def test_fn(vector):
            yield "voltage", 3.3
            yield "current", 0.1

        with harness.step():
            tv = harness.run_with_retry(Vector(_index=0), test_fn)

        assert len(tv.measurements) == 2
        assert tv.measurements[0].name == "voltage"
        assert tv.measurements[1].name == "current"


class TestHarnessStep:
    """Tests for TestHarness.step context manager."""

    def test_step_basic(self):
        harness = TestHarness()

        with harness.step(name="test_voltage") as step:
            assert step.name == "test_voltage"
            assert step.started_at is not None

        assert step.ended_at is not None

    def test_step_computes_outcome(self):
        harness = TestHarness()
        limit = Limit(low=3.0, high=3.6, units="V")

        with harness.step() as step:
            with harness.run_vector(Vector(_index=0)):
                harness.measure("voltage", 4.0, limit=limit)  # Fail

        assert step.outcome == Outcome.FAIL


class TestHarnessRunAll:
    """Tests for TestHarness.run_all method."""

    def test_run_all_basic(self):
        config = {"vectors": [{"voltage": 3.3}, {"voltage": 5.0}]}
        harness = TestHarness(config=config)

        def test_fn(vector):
            return vector["voltage"]

        step = harness.run_all(test_fn, step_name="test_sweep")

        assert step.name == "test_sweep"
        assert len(step.vectors) == 2
        assert step.vectors[0].measurements[0].value == 3.3
        assert step.vectors[1].measurements[0].value == 5.0

    def test_run_all_with_generator(self):
        config = {"vectors": [{"voltage": 3.3}]}
        harness = TestHarness(config=config)

        def test_fn(vector):
            yield "voltage", vector["voltage"]
            yield "current", 0.1

        step = harness.run_all(test_fn)

        assert len(step.vectors[0].measurements) == 2


class TestHarnessMockConfiguration:
    """Tests for TestHarness mock configuration per vector."""

    def test_configure_mocks_calls_set_mock_value(self):
        """Test that _configure_mocks calls set_mock_value on instruments."""
        from litmus.instruments import Mock

        dmm = Mock(FakeDMM)
        psu = Mock(FakePSU)
        instruments = {"dmm": dmm, "psu": psu}

        harness = TestHarness(instruments=instruments, mock_instruments=True)
        harness._configure_mocks({"dmm.measure_voltage": 3.3, "psu.measure_current": 0.5})

        assert float(dmm.measure_voltage()) == 3.3
        assert float(psu.measure_current()) == 0.5

    def test_run_vector_configures_mocks_when_simulating(self):
        """Test that run_vector applies _mock config from vector."""
        from litmus.instruments import Mock

        dmm = Mock(FakeDMM)
        instruments = {"dmm": dmm}

        config = {
            "vectors": [
                {"vin": 5.0, "_mocks": {"dmm.measure_voltage": 3.3}},
            ]
        }
        harness = TestHarness(config=config, instruments=instruments, mock_instruments=True)

        with harness.step():
            with harness.run_vector(harness.vectors[0]):
                assert float(dmm.measure_voltage()) == 3.3

    def test_per_vector_mock_config(self):
        """Test that each vector gets its own mock values."""
        from litmus.instruments import Mock

        dmm = Mock(FakeDMM)
        instruments = {"dmm": dmm}

        config = {
            "vectors": [
                {"load": 0.1, "_mocks": {"dmm.measure_voltage": 3.32}},
                {"load": 0.5, "_mocks": {"dmm.measure_voltage": 3.30}},
                {"load": 0.8, "_mocks": {"dmm.measure_voltage": 3.28}},
            ]
        }
        harness = TestHarness(config=config, instruments=instruments, mock_instruments=True)

        measurements = []
        with harness.step():
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    measurements.append(float(dmm.measure_voltage()))

        assert measurements == [3.32, 3.30, 3.28]

    def test_test_level_mock_fallback(self):
        """Test that test-level _mock is used when vector has none."""
        from litmus.instruments import Mock

        dmm = Mock(FakeDMM)
        instruments = {"dmm": dmm}

        config = {
            "vectors": [{"vin": 5.0}],
            "mocks": {"dmm.measure_voltage": 3.3},  # Test-level mock
        }
        harness = TestHarness(config=config, instruments=instruments, mock_instruments=True)

        with harness.step():
            with harness.run_vector(harness.vectors[0]):
                assert float(dmm.measure_voltage()) == 3.3

    def test_no_mock_config_when_not_mocking(self):
        """Test that mocks are not configured when mock_instruments=False."""
        from litmus.instruments import Mock

        dmm = Mock(FakeDMM, measure_voltage=0.0)
        instruments = {"dmm": dmm}

        config = {
            "vectors": [{"vin": 5.0, "_mocks": {"dmm.measure_voltage": 3.3}}],
        }
        # mock_instruments=False
        harness = TestHarness(config=config, instruments=instruments, mock_instruments=False)

        with harness.step():
            with harness.run_vector(harness.vectors[0]):
                # Mock should not have been configured
                assert float(dmm.measure_voltage()) == 0.0

    def test_callable_mock_receives_context(self):
        """Test that callable mock values receive the current context."""
        from litmus.instruments import Mock

        dmm = Mock(FakeDMM)
        instruments = {"dmm": dmm}

        # Callable that uses context to compute return value
        def dynamic_voltage(*, context=None):
            load = context.get_in("load", 0) if context else 0
            return 3.3 - load * 0.1  # Voltage droops with load

        config = {
            "vectors": [
                {"load": 0.0, "_mocks": {"dmm.measure_voltage": dynamic_voltage}},
                {"load": 1.0, "_mocks": {"dmm.measure_voltage": dynamic_voltage}},
                {"load": 2.0, "_mocks": {"dmm.measure_voltage": dynamic_voltage}},
            ],
        }
        harness = TestHarness(config=config, instruments=instruments, mock_instruments=True)

        measurements = []
        with harness.step():
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    measurements.append(dmm.measure_voltage())

        assert measurements[0] == pytest.approx(3.3)  # load=0.0
        assert measurements[1] == pytest.approx(3.2)  # load=1.0
        assert measurements[2] == pytest.approx(3.1)  # load=2.0

    def test_dict_mock_for_scpi(self):
        """Test that dict mock values work for SCPI-style mocking."""
        from litmus.instruments import Mock

        dmm = Mock(FakeDMM)
        instruments = {"dmm": dmm}

        config = {
            "vectors": [{"vin": 5.0}],
            "mocks": {
                "dmm.query": {
                    "MEAS:VOLT:DC?": "3.3",
                    "MEAS:CURR:DC?": "0.1",
                }
            },
        }
        harness = TestHarness(config=config, instruments=instruments, mock_instruments=True)

        with harness.step():
            with harness.run_vector(harness.vectors[0]):
                assert dmm.query("MEAS:VOLT:DC?") == "3.3"
                assert dmm.query("MEAS:CURR:DC?") == "0.1"
                assert dmm.query("UNKNOWN?") is None


class TestHarnessPrompt:
    """Tests for TestHarness.prompt method."""

    def test_prompt_formats_message(self):
        harness = TestHarness()
        captured_config = None

        def mock_handler(config):
            nonlocal captured_config
            captured_config = config
            return True

        harness._prompt_handler = mock_handler

        with harness.step():
            with harness.run_vector(Vector(temp=25, _index=0)):
                harness.prompt("Set temperature to {temp}C")

        assert captured_config is not None
        assert captured_config.message == "Set temperature to 25C"

    def test_prompt_type_choice(self):
        harness = TestHarness()
        result = None

        def mock_handler(config):
            if config.prompt_type == "choice":
                return config.choices[0]
            return True

        harness._prompt_handler = mock_handler

        with harness.step():
            with harness.run_vector(Vector(_index=0)):
                result = harness.prompt(
                    "Select option", prompt_type="choice", choices=["A", "B", "C"]
                )

        assert result == "A"


class TestContext:
    """Tests for Context class (hierarchical context with scoped inheritance)."""

    def test_configure_adds_to_inputs(self):
        """Test that configure() adds values to inputs dict."""
        ctx = Context()
        ctx.configure("psu.voltage", 5.0)
        ctx.configure("temperature", 25)

        assert ctx.get_in("psu.voltage") == 5.0
        assert ctx.get_in("temperature") == 25
        assert ctx.inputs == {"psu.voltage": 5.0, "temperature": 25}

    def test_observe_adds_to_outputs(self):
        """Test that observe() adds values to outputs dict."""
        ctx = Context()
        ctx.observe("temp_probe.temperature", 24.8)
        ctx.observe("temp_probe.humidity", 45.2)

        assert ctx.get_out("temp_probe.temperature") == 24.8
        assert ctx.get_out("temp_probe.humidity") == 45.2
        assert ctx.outputs == {"temp_probe.temperature": 24.8, "temp_probe.humidity": 45.2}

    def test_configure_all_bulk(self):
        """Test that configure_all() adds multiple inputs at once."""
        ctx = Context()
        ctx.configure_all({"psu.voltage": 5.0, "eload.current": 0.8})

        assert ctx.inputs == {"psu.voltage": 5.0, "eload.current": 0.8}

    def test_observe_all_bulk(self):
        """Test that observe_all() adds multiple outputs at once."""
        ctx = Context()
        ctx.observe_all({"temp_probe.temperature": 24.8, "temp_probe.humidity": 45.2})

        assert ctx.outputs == {"temp_probe.temperature": 24.8, "temp_probe.humidity": 45.2}

    def test_get_default_value(self):
        """Test that get_in/get_out return default when key missing."""
        ctx = Context()

        assert ctx.get_in("missing") is None
        assert ctx.get_in("missing", 42) == 42
        assert ctx.get_out("missing") is None
        assert ctx.get_out("missing", "default") == "default"

    def test_set_inputs_initializes_from_vector(self):
        """Test that set_inputs() sets initial values (from vector params)."""
        ctx = Context()
        ctx.set_inputs({"temperature": 25, "load": 0.8})

        assert ctx.inputs == {"temperature": 25, "load": 0.8}

    def test_set_outputs_sets_observations(self):
        """Test that set_outputs() sets observation values."""
        ctx = Context()
        ctx.set_outputs({"temp_probe.temperature": 24.8, "temp_probe.humidity": 45.2})

        assert ctx.outputs == {"temp_probe.temperature": 24.8, "temp_probe.humidity": 45.2}

    def test_child_creates_new_context_with_parent(self):
        """Test that child() creates a new context with this as parent."""
        parent = Context()
        parent.configure("operator", "jane")

        child = parent.child()

        assert child._parent is parent
        assert child.get_in("operator") == "jane"

    def test_child_inherits_inputs_from_parent(self):
        """Test that child context inherits inputs from parent chain."""
        run_ctx = Context()
        run_ctx.configure("operator", "jane")
        run_ctx.configure("station", "station_01")

        step_ctx = run_ctx.child()
        step_ctx.configure("fixture.id", "FIX-01")

        vector_ctx = step_ctx.child()
        vector_ctx.configure("temp", 25)

        # Vector sees all inherited values
        assert vector_ctx.get_in("operator") == "jane"
        assert vector_ctx.get_in("station") == "station_01"
        assert vector_ctx.get_in("fixture.id") == "FIX-01"
        assert vector_ctx.get_in("temp") == 25

        # Inputs property merges the full chain
        assert vector_ctx.inputs == {
            "operator": "jane",
            "station": "station_01",
            "fixture.id": "FIX-01",
            "temp": 25,
        }

    def test_child_inherits_outputs_from_parent(self):
        """Test that child context inherits outputs from parent chain."""
        run_ctx = Context()
        run_ctx.observe("start_time", "2026-01-15T10:00:00")

        step_ctx = run_ctx.child()
        step_ctx.observe("setup.duration", 5.2)

        vector_ctx = step_ctx.child()
        vector_ctx.observe("temp_probe.temp", 24.8)

        # Outputs property merges the full chain
        assert vector_ctx.outputs == {
            "start_time": "2026-01-15T10:00:00",
            "setup.duration": 5.2,
            "temp_probe.temp": 24.8,
        }

    def test_child_can_override_parent_value(self):
        """Test that child can override a parent's value locally."""
        parent = Context()
        parent.configure("temp", 25)

        child = parent.child()
        child.configure("temp", 85)

        # Child sees its own value
        assert child.get_in("temp") == 85
        # Parent still has original value
        assert parent.get_in("temp") == 25

    def test_sibling_contexts_are_independent(self):
        """Test that sibling child contexts don't share data."""
        parent = Context()
        parent.configure("operator", "jane")

        child1 = parent.child()
        child1.configure("temp", 25)

        child2 = parent.child()
        child2.configure("temp", 85)

        # Each child has its own temp
        assert child1.get_in("temp") == 25
        assert child2.get_in("temp") == 85

        # But both inherit operator
        assert child1.get_in("operator") == "jane"
        assert child2.get_in("operator") == "jane"

    def test_run_context_compatibility_set_get(self):
        """Test that Context has RunContext-compatible set/get methods."""
        ctx = Context()
        ctx.set("operator_badge", "EMP-12345")

        assert ctx.get("operator_badge") == "EMP-12345"
        assert ctx.get("missing", "default") == "default"

    def test_run_context_compatibility_update(self):
        """Test that Context has RunContext-compatible update method."""
        ctx = Context()
        ctx.update(operator_badge="EMP-12345", fixture_serial="FIX-001")

        assert ctx.get("operator_badge") == "EMP-12345"
        assert ctx.get("fixture_serial") == "FIX-001"

    def test_run_context_compatibility_metadata(self):
        """Test that Context has RunContext-compatible metadata property."""
        ctx = Context()
        ctx.set("operator_badge", "EMP-12345")

        assert ctx.metadata == {"operator_badge": "EMP-12345"}


class TestHarnessContext:
    """Tests for Context integration with TestHarness."""

    def test_context_property_available(self):
        """Test that harness has context property."""
        harness = TestHarness()
        assert isinstance(harness.context, Context)

    def test_run_context_persists_across_steps(self):
        """Test that run context is available across steps."""
        harness = TestHarness()
        harness.run_context.configure("operator", "jane")

        with harness.step():
            with harness.run_vector(Vector(_index=0)):
                # Run context value should be inherited
                assert harness.context.get_in("operator") == "jane"

        with harness.step():
            with harness.run_vector(Vector(_index=0)):
                # Still available in second step
                assert harness.context.get_in("operator") == "jane"

    def test_step_context_inherits_from_run(self):
        """Test that step context inherits from run context."""
        harness = TestHarness()
        harness.run_context.configure("operator", "jane")

        with harness.step():
            # In step but outside vector, context is step context
            harness.context.configure("fixture.id", "FIX-01")

            # Step context has both run and step values
            assert harness.context.get_in("operator") == "jane"
            assert harness.context.get_in("fixture.id") == "FIX-01"

    def test_vector_context_inherits_from_step_and_run(self):
        """Test that vector context inherits from step and run."""
        harness = TestHarness()
        harness.run_context.configure("operator", "jane")

        with harness.step():
            harness.context.configure("fixture.id", "FIX-01")

            with harness.run_vector(Vector(temp=25, _index=0)):
                # Vector context sees all levels
                assert harness.context.get_in("operator") == "jane"
                assert harness.context.get_in("fixture.id") == "FIX-01"
                assert harness.context.get_in("temp") == 25

    def test_vector_context_fresh_for_each_vector(self):
        """Test that each vector gets a fresh context."""
        harness = TestHarness()

        with harness.step():
            # First vector
            with harness.run_vector(Vector(temp=25, _index=0)):
                harness.context.observe("probe.temp", 24.8)
                assert harness.context.get_out("probe.temp") == 24.8

            # Second vector - should not have first vector's observations
            with harness.run_vector(Vector(temp=85, _index=1)):
                assert harness.context.get_out("probe.temp") is None

    def test_vector_params_in_context_inputs(self):
        """Test that vector params are in context inputs."""
        harness = TestHarness()

        with harness.step():
            with harness.run_vector(Vector(temperature=25, load=0.8, _index=0)):
                assert harness.context.get_in("temperature") == 25
                assert harness.context.get_in("load") == 0.8

    def test_observations_stored_in_test_vector(self):
        """Test that observations flow to TestVector.observations."""
        harness = TestHarness()

        with harness.step():
            with harness.run_vector(Vector(_index=0)) as tv:
                harness.context.observe("temp_probe.temperature", 24.8)
                harness.context.observe("temp_probe.humidity", 45.2)

        assert tv.observations == {
            "temp_probe.temperature": 24.8,
            "temp_probe.humidity": 45.2,
        }

    def test_inherited_inputs_stored_in_test_vector(self):
        """Test that inherited inputs are stored in TestVector.params."""
        harness = TestHarness()
        harness.run_context.configure("operator", "jane")

        with harness.step():
            harness.context.configure("fixture.id", "FIX-01")

            with harness.run_vector(Vector(temp=25, _index=0)) as tv:
                pass

        # TestVector params should include inherited values
        assert tv.params["operator"] == "jane"
        assert tv.params["fixture.id"] == "FIX-01"
        assert tv.params["temp"] == 25

    def test_step_context_not_visible_in_next_step(self):
        """Test that step context is discarded after step ends."""
        harness = TestHarness()

        with harness.step():
            harness.context.configure("step1.value", 100)
            assert harness.context.get_in("step1.value") == 100

        with harness.step():
            # Step context from previous step should not be visible
            assert harness.context.get_in("step1.value") is None

    def test_context_outside_step_is_run_context(self):
        """Test that context outside step returns run context."""
        harness = TestHarness()
        harness.run_context.configure("operator", "jane")

        # Outside step, context should be run context
        assert harness.context.get_in("operator") == "jane"
        harness.context.configure("global.value", 42)
        assert harness.run_context.get_in("global.value") == 42


class TestHarnessSpecId:
    """Tests for spec_id propagation in TestHarness."""

    def test_measure_copies_spec_id_from_limit(self):
        """Test that measure() copies spec_id from resolved limit."""
        limit = Limit(
            low=3.0,
            high=3.6,
            units="V",
            spec_id="output_voltage",
            spec_ref="Table 4.2 @ temp=25",
        )
        harness = TestHarness()

        with harness.step():
            with harness.run_vector(Vector(_index=0)):
                m = harness.measure("vout", 3.3, limit=limit)

        assert m.spec_id == "output_voltage"
        assert m.spec_ref == "Table 4.2 @ temp=25"

    def test_measure_spec_id_none_without_limit(self):
        """Test that spec_id is None when no limit is provided."""
        harness = TestHarness()

        with harness.step():
            with harness.run_vector(Vector(_index=0)):
                m = harness.measure("vout", 3.3)

        assert m.spec_id is None
        assert m.spec_ref is None
