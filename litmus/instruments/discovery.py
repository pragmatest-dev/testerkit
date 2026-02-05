"""Instrument discovery and identification utilities.

Litmus does NOT wrap instrument drivers - users use PyMeasure, PyVISA,
vendor libraries, etc. directly. This module provides:

- **Discovery functions** (slow, setup time) - scan for available instruments
- **Info functions** (fast, runtime) - query specific resource for identity
- **Protocol registry** - pluggable support for different instrument protocols

Usage:
    # Setup time: discover what's connected
    from litmus.instruments.discovery import discover_visa
    resources = discover_visa()  # ["GPIB::16::INSTR", "USB::0x1234::INSTR"]

    # Runtime: query specific instrument
    from litmus.instruments.discovery import get_info_visa
    info = get_info_visa("GPIB::16::INSTR")
    # InstrumentInfo(manufacturer="Keithley", model="2000", serial="ABC123")

    # Custom protocols
    from litmus.instruments.discovery import register_protocol
    register_protocol("myproto", discover=my_discover, get_info=my_get_info)
"""

from __future__ import annotations

from collections.abc import Callable

from litmus.instruments.models import InstrumentInfo

# Type alias to keep lines short
DiscoverFn = Callable[[], list[str]]
GetInfoFn = Callable[[str], InstrumentInfo | None]

# =============================================================================
# Protocol Registry
# =============================================================================

# Registry of protocol handlers: protocol_name -> (discover_fn, get_info_fn)
_PROTOCOL_REGISTRY: dict[str, tuple[DiscoverFn, GetInfoFn]] = {}


def register_protocol(
    name: str,
    discover: Callable[[], list[str]],
    get_info: Callable[[str], InstrumentInfo | None],
) -> None:
    """Register a custom discovery protocol.

    Args:
        name: Protocol name (e.g., "myproto")
        discover: Function that returns list of resource strings
        get_info: Function that takes resource string and returns InstrumentInfo

    Example:
        def my_discover():
            return ["COM1", "COM2"]

        def my_get_info(resource):
            # Query device and return info
            return InstrumentInfo(manufacturer="Acme", model="Widget")

        register_protocol("myproto", my_discover, my_get_info)
    """
    _PROTOCOL_REGISTRY[name] = (discover, get_info)


def get_protocol(name: str) -> tuple[DiscoverFn, GetInfoFn] | None:
    """Get discovery functions for a protocol.

    Returns:
        Tuple of (discover_fn, get_info_fn) or None if protocol not registered.
    """
    return _PROTOCOL_REGISTRY.get(name)


def list_protocols() -> list[str]:
    """List all registered protocols."""
    return list(_PROTOCOL_REGISTRY.keys())


# =============================================================================
# IDN Parsing
# =============================================================================


def parse_idn(idn_response: str) -> InstrumentInfo:
    """Parse IEEE 488.2 *IDN? response into InstrumentInfo.

    Standard format: "Manufacturer,Model,Serial,Firmware"

    Some instruments deviate from the standard:
    - May have extra fields
    - May have missing fields
    - May use different separators

    This parser handles common variations.

    Args:
        idn_response: Raw *IDN? response string

    Returns:
        InstrumentInfo with parsed fields (may have None values)
    """
    if not idn_response:
        return InstrumentInfo()

    # Split on comma (standard separator)
    parts = [p.strip() for p in idn_response.split(",")]

    return InstrumentInfo(
        manufacturer=parts[0] if len(parts) > 0 and parts[0] else None,
        model=parts[1] if len(parts) > 1 and parts[1] else None,
        serial=parts[2] if len(parts) > 2 and parts[2] else None,
        firmware=parts[3] if len(parts) > 3 and parts[3] else None,
    )


# =============================================================================
# VISA Protocol (PyVISA)
# =============================================================================


def _check_pyvisa() -> None:
    """Check if pyvisa is available."""
    try:
        import pyvisa  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "Protocol 'visa' requires pyvisa. Install with: pip install pyvisa pyvisa-py"
        ) from e


def discover_visa(
    query: str = "?*::INSTR",
    timeout_ms: int = 2000,
) -> list[str]:
    """Scan for VISA resources.

    This is SLOW - use only at setup time, not during test execution.
    Scans all configured VISA backends for matching resources.

    Args:
        query: VISA resource query pattern (default: all INSTR resources)
        timeout_ms: Timeout for resource manager operations

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
    except Exception:
        return []


def get_info_visa(
    resource: str,
    timeout_ms: int = 2000,
) -> InstrumentInfo | None:
    """Query *IDN? for a specific VISA resource.

    This is FAST - queries only the specified resource, not a scan.
    Safe to use at runtime for instrument verification.

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
        inst = rm.open_resource(resource)
        inst.timeout = timeout_ms
        inst.write_termination = "\n"
        inst.read_termination = "\n"

        idn = inst.query("*IDN?").strip()
        inst.close()
        rm.close()

        return parse_idn(idn)
    except Exception:
        return None


