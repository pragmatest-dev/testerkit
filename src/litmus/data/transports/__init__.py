"""Pluggable transports for shipping result files to remote destinations.

Extend via entry points in pyproject.toml::

    [project.entry-points."litmus.transports"]
    minio = "my_package.transports:MinioTransport"
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points as _entry_points

# Import built-in transports (triggers __init_subclass__ registration)
import litmus.data.transports.file_transport  # noqa: F401
from litmus.data.transports._base import Transport, get_transport, list_transports

logger = logging.getLogger(__name__)

# Optional transports
try:
    import litmus.data.transports.s3_transport  # noqa: F401
except ImportError:
    pass
try:
    import litmus.data.transports.azure_transport  # noqa: F401
except ImportError:
    pass
try:
    import litmus.data.transports.gcs_transport  # noqa: F401
except ImportError:
    pass

# Load third-party transport plugins via entry points
for _ep in _entry_points(group="litmus.transports"):
    try:
        _ep.load()
    except Exception as _exc:
        logger.debug("Transport plugin %s failed to load: %s", _ep.name, _exc)

__all__ = [
    "Transport",
    "get_transport",
    "list_transports",
]
