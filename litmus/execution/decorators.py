"""Measurement decorators for test functions."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from functools import wraps
from typing import TYPE_CHECKING, Any

from litmus.data.models import Measurement, Outcome

if TYPE_CHECKING:
    from litmus.config.models import Limit, MeasurementLimitConfig, RetryConfig
    from litmus.execution.harness import TestHarness
    from litmus.execution.logger import TestRunLogger
    from litmus.execution.vectors import Vector

# Module-level storage for current logger and harness
_current_logger: TestRunLogger | None = None
_current_harness: TestHarness | None = None


def set_current_logger(logger: TestRunLogger | None):
    """Set the current logger for measurement capture."""
    global _current_logger
    _current_logger = logger


def get_current_logger() -> TestRunLogger | None:
    """Get the current logger."""
    return _current_logger


def set_current_harness(harness: TestHarness | None):
    """Set the current harness for @litmus_test decorator."""
    global _current_harness
    _current_harness = harness


def get_current_harness() -> TestHarness | None:
    """Get the current harness."""
    return _current_harness


def litmus_step(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that tracks a test as a step without requiring measurements.

    Use this for tests that don't return measurement values but should still
    be tracked as steps in the test run (e.g., dialog confirmations, setup steps).

    Example:
        @litmus_step
        async def test_confirm_dut_ready():
            response = await dialog_manager.confirm("Is DUT ready?")
            assert response.confirmed
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        step_name = func.__name__
        if _current_logger is not None:
            _current_logger.start_step(step_name)
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            if _current_logger is not None:
                _current_logger.end_step()

    # Handle async functions
    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            step_name = func.__name__
            if _current_logger is not None:
                _current_logger.start_step(step_name)
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                if _current_logger is not None:
                    _current_logger.end_step()
        return async_wrapper

    return wrapper


def measure(
    name: str | None = None,
    limit: Limit | None = None,
    units: str | None = None,
    raise_on_fail: bool = True,
):
    """Decorator that captures measurement, checks limit, logs result.

    Args:
        name: Measurement name (defaults to function name)
        limit: Limit object with low/high bounds
        units: Measurement units (overrides limit.units)
        raise_on_fail: Raise AssertionError if limit check fails

    Returns:
        Decorated function that returns a Measurement object
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Measurement]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Measurement:
            # Execute measurement function
            value = func(*args, **kwargs)

            # Convert to float if needed
            if value is not None and not isinstance(value, float):
                value = float(value)

            # Build measurement
            measurement = Measurement(
                name=name or func.__name__,
                value=value,
                units=units or (limit.units if limit else None),
                low_limit=limit.low if limit else None,
                high_limit=limit.high if limit else None,
                nominal=limit.nominal if limit else None,
                spec_ref=limit.spec_ref if limit else None,
            )

            # Check limits
            result = measurement.check_limit()

            # Log if logger available
            if _current_logger is not None:
                _current_logger.log_measurement(measurement)

            # Raise on failure if configured
            if raise_on_fail and result == Outcome.FAIL:
                raise AssertionError(
                    f"Measurement '{measurement.name}' FAILED: "
                    f"{measurement.value} not in "
                    f"[{measurement.low_limit}, {measurement.high_limit}]"
                )

            return measurement

        return wrapper

    return decorator


