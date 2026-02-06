"""Tests for the instrument catalog loader."""

from pathlib import Path

from litmus.catalog.loader import load_catalog_entry, load_catalog_from_directory
from litmus.config.models import Direction, MeasurementFunction

CATALOG_DIR = Path(__file__).parent.parent.parent / "catalog"


class TestLoadCatalogEntry:
    """Tests for loading individual catalog entries."""

    def test_load_keysight_34461a(self):
        """Load the Keysight 34461A DMM catalog entry."""
        path = CATALOG_DIR / "keysight_34461a.yaml"
        if not path.exists():
            return  # Skip if catalog not present
        entry = load_catalog_entry(path)

        assert entry.id == "keysight_34461a"
        assert entry.manufacturer == "Keysight"
        assert entry.model == "34461A"
        assert entry.instrument_class == "dmm"
        assert len(entry.capabilities) > 0

        # Should have dc_voltage input capability
        dc_v_caps = [
            c for c in entry.capabilities
            if c.function == MeasurementFunction.DC_VOLTAGE
            and c.direction == Direction.INPUT
        ]
        assert len(dc_v_caps) >= 1
        cap = dc_v_caps[0]
        assert "voltage" in cap.parameters
        assert cap.parameters["voltage"].range is not None
        assert cap.parameters["voltage"].range.max >= 1000

    def test_load_keysight_e36312a(self):
        """Load the Keysight E36312A PSU catalog entry."""
        path = CATALOG_DIR / "keysight_e36312a.yaml"
        if not path.exists():
            return
        entry = load_catalog_entry(path)

        assert entry.id == "keysight_e36312a"
        assert entry.instrument_class == "dc_power"
        assert len(entry.capabilities) > 0

        # Should have dc_voltage output capability
        dc_v_out = [
            c for c in entry.capabilities
            if c.function == MeasurementFunction.DC_VOLTAGE
            and c.direction == Direction.OUTPUT
        ]
        assert len(dc_v_out) >= 1

    def test_load_rigol_ds1054z(self):
        """Load the Rigol DS1054Z scope catalog entry."""
        path = CATALOG_DIR / "rigol_ds1054z.yaml"
        if not path.exists():
            return
        entry = load_catalog_entry(path)

        assert entry.id == "rigol_ds1054z"
        assert entry.instrument_class == "scope"

        # Should have waveform input capability
        waveform_caps = [
            c for c in entry.capabilities
            if c.function == MeasurementFunction.WAVEFORM
        ]
        assert len(waveform_caps) >= 1


class TestLoadCatalogFromDirectory:
    """Tests for loading all catalog entries from a directory."""

    def test_load_all_entries(self):
        """Load all seed catalog entries."""
        if not CATALOG_DIR.exists():
            return
        entries = load_catalog_from_directory(CATALOG_DIR)

        assert len(entries) >= 4  # At least the seed entries
        assert "keysight_34461a" in entries
        assert "keysight_e36312a" in entries

    def test_empty_directory(self, tmp_path):
        """Loading from empty directory returns empty dict."""
        entries = load_catalog_from_directory(tmp_path)
        assert entries == {}

    def test_nonexistent_directory(self, tmp_path):
        """Loading from nonexistent directory returns empty dict."""
        entries = load_catalog_from_directory(tmp_path / "does_not_exist")
        assert entries == {}


class TestCatalogModels:
    """Tests for catalog model validation."""

    def test_measurement_function_enum_values(self):
        """Key measurement functions exist in the enum."""
        assert MeasurementFunction.DC_VOLTAGE == "dc_voltage"
        assert MeasurementFunction.AC_VOLTAGE == "ac_voltage"
        assert MeasurementFunction.DC_CURRENT == "dc_current"
        assert MeasurementFunction.RESISTANCE == "resistance"
        assert MeasurementFunction.RESISTANCE_4W == "resistance_4w"
        assert MeasurementFunction.WAVEFORM == "waveform"
        assert MeasurementFunction.FREQUENCY == "frequency"
        assert MeasurementFunction.TEMPERATURE == "temperature"
        assert MeasurementFunction.DC_POWER == "dc_power"

    def test_direction_enum_values(self):
        """Direction enum values unchanged."""
        assert Direction.INPUT == "input"
        assert Direction.OUTPUT == "output"
        assert Direction.BIDIR == "bidir"
