"""Tests for TestHarness class."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from litmus.config.models import Limit, RetryConfig
from litmus.data.models import Outcome, TestVector
from litmus.execution.harness import TestHarness
from litmus.execution.vectors import Vector


class TestHarnessInit:
    """Tests for TestHarness initialization."""

    def test_basic_init(self):
        harness = TestHarness()
        assert len(harness.vectors) == 1  # Single empty vector
        assert harness.vectors[0].params() == {}

    def test_init_with_explicit_vectors(self):
        config = {
            "vectors": [{"voltage": 3.3}, {"voltage": 5.0}, {"voltage": 12.0}]
        }
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
        config = {
            "retry": {"max_attempts": 3, "delay_seconds": 0.5}
        }
        harness = TestHarness(config=config)
        assert harness.retry_config.max_attempts == 3
        assert harness.retry_config.delay_seconds == 0.5

    def test_init_with_retry_override(self):
        config = {
            "retry": {"max_attempts": 3}
        }
        override = RetryConfig(max_attempts=5, delay_seconds=1.0)
        harness = TestHarness(config=config, retry=override)
        assert harness.retry_config.max_attempts == 5

    def test_init_with_limits(self):
        config = {
            "limits": {
                "voltage": {"low": 3.0, "high": 3.6, "units": "V"}
            }
        }
        harness = TestHarness(config=config)
        assert "voltage" in harness._limits


class TestHarnessMeasure:
    """Tests for TestHarness.measure method."""

    def test_measure_basic(self):
        harness = TestHarness()
        with harness.step():
            with harness.run_vector(Vector(voltage=3.3, _index=0)) as tv:
                m = harness.measure("output", 3.28)
                assert m.name == "output"
                assert m.value == Decimal("3.28")
                assert m.outcome == Outcome.PASS

    def test_measure_with_explicit_limit(self):
        harness = TestHarness()
        limit = Limit(low=Decimal("3.0"), high=Decimal("3.6"), units="V")

        with harness.step():
            with harness.run_vector(Vector(_index=0)) as tv:
                m = harness.measure("voltage", 3.3, limit=limit)
                assert m.low_limit == Decimal("3.0")
                assert m.high_limit == Decimal("3.6")
                assert m.units == "V"
                assert m.outcome == Outcome.PASS

    def test_measure_fail_updates_vector_outcome(self):
        harness = TestHarness()
        limit = Limit(low=Decimal("3.0"), high=Decimal("3.6"), units="V")

        with harness.step():
            with harness.run_vector(Vector(_index=0)) as tv:
                harness.measure("voltage", 4.0, limit=limit)  # Out of range

        assert tv.outcome == Outcome.FAIL

    def test_measure_from_config_limits(self):
        config = {
            "limits": {
                "voltage": {"low": 3.0, "high": 3.6, "units": "V"}
            }
        }
        harness = TestHarness(config=config)

        with harness.step():
            with harness.run_vector(Vector(_index=0)) as tv:
                m = harness.measure("voltage", 3.3)
                assert m.low_limit == Decimal("3.0")
                assert m.outcome == Outcome.PASS

    def test_measure_no_limit_passes(self):
        harness = TestHarness()
        with harness.step():
            with harness.run_vector(Vector(_index=0)) as tv:
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

        with harness.step():
            with pytest.raises(ValueError):
                with harness.run_vector(vector) as tv:
                    raise ValueError("Test error")

        assert tv.outcome == Outcome.ERROR
        assert tv.error_message == "Test error"

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
        config = {
            "retry": {"max_attempts": 3, "delay_seconds": 0}
        }
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
        config = {
            "retry": {"max_attempts": 3, "delay_seconds": 0}
        }
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
        config = {
            "retry": {"max_attempts": 2, "delay_seconds": 0}
        }
        harness = TestHarness(config=config)

        def test_fn(vector):
            raise ValueError("Always fail")

        with harness.step():
            tv = harness.run_with_retry(Vector(_index=0), test_fn)

        assert tv.attempt == 2
        assert tv.outcome == Outcome.ERROR

    def test_retry_with_generator(self):
        """Test that yield pattern works with retry."""
        config = {
            "retry": {"max_attempts": 2, "delay_seconds": 0}
        }
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
        limit = Limit(low=Decimal("3.0"), high=Decimal("3.6"), units="V")

        with harness.step() as step:
            with harness.run_vector(Vector(_index=0)) as tv:
                harness.measure("voltage", 4.0, limit=limit)  # Fail

        assert step.outcome == Outcome.FAIL


class TestHarnessRunAll:
    """Tests for TestHarness.run_all method."""

    def test_run_all_basic(self):
        config = {
            "vectors": [{"voltage": 3.3}, {"voltage": 5.0}]
        }
        harness = TestHarness(config=config)

        def test_fn(vector):
            return vector["voltage"]

        step = harness.run_all(test_fn, step_name="test_sweep")

        assert step.name == "test_sweep"
        assert len(step.vectors) == 2
        assert step.vectors[0].measurements[0].value == Decimal("3.3")
        assert step.vectors[1].measurements[0].value == Decimal("5.0")

    def test_run_all_with_generator(self):
        config = {
            "vectors": [{"voltage": 3.3}]
        }
        harness = TestHarness(config=config)

        def test_fn(vector):
            yield "voltage", vector["voltage"]
            yield "current", 0.1

        step = harness.run_all(test_fn)

        assert len(step.vectors[0].measurements) == 2


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
                    "Select option",
                    prompt_type="choice",
                    choices=["A", "B", "C"]
                )

        assert result == "A"