def identify_visa(resource: str, timeout_ms: int = 2000) -> tuple[str, InstrumentInfo | None]:
    """Discover and identify a VISA resource.

    Convenience function that returns both the resource string and its info.

    Returns:
        Tuple of (resource, InstrumentInfo or None)
    """
    return resource, get_info_visa(resource, timeout_ms)


# Register VISA protocol
register_protocol("visa", discover_visa, get_info_visa)


# =============================================================================
# NI System Configuration API
# =============================================================================


def _check_nisyscfg() -> None:
    """Check if NI System Configuration is available."""
    try:
        import nisyscfg  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "Protocol 'ni' requires nisyscfg. Install NI System Configuration from ni.com"
        ) from e


def discover_ni() -> list[str]:
    """Scan for NI devices using System Configuration API.

    This is SLOW - use only at setup time. Scans local system for
    NI hardware including DAQmx, PXI, USB devices, etc.

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
                # Get device name if available
                name = getattr(resource, "name", None) or getattr(
                    resource, "user_alias", None
                )
                if name:
                    devices.append(name)
    except Exception:
        pass
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
            # Find the specific device
            for resource in session.find_hardware():
                name = getattr(resource, "name", None) or getattr(
                    resource, "user_alias", None
                )
                if name == device:
                    return InstrumentInfo(
                        manufacturer="National Instruments",
                        model=getattr(resource, "product_name", None),
                        serial=str(getattr(resource, "serial_number", "")),
                        firmware=getattr(resource, "firmware_revision", None),
                    )
    except Exception:
        pass
    return None


# Register NI protocol
register_protocol("ni", discover_ni, get_info_ni)


# =============================================================================
# Serial Protocol (pyserial)
# =============================================================================


def _check_pyserial() -> None:
    """Check if pyserial is available."""
    try:
        import serial.tools.list_ports  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "Protocol 'serial' requires pyserial. Install with: pip install pyserial"
        ) from e


def discover_serial() -> list[str]:
    """Scan for serial ports.

    Returns:
        List of serial port names (e.g., ["COM1", "/dev/ttyUSB0"])
    """
    _check_pyserial()
    import serial.tools.list_ports

    return [port.device for port in serial.tools.list_ports.comports()]


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


# Register serial protocol
register_protocol("serial", discover_serial, get_info_serial)


# =============================================================================
# Unified Discovery Interface
# =============================================================================


def discover(protocols: list[str] | None = None) -> dict[str, list[str]]:
    """Discover instruments across multiple protocols.

    Args:
        protocols: List of protocol names to scan, or None for all registered.
                   Available: "visa", "ni", "serial" (if dependencies installed)

    Returns:
        Dict mapping protocol name to list of resources found.

    Example:
        >>> discover(["visa", "ni"])
        {
            "visa": ["GPIB::16::INSTR", "USB::0x1234::INSTR"],
            "ni": ["Dev1", "PXI1Slot2"]
        }
    """
    if protocols is None:
        protocols = list_protocols()

    results: dict[str, list[str]] = {}
    for proto in protocols:
        handler = get_protocol(proto)
        if handler is None:
            continue
        discover_fn, _ = handler
        try:
            results[proto] = discover_fn()
        except ImportError:
            # Dependency not installed - skip this protocol
            results[proto] = []
        except Exception:
            results[proto] = []

    return results


def get_info(protocol: str, resource: str) -> InstrumentInfo | None:
    """Get instrument info using the specified protocol.

    Args:
        protocol: Protocol name ("visa", "ni", "serial", or custom)
        resource: Resource string appropriate for the protocol

    Returns:
        InstrumentInfo or None if query fails or protocol not registered

    Raises:
        ImportError: If protocol's dependency is not installed
    """
    handler = get_protocol(protocol)
    if handler is None:
        return None
    _, get_info_fn = handler
    return get_info_fn(resource)


def discover_and_identify(
    protocols: list[str] | None = None,
) -> dict[str, list[tuple[str, InstrumentInfo | None]]]:
    """Discover instruments and query their identity.

    This is the most comprehensive (and slowest) discovery function.
    For each discovered resource, queries its identity information.

    Args:
        protocols: List of protocol names to scan, or None for all.

    Returns:
        Dict mapping protocol name to list of (resource, InstrumentInfo) tuples.

    Example:
        >>> discover_and_identify(["visa"])
        {
            "visa": [
                ("GPIB::16::INSTR", InstrumentInfo(manufacturer="Keithley", ...)),
                ("USB::0x1234::INSTR", InstrumentInfo(manufacturer="Rigol", ...)),
            ]
        }
    """
    if protocols is None:
        protocols = list_protocols()

    results: dict[str, list[tuple[str, InstrumentInfo | None]]] = {}
    for proto in protocols:
        handler = get_protocol(proto)
        if handler is None:
            continue
        discover_fn, get_info_fn = handler
        try:
            resources = discover_fn()
            results[proto] = [(r, get_info_fn(r)) for r in resources]
        except ImportError:
            results[proto] = []
        except Exception:
            results[proto] = []

    return results
