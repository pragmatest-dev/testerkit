"""Tests for the instrument catalog loader."""

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

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
        assert entry.type == "dmm"
        assert len(entry.capabilities) > 0

        # Should have dc_voltage input capability
        dc_v_caps = [
            c for c in entry.capabilities
            if c.function == MeasurementFunction.DC_VOLTAGE
            and c.direction == Direction.INPUT
        ]
        assert len(dc_v_caps) >= 1
        cap = dc_v_caps[0]
        assert "voltage" in cap.signals
        assert cap.signals["voltage"].range is not None
        assert cap.signals["voltage"].range.max >= 1000

    def test_load_keysight_e36312a(self):
        """Load the Keysight E36312A PSU catalog entry."""
        path = CATALOG_DIR / "keysight_e36312a.yaml"
        if not path.exists():
            return
        entry = load_catalog_entry(path)

        assert entry.id == "keysight_e36312a"
        assert entry.type == "dc_power"
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
        assert entry.type == "scope"

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

    def test_new_measurement_functions(self):
        """New enum values exist."""
        assert MeasurementFunction.DIODE == "diode"
        assert MeasurementFunction.CONTINUITY == "continuity"


# ---------------------------------------------------------------------------
# Helper to write YAML fixture files
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, text: str) -> Path:
    path.write_text(dedent(text))
    return path


# ---------------------------------------------------------------------------
# Variant inheritance tests
# ---------------------------------------------------------------------------


