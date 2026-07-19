"""Shared instrument lifecycle: load, connect, verify, proxy.

Used by both ``StationConnection.instrument()`` and the pytest plugin's
``instruments`` fixture. Keeps the driver loading, identity verification,
calibration checking, and proxy wrapping logic in one place.
"""

from __future__ import annotations

import importlib
import logging
import warnings
from typing import Any
from uuid import UUID

from testerkit.data.event_log import EventLog
from testerkit.instruments.observer import DriverObserver
from testerkit.models.instrument import CalibrationInfo, InstrumentInfo, InstrumentRecord

logger = logging.getLogger(__name__)


def load_driver_class(driver_path: str | None) -> type | None:
    """Load a driver class from a dotted import path.

    Args:
        driver_path: e.g. ``"pymeasure.instruments.keithley.Keithley2400"``

    Returns:
        The class, or ``None`` if not found.
    """
    if not driver_path:
        return None
    try:
        module_path, class_name = driver_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except (ImportError, AttributeError, ValueError):
        return None


_CALIBRATION_WARNING_DAYS = 30


def check_calibration(role: str, calibration: CalibrationInfo) -> None:
    """Emit warnings if calibration is expired or due soon."""
    if not calibration or not calibration.due_date:
        return
    days_until = calibration.days_until_due()
    if days_until is None:
        return
    if days_until < 0:
        warnings.warn(
            f"{role}: CALIBRATION EXPIRED (due {calibration.due_date}, {-days_until} days overdue)",
            UserWarning,
            stacklevel=3,
        )
    elif days_until < _CALIBRATION_WARNING_DAYS:
        warnings.warn(
            f"{role}: calibration due soon ({calibration.due_date}, {days_until} days remaining)",
            UserWarning,
            stacklevel=3,
        )


def verify_instrument_identity(
    role: str,
    actual: InstrumentInfo,
    expected: InstrumentInfo,
    strict: bool = False,
) -> None:
    """Verify instrument identity matches expected configuration.

    Raises:
        RuntimeError: If strict and identity doesn't match.
    """
    if not expected:
        return
    matches, mismatches = actual.matches(expected)
    if not matches:
        msg = f"{role}: instrument identity mismatch - {'; '.join(mismatches)}"
        if strict:
            raise RuntimeError(msg)
        warnings.warn(msg, UserWarning, stacklevel=3)


def get_instrument_info(inst: Any) -> InstrumentInfo | None:
    """Query instrument identity from a connected instance.

    Bring-your-own-driver: instruments may either expose typed
    ``manufacturer`` / ``model`` / ``serial`` / ``firmware`` attributes
    (PyMeasure, Lantz, qcodes patterns), or expose ``query("*IDN?")``
    (raw VISA / SCPI). The first branch trusts ``manufacturer`` is
    truthy and reads the others defensively — partial conformance
    (``manufacturer`` set but ``firmware`` missing) is real.
    """
    if hasattr(inst, "manufacturer") and inst.manufacturer:
        return InstrumentInfo(
            manufacturer=inst.manufacturer,
            model=getattr(inst, "model", None),
            serial=getattr(inst, "serial", None),
            firmware=getattr(inst, "firmware", None),
        )
    if hasattr(inst, "query"):
        try:
            from testerkit.instruments.discovery import parse_idn

            idn = inst.query("*IDN?")
            return parse_idn(idn)
        except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
            logger.debug("Could not query instrument info: %s", exc)
    return None


def load_and_connect(
    record: InstrumentRecord,
    mock: bool = False,
    mock_config: dict[str, Any] | None = None,
    driver_class: type | None = None,
) -> Any:
    """Load driver class, instantiate, and connect. Returns the raw driver.

    Args:
        record: Full instrument record with resource, driver path, etc.
        mock: If True, create a mock instrument instead of real hardware.
        mock_config: Method return values for mock instruments (e.g. ``{"measure_voltage": 3.3}``).
        driver_class: Pre-resolved driver class. Avoids redundant resolution
            when the caller already loaded the class for observer construction.

    Returns:
        Connected driver instance.
    """
    from testerkit.instruments.mocks import Mock

    if mock or record.mocked:
        inst: Any = Mock(object, **(mock_config or {}))
        if record.info:
            inst.manufacturer = record.info.manufacturer
            inst.model = record.info.model
            inst.serial = record.info.serial
            inst.firmware = record.info.firmware
        return inst

    if driver_class is None:
        driver_class = load_driver_class(record.driver)
    if driver_class is not None:
        inst = driver_class(record.resource)
    elif record.resource:
        import pyvisa

        rm = pyvisa.ResourceManager("@py")
        inst = rm.open_resource(record.resource)
    else:
        raise ValueError(f"No driver or resource for instrument {record.role!r}")

    # Bring-your-own-driver: PyMeasure / Lantz / etc. expose ``connect``;
    # raw PyVISA resources (the ``rm.open_resource`` branch above) do not.
    # The ``getattr(..., None)`` is intentionally permissive for that
    # split — PyVISA is already connected by ``open_resource``.
    connect_fn = getattr(inst, "connect", None)
    if callable(connect_fn):
        connect_fn()

    return inst


def verify_and_wrap(
    driver: Any,
    role: str,
    record: InstrumentRecord,
    event_log: EventLog | None,
    session_id: UUID | None,
    observer: DriverObserver | None = None,
) -> Any:
    """Verify identity, check calibration, wrap in InstrumentProxy.

    Args:
        observer: Observer for event interpretation. If provided along with
            event_log and session_id, wraps driver in an InstrumentProxy.

    Returns the (possibly proxied) driver.
    """
    if not record.mocked:
        actual_info = get_instrument_info(driver)
        if actual_info:
            verify_instrument_identity(role, actual_info, record.info, strict=False)
            record.info = actual_info
        elif record.info:
            warnings.warn(
                f"{role}: could not query instrument identity",
                UserWarning,
                stacklevel=2,
            )

    check_calibration(role, record.calibration)

    if event_log is not None and session_id is not None and observer is not None:
        from testerkit.instruments.proxy import InstrumentProxy

        return InstrumentProxy(driver, role, observer)

    return driver


def disconnect(inst: Any, role: str) -> None:
    """Disconnect/close an instrument, swallowing errors."""
    try:
        if hasattr(inst, "disconnect"):
            inst.disconnect()
        elif hasattr(inst, "close"):
            inst.close()
    except (OSError, RuntimeError) as exc:
        warnings.warn(f"Failed to cleanup instrument '{role}': {exc}", stacklevel=2)
