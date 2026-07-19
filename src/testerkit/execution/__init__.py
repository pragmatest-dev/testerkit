"""Test execution infrastructure.

Import directly from the submodule that owns the type:

    from testerkit.execution.harness import TestHarness, Context
    from testerkit.execution.run_scope import RunScope, RunContext
    from testerkit.execution.vectors import Vector, expand_vectors
    from testerkit.execution.verify import LimitFailure, VerifyFn
    from testerkit.execution.accessors import InstrumentAccessor
    from testerkit.execution._state import get_current_run_scope
"""
