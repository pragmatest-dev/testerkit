"""Instrument identity and calibration models.

These models capture instrument metadata for traceability:
- InstrumentInfo: Identity queried from device (*IDN? or protocol-specific)
- CalibrationInfo: Calibration status from configuration (not queryable)
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ChannelKind(StrEnum):
    """Classification for instrument channels/attributes.

    Drives event emission in InstrumentProxy:
    - read: measurement/get → writes channel; emits ``ChannelStarted`` on
      first write per (channel, session); subsequent reads are silent on
      the event log (sample data lives in ChannelStore — Position 2)
    - set: set/write → emits ``InstrumentSet``
    - control: read-write property → read side as above; emits
      ``InstrumentSet`` on set
    - configure: setup/init → emits ``InstrumentConfigure``
    """

    read = "read"
    set = "set"
    control = "control"
    configure = "configure"


class InstrumentInfo(BaseModel):
    """Instrument identity queried from device.

    For VISA instruments, this is parsed from the *IDN? response.
    For other protocols, this is populated via protocol-specific APIs.

    All fields are optional because some instruments may not report
    all identity information.
    """

    model_config = {"extra": "forbid"}

    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None

    @field_validator("model", "serial", "firmware", mode="before")
    @classmethod
    def _coerce_to_str(cls, v: Any) -> str | None:
        """Coerce numeric values to string (YAML loads 2400 as int)."""
        return str(v) if v is not None else None

    def __bool__(self) -> bool:
        """Return True if any identity field is populated."""
        return any([self.manufacturer, self.model, self.serial, self.firmware])

    def matches(self, expected: InstrumentInfo) -> tuple[bool, list[str]]:
        """Check if this info matches expected, returning mismatches.

        Only compares fields that are set in expected. This allows
        partial matching (e.g., only verify serial number).

        Returns:
            Tuple of (matches, list of mismatch descriptions)
        """
        mismatches = []
        if expected.manufacturer and self.manufacturer != expected.manufacturer:
            mismatches.append(
                f"manufacturer: expected {expected.manufacturer!r}, got {self.manufacturer!r}"
            )
        if expected.model and self.model != expected.model:
            mismatches.append(f"model: expected {expected.model!r}, got {self.model!r}")
        if expected.serial and self.serial != expected.serial:
            mismatches.append(f"serial: expected {expected.serial!r}, got {self.serial!r}")
        if expected.firmware and self.firmware != expected.firmware:
            mismatches.append(f"firmware: expected {expected.firmware!r}, got {self.firmware!r}")
        return len(mismatches) == 0, mismatches


class CalibrationInfo(BaseModel):
    """Calibration status from configuration.

    This information is NOT queryable from the device - it comes from
    the instrument configuration file and is tracked by the organization's
    calibration management system.
    """

    model_config = {"extra": "forbid"}

    due_date: date | None = None
    last_cal: date | None = None
    certificate: str | None = None
    lab: str | None = None

    def __bool__(self) -> bool:
        """Return True if any calibration field is populated."""
        return any([self.due_date, self.last_cal, self.certificate, self.lab])

    def is_expired(self) -> bool:
        """Check if calibration is expired."""
        if self.due_date is None:
            return False
        return self.due_date < date.today()

    def days_until_due(self) -> int | None:
        """Return days until calibration is due, or None if no due date."""
        if self.due_date is None:
            return None
        return (self.due_date - date.today()).days


class InstrumentRecord(BaseModel):
    """Complete instrument record combining identity and calibration.

    Used by the fixture/logger to track everything about an instrument
    in a test session.
    """

    model_config = {"extra": "forbid"}

    # Station role (e.g., "dmm", "psu")
    role: str

    # Instrument file ID (e.g., "keithley_dmm_001")
    instrument_id: str

    # Connection resource (e.g., "GPIB::16::INSTR", "Dev1")
    resource: str

    # Protocol (e.g., "visa", "ni", "serial")
    protocol: str = "visa"

    # Identity from device query
    info: InstrumentInfo = Field(default_factory=InstrumentInfo)

    # Calibration from config
    calibration: CalibrationInfo = Field(default_factory=CalibrationInfo)

    # Driver class path (e.g., "pymeasure.instruments.keithley.Keithley2000")
    driver: str | None = None

    # Catalog reference (e.g., "keysight_34461a") for capability lookup
    catalog_ref: str | None = None

    # Whether this instrument is running in mock mode
    mocked: bool = False
