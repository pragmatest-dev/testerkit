"""Litmus - Hardware test platform for the AI-assisted era.

Import directly from the submodule that owns the type:

    from litmus.execution.harness import TestHarness
    from litmus.client import LitmusClient
    from litmus.connect import connect

Inline list-builders for ``litmus_sweeps``:

    from litmus.expand import linspace, arange, logspace, geomspace, repeat
"""

from litmus.expand import arange, geomspace, linspace, logspace, repeat
from litmus.models.test_config import Limit

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Limit",
    "arange",
    "geomspace",
    "linspace",
    "logspace",
    "repeat",
]
