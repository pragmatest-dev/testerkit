"""Litmus - Hardware test platform for the AI-assisted era.

Import directly from the submodule that owns the type:

    from litmus.execution.harness import TestHarness
    from litmus.client import LitmusClient
    from litmus.connect import connect

Inline list-builders for ``litmus_sweeps``:

    from litmus.expand import linspace, arange, logspace, geomspace, repeat
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from litmus.expand import arange, geomspace, linspace, logspace, repeat
from litmus.models.test_config import Limit

try:
    # Single source of truth — read from the installed wheel's metadata
    # so __version__ tracks pyproject.toml automatically. Editable
    # installs (`uv pip install -e .`) populate metadata too.
    __version__ = _pkg_version("litmus-test")
except PackageNotFoundError:
    # Imported from a source tree without `pip install`; safe fallback.
    __version__ = "0.0.0+unknown"

__all__ = [
    "__version__",
    "Limit",
    "arange",
    "geomspace",
    "linspace",
    "logspace",
    "repeat",
]
