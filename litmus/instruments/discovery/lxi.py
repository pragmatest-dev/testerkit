"""LXI (LAN eXtensions for Instruments) discovery via mDNS.

Discovers networked instruments advertising the ``_lxi._tcp.local.`` service
type and retrieves identity from the standard ``/lxi/identification`` HTTP
endpoint defined by the LXI specification.

Requires the optional ``zeroconf`` package::

    pip install zeroconf
    # or: pip install litmus-test[lxi]
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.request import urlopen

from litmus.instruments.discovery._base import DiscoveryProtocol, check_import
from litmus.instruments.models import InstrumentInfo

_LXI_SERVICE_TYPE = "_lxi._tcp.local."


def discover_lxi(timeout: float = 3.0) -> list[str]:
    """Browse mDNS for LXI instruments.

    Returns list of ``LXI::{ip}:{port}`` resource strings.

    Args:
        timeout: Seconds to wait for mDNS responses.

    Raises:
        ImportError: If zeroconf is not installed.
    """
    check_import(
        "zeroconf",
        "Protocol 'lxi' requires zeroconf. Install with: pip install zeroconf",
    )
    import threading

    from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

    resources: list[str] = []
    lock = threading.Lock()

    class _Listener(ServiceListener):
        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name)
            if info is None:
                return
            for addr in info.parsed_addresses():
                with lock:
                    resources.append(f"LXI::{addr}:{info.port}")

        def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            pass

        def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, _LXI_SERVICE_TYPE, _Listener())
        threading.Event().wait(timeout)
    finally:
        zc.close()

    return resources


def _parse_resource(resource: str) -> tuple[str, int]:
    """Parse ``LXI::{ip}:{port}`` into (ip, port).

    Raises:
        ValueError: If the resource string is not in the expected format.
    """
    if not resource.startswith("LXI::"):
        raise ValueError(f"Invalid LXI resource string: {resource!r}")
    rest = resource[5:]  # strip "LXI::"
    parts = rest.rsplit(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid LXI resource string: {resource!r}")
    ip, port_str = parts
    return ip, int(port_str)


def get_info_lxi(resource: str, timeout: float = 5.0) -> InstrumentInfo | None:
    """Fetch ``/lxi/identification`` XML and parse into InstrumentInfo.

    Args:
        resource: ``LXI::{ip}:{port}`` resource string.
        timeout: HTTP request timeout in seconds.

    Returns:
        InstrumentInfo or None if the request or parsing fails.
    """
    try:
        ip, port = _parse_resource(resource)
    except ValueError:
        return None

    try:
        url = f"http://{ip}:{port}/lxi/identification"
        with urlopen(url, timeout=timeout) as resp:  # noqa: S310
            xml_bytes = resp.read()
    except (OSError, ValueError):
        return None

    return _parse_identification_xml(xml_bytes)


def _parse_identification_xml(xml_bytes: bytes) -> InstrumentInfo | None:
    """Parse LXI identification XML into InstrumentInfo.

    The XML contains elements like ``<Manufacturer>``, ``<Model>``,
    ``<SerialNumber>``, ``<FirmwareRevision>`` (case may vary by vendor).
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    def _find(tag: str) -> str | None:
        for elem in root.iter():
            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local.lower() == tag.lower():
                text = (elem.text or "").strip()
                return text if text else None
        return None

    return InstrumentInfo(
        manufacturer=_find("Manufacturer"),
        model=_find("Model"),
        serial=_find("SerialNumber"),
        firmware=_find("FirmwareRevision"),
    )


class LxiDiscovery(DiscoveryProtocol):
    """LXI/mDNS discovery protocol."""

    name = "lxi"

    def discover(self) -> list[str]:
        return discover_lxi()

    def get_info(self, resource: str) -> InstrumentInfo | None:
        return get_info_lxi(resource)


