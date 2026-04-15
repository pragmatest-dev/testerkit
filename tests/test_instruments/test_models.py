"""Tests for instrument models."""

from datetime import date, timedelta

from litmus.models.instrument import (
    CalibrationInfo,
    InstrumentInfo,
    InstrumentRecord,
)


class TestInstrumentInfo:
    """Tests for InstrumentInfo dataclass."""

    def test_empty_info_is_falsy(self):
        """Empty InstrumentInfo evaluates to False."""
        info = InstrumentInfo()
        assert not info

    def test_partial_info_is_truthy(self):
        """InstrumentInfo with any field is truthy."""
        assert InstrumentInfo(manufacturer="Keithley")
        assert InstrumentInfo(model="2400")
        assert InstrumentInfo(serial="ABC123")
        assert InstrumentInfo(firmware="1.0")

    def test_full_info(self):
        """InstrumentInfo with all fields."""
        info = InstrumentInfo(
            manufacturer="Keithley",
            model="2400",
            serial="ABC123",
            firmware="A02",
        )
        assert info.manufacturer == "Keithley"
        assert info.model == "2400"
        assert info.serial == "ABC123"
        assert info.firmware == "A02"

    def test_matches_exact(self):
        """Exact match returns True with no mismatches."""
        actual = InstrumentInfo(
            manufacturer="Keithley",
            model="2400",
            serial="ABC123",
            firmware="A02",
        )
        expected = InstrumentInfo(
            manufacturer="Keithley",
            model="2400",
            serial="ABC123",
            firmware="A02",
        )
        matches, mismatches = actual.matches(expected)
        assert matches
        assert mismatches == []

    def test_matches_partial_expected(self):
        """Partial expected only compares set fields."""
        actual = InstrumentInfo(
            manufacturer="Keithley",
            model="2400",
            serial="ABC123",
            firmware="A02",
        )
        expected = InstrumentInfo(serial="ABC123")
        matches, mismatches = actual.matches(expected)
        assert matches
        assert mismatches == []

    def test_matches_mismatch(self):
        """Mismatch returns False with description."""
        actual = InstrumentInfo(serial="ABC123")
        expected = InstrumentInfo(serial="XYZ789")
        matches, mismatches = actual.matches(expected)
        assert not matches
        assert len(mismatches) == 1
        assert "serial" in mismatches[0]

    def test_matches_multiple_mismatches(self):
        """Multiple mismatches all reported."""
        actual = InstrumentInfo(
            manufacturer="Keithley",
            model="2400",
            serial="ABC123",
        )
        expected = InstrumentInfo(
            manufacturer="Agilent",
            model="34401A",
            serial="ABC123",
        )
        matches, mismatches = actual.matches(expected)
        assert not matches
        assert len(mismatches) == 2


class TestCalibrationInfo:
    """Tests for CalibrationInfo dataclass."""

    def test_empty_calibration_is_falsy(self):
        """Empty CalibrationInfo evaluates to False."""
        cal = CalibrationInfo()
        assert not cal

    def test_partial_calibration_is_truthy(self):
        """CalibrationInfo with any field is truthy."""
        assert CalibrationInfo(due_date=date.today())
        assert CalibrationInfo(last_cal=date.today())
        assert CalibrationInfo(certificate="CAL-001")
        assert CalibrationInfo(lab="Acme Cal")

    def test_is_expired_no_date(self):
        """No due date means not expired."""
        cal = CalibrationInfo()
        assert not cal.is_expired()

    def test_is_expired_future_date(self):
        """Future due date is not expired."""
        cal = CalibrationInfo(due_date=date.today() + timedelta(days=30))
        assert not cal.is_expired()

    def test_is_expired_past_date(self):
        """Past due date is expired."""
        cal = CalibrationInfo(due_date=date.today() - timedelta(days=1))
        assert cal.is_expired()

    def test_is_expired_today(self):
        """Due today is not expired (expires at end of day)."""
        cal = CalibrationInfo(due_date=date.today())
        assert not cal.is_expired()

    def test_days_until_due_no_date(self):
        """No due date returns None."""
        cal = CalibrationInfo()
        assert cal.days_until_due() is None

    def test_days_until_due_future(self):
        """Future due date returns positive days."""
        cal = CalibrationInfo(due_date=date.today() + timedelta(days=30))
        assert cal.days_until_due() == 30

    def test_days_until_due_past(self):
        """Past due date returns negative days."""
        cal = CalibrationInfo(due_date=date.today() - timedelta(days=10))
        assert cal.days_until_due() == -10

    def test_days_until_due_today(self):
        """Due today returns 0."""
        cal = CalibrationInfo(due_date=date.today())
        assert cal.days_until_due() == 0


class TestInstrumentRecord:
    """Tests for InstrumentRecord dataclass."""

    def test_basic_record(self):
        """Basic record with minimal fields."""
        record = InstrumentRecord(
            role="dmm",
            instrument_id="keithley_dmm_001",
            resource="GPIB::16::INSTR",
        )
        assert record.role == "dmm"
        assert record.instrument_id == "keithley_dmm_001"
        assert record.resource == "GPIB::16::INSTR"
        assert record.protocol == "visa"  # default

    def test_full_record(self):
        """Record with all fields."""
        record = InstrumentRecord(
            role="dmm",
            instrument_id="keithley_dmm_001",
            resource="GPIB::16::INSTR",
            protocol="visa",
            info=InstrumentInfo(manufacturer="Keithley", model="2000"),
            calibration=CalibrationInfo(
                due_date=date(2025, 6, 15),
                certificate="CAL-2024-001",
            ),
            driver="pymeasure.instruments.keithley.Keithley2000",
        )
        assert record.info.manufacturer == "Keithley"
        assert record.calibration.certificate == "CAL-2024-001"
        assert record.driver == "pymeasure.instruments.keithley.Keithley2000"

    def test_model_dump(self):
        """model_dump produces serializable output."""
        record = InstrumentRecord(
            role="dmm",
            instrument_id="keithley_dmm_001",
            resource="GPIB::16::INSTR",
            info=InstrumentInfo(manufacturer="Keithley", model="2000", serial="ABC123"),
            calibration=CalibrationInfo(
                due_date=date(2025, 6, 15),
                last_cal=date(2024, 6, 15),
                certificate="CAL-2024-001",
                lab="Acme Cal",
            ),
        )
        d = record.model_dump()
        assert d["role"] == "dmm"
        assert d["instrument_id"] == "keithley_dmm_001"
        assert d["info"]["manufacturer"] == "Keithley"
        assert d["info"]["serial"] == "ABC123"
        assert d["calibration"]["due_date"] == date(2025, 6, 15)
        assert d["calibration"]["certificate"] == "CAL-2024-001"

    def test_model_dump_empty_info(self):
        """model_dump handles empty info/calibration."""
        record = InstrumentRecord(
            role="dmm",
            instrument_id="dmm_001",
            resource="GPIB::16::INSTR",
        )
        d = record.model_dump()
        assert d["info"]["manufacturer"] is None
        assert d["calibration"]["due_date"] is None
