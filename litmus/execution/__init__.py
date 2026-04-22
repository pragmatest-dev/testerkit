"""Test execution infrastructure."""

from litmus.execution.accessors import InstrumentAccessor
from litmus.execution.decorators import (
    get_current_harness,
    get_current_logger,
    litmus_step,
    litmus_test,
    measure,
)
from litmus.execution.harness import Context, TestHarness
from litmus.execution.logger import RunContext, TestRunLogger

# Plugin is only available when pytest is installed (dev dependency). The
# fallback path is taken only when pytest isn't on the import path; pyright
# analyses both branches and would otherwise flag the LitmusSequence
# re-definition as a type clash.
try:
    from litmus.execution.plugin import (
        LitmusSequence,  # pyright: ignore[reportAssignmentType]
    )
except ImportError:

    class LitmusSequence:  # type: ignore[no-redef]
        """Fallback when pytest is not installed."""


from litmus.execution.vectors import (
    Vector,
    expand_product,
    expand_vectors,
    expand_zip,
)

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
    # Decorators
    "get_current_harness",
    "get_current_logger",
    "litmus_step",
    "litmus_test",
    "measure",
    # Logger
    "RunContext",
    "TestRunLogger",
    # Plugin
    "LitmusSequence",
]