def litmus_test(
    func: Callable[..., Any] | None = None,
    *,
    config: Mapping[str, Any] | None = None,
    config_file: str | None = None,
    retry: RetryConfig | None = None,
    limits: dict[str, MeasurementLimitConfig | Limit] | None = None,
    raise_on_fail: bool = True,
) -> Callable[..., None] | Callable[[Callable[..., Any]], Callable[..., None]]:
    """Decorator for vector-based test functions.

    Automatically loops over expanded vectors, handles retries, and logs
    measurements. The decorated function receives a `context` argument
    containing the current parameter values and test context.

    The test function can:
    - Return a single value → logged as measurement with function name
    - Return a dict → logged as multiple named measurements
    - Yield values → streamed as measurements (for progress/arrays)

    Config resolution (in order of precedence):
    1. Inline parameters (config=, retry=, limits=)
    2. config_file parameter (explicit path)
    3. Auto-discovered config.yaml in test file's directory

    Args:
        func: Test function (when used without parentheses).
        config: Test configuration dict with 'vectors', 'retry', 'limits'.
        config_file: Path to YAML config file (relative to test file).
        retry: Override retry configuration.
        limits: Override limit configurations by measurement name.
        raise_on_fail: Raise AssertionError if any measurement fails.

    Returns:
        Decorated function that executes across all vectors.

    Example (return single value):
        @litmus_test
        def test_voltage(context, dmm):
            return dmm.measure_dc_voltage()

    Example (accessing vector parameters):
        @litmus_test
        def test_sweep(context, psu, dmm):
            vin = context.get_in("vin")  # From vectors in config.yaml
            psu.set_voltage(vin)
            return dmm.measure_voltage()

    Example (with observations):
        @litmus_test
        def test_power(context, psu, dmm):
            context.observe("ambient_temp", 24.5)
            return {
                "output_voltage": dmm.measure_voltage(),
                "output_current": dmm.measure_current(),
            }

    Example (with inline config):
        @litmus_test(config={"vectors": {"expand": "product", "v": [1, 2, 3]}})
        def test_sweep(context, dmm):
            v = context.get_in("v")
            return dmm.measure()
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., None]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> None:
            # Import here to avoid circular dependency
            from pathlib import Path

            from litmus.config.loader import get_test_config
            from litmus.execution.harness import TestHarness

            # Get harness from kwargs, current harness, or create new one
            harness = kwargs.pop("harness", None)
            if harness is None:
                harness = _current_harness
            if harness is None:
                # Resolve config: sequence step > inline decorator > config_file > auto-discover
                from litmus.execution.plugin import _CURRENT_STEP_CONFIG

                resolved_config: dict[str, Any] = {}
                resolved_limits = limits
                resolved_retry = retry

                if _CURRENT_STEP_CONFIG:
                    # Sequence step config replaces all other config sources
                    resolved_config = dict(_CURRENT_STEP_CONFIG)
                    if "limits" in _CURRENT_STEP_CONFIG:
                        resolved_limits = _CURRENT_STEP_CONFIG["limits"]
                    if "retry" in _CURRENT_STEP_CONFIG:
                        from litmus.config.models import RetryConfig as _RC
                        r = _CURRENT_STEP_CONFIG["retry"]
                        resolved_retry = r if isinstance(r, _RC) else _RC.model_validate(r)
                else:
                    # No sequence — use inline decorator or file config
                    test_file = Path(inspect.getfile(fn))
                    file_config = None

                    if config_file:
                        from litmus.config.loader import load_test_config

                        config_path = test_file.parent / config_file
                        if config_path.exists():
                            all_configs = load_test_config(config_path)
                            file_config = all_configs.get(fn.__name__)
                    else:
                        # Auto-discover config.yaml
                        file_config = get_test_config(fn.__name__, test_file)

                    # Merge file config (lower precedence)
                    if file_config:
                        resolved_config = dict(file_config)
                        if "limits" in file_config and resolved_limits is None:
                            resolved_limits = file_config["limits"]
                        if "retry" in file_config and resolved_retry is None:
                            resolved_retry = file_config["retry"]

                    # Merge inline config (higher precedence)
                    if config:
                        resolved_config.update(config)

                # Extract instruments for mock configuration per vector
                # First try kwargs (if test explicitly includes these fixtures)
                instruments_fixture = kwargs.get("instruments")

                # If not in kwargs, try to get from the plugin's active instruments
                if instruments_fixture is None:
                    from litmus.execution.plugin import _ACTIVE_INSTRUMENTS
                    instruments_fixture = _ACTIVE_INSTRUMENTS if _ACTIVE_INSTRUMENTS else {}

                # If still empty, extract individual instrument fixtures from kwargs
                # This handles cases where tests use psu, dmm, eload fixtures directly
                if not instruments_fixture:
                    from litmus.execution.plugin import _INSTRUMENT_RECORDS

                    instrument_names = list(_INSTRUMENT_RECORDS.keys())
                    extracted = {}
                    for name in instrument_names:
                        if name in kwargs and kwargs[name] is not None:
                            extracted[name] = kwargs[name]
                    if extracted:
                        instruments_fixture = extracted

                # Set per-step instrument arrays on the logger
                # Detect which instrument roles are used by this test function
                if _current_logger is not None:
                    from litmus.execution.plugin import _INSTRUMENT_RECORDS

                    step_roles = [
                        name
                        for name in _INSTRUMENT_RECORDS
                        if name in kwargs and kwargs[name] is not None
                    ]
                    if step_roles:
                        _current_logger.set_step_instruments(step_roles)

                # Detect mock mode by checking if instruments have set_mock_value
                using_mocks = False
                if instruments_fixture:
                    for inst in instruments_fixture.values():
                        if hasattr(inst, "set_mock_value"):
                            using_mocks = True
                            break

                # Get spec_context for spec-driven limit resolution (ref:)
                from litmus.execution.plugin import _ACTIVE_SPEC_CONTEXT

                spec_ctx = kwargs.get("spec_context") or _ACTIVE_SPEC_CONTEXT

                # Create harness from resolved config
                harness = TestHarness(
                    config=resolved_config,
                    step_name=fn.__name__,
                    retry=resolved_retry,
                    limits=resolved_limits,
                    logger=_current_logger,
                    instruments=instruments_fixture,
                    mock_instruments=using_mocks,
                    spec_context=spec_ctx,
                )

            # Strip context sentinel from args/kwargs - we inject the real context
            from litmus.execution.plugin import _PYTEST_CONTEXT_SENTINEL

            fixture_args = tuple(
                arg for arg in args if arg is not _PYTEST_CONTEXT_SENTINEL
            )
            kwargs.pop("context", None)

            # Check if function expects 'context' parameter
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())

            # Run the test function across all vectors
            # Context is injected as first param (or via kwargs if not first)
            # Note: vector param is required by run_all() signature but we use
            # harness.context instead (which is set up from the vector by run_vector)
            def test_fn(_vector: Vector) -> Any:
                if params and params[0] == "context":
                    # context is first positional arg
                    result = fn(harness.context, *fixture_args, **kwargs)
                else:
                    # Inject context into kwargs
                    call_kwargs = dict(kwargs)
                    call_kwargs["context"] = harness.context
                    result = fn(*fixture_args, **call_kwargs)

                return result

            step = harness.run_all(test_fn, step_name=fn.__name__)

            # Check for failures
            if raise_on_fail:
                for tv in step.vectors:
                    if tv.outcome == Outcome.FAIL:
                        failed_measurements = [
                            m for m in tv.measurements if m.outcome == Outcome.FAIL
                        ]
                        if failed_measurements:
                            m = failed_measurements[0]
                            raise AssertionError(
                                f"Measurement '{m.name}' FAILED at vector {tv.index}: "
                                f"{m.value} not in [{m.low_limit}, {m.high_limit}]"
                            )
                        raise AssertionError(
                            f"Vector {tv.index} FAILED: {tv.error_message or 'unknown error'}"
                        )
                    elif tv.outcome == Outcome.ERROR:
                        raise RuntimeError(
                            f"Vector {tv.index} ERROR: {tv.error_message or 'unknown error'}"
                        )

            return step

        return wrapper

    # Handle both @litmus_test and @litmus_test(...) syntax
    if func is not None:
        return decorator(func)
    return decorator
