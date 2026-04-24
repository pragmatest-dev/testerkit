"""Shared contextvars for step/vector resolution across execution modules.

Both logger.py and harness.py need to read/write current step and vector
state. These vars live here so neither module imports private symbols
from the other.
"""

from contextvars import ContextVar
from typing import Any

current_step_var: ContextVar[Any] = ContextVar("current_step", default=None)
current_vector_var: ContextVar[Any] = ContextVar("current_vector", default=None)
