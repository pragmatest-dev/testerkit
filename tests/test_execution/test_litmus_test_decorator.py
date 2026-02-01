"""Tests for @litmus_test decorator."""

from decimal import Decimal

import pytest

from litmus.data.models import Outcome
from litmus.execution.decorators import litmus_test, set_current_harness
from litmus.execution.harness import TestHarness


class TestLitmusTestDecorator:
    """Tests for @litmus_test decorator."""

    def setup_method(self):
        """Reset harness before each test."""
        set_current_harness(None)

    def teardown_method(self):
        """Reset harness after each test."""
        set_current_harness(None)

    def test_basic_decorator(self):
        """Test basic decorator usage with return value."""
        config = {
            "vectors": [{"voltage": 3.3}, {"voltage": 5.0}]
        }

        @litmus_test(config=config, raise_on_fail=False)
        def test_sweep(vector):
            return vector["voltage"]

        step = test_sweep()

        assert step.name == "test_sweep"
        assert len(step.vectors) == 2
        assert step.vectors[0].measurements[0].value == Decimal("3.3")
        assert step.vectors[1].measurements[0].value == Decimal("5.0")

    def test_decorator_without_parens(self):
        """Test @litmus_test without parentheses."""
        @litmus_test
        def test_simple(vector):
            return 42

        step = test_simple()

        assert len(step.vectors) == 1
        assert step.vectors[0].measurements[0].value == Decimal("42")

    def test_decorator_with_yield(self):
        """Test generator pattern for multiple measurements."""
        config = {
            "vectors": [{"voltage": 3.3}]
        }

        @litmus_test(config=config, raise_on_fail=False)
        def test_multi(vector):
            yield "output_voltage", vector["voltage"]
            yield "output_current", 0.1

        step = test_multi()

        assert len(step.vectors[0].measurements) == 2
        assert step.vectors[0].measurements[0].name == "output_voltage"
        assert step.vectors[0].measurements[1].name == "output_current"

    def test_decorator_with_product_expansion(self):
        """Test decorator with product expansion."""
        config = {
            "vectors": {
                "expand": "product",
                "voltage": [3.3, 5.0],
                "load": [0.1, 0.5],
            }
        }

        @litmus_test(config=config, raise_on_fail=False)
        def test_matrix(vector):
            return vector["voltage"] * vector["load"]

        step = test_matrix()

        assert len(step.vectors) == 4  # 2 x 2

    def test_decorator_with_limits_pass(self):
        """Test decorator with passing limits."""
        config = {
            "vectors": [{"voltage": 3.3}],
            "limits": {
                "output": {"low": 3.0, "high": 3.6, "units": "V"}
            }
        }

        @litmus_test(config=config)
        def test_with_limit(vector):
            return 3.3  # Within limits

        step = test_with_limit()

        assert step.vectors[0].outcome == Outcome.PASS
        assert step.vectors[0].measurements[0].outcome == Outcome.PASS

    def test_decorator_with_limits_fail(self):
        """Test decorator raises on limit failure."""
        config = {
            "vectors": [{"voltage": 3.3}],
            "limits": {
                # Limit key must match function name (measurement name defaults to fn name)
                "test_fails": {"low": 3.0, "high": 3.2, "units": "V"}
            }
        }

        @litmus_test(config=config)
        def test_fails(vector):
            return 3.5  # Above high limit

        with pytest.raises(AssertionError, match="FAILED"):
            test_fails()

    def test_decorator_no_raise_on_fail(self):
        """Test decorator with raise_on_fail=False."""
        config = {
            "vectors": [{"voltage": 3.3}],
            "limits": {
                # Limit key must match function name
                "test_no_raise": {"low": 3.0, "high": 3.2, "units": "V"}
            }
        }

        @litmus_test(config=config, raise_on_fail=False)
        def test_no_raise(vector):
            return 3.5  # Above high limit

        step = test_no_raise()

        assert step.vectors[0].outcome == Outcome.FAIL

    def test_decorator_with_retry(self):
        """Test decorator with retry configuration."""
        call_count = 0

        config = {
            "vectors": [{"voltage": 3.3}],
            "retry": {"max_attempts": 3, "delay_seconds": 0},
        }

        @litmus_test(config=config, raise_on_fail=False)
        def test_retry(vector):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Transient failure")
            return 3.3

        step = test_retry()

        assert call_count == 3
        # Each retry attempt creates a separate TestVector record
        # So we have 3 vectors: attempt 1 (error), 2 (error), 3 (pass)
        assert len(step.vectors) == 3
        assert step.vectors[0].attempt == 1
        assert step.vectors[0].outcome == Outcome.ERROR
        assert step.vectors[2].attempt == 3
        assert step.vectors[2].outcome == Outcome.PASS

    def test_decorator_with_explicit_harness(self):
        """Test passing harness via kwargs."""
        harness = TestHarness(
            config={"vectors": [{"x": 1}, {"x": 2}]},
            step_name="custom_step",
        )

        @litmus_test(raise_on_fail=False)
        def test_with_harness(vector, harness=None):
            return vector["x"]

        step = test_with_harness(harness=harness)

        assert len(step.vectors) == 2


class TestLitmusTestDecoratorWithInstruments:
    """Tests for @litmus_test decorator with instrument-like fixtures."""

    def test_decorator_with_extra_args(self):
        """Test decorator with additional arguments (simulating fixtures)."""
        config = {
            "vectors": [{"voltage": 3.3}]
        }

        class MockDMM:
            def measure(self):
                return 3.28

        @litmus_test(config=config, raise_on_fail=False)
        def test_with_dmm(vector, dmm):
            return dmm.measure()

        mock_dmm = MockDMM()
        step = test_with_dmm(dmm=mock_dmm)

        assert step.vectors[0].measurements[0].value == Decimal("3.28")

    def test_decorator_changed_detection(self):
        """Test that vector.changed() works in decorated function."""
        config = {
            "vectors": {
                "expand": "nested",
                "loops": [
                    {"name": "temp", "values": [25, 85]},
                    {"name": "volt", "values": [3.3, 5.0]},
                ]
            }
        }

        changes = []

        @litmus_test(config=config, raise_on_fail=False)
        def test_changed(vector):
            changes.append(vector.changed("temp"))
            return vector["volt"]

        test_changed()

        # First vector always shows changed
        assert changes[0] is True
        # Second vector (same temp, diff volt) - temp not changed
        assert changes[1] is False
        # Third vector (diff temp) - temp changed
        assert changes[2] is True
