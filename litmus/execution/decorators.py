"""Measurement decorators for test functions."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from functools import wraps
from typing import TYPE_CHECKING, Any

from litmus.data.models import Measurement, Outcome

if TYPE_CHECKING:
    from litmus.config.models import Limit, MeasurementLimitConfig, RetryConfig
    from litmus.execution.harness import TestHarness
    from litmus.execution.logger import TestRunLogger
    from litmus.execution.vectors import Vector

# Contextvars for concurrency-safe logger/harness resolution.
_current_logger_var: ContextVar[TestRunLogger | None] = ContextVar(
    "_current_logger", default=None
)
_current_harness_var: ContextVar[TestHarness | None] = ContextVar(
    "_current_harness", default=None
)


def set_current_logger(logger: TestRunLogger | None):
    """Set the current logger for measurement capture."""
    _current_logger_var.set(logger)


def get_current_logger() -> TestRunLogger | None:
    """Get the current logger."""
    return _current_logger_var.get()


def set_current_harness(harness: TestHarness | None):
    """Set the current harness for @litmus_test decorator."""
    _current_harness_var.set(harness)


def get_current_harness() -> TestHarness | None:
    """Get the current harness."""
    return _current_harness_var.get()


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

    _code_identity = {
        "function": func.__name__,
        "module": getattr(func, "__module__", None),
    }

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        step_name = func.__name__
        _logger = get_current_logger()
        if _logger is not None:
            _logger.start_step(step_name, **_code_identity)
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            _logger = get_current_logger()
            if _logger is not None:
                _logger.end_step()

    # Handle async functions
    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            step_name = func.__name__
            _logger = get_current_logger()
            if _logger is not None:
                _logger.start_step(step_name, **_code_identity)
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                _logger = get_current_logger()
                if _logger is not None:
                    _logger.end_step()
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

            # Build measurement
            measurement = Measurement(
                name=name or func.__name__,
                value=float(value) if value is not None else None,
                units=units or (limit.units if limit else None),
                low_limit=limit.low if limit else None,
                high_limit=limit.high if limit else None,
                nominal=limit.nominal if limit else None,
                spec_ref=limit.spec_ref if limit else None,
            )

            # Check limits
            result = measurement.check_limit()

            # Log if logger available
            _logger = get_current_logger()
            if _logger is not None:
                _logger.log_measurement(measurement)

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


def _resolve_test_config(
    fn: Callable[..., Any],
    config: Mapping[str, Any] | None,
    config_file: str | None,
    limits: dict[str, MeasurementLimitConfig | Limit] | None,
    retry: RetryConfig | None,
    raise_on_fail: bool = True,
) -> tuple[
    dict[str, Any],
    dict[str, MeasurementLimitConfig | Limit] | None,
    RetryConfig | None,
    bool,
]:
    """Resolve test config from sequence step, file, or inline decorator.

    Resolution: step config > sequence default > decorator default.
    """
    from pathlib import Path

    from litmus.config.loader import get_test_config
    from litmus.execution.plugin import get_current_step_config

    resolved_config: dict[str, Any] = {}
    resolved_limits = limits
    resolved_retry = retry
    resolved_raise_on_fail = raise_on_fail

    _step_cfg = get_current_step_config()
    if _step_cfg:
        resolved_config = dict(_step_cfg)
        if "limits" in _step_cfg:
            resolved_limits = _step_cfg["limits"]
        if _step_cfg.get("retry"):
            from litmus.config.models import RetryConfig as _RC
            r = _step_cfg["retry"]
            resolved_retry = r if isinstance(r, _RC) else _RC.model_validate(r)
        if "raise_on_fail" in _step_cfg:
            resolved_raise_on_fail = bool(_step_cfg["raise_on_fail"])
    else:
        test_file = Path(inspect.getfile(fn))
        file_config = None

        if config_file:
            from litmus.config.loader import load_test_config

            config_path = test_file.parent / config_file
            if config_path.exists():
                all_configs = load_test_config(config_path)
                file_config = all_configs.get(fn.__name__)
        else:
            file_config = get_test_config(fn.__name__, test_file)

        if file_config:
            resolved_config = dict(file_config)
            if "limits" in file_config and resolved_limits is None:
                resolved_limits = file_config["limits"]
            if "retry" in file_config and resolved_retry is None:
                resolved_retry = file_config["retry"]

        if config:
            resolved_config.update(config)

    return resolved_config, resolved_limits, resolved_retry, resolved_raise_on_fail


def _resolve_instruments(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Resolve instrument instances from kwargs or plugin state."""
    from litmus.execution.plugin import get_active_instruments, get_instrument_records

    instruments_fixture = kwargs.get("instruments")

    if instruments_fixture is None:
        _ai = get_active_instruments()
        instruments_fixture = _ai if _ai else {}

    if not instruments_fixture:
        instrument_names = list(get_instrument_records().keys())
        extracted = {}
        for name in instrument_names:
            if name in kwargs and kwargs[name] is not None:
                extracted[name] = kwargs[name]
        if extracted:
            instruments_fixture = extracted

    return instruments_fixture


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

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from litmus.execution.harness import TestHarness

            # Get harness from kwargs, current harness, or create new one
            resolved_raise_on_fail = raise_on_fail  # decorator default
            harness = kwargs.pop("harness", None)
            if harness is None:
                harness = get_current_harness()
            if harness is None:
                (
                    resolved_config, resolved_limits,
                    resolved_retry, resolved_raise_on_fail,
                ) = _resolve_test_config(
                    fn, config, config_file, limits, retry, raise_on_fail,
                )
                instruments_fixture = _resolve_instruments(kwargs)

                # Set per-step instrument arrays on the logger
                _logger = get_current_logger()
                if _logger is not None:
                    from litmus.execution.plugin import get_instrument_records

                    step_roles = [
                        name
                        for name in get_instrument_records()
                        if name in kwargs and kwargs[name] is not None
                    ]
                    if step_roles:
                        _logger.set_step_instruments(step_roles)

                # Detect mock mode by checking if instruments have set_mock_value
                using_mocks = any(
                    hasattr(inst, "set_mock_value")
                    for inst in (instruments_fixture or {}).values()
                )

                # Get spec_context for spec-driven limit resolution (ref:)
                from litmus.execution.plugin import get_active_spec_context

                spec_ctx = kwargs.get("spec_context") or get_active_spec_context()

                from litmus.execution.plugin import get_channel_store

                harness = TestHarness(
                    config=resolved_config,
                    step_name=fn.__name__,
                    retry=resolved_retry,
                    limits=resolved_limits,
                    logger=_logger,
                    instruments=instruments_fixture,
                    mock_instruments=using_mocks,
                    spec_context=spec_ctx,
                    channel_store=get_channel_store(),
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

            # Check for failures (step > sequence > decorator)
            if resolved_raise_on_fail:
                for tv in step.vectors:
                    if tv.outcome == Outcome.FAIL:
                        failed_measurements = [
                            m for m in tv.measurements if m.outcome == Outcome.FAIL
                        ]
                        if failed_measurements:
                            m = failed_measurements[0]
                            if m.name == "assert":
                                # From a user assert statement
                                raise AssertionError(
                                    tv.error_message or "assertion failed"
                                )
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
