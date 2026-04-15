"""
Test Architect Examples: Advanced Patterns
==========================================

This file demonstrates ADVANCED patterns for test architects who need:
- Fine-grained control over test execution
- Custom measurement functions with @measure
- Non-measurement step tracking with @litmus_step
- Direct TestHarness usage for maximum flexibility
- Spec-driven limit derivation

These patterns are for complex scenarios where @litmus_test is too magical.

PATTERNS DEMONSTRATED:
- Pattern A: @measure decorator for reusable measurement functions
- Pattern B: @litmus_step for non-measurement operations
- Pattern C: TestHarness direct usage with explicit control
- Pattern D: Spec-driven testing with SpecContext
- Pattern E: Custom retry and prompt handling

Run with:
    cd demo
    pytest tests/test_architect.py --station=demo_station_001 --mock-instruments -v
"""

from litmus.data.models import Outcome
from litmus.execution.decorators import litmus_step, measure
from litmus.execution.harness import TestHarness
from litmus.models.config import Limit

# =============================================================================
# Pattern A: @measure Decorator
#
# Use @measure to create reusable measurement functions that:
# - Execute measurement code
# - Apply limits and check pass/fail
# - Log to the current test logger
# - Return a Measurement object
# =============================================================================


@measure(
    name="output_voltage",
    limit=Limit(low=3.2, high=3.4, nominal=3.3, units="V"),
    units="V",
    raise_on_fail=False,  # Don't raise, let caller handle
)
def measure_output_voltage(dmm):
    """Reusable output voltage measurement with embedded limit.

    This function can be called from multiple tests.
    The @measure decorator handles logging and limit checking.
    """
    return dmm.measure_dc_voltage()


@measure(
    name="input_current",
    limit=Limit(low=0.0, high=1.0, nominal=0.5, units="A"),
)
def measure_input_current(psu):
    """Reusable input current measurement."""
    return psu.measure_current()


class TestMeasureDecorator:
    """Tests using @measure decorated functions."""

    def test_reusable_measurement(self, psu, dmm, litmus_logger):
        """Use @measure decorated function for measurement.

        The @measure decorator:
        1. Calls the function to get a value
        2. Creates a Measurement with the embedded limit
        3. Logs to litmus_logger automatically
        4. Returns the Measurement object
        """
        psu.set_voltage(5.0)
        psu.enable_output()

        # Call the decorated function - it returns a Measurement
        result = measure_output_voltage(dmm)

        # result is a Measurement object with outcome set
        assert result.outcome in (Outcome.PASS, Outcome.FAIL)
        assert result.name == "output_voltage"
        assert result.units == "V"

    def test_multiple_decorated_measurements(self, psu, dmm, litmus_logger):
        """Call multiple @measure functions in one test."""
        psu.set_voltage(5.0)
        psu.set_current_limit(1.0)
        psu.enable_output()

        # Each decorated function logs its measurement
        voltage = measure_output_voltage(dmm)
        current = measure_input_current(psu)

        # Calculate derived value manually
        power = (voltage.value or 0.0) * (current.value or 0.0)

        # Log derived value manually (not decorated)
        litmus_logger.measure(
            name="output_power",
            value=power,
            limit=Limit(low=0.0, high=5.0, units="W"),
        )


# =============================================================================
# Pattern B: @litmus_step Decorator
#
# Use @litmus_step for operations that should be tracked as steps but
# don't produce measurements (setup, teardown, dialogs, etc.)
# =============================================================================


@litmus_step
def verify_dut_connection(psu, mock_instruments: bool = False):
    """Step that verifies DUT is connected (no measurement).

    @litmus_step tracks this as a test step without requiring
    a return value. Good for:
    - Setup verification
    - Configuration steps
    - Operator confirmations

    In mock mode, the check is skipped since mock
    instruments return static values.
    """
    # In production, this might check continuity or ID
    psu.set_voltage(0.1)
    psu.set_current_limit(0.001)
    psu.enable_output()
    current = float(psu.measure_current())
    psu.disable_output()

    # In mock mode, skip the assertion (mock values are static)
    if not mock_instruments:
        assert current < 0.001, "DUT appears to be shorted!"


