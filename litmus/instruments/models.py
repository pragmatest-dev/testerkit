"""Instrument identity and calibration models.

These models capture instrument metadata for traceability:
- InstrumentInfo: Identity queried from device (*IDN? or protocol-specific)
- CalibrationInfo: Calibration status from configuration (not queryable)
"""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class InstrumentInfo:
    """Instrument identity queried from device.

    For VISA instruments, this is parsed from the *IDN? response.
    For other protocols, this is populated via protocol-specific APIs.

    All fields are optional because some instruments may not report
    all identity information.
    """

    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None

    def __bool__(self) -> bool:
        """Return True if any identity field is populated."""
        return any([self.manufacturer, self.model, self.serial, self.firmware])

    def matches(self, expected: "InstrumentInfo") -> tuple[bool, list[str]]:
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
            mismatches.append(
                f"serial: expected {expected.serial!r}, got {self.serial!r}"
            )
        if expected.firmware and self.firmware != expected.firmware:
            mismatches.append(
                f"firmware: expected {expected.firmware!r}, got {self.firmware!r}"
            )
        return len(mismatches) == 0, mismatches


@dataclass
class CalibrationInfo:
    """Calibration status from configuration.

    This information is NOT queryable from the device - it comes from
    the instrument configuration file and is tracked by the organization's
    calibration management system.
    """

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


@dataclass
class InstrumentRecord:
    """Complete instrument record combining identity and calibration.

    Used by the fixture/logger to track everything about an instrument
    in a test session.
    """

    # Station role (e.g., "dmm", "psu")
    role: str

    # Instrument file ID (e.g., "keithley_dmm_001")
    instrument_id: str

    # Connection resource (e.g., "GPIB::16::INSTR", "Dev1")
    resource: str

    # Protocol (e.g., "visa", "ni", "serial")
    protocol: str = "visa"

    # Identity from device query
    info: InstrumentInfo = field(default_factory=InstrumentInfo)

    # Calibration from config
    calibration: CalibrationInfo = field(default_factory=CalibrationInfo)

    # Driver class path (e.g., "pymeasure.instruments.keithley.Keithley2000")
    driver: str | None = None

    # Catalog reference (e.g., "keysight_34461a") for capability lookup
    catalog_ref: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/serialization."""
        return {
            "role": self.role,
            "instrument_id": self.instrument_id,
            "resource": self.resource,
            "protocol": self.protocol,
            "manufacturer": self.info.manufacturer,
            "model": self.info.model,
            "serial": self.info.serial,
            "firmware": self.info.firmware,
            "cal_due": self.calibration.due_date.isoformat()
            if self.calibration.due_date
            else None,
            "cal_last": self.calibration.last_cal.isoformat()
            if self.calibration.last_cal
            else None,
            "cal_certificate": self.calibration.certificate,
            "cal_lab": self.calibration.lab,
            "driver": self.driver,
            "catalog_ref": self.catalog_ref,
        }
