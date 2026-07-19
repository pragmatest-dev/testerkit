"""TesterKit - Hardware test platform for the AI-assisted era.

User-facing surface. Everything in this module is part of the public
API contract that test writers, interactive users, custom UI builders,
and data-mover scripts should reach for.

Three import shapes — pick the shape that matches your use:

**Top-level names** — frequently-typed surfaces, available directly::

    from testerkit import (
        connect, verify, observe, stream,
        Limit, Waveform, XYData, Outcome,
        arange, linspace, logspace, geomspace, repeat,
        Mock, TesterKitClient,
    )

**Grouped submodules** — related surfaces under a single namespace::

    import testerkit.channels   # channels.write, channels.stream
    import testerkit.files      # files.write, files.stream
    import testerkit.queries    # queries.RunsQuery, queries.MeasurementsQuery, ...

**Deep paths** — internals for TesterKit contributors only. If you find
yourself reaching for one of these in a test, example, doc, or
external script, file an issue — the user-facing surface is missing
a re-export.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from testerkit.client import TesterKitClient
from testerkit.connect import connect
from testerkit.data.models import Outcome, Waveform, XYData
from testerkit.expand import arange, geomspace, linspace, logspace, repeat
from testerkit.instruments.mocks import Mock
from testerkit.models.test_config import Limit
from testerkit.verbs import measure, observe, stream, verify

try:
    # Single source of truth — read from the installed wheel's metadata
    # so __version__ tracks pyproject.toml automatically. Editable
    # installs (`uv pip install -e .`) populate metadata too.
    __version__ = _pkg_version("testerkit")
except PackageNotFoundError:
    # Imported from a source tree without `pip install`; safe fallback.
    __version__ = "0.0.0+unknown"

__all__ = [
    "__version__",
    "Limit",
    "TesterKitClient",
    "Mock",
    "Outcome",
    "Waveform",
    "XYData",
    "arange",
    "connect",
    "geomspace",
    "linspace",
    "logspace",
    "measure",
    "observe",
    "repeat",
    "stream",
    "verify",
]
