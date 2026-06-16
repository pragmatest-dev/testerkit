"""Test execution infrastructure.

Import directly from the submodule that owns the type:

    from litmus.execution.harness import TestHarness, Context
    from litmus.execution.logger import RunScope, RunContext
    from litmus.execution.vectors import Vector, expand_vectors
    from litmus.execution.verify import LimitFailure, VerifyFn
    from litmus.execution.accessors import InstrumentAccessor
    from litmus.execution._state import get_current_run_scope
"""
