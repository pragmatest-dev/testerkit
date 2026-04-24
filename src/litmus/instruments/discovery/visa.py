"""VISA protocol discovery via PyVISA."""

from __future__ import annotations

import logging

from litmus.instruments.discovery._base import (
    DiscoveryProtocol,
    check_import,
    parse_idn,
)
from litmus.models.instrument import InstrumentInfo

logger = logging.getLogger(__name__)


def _check_pyvisa() -> None:
    """Check if pyvisa is available."""
    check_import(
        "pyvisa",
        "Protocol 'visa' requires pyvisa. Install with: pip install pyvisa pyvisa-py",
    )


def discover_visa(
    query: str = "?*::INSTR",
) -> list[str]:
    """Scan for VISA resources.

    This is SLOW - use only at setup time, not during test execution.

    Args:
        query: VISA resource query pattern (default: all INSTR resources)

    Returns:
        List of VISA resource strings found

    Raises:
        ImportError: If pyvisa is not installed
    """
    _check_pyvisa()
    import pyvisa

    try:
        rm = pyvisa.ResourceManager()
        resources = list(rm.list_resources(query))
        rm.close()
        return resources
    except (OSError, ValueError, pyvisa.errors.VisaIOError) as exc:
        logger.debug("VISA discovery failed: %s", exc)
        return []


def get_info_visa(
    resource: str,
    timeout_ms: int = 2000,
) -> InstrumentInfo | None:
    """Query *IDN? for a specific VISA resource.

    This is FAST - queries only the specified resource, not a scan.

    Args:
        resource: VISA resource string (e.g., "GPIB::16::INSTR")
        timeout_ms: Communication timeout

    Returns:
        InstrumentInfo parsed from *IDN? response, or None if query fails
    """
    _check_pyvisa()
    import pyvisa

    try:
        rm = pyvisa.ResourceManager()
        inst: pyvisa.resources.MessageBasedResource = rm.open_resource(resource)  # type: ignore[assignment]
        inst.timeout = timeout_ms
        inst.write_termination = "\n"
        inst.read_termination = "\n"

        idn = inst.query("*IDN?").strip()
        inst.close()
        rm.close()

        return parse_idn(idn)
    except (OSError, ValueError, pyvisa.errors.VisaIOError) as exc:
        logger.debug("VISA get_info failed for %s: %s", resource, exc)
        return None


class VisaDiscovery(DiscoveryProtocol):
    """VISA discovery protocol."""

    name = "visa"

    def discover(self) -> list[str]:
        return discover_visa()

    def get_info(self, resource: str) -> InstrumentInfo | None:
        return get_info_visa(resource)