class TestCatalogInheritance:
    """Tests for variant inheritance via the ``base`` field."""

    def _base_yaml(self) -> str:
        return """\
            catalog_entry:
              id: base_dmm
              manufacturer: Acme
              model: "1000"
              name: "Acme 1000 DMM"
              type: dmm
              channels:
                "1":
                  terminals: [hi, lo]
                  connector: binding_post
                  ground: shared

            capabilities:
              - function: dc_voltage
                direction: input
                signals:
                  voltage:
                    range: {min: 0.001, max: 100, units: V}
                    accuracy: {pct_reading: 0.01}
                channels: ["1"]
        """

    def test_variant_inherits_capabilities(self, tmp_path):
        """Variant without capabilities: gets base's."""
        _write_yaml(tmp_path / "base_dmm.yaml", self._base_yaml())
        _write_yaml(tmp_path / "variant.yaml", """\
            catalog_entry:
              id: variant_dmm
              model: "1001"
              name: "Acme 1001 DMM"
              base: base_dmm
              channels:
                "1":
                  terminals: [hi, lo]
                  connector: bnc
                  ground: shared
        """)
        entry = load_catalog_entry(tmp_path / "variant.yaml", catalog_dir=tmp_path)
        assert entry.id == "variant_dmm"
        assert len(entry.capabilities) == 1
        assert entry.capabilities[0].function == MeasurementFunction.DC_VOLTAGE

    def test_variant_overrides_channels(self, tmp_path):
        """Variant with channels: replaces base's."""
        _write_yaml(tmp_path / "base_dmm.yaml", self._base_yaml())
        _write_yaml(tmp_path / "variant.yaml", """\
            catalog_entry:
              id: variant_dmm
              model: "1001"
              base: base_dmm
              channels:
                "A":
                  terminals: [signal]
                  connector: bnc
                  ground: earth
        """)
        entry = load_catalog_entry(tmp_path / "variant.yaml", catalog_dir=tmp_path)
        assert list(entry.channels.keys()) == ["A"]

    def test_variant_overrides_capabilities(self, tmp_path):
        """Variant with capabilities: replaces base's."""
        _write_yaml(tmp_path / "base_dmm.yaml", self._base_yaml())
        _write_yaml(tmp_path / "variant.yaml", """\
            catalog_entry:
              id: variant_dmm
              model: "2000"
              base: base_dmm

            capabilities:
              - function: ac_voltage
                direction: input
                signals:
                  voltage:
                    range: {min: 0.01, max: 750, units: V}
                channels: ["1"]
        """)
        entry = load_catalog_entry(tmp_path / "variant.yaml", catalog_dir=tmp_path)
        assert len(entry.capabilities) == 1
        assert entry.capabilities[0].function == MeasurementFunction.AC_VOLTAGE

    def test_variant_inherits_header_fields(self, tmp_path):
        """manufacturer, type inherited from base."""
        _write_yaml(tmp_path / "base_dmm.yaml", self._base_yaml())
        _write_yaml(tmp_path / "variant.yaml", """\
            catalog_entry:
              id: variant_dmm
              model: "1001"
              base: base_dmm
        """)
        entry = load_catalog_entry(tmp_path / "variant.yaml", catalog_dir=tmp_path)
        assert entry.manufacturer == "Acme"
        assert entry.type == "dmm"

    def test_circular_inheritance_raises(self, tmp_path):
        """Circular base references raise ValueError."""
        _write_yaml(tmp_path / "a.yaml", """\
            catalog_entry:
              id: a
              manufacturer: X
              model: "A"
              name: A
              type: dmm
              base: b
        """)
        _write_yaml(tmp_path / "b.yaml", """\
            catalog_entry:
              id: b
              manufacturer: X
              model: "B"
              name: B
              type: dmm
              base: a
        """)
        with pytest.raises(ValueError, match="[Cc]ircular"):
            load_catalog_entry(tmp_path / "a.yaml", catalog_dir=tmp_path)

    def test_missing_base_raises(self, tmp_path):
        """Non-existent base raises ValueError."""
        _write_yaml(tmp_path / "orphan.yaml", """\
            catalog_entry:
              id: orphan
              manufacturer: X
              model: "O"
              name: Orphan
              type: dmm
              base: does_not_exist
        """)
        with pytest.raises(ValueError, match="not found"):
            load_catalog_entry(tmp_path / "orphan.yaml", catalog_dir=tmp_path)

    def test_recursive_inheritance(self, tmp_path):
        """A → B → C chain merges correctly."""
        _write_yaml(tmp_path / "c.yaml", """\
            catalog_entry:
              id: c
              manufacturer: Acme
              model: "C"
              name: "Acme C"
              type: dmm
              channels:
                "1":
                  terminals: [hi, lo]
                  connector: binding_post
                  ground: shared

            capabilities:
              - function: dc_voltage
                direction: input
                signals:
                  voltage:
                    range: {min: 0.001, max: 100, units: V}
                channels: ["1"]
        """)
        _write_yaml(tmp_path / "b.yaml", """\
            catalog_entry:
              id: b
              model: "B"
              name: "Acme B"
              base: c
              channels:
                "1":
                  terminals: [hi, lo, sense_hi, sense_lo]
                  connector: binding_post
                  ground: shared
        """)
        _write_yaml(tmp_path / "a.yaml", """\
            catalog_entry:
              id: a
              model: "A"
              name: "Acme A"
              base: b
        """)
        entry = load_catalog_entry(tmp_path / "a.yaml", catalog_dir=tmp_path)
        assert entry.id == "a"
        assert entry.manufacturer == "Acme"  # from C
        assert entry.type == "dmm"  # from C
        # Channels from B (overrode C), inherited by A
        assert len(entry.channels["1"].terminals) == 4
        # Capabilities from C, inherited through B to A
        assert len(entry.capabilities) == 1


# ---------------------------------------------------------------------------
# Parametrized: load every catalog/*.yaml
# ---------------------------------------------------------------------------


def _catalog_yaml_files():
    """Collect all catalog YAML files for parametrized test."""
    if not CATALOG_DIR.exists():
        return []
    return sorted(CATALOG_DIR.glob("**/*.yaml"))


@pytest.mark.parametrize(
    "yaml_path",
    _catalog_yaml_files(),
    ids=lambda p: p.stem,
)
class TestLoadAllCatalogEntries:
    """Every catalog YAML file must load without error."""

    def test_loads_successfully(self, yaml_path):
        entry = load_catalog_entry(yaml_path, catalog_dir=CATALOG_DIR)
        assert entry.id
        assert entry.manufacturer
