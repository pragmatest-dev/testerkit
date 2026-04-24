"""NI System Configuration discovery.

Discovers National Instruments hardware (DAQmx, PXI, USB devices, etc.)
using the NI System Configuration API.

Requires the ``nisyscfg`` package AND the NI System Configuration runtime::

    pip install nisyscfg
    # Also install NI System Configuration from ni.com
"""

from __future__ import annotations

import logging

from litmus.instruments.discovery._base import DiscoveryProtocol, check_import
from litmus.models.instrument import InstrumentInfo

logger = logging.getLogger(__name__)


def _check_nisyscfg() -> None:
    """Check if NI System Configuration is available."""
    check_import(
        "nisyscfg",
        "Protocol 'ni' requires nisyscfg. Install NI System Configuration from ni.com",
    )


def discover_ni() -> list[str]:
    """Scan for NI devices using System Configuration API.

    This is SLOW - use only at setup time.

    Returns:
        List of NI device names (e.g., ["Dev1", "PXI1Slot2"])

    Raises:
        ImportError: If nisyscfg is not installed
    """
    _check_nisyscfg()
    import nisyscfg

    devices = []
    try:
        with nisyscfg.Session() as session:
            for resource in session.find_hardware():
                name = getattr(resource, "name", None) or getattr(resource, "user_alias", None)
                if name:
                    devices.append(name)
    except (OSError, RuntimeError) as exc:
        logger.debug("NI discovery failed: %s", exc)
    return devices


def get_info_ni(device: str) -> InstrumentInfo | None:
    """Query NI device information.

    This is FAST - queries only the specified device.

    Args:
        device: NI device name (e.g., "Dev1", "PXI1Slot2")

    Returns:
        InstrumentInfo with NI device details, or None if query fails
    """
    _check_nisyscfg()
    import nisyscfg

    try:
        with nisyscfg.Session() as session:
            for resource in session.find_hardware():
                name = getattr(resource, "name", None) or getattr(resource, "user_alias", None)
                if name == device:
                    return InstrumentInfo(
                        manufacturer="National Instruments",
                        model=getattr(resource, "product_name", None),
                        serial=str(getattr(resource, "serial_number", "")),
                        firmware=getattr(resource, "firmware_revision", None),
                    )
    except (OSError, RuntimeError) as exc:
        logger.debug("NI get_info failed for %s: %s", device, exc)
    return None


class NiDiscovery(DiscoveryProtocol):
    """NI System Configuration discovery protocol."""

    name = "ni"

    def discover(self) -> list[str]:
        return discover_ni()

    def get_info(self, resource: str) -> InstrumentInfo | None:
        return get_info_ni(resource)
