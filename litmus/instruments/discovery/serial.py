"""Serial port discovery via pyserial."""

from __future__ import annotations

import logging

from litmus.instruments.discovery._base import DiscoveryProtocol, check_import
from litmus.models.instrument import InstrumentInfo

logger = logging.getLogger(__name__)


def _check_pyserial() -> None:
    """Check if pyserial is available."""
    check_import(
        "serial.tools.list_ports",
        "Protocol 'serial' requires pyserial. Install with: pip install pyserial",
    )


def discover_serial() -> list[str]:
    """Scan for serial ports.

    Returns:
        List of serial port names (e.g., ["COM1", "/dev/ttyUSB0"])
    """
    _check_pyserial()
    import serial.tools.list_ports

    try:
        return [port.device for port in serial.tools.list_ports.comports()]
    except OSError as exc:
        logger.debug("Serial discovery failed: %s", exc)
        return []


def get_info_serial(port: str) -> InstrumentInfo | None:
    """Get serial port hardware info.

    Note: This returns USB device info if available, not the connected
    instrument's identity. For instrument identity, use a protocol-specific
    query (e.g., SCPI *IDN? over serial).

    Args:
        port: Serial port name (e.g., "COM1", "/dev/ttyUSB0")

    Returns:
        InstrumentInfo with USB device info, or None if not found
    """
    _check_pyserial()
    import serial.tools.list_ports

    for p in serial.tools.list_ports.comports():
        if p.device == port:
            return InstrumentInfo(
                manufacturer=p.manufacturer,
                model=p.product or p.description,
                serial=p.serial_number,
                firmware=None,
            )
    return None


class SerialDiscovery(DiscoveryProtocol):
    """Serial port discovery protocol."""

    name = "serial"

    def discover(self) -> list[str]:
        return discover_serial()

    def get_info(self, resource: str) -> InstrumentInfo | None:
        return get_info_serial(resource)


