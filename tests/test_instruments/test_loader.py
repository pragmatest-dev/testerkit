"""Tests for instrument and station configuration loader."""

from datetime import date
from pathlib import Path
from textwrap import dedent

import pytest

from litmus.instruments.loader import (
    load_calibration_info,
    load_instrument_file,
    load_instrument_files,
    load_instrument_info,
    load_station_file,
    resolve_station_instruments,
)
from litmus.instruments.models import CalibrationInfo, InstrumentInfo


class TestLoadInstrumentInfo:
    """Tests for load_instrument_info."""

    def test_empty_dict(self):
        """Empty dict returns empty InstrumentInfo."""
        info = load_instrument_info({})
        assert not info

    def test_full_info(self):
        """Load all fields."""
        data = {
            "manufacturer": "Keithley",
            "model": "2400",
            "serial": "ABC123",
            "firmware": "A02",
        }
        info = load_instrument_info(data)
        assert info.manufacturer == "Keithley"
        assert info.model == "2400"
        assert info.serial == "ABC123"
        assert info.firmware == "A02"

    def test_numeric_fields_converted_to_string(self):
        """Numeric model/serial converted to string."""
        data = {
            "model": 2400,
            "serial": 12345,
        }
        info = load_instrument_info(data)
        assert info.model == "2400"
        assert info.serial == "12345"


class TestLoadCalibrationInfo:
    """Tests for load_calibration_info."""

    def test_empty_dict(self):
        """Empty dict returns empty CalibrationInfo."""
        cal = load_calibration_info({})
        assert not cal

    def test_full_calibration(self):
        """Load all fields."""
        data = {
            "due_date": "2025-06-15",
            "last_cal": "2024-06-15",
            "certificate": "CAL-2024-001",
            "lab": "Acme Calibration",
        }
        cal = load_calibration_info(data)
        assert cal.due_date == date(2025, 6, 15)
        assert cal.last_cal == date(2024, 6, 15)
        assert cal.certificate == "CAL-2024-001"
        assert cal.lab == "Acme Calibration"

    def test_date_object_preserved(self):
        """Date objects passed through."""
        data = {"due_date": date(2025, 6, 15)}
        cal = load_calibration_info(data)
        assert cal.due_date == date(2025, 6, 15)

    def test_alternate_date_formats(self):
        """Various date formats supported."""
        # ISO format
        cal = load_calibration_info({"due_date": "2025-06-15"})
        assert cal.due_date == date(2025, 6, 15)

        # Slash format
        cal = load_calibration_info({"due_date": "2025/06/15"})
        assert cal.due_date == date(2025, 6, 15)

    def test_invalid_date_returns_none(self):
        """Invalid date string returns None."""
        cal = load_calibration_info({"due_date": "not-a-date"})
        assert cal.due_date is None


class TestLoadInstrumentFile:
    """Tests for load_instrument_file."""

    def test_load_full_instrument(self, tmp_path):
        """Load complete instrument file."""
        content = dedent("""
            id: keithley_dmm_001
            protocol: visa
            driver: pymeasure.instruments.keithley.Keithley2000

            info:
              manufacturer: Keithley
              model: "2000"
              serial: ABC123
              firmware: A02

            calibration:
              due_date: 2025-06-15
              last_cal: 2024-06-15
              certificate: CAL-2024-001
              lab: Acme Calibration
        """).strip()

        inst_file = tmp_path / "keithley_dmm_001.yaml"
        inst_file.write_text(content)

        data = load_instrument_file(inst_file)

        assert data["id"] == "keithley_dmm_001"
        assert data["protocol"] == "visa"
        assert data["driver"] == "pymeasure.instruments.keithley.Keithley2000"

        info = data["_info"]
        assert info.manufacturer == "Keithley"
        assert info.model == "2000"

        cal = data["_calibration"]
        assert cal.due_date == date(2025, 6, 15)
        assert cal.certificate == "CAL-2024-001"

    def test_load_minimal_instrument(self, tmp_path):
        """Load minimal instrument file."""
        content = dedent("""
            id: my_instrument
            protocol: visa
        """).strip()

        inst_file = tmp_path / "my_instrument.yaml"
        inst_file.write_text(content)

        data = load_instrument_file(inst_file)
        assert data["id"] == "my_instrument"
        assert not data["_info"]  # Empty
        assert not data["_calibration"]  # Empty


