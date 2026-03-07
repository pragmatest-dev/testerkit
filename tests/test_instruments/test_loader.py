"""Tests for instrument and station configuration loader."""

from datetime import date
from textwrap import dedent

from litmus.instruments.loader import resolve_station_instruments
from litmus.instruments.models import CalibrationInfo, InstrumentInfo
from litmus.store import (
    load_instrument_asset as load_instrument_file,
)
from litmus.store import (
    load_instrument_files,
)


class TestInstrumentInfo:
    """Tests for InstrumentInfo Pydantic model."""

    def test_empty(self):
        """Empty model is falsy."""
        info = InstrumentInfo()
        assert not info

    def test_full_info(self):
        """model_validate all fields."""
        data = {
            "manufacturer": "Keithley",
            "model": "2400",
            "serial": "ABC123",
            "firmware": "A02",
        }
        info = InstrumentInfo.model_validate(data)
        assert info.manufacturer == "Keithley"
        assert info.model == "2400"
        assert info.serial == "ABC123"
        assert info.firmware == "A02"

    def test_numeric_fields_coerced(self):
        """Numeric model/serial coerced to string by Pydantic."""
        data = {"model": 2400, "serial": 12345}
        info = InstrumentInfo.model_validate(data)
        assert info.model == "2400"
        assert info.serial == "12345"


class TestCalibrationInfo:
    """Tests for CalibrationInfo Pydantic model."""

    def test_empty(self):
        """Empty model is falsy."""
        cal = CalibrationInfo()
        assert not cal

    def test_full_calibration(self):
        """model_validate all fields."""
        data = {
            "due_date": "2025-06-15",
            "last_cal": "2024-06-15",
            "certificate": "CAL-2024-001",
            "lab": "Acme Calibration",
        }
        cal = CalibrationInfo.model_validate(data)
        assert cal.due_date == date(2025, 6, 15)
        assert cal.last_cal == date(2024, 6, 15)
        assert cal.certificate == "CAL-2024-001"
        assert cal.lab == "Acme Calibration"

    def test_date_object_preserved(self):
        """Date objects passed through."""
        cal = CalibrationInfo.model_validate({"due_date": date(2025, 6, 15)})
        assert cal.due_date == date(2025, 6, 15)


class TestLoadInstrumentFile:
    """Tests for load_instrument_file (returns InstrumentAssetFile)."""

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

        asset = load_instrument_file(inst_file)

        assert asset.id == "keithley_dmm_001"
        assert asset.protocol == "visa"
        assert asset.driver == "pymeasure.instruments.keithley.Keithley2000"
        assert asset.info.manufacturer == "Keithley"
        assert asset.info.model == "2000"
        assert asset.calibration.due_date == date(2025, 6, 15)
        assert asset.calibration.certificate == "CAL-2024-001"

    def test_load_minimal_instrument(self, tmp_path):
        """Load minimal instrument file."""
        content = dedent("""
            id: my_instrument
            protocol: visa
        """).strip()

        inst_file = tmp_path / "my_instrument.yaml"
        inst_file.write_text(content)

        asset = load_instrument_file(inst_file)
        assert asset.id == "my_instrument"
        assert not asset.info  # Empty
        assert not asset.calibration  # Empty


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

    def test_resolves_with_asset_files(self, tmp_path):
        """Resolve StationConfig instruments enriched by asset files."""
        from litmus.schemas import StationConfig

        # Create instrument asset file
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

        instrument_files = load_instrument_files(tmp_path)

        station_config = StationConfig(
            id="test_station",
            name="Test Station",
            instruments={
                "dmm": {
                    "type": "dmm",
                    "driver": "pymeasure.instruments.keithley.Keithley2000",
                    "resource": "GPIB::16::INSTR",
                },
            },
        )

        records = resolve_station_instruments(station_config, instrument_files)

        assert len(records) == 1
        assert "dmm" in records

        dmm = records["dmm"]
        assert dmm.role == "dmm"
        assert dmm.resource == "GPIB::16::INSTR"
        assert dmm.protocol == "visa"
        assert dmm.driver == "pymeasure.instruments.keithley.Keithley2000"

    def test_resolves_without_asset_files(self):
        """Resolve StationConfig instruments with no asset files."""
        from litmus.schemas import StationConfig

        station_config = StationConfig(
            id="test_station",
            name="Test Station",
            instruments={
                "dmm": {
                    "type": "dmm",
                    "driver": "pymeasure.instruments.keithley.Keithley2000",
                    "resource": "GPIB::16::INSTR",
                },
            },
        )

        records = resolve_station_instruments(station_config, {})

        assert len(records) == 1
        dmm = records["dmm"]
        assert dmm.role == "dmm"
        assert dmm.resource == "GPIB::16::INSTR"
        assert dmm.driver == "pymeasure.instruments.keithley.Keithley2000"

    def test_asset_file_enriches_calibration(self, tmp_path):
        """Asset file provides calibration and identity info."""
        from litmus.schemas import StationConfig

        inst_content = dedent("""
            id: dmm_asset
            protocol: visa
            driver: pymeasure.instruments.keithley.Keithley2000
            info:
              manufacturer: Keithley
              model: "2000"
              serial: XYZ789
            calibration:
              due_date: 2025-12-31
        """).strip()
        (tmp_path / "dmm_asset.yaml").write_text(inst_content)

        instrument_files = load_instrument_files(tmp_path)

        station_config = StationConfig(
            id="test_station",
            name="Test Station",
            instruments={
                "dmm": {
                    "type": "dmm",
                    "driver": "pymeasure.instruments.keithley.Keithley2000",
                    "resource": "GPIB::16::INSTR",
                },
            },
        )

        records = resolve_station_instruments(station_config, instrument_files)

        # Without matching asset file key, no enrichment
        dmm = records["dmm"]
        assert dmm.info.serial is None

    def test_multiple_instruments(self, tmp_path):
        """Multiple instruments in one station."""
        from litmus.schemas import StationConfig

        station_config = StationConfig(
            id="test_station",
            name="Test Station",
            instruments={
                "dmm": {
                    "type": "dmm",
                    "driver": "mydriver.DMM",
                    "resource": "GPIB::16::INSTR",
                },
                "psu": {
                    "type": "psu",
                    "driver": "mydriver.PSU",
                    "resource": "GPIB::17::INSTR",
                },
            },
        )

        records = resolve_station_instruments(station_config, {})

        assert len(records) == 2
        assert records["dmm"].resource == "GPIB::16::INSTR"
        assert records["psu"].resource == "GPIB::17::INSTR"
