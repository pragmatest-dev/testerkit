"""Instrument discovery and identification.

Pluggable protocol registry for scanning and identifying instruments.
Built-in protocols: VISA, NI, Serial, LXI.

Extend via entry points in pyproject.toml::

    [project.entry-points."litmus.discovery"]
    myproto = "my_package.discovery:MyDiscovery"
"""

import logging

import litmus.instruments.discovery.serial  # noqa: F401
import litmus.instruments.discovery.visa  # noqa: F401
from litmus.instruments.discovery._base import (
    DiscoveryProtocol,
    check_import,
    discover,
    discover_and_identify,
    get_info,
    get_protocol,
    list_protocols,
    parse_idn,
)
from litmus.instruments.discovery.visa import discover_visa, get_info_visa
from litmus.models.instrument import InstrumentInfo

# Optional built-in protocols
try:
    import litmus.instruments.discovery.ni  # noqa: F401
except ImportError:
    pass
try:
    import litmus.instruments.discovery.lxi  # noqa: F401
except ImportError:
    pass

# Load third-party plugins via entry points
from importlib.metadata import entry_points as _entry_points

logger = logging.getLogger(__name__)

for _ep in _entry_points(group="litmus.discovery"):
    try:
        _ep.load()
    except Exception as _exc:
        logger.debug("Discovery plugin %s failed to load: %s", _ep.name, _exc)

__all__ = [
    "DiscoveryProtocol",
    "InstrumentInfo",
    "check_import",
    "discover",
    "discover_and_identify",
    "discover_visa",
    "get_info",
    "get_info_visa",
    "get_protocol",
    "list_protocols",
    "parse_idn",
]
