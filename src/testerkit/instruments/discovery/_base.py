"""Discovery protocol interface, registry, and orchestrator.

Built-in protocols live in sibling modules (visa.py, ni.py, serial.py,
lxi.py). Third-party plugins are loaded via entry points.

Extending discovery
~~~~~~~~~~~~~~~~~~~
Subclass ``DiscoveryProtocol``, set ``name``, and declare an entry point::

    # my_package/discovery.py
    from testerkit.instruments.discovery import DiscoveryProtocol
    from testerkit.models.instrument import InstrumentInfo

    class SrsDiscovery(DiscoveryProtocol):
        name = "srs"
        def discover(self) -> list[str]: ...
        def get_info(self, resource: str) -> InstrumentInfo | None: ...

    # pyproject.toml
    # [project.entry-points."testerkit.discovery"]
    # srs = "my_package.discovery:SrsDiscovery"
"""

from __future__ import annotations

import abc
import logging
from typing import Any

from testerkit.models.instrument import InstrumentInfo

logger = logging.getLogger(__name__)

# =============================================================================
# Protocol Interface + Auto-Registration
# =============================================================================

_PROTOCOL_REGISTRY: dict[str, DiscoveryProtocol] = {}


class DiscoveryProtocol(abc.ABC):
    """Abstract base class for instrument discovery protocols.

    Subclass and set ``name`` to auto-register. Each protocol provides:

    - ``discover()`` — scan for available resources (SLOW, setup-time only)
    - ``get_info(resource)`` — query a specific resource's identity (FAST)
    """

    name: str

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "name"):
            _PROTOCOL_REGISTRY[cls.name] = cls()

    @abc.abstractmethod
    def discover(self) -> list[str]:
        """Scan for available instrument resources."""

    @abc.abstractmethod
    def get_info(self, resource: str) -> InstrumentInfo | None:
        """Query identity for a specific resource."""


def get_protocol(name: str) -> DiscoveryProtocol | None:
    """Get the discovery protocol handler for a given name."""
    return _PROTOCOL_REGISTRY.get(name)


def list_protocols() -> list[str]:
    """List all registered protocols."""
    return list(_PROTOCOL_REGISTRY.keys())


# =============================================================================
# Shared Utilities
# =============================================================================


def check_import(module: str, install_hint: str) -> None:
    """Check if an optional dependency is available."""
    try:
        __import__(module)
    except ImportError as e:
        raise ImportError(install_hint) from e


def parse_idn(idn_response: str) -> InstrumentInfo:
    """Parse IEEE 488.2 *IDN? response into InstrumentInfo.

    Standard format: "Manufacturer,Model,Serial,Firmware"

    Handles common deviations (extra fields, missing fields, whitespace).
    """
    if not idn_response:
        return InstrumentInfo()

    parts = [p.strip() for p in idn_response.split(",")]

    return InstrumentInfo(
        manufacturer=parts[0] if len(parts) > 0 and parts[0] else None,
        model=parts[1] if len(parts) > 1 and parts[1] else None,
        serial=parts[2] if len(parts) > 2 and parts[2] else None,
        firmware=parts[3] if len(parts) > 3 and parts[3] else None,
    )


# =============================================================================
# Unified Discovery Interface
# =============================================================================


def _iter_protocols(
    protocols: list[str] | None = None,
) -> list[tuple[str, DiscoveryProtocol]]:
    """Resolve protocol names to their handlers."""
    if protocols is None:
        protocols = list_protocols()
    result = []
    for name in protocols:
        handler = get_protocol(name)
        if handler is not None:
            result.append((name, handler))
    return result


def discover(protocols: list[str] | None = None) -> dict[str, list[str]]:
    """Discover instruments across multiple protocols.

    Args:
        protocols: List of protocol names to scan, or None for all registered.

    Returns:
        Dict mapping protocol name to list of resources found.
    """
    results: dict[str, list[str]] = {}
    for name, proto in _iter_protocols(protocols):
        try:
            results[name] = proto.discover()
        except Exception as exc:
            logger.debug("Discovery protocol %s failed: %s", name, exc)
            results[name] = []
    return results


def get_info(protocol: str, resource: str) -> InstrumentInfo | None:
    """Get instrument info using the specified protocol.

    Returns:
        InstrumentInfo or None if query fails or protocol not registered
    """
    handler = get_protocol(protocol)
    if handler is None:
        return None
    return handler.get_info(resource)


def discover_and_identify(
    protocols: list[str] | None = None,
) -> dict[str, list[tuple[str, InstrumentInfo | None]]]:
    """Discover instruments and query their identity.

    This is the most comprehensive (and slowest) discovery function.
    """
    results: dict[str, list[tuple[str, InstrumentInfo | None]]] = {}
    for name, proto in _iter_protocols(protocols):
        try:
            resources = proto.discover()
            results[name] = [(r, proto.get_info(r)) for r in resources]
        except Exception as exc:
            logger.debug("Discovery protocol %s failed: %s", name, exc)
            results[name] = []
    return results
