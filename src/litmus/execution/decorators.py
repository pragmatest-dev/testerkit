"""Measurement decorator for test functions.

The ``@measure`` decorator is the one remaining public decorator — it
wraps a callable that returns a scalar and logs the result as a
measurement against an optional ``Limit``. See
``litmus.pytest_plugin`` for the pytest-native fixtures (``context``,
``verify``, ``logger``, ``spec``) that drive modern Litmus tests.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from typing import TYPE_CHECKING, Any

from litmus.data.models import Measurement, Outcome
from litmus.execution.logger import _stringify_comparator

if TYPE_CHECKING:
    from litmus.execution.logger import TestRunLogger
    from litmus.models.config import Limit

_current_logger_var: ContextVar[TestRunLogger | None] = ContextVar("_current_logger", default=None)


def set_current_logger(logger: TestRunLogger | None):
    """Set the current logger for measurement capture."""
    _current_logger_var.set(logger)


def get_current_logger() -> TestRunLogger | None:
    """Get the current logger."""
    return _current_logger_var.get()


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
            value = func(*args, **kwargs)

            cmp_str = _stringify_comparator(getattr(limit, "comparator", None) if limit else None)

            measurement = Measurement(
                name=name or func.__name__,
                value=float(value) if value is not None else None,
                units=units or (limit.units if limit else None),
                low_limit=limit.low if limit else None,
                high_limit=limit.high if limit else None,
                nominal=limit.nominal if limit else None,
                comparator=cmp_str,
                spec_ref=limit.spec_ref if limit else None,
            )

            result = measurement.check_limit()

            _logger = get_current_logger()
            if _logger is not None:
                _logger.log_measurement(measurement)

            if raise_on_fail and result == Outcome.FAIL:
                raise AssertionError(
                    f"Measurement '{measurement.name}' FAILED: "
                    f"{measurement.value} not in "
                    f"[{measurement.low_limit}, {measurement.high_limit}]"
                )

            return measurement

        return wrapper

    return decorator
