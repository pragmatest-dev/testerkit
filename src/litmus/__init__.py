"""Litmus - Hardware test platform for the AI-assisted era.

User-facing surface. Everything in this module is part of the public
API contract that test writers, interactive users, custom UI builders,
and data-mover scripts should reach for.

Three import shapes — pick the shape that matches your use:

**Top-level names** — frequently-typed surfaces, available directly::

    from litmus import (
        connect, verify, observe, stream,
        Limit, Waveform, XYData, Outcome,
        arange, linspace, logspace, geomspace, repeat,
        Mock, LitmusClient,
    )

**Grouped submodules** — related surfaces under a single namespace::

    import litmus.channels   # channels.write, channels.stream
    import litmus.files      # files.write, files.stream
    import litmus.queries    # queries.RunsQuery, queries.MeasurementsQuery, ...

**Deep paths** — internals for Litmus contributors only. If you find
yourself reaching for one of these in a test, example, doc, or
external script, file an issue — the user-facing surface is missing
a re-export.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from litmus.client import LitmusClient
from litmus.connect import connect
from litmus.data.models import Outcome, Waveform, XYData
from litmus.expand import arange, geomspace, linspace, logspace, repeat
from litmus.instruments.mocks import Mock
from litmus.models.test_config import Limit
from litmus.verbs import observe, stream, verify

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
    "LitmusClient",
    "Mock",
    "Outcome",
    "Waveform",
    "XYData",
    "arange",
    "connect",
    "geomspace",
    "linspace",
    "logspace",
    "observe",
    "repeat",
    "stream",
    "verify",
]