class TestLoadInstrumentFiles:
    """Tests for load_instrument_files."""

    def test_load_directory(self, tmp_path):
        """Load all instrument files from directory."""
        # Create instrument files
        (tmp_path / "dmm_001.yaml").write_text("id: dmm_001\nprotocol: visa\n")
        (tmp_path / "psu_001.yaml").write_text("id: psu_001\nprotocol: visa\n")
        (tmp_path / "not_yaml.txt").write_text("ignore me")

        instruments = load_instrument_files(tmp_path)

        assert len(instruments) == 2
        assert "dmm_001" in instruments
        assert "psu_001" in instruments

    def test_empty_directory(self, tmp_path):
        """Empty directory returns empty dict."""
        instruments = load_instrument_files(tmp_path)
        assert instruments == {}

    def test_nonexistent_directory(self, tmp_path):
        """Nonexistent directory returns empty dict."""
        instruments = load_instrument_files(tmp_path / "does_not_exist")
        assert instruments == {}


class TestResolveStationInstruments:
    """Tests for resolve_station_instruments."""

    def test_new_format_with_references(self, tmp_path):
        """Resolve new format with instrument file references."""
        # Create instrument file
        inst_content = dedent("""
            id: keithley_dmm_001
            protocol: visa
            driver: pymeasure.instruments.keithley.Keithley2000
            info:
              manufacturer: Keithley
              model: "2000"
              serial: ABC123
            calibration:
              due_date: 2025-06-15
              certificate: CAL-001
        """).strip()
        (tmp_path / "keithley_dmm_001.yaml").write_text(inst_content)

        # Load instrument files
        instrument_files = load_instrument_files(tmp_path)

        # Station config (new format)
        station_config = {
            "instruments": {
                "dmm": "keithley_dmm_001",
            },
            "resources": {
                "keithley_dmm_001": "GPIB::16::INSTR",
            },
        }

        records = resolve_station_instruments(station_config, instrument_files)

        assert len(records) == 1
        assert "dmm" in records

        dmm = records["dmm"]
        assert dmm.role == "dmm"
        assert dmm.instrument_id == "keithley_dmm_001"
        assert dmm.resource == "GPIB::16::INSTR"
        assert dmm.protocol == "visa"
        assert dmm.info.manufacturer == "Keithley"
        assert dmm.calibration.certificate == "CAL-001"

    def test_legacy_inline_format(self):
        """Resolve legacy format with inline instrument config."""
        station_config = {
            "instruments": {
                "dmm": {
                    "driver": "pymeasure.instruments.keithley.Keithley2000",
                    "resource": "GPIB::16::INSTR",
                    "protocol": "visa",
                    "info": {
                        "manufacturer": "Keithley",
                        "model": "2000",
                        "serial": "XYZ789",
                    },
                    "calibration": {
                        "due_date": "2025-12-31",
                    },
                }
            }
        }

        records = resolve_station_instruments(station_config, {})

        assert len(records) == 1
        dmm = records["dmm"]
        assert dmm.role == "dmm"
        assert dmm.resource == "GPIB::16::INSTR"
        assert dmm.info.serial == "XYZ789"
        assert dmm.calibration.due_date == date(2025, 12, 31)

    def test_mixed_formats(self, tmp_path):
        """Mix of new and legacy formats."""
        # Create instrument file for one
        inst_content = "id: psu_001\nprotocol: visa\n"
        (tmp_path / "psu_001.yaml").write_text(inst_content)

        instrument_files = load_instrument_files(tmp_path)

        station_config = {
            "instruments": {
                # Legacy inline
                "dmm": {
                    "driver": "mydriver.DMM",
                    "resource": "GPIB::16::INSTR",
                },
                # New reference
                "psu": "psu_001",
            },
            "resources": {
                "psu_001": "GPIB::17::INSTR",
            },
        }

        records = resolve_station_instruments(station_config, instrument_files)

        assert len(records) == 2
        assert records["dmm"].resource == "GPIB::16::INSTR"
        assert records["psu"].resource == "GPIB::17::INSTR"
