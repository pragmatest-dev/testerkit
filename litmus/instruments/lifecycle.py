"""Shared instrument lifecycle: load, connect, verify, proxy.

Used by both ``StationConnection.instrument()`` and the pytest plugin's
``instruments`` fixture. Keeps the driver loading, identity verification,
calibration checking, and proxy wrapping logic in one place.
"""

from __future__ import annotations

import importlib
import warnings
from typing import Any
from uuid import UUID

from litmus.data.event_log import EventLog
from litmus.instruments.models import CalibrationInfo, InstrumentInfo, InstrumentRecord


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


def check_calibration(role: str, calibration: CalibrationInfo) -> None:
    """Emit warnings if calibration is expired or due soon."""
    if not calibration or not calibration.due_date:
        return
    days_until = calibration.days_until_due()
    if days_until is None:
        return
    if days_until < 0:
        warnings.warn(
            f"{role}: CALIBRATION EXPIRED (due {calibration.due_date}, "
            f"{-days_until} days overdue)",
            UserWarning,
            stacklevel=3,
        )
    elif days_until < 30:
        warnings.warn(
            f"{role}: calibration due soon ({calibration.due_date}, "
            f"{days_until} days remaining)",
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
    """Query instrument identity from a connected instance."""
    if hasattr(inst, "manufacturer") and inst.manufacturer:
        return InstrumentInfo(
            manufacturer=getattr(inst, "manufacturer", None),
            model=getattr(inst, "model", None),
            serial=getattr(inst, "serial", None),
            firmware=getattr(inst, "firmware", None),
        )
    if hasattr(inst, "query"):
        try:
            from litmus.instruments.discovery import parse_idn

            idn = inst.query("*IDN?")
            return parse_idn(idn)
        except (TimeoutError, RuntimeError, OSError, ValueError):
            pass
    return None


def load_and_connect(
    record: InstrumentRecord,
    mock: bool = False,
    mock_config: dict[str, Any] | None = None,
) -> Any:
    """Load driver class, instantiate, and connect. Returns the raw driver.

    Args:
        record: Full instrument record with resource, driver path, etc.
        mock: If True, create a mock instrument instead of real hardware.
        mock_config: Method return values for mock instruments (e.g. ``{"measure_voltage": 3.3}``).

    Returns:
        Connected driver instance.
    """
    from litmus.instruments.mocks import Mock

    if mock or record.mocked:
        inst: Any = Mock(object, **(mock_config or {}))
        if record.info:
            inst.manufacturer = record.info.manufacturer
            inst.model = record.info.model
            inst.serial = record.info.serial
            inst.firmware = record.info.firmware
        return inst

    driver_class = load_driver_class(record.driver)
    if driver_class is not None:
        inst = driver_class(record.resource)
    elif record.resource:
        import pyvisa

        rm = pyvisa.ResourceManager("@py")
        inst = rm.open_resource(record.resource)
    else:
        raise ValueError(f"No driver or resource for instrument {record.role!r}")

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
    run_id: UUID | None = None,
) -> Any:
    """Verify identity, check calibration, wrap in InstrumentProxy.

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

    if event_log is not None and session_id is not None:
        from litmus.instruments.proxy import InstrumentProxy

        return InstrumentProxy(driver, role, event_log, session_id, run_id)

    return driver


def disconnect(inst: Any, role: str) -> None:
    """Disconnect/close an instrument, swallowing errors."""
    try:
        if hasattr(inst, "disconnect"):
            inst.disconnect()
        elif hasattr(inst, "close"):
            inst.close()
    except Exception as exc:
        warnings.warn(f"Failed to cleanup instrument '{role}': {exc}", stacklevel=2)
