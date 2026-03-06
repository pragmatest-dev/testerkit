"""Test execution infrastructure."""

from litmus.execution.accessors import InstrumentAccessor
from litmus.execution.decorators import (
    get_current_harness,
    get_current_logger,
    litmus_test,
    measure,
    set_current_harness,
    set_current_logger,
)
from litmus.execution.harness import Context, TestHarness, VectorContext
from litmus.execution.logger import RunContext, TestRunLogger

# Plugin is only available when pytest is installed (dev dependency)
try:
    from litmus.execution.plugin import get_step_outcomes
except ImportError:
    def get_step_outcomes() -> dict[str, bool]:
        return {}
from litmus.execution.vectors import (
    Vector,
    expand_list,
    expand_nested,
    expand_product,
    expand_range,
    expand_vectors,
    expand_zip,
)

__all__ = [
    # Accessors
    "InstrumentAccessor",
    # Harness
    "TestHarness",
    "Context",
    "VectorContext",  # Deprecated alias for Context
    # Vectors
    "Vector",
    "expand_list",
    "expand_nested",
    "expand_product",
    "expand_range",
    "expand_vectors",
    "expand_zip",
    # Decorators
    "get_current_harness",
    "get_current_logger",
    "litmus_test",
    "measure",
    "set_current_harness",
    "set_current_logger",
    # Logger
    "RunContext",
    "TestRunLogger",
    # Plugin
    "get_step_outcomes",
]
