"""Litmus - Hardware test platform for the AI-assisted era.

The top-level ``litmus`` namespace re-exports the most commonly used entry
points:

- :data:`__version__` — installed package version
- :class:`LitmusClient` — programmatic client for scripts and notebooks
- :func:`connect` — session-scoped context manager for interactive work
- :class:`TestHarness` — direct harness construction for advanced cases

Inline list-builders for ``litmus_sweeps`` (Python counterparts to the
YAML range expanders) — give you IDE autocomplete + signature help:

- :func:`linspace`, :func:`arange`, :func:`logspace`, :func:`geomspace`,
  :func:`repeat` — produce lists for one sweep axis

For zipped (paired) axes use a multi-key dict — keys pair together,
one value-list each: ``@pytest.mark.litmus_sweeps([{"vin": [3, 4], "vout": [5, 6]}])``.

Tests themselves are plain pytest functions that consume the
``context`` / ``verify`` / ``logger`` / ``spec`` fixtures provided by
the Litmus pytest plugin — see ``docs/reference/pytest-native.md``.

Every other public API lives under a sub-namespace; see
``docs/audits/public-api.md`` for the stable surface.
"""

from litmus.client import LitmusClient
from litmus.connect import connect
from litmus.execution import TestHarness
from litmus.expand import arange, geomspace, linspace, logspace, repeat

__version__ = "0.1.0"

__all__ = [
    "LitmusClient",
    "TestHarness",
    "__version__",
    "arange",
    "connect",
    "geomspace",
    "linspace",
    "logspace",
    "repeat",
]
