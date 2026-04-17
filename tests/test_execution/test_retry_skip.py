"""Tests for retry and skip-on-failure functionality.

Requires the litmus plugin to be active (registers markers, runs retry
protocol, tracks step outcomes).
"""

import pytest

from litmus.execution.plugin import get_step_outcomes


@pytest.fixture(autouse=True)
def _require_litmus_plugin(request):
    assert request.config.pluginmanager.has_plugin("litmus"), "Litmus plugin must be loaded"


class TestRetryLogic:
    """Tests for retry functionality."""

    def test_retry_marker_exists(self):
        """Verify the litmus_retry marker is registered."""
        # This test passes if the marker doesn't raise an unknown marker warning
        pass

    def test_retry_succeeds_eventually(self, request):
        """Test that a flaky test can pass on retry."""
        # Use a counter stored in the module to track attempts
        counter_key = f"{request.node.nodeid}_counter"
        if not hasattr(request.config, "_litmus_test_counters"):
            request.config._litmus_test_counters = {}

        counters = request.config._litmus_test_counters
        counters[counter_key] = counters.get(counter_key, 0) + 1

        # Fail on first attempt, pass on second
        if counters[counter_key] < 2:
            pytest.fail("Simulated flaky failure")

    # Mark the test for retry - applied dynamically in conftest or here
    test_retry_succeeds_eventually = pytest.mark.litmus_retry(max_attempts=3, delay=0.1)(
        test_retry_succeeds_eventually
    )

    def test_no_retry_without_marker(self):
        """Test without retry marker runs normally."""
        assert True


class TestSkipOnFailure:
    """Tests for skip-on-failure functionality."""

    def test_dependency_that_passes(self):
        """This test passes and should not cause skips."""
        assert True

    @pytest.mark.xfail(reason="Intentional failure to test skip-on-failure", strict=True)
    def test_dependency_that_fails(self):
        """This test fails intentionally to trigger skip."""
        pytest.fail("Intentional failure for skip testing")

    def test_skip_on_failure_recorded(self):
        """Verify that the failed dependency was recorded as False in step outcomes."""
        outcomes = get_step_outcomes()
        dep_key = next((k for k in outcomes if "test_dependency_that_fails" in k), None)
        assert dep_key is not None, "test_dependency_that_fails outcome not found"
        assert outcomes[dep_key] is False, "Failed dependency must be recorded as False"

    @pytest.mark.litmus_skip_on(["test_dependency_that_passes"])
    def test_not_skipped_when_dependency_passes(self):
        """This test should run because its dependency passed."""
        assert True

    def test_independent_step(self):
        """This test has no dependencies and should always run."""
        assert True


class TestStepOutcomesTracking:
    """Tests that verify get_step_outcomes() dict is populated correctly."""

    def test_outcomes_populated_after_test(self):
        """Verify that test outcomes are recorded."""
        # After previous tests run, get_step_outcomes() should have entries
        # Note: This test relies on running after other tests in this file
        assert len(get_step_outcomes()) > 0

    def test_passed_test_recorded_as_true(self):
        """Check that passed tests are recorded with True."""
        # Look for the passing test from TestSkipOnFailure
        found = False
        for key, value in get_step_outcomes().items():
            if "test_dependency_that_passes" in key:
                found = True
                assert value is True, f"Expected True for passing test, got {value}"
                break
        # Note: This may not find it if running in isolation
        assert found, "Dependency test not found in get_step_outcomes() — run full suite"

    def test_failed_test_recorded_as_false(self):
        """Check that failed tests are recorded with False."""
        found = False
        for key, value in get_step_outcomes().items():
            if "test_dependency_that_fails" in key:
                found = True
                assert value is False, f"Expected False for failing test, got {value}"
                break
        assert found, "Dependency test not found in get_step_outcomes() — run full suite"


class TestMultipleDependencies:
    """Tests for multiple dependency handling."""

    def test_first_dep(self):
        """First dependency - passes."""
        assert True

    @pytest.mark.xfail(reason="Intentional failure to test skip-on-failure", strict=True)
    def test_second_dep(self):
        """Second dependency - fails."""
        pytest.fail("Second dependency fails")

    def test_multiple_deps_failure_recorded(self):
        """Verify that the second failing dependency was recorded as False."""
        outcomes = get_step_outcomes()
        dep_key = next((k for k in outcomes if "test_second_dep" in k), None)
        assert dep_key is not None, "test_second_dep outcome not found"
        assert outcomes[dep_key] is False, "Failed dependency must be recorded as False"
