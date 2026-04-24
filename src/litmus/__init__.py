"""Litmus - Hardware test platform for the AI-assisted era.

The top-level ``litmus`` namespace re-exports the most commonly used entry
points:

- :data:`__version__` — installed package version
- :class:`LitmusClient` — programmatic client for scripts and notebooks
- :func:`connect` — session-scoped context manager for interactive work
- :func:`litmus_test` — primary decorator for pytest-native tests
- :class:`TestHarness` — direct harness construction for advanced cases

Every other public API lives under a sub-namespace; see
``docs/audits/public-api.md`` for the stable surface.
"""

from litmus.client import LitmusClient
from litmus.connect import connect
from litmus.execution import TestHarness, litmus_test

__version__ = "0.1.0"

__all__ = [
    "LitmusClient",
    "TestHarness",
    "__version__",
    "connect",
    "litmus_test",
]