@litmus_step
def configure_for_full_load(psu, eload):
    """Configuration step - no measurement returned."""
    psu.set_voltage(5.0)
    psu.set_current_limit(1.5)
    psu.enable_output()

    eload.set_current(0.8)
    eload.enable()


class TestLitmusStep:
    """Tests using @litmus_step decorated functions."""

    def test_with_step_functions(self, psu, dmm, eload, litmus_logger, mock_instruments):
        """Combine @litmus_step with measurements.

        Steps are tracked in the test run even though they don't
        produce measurements.
        """
        # Step 1: Verify connection (tracked as step)
        verify_dut_connection(psu, mock_instruments=mock_instruments)

        # Step 2: Configure (tracked as step)
        configure_for_full_load(psu, eload)

        # Step 3: Signal (logged with limit)
        result = measure_output_voltage(dmm)

        # Cleanup
        eload.disable()

        assert result.outcome == Outcome.PASS


# =============================================================================
# Pattern C: Direct TestHarness Usage
#
# Use TestHarness directly when you need:
# - Explicit vector iteration
# - Custom retry logic
# - Manual prompt control
# - Full access to test execution state
# =============================================================================


class TestDirectHarness:
    """Tests using TestHarness directly for maximum control."""

    def test_explicit_vector_loop(self, psu, dmm, eload, litmus_logger):
        """Manual iteration over vectors with harness.

        This pattern gives you full control over:
        - When vectors execute
        - How measurements are recorded
        - Custom logic between vectors
        """
        # Create harness with inline config
        harness = TestHarness(
            config={
                "vectors": [
                    {"load": 0.1},
                    {"load": 0.4},
                    {"load": 0.8},
                ],
                "limits": {
                    "vout": {
                        "low": 3.2,
                        "high": 3.4,
                        "nominal": 3.3,
                        "units": "V",
                    }
                },
            },
            logger=litmus_logger,
            step_name="explicit_loop_test",
        )

        # Setup once
        psu.set_voltage(5.0)
        psu.set_current_limit(1.5)
        psu.enable_output()

        # Explicit loop - YOU control iteration
        for vector in harness.vectors:
            # Get load from vector
            load = vector["load"]

            # Apply load
            eload.set_current(load)
            eload.enable()

            # Context manager handles retry and logging
            with harness.run_vector(vector):
                vout = float(dmm.measure_dc_voltage())

                # Use harness.measure() for automatic limit resolution
                harness.measure("vout", vout)

            eload.disable()

    def test_with_change_detection(self, psu, dmm, eload, litmus_logger):
        """Use vector.changed() to optimize slow operations.

        Product expansion where outer param changes slowly - only
        reconfigure when it changes.
        """
        harness = TestHarness(
            config={
                "vectors": {
                    "expand": "product",
                    "vin": [4.75, 5.0, 5.5],
                    "load": [0.1, 0.5],
                },
                "limits": {
                    "vout": {"low": 3.1, "high": 3.5, "nominal": 3.3, "units": "V"},
                },
            },
            logger=litmus_logger,
            step_name="change_detection_test",
        )

        for vector in harness.vectors:
            # Only reconfigure PSU when VIN changes
            if vector.changed("vin"):
                psu.set_voltage(vector["vin"])
                psu.set_current_limit(1.5)
                psu.enable_output()

            # Load changes every iteration
            eload.set_current(vector["load"])
            eload.enable()

            with harness.run_vector(vector):
                harness.measure("vout", dmm.measure_dc_voltage())

            eload.disable()

    def test_with_prompts(self, psu, dmm, litmus_logger):
        """Use harness.prompt() for operator interaction.

        Prompts can be formatted with vector parameters.
        """
        harness = TestHarness(
            config={
                "vectors": [
                    {"condition": "no_load"},
                    {"condition": "with_load"},
                ],
            },
            logger=litmus_logger,
            step_name="prompt_test",
            # Custom prompt handler for testing (doesn't block)
            prompt_handler=lambda p: True,  # Auto-confirm
        )

        psu.set_voltage(5.0)
        psu.enable_output()

        for vector in harness.vectors:
            with harness.run_vector(vector):
                # Prompt with formatted message
                harness.prompt(
                    "Please verify DUT is in {condition} state",
                    prompt_type="confirm",
                )

                harness.measure("vout", dmm.measure_dc_voltage())


