"""Test execution infrastructure."""

from litmus.execution._state import get_current_logger
from litmus.execution.accessors import InstrumentAccessor
from litmus.execution.harness import Context, TestHarness
from litmus.execution.logger import RunContext, TestRunLogger
from litmus.execution.vectors import (
    Vector,
    expand_product,
    expand_vectors,
    expand_zip,
)
from litmus.execution.verify import LimitFailure, LimitsFn, VerifyFn

__all__ = [
    # Accessors
    "InstrumentAccessor",
    # Harness
    "TestHarness",
    "Context",
    # Vectors
    "Vector",
    "expand_product",
    "expand_vectors",
    "expand_zip",
    # Logger
    "get_current_logger",
    "RunContext",
    "TestRunLogger",
    # Verify fixture types
    "LimitFailure",
    "LimitsFn",
    "VerifyFn",
]
