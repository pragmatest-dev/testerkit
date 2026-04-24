"""Litmus - Hardware test platform for the AI-assisted era.

The top-level ``litmus`` namespace re-exports the most commonly used entry
points:

- :data:`__version__` — installed package version
- :class:`LitmusClient` — programmatic client for scripts and notebooks
- :func:`connect` — session-scoped context manager for interactive work
- :class:`TestHarness` — direct harness construction for advanced cases

Tests themselves are plain pytest functions that consume the
``context`` / ``verify`` / ``logger`` / ``spec`` fixtures provided by
the Litmus pytest plugin — see ``docs/reference/pytest-native.md``.

Every other public API lives under a sub-namespace; see
``docs/audits/public-api.md`` for the stable surface.
"""

from litmus.client import LitmusClient
from litmus.connect import connect
from litmus.execution import TestHarness

__version__ = "0.1.0"

__all__ = [
    "LitmusClient",
    "TestHarness",
    "__version__",
    "connect",
]