# =============================================================================
# Pattern D: Spec-Driven Testing with SpecContext
#
# Use SpecContext to derive limits from product specs automatically.
# This eliminates hardcoded limits in tests.
# =============================================================================


class TestSpecDriven:
    """Tests using SpecContext for spec-driven limit derivation."""

    def test_spec_derived_limits(self, psu, dmm, spec_context, litmus_logger):
        """Limits derived automatically from product spec.

        With SpecContext:
        1. Measurement name → characteristic lookup
        2. Vector params → condition matching
        3. Limits derived with guardband
        """
        harness = TestHarness(
            config={
                "vectors": [
                    {"temperature": 25, "load": 0.1},
                    {"temperature": 25, "load": 0.8},
                ],
            },
            logger=litmus_logger,
            step_name="spec_driven_test",
            spec_context=spec_context,  # Enable spec-driven limits
        )

        psu.set_voltage(5.0)
        psu.enable_output()

        for vector in harness.vectors:
            with harness.run_vector(vector):
                # Limit is automatically derived from spec_context
                # based on measurement name "output_voltage" and
                # current vector conditions (temperature, load)
                harness.measure("output_voltage", dmm.measure_dc_voltage())

    def test_explicit_limit_from_spec(self, psu, dmm, spec_context, litmus_logger):
        """Explicitly get limit from spec for custom logic."""
        psu.set_voltage(5.0)
        psu.enable_output()

        # Get limit from spec with conditions
        limit = spec_context.get_limit(
            "output_voltage",
            temperature=25,
            load=0.1,
            guardband_pct=10.0,  # Tighten by 10%
        )

        vout = float(dmm.measure_dc_voltage())

        # Log with explicit limit
        litmus_logger.measure(
            name="output_voltage",
            value=vout,
            limit=limit,
        )

        assert limit.low <= vout <= limit.high


# =============================================================================
# Pattern E: Custom Retry and Error Handling
#
# Override default retry behavior for special scenarios.
# =============================================================================


class TestCustomRetry:
    """Tests with custom retry and error handling."""

    def test_with_custom_retry(self, psu, dmm, litmus_logger):
        """Configure retry at harness level."""
        from litmus.models.config import RetryConfig

        harness = TestHarness(
            config={
                "vectors": [{"vin": 5.0}],
                "limits": {
                    "vout": {"low": 3.2, "high": 3.4, "nominal": 3.3, "units": "V"},
                },
            },
            logger=litmus_logger,
            step_name="custom_retry_test",
            retry=RetryConfig(
                max_attempts=3,
                delay_seconds=0.5,
            ),
        )

        psu.set_voltage(5.0)
        psu.enable_output()

        for vector in harness.vectors:
            with harness.run_vector(vector):
                harness.measure("vout", dmm.measure_dc_voltage())

    def test_run_all_convenience(self, psu, dmm, litmus_logger):
        """Use harness.run_all() for simple cases.

        run_all() handles vector iteration and step creation.
        """
        harness = TestHarness(
            config={
                "vectors": [
                    {"load": 0.1},
                    {"load": 0.5},
                ],
                "limits": {
                    "vout": {"low": 3.2, "high": 3.4, "nominal": 3.3, "units": "V"},
                },
            },
            logger=litmus_logger,
        )

        psu.set_voltage(5.0)
        psu.enable_output()

        # Define what runs for each vector
        def test_func(vector):
            # This runs for each vector
            return dmm.measure_dc_voltage()

        # run_all handles iteration, retry, and logging
        step = harness.run_all(test_func, step_name="run_all_test")

        # Check results
        assert step.outcome in (Outcome.PASS, Outcome.FAIL)
        assert len(step.vectors) == 2
