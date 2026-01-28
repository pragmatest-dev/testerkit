"""Measurement decorators for test functions."""

from collections.abc import Callable
from decimal import Decimal
from functools import wraps
from typing import TYPE_CHECKING, Any

from litmus.data.models import Measurement, PassFail

if TYPE_CHECKING:
    from litmus.config.models import Limit
    from litmus.execution.logger import TestRunLogger

# Module-level storage for current logger
_current_logger: "TestRunLogger | None" = None


def set_current_logger(logger: "TestRunLogger | None"):
    """Set the current logger for measurement capture."""
    global _current_logger
    _current_logger = logger


def get_current_logger() -> "TestRunLogger | None":
    """Get the current logger."""
    return _current_logger


def measure(
    name: str | None = None,
    limit: "Limit | None" = None,
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

            # Convert to Decimal if needed
            if value is not None and not isinstance(value, Decimal):
                value = Decimal(str(value))

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
            if raise_on_fail and result == PassFail.FAIL:
                raise AssertionError(
                    f"Measurement '{measurement.name}' FAILED: "
                    f"{measurement.value} not in "
                    f"[{measurement.low_limit}, {measurement.high_limit}]"
                )

            return measurement

        return wrapper

    return decorator
