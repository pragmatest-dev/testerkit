"""Test execution infrastructure."""

from litmus.execution.decorators import (
    get_current_harness,
    get_current_logger,
    litmus_test,
    measure,
    set_current_harness,
    set_current_logger,
)
from litmus.execution.harness import TestHarness
from litmus.execution.logger import RunContext, TestRunLogger

# Plugin is only available when pytest is installed (dev dependency)
try:
    from litmus.execution.plugin import STEP_OUTCOMES
except ImportError:
    STEP_OUTCOMES = {}
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
    # Harness
    "TestHarness",
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
    "STEP_OUTCOMES",
]
