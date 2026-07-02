"""Tests for the instrument catalog loader."""

from pathlib import Path
from textwrap import dedent

import pytest

from litmus.models.capability import CATALOG_SCHEMA_VERSION
from litmus.models.enums import Direction, MeasurementFunction
from litmus.store import load_catalog_entry, load_catalog_from_directory

CATALOG_DIR = Path(__file__).parent.parent.parent / "src" / "litmus" / "catalog" / "generic"


class TestSchemaVersion:
    """Pin the catalog schema version. Bumping requires a deliberate migration plan."""

    def test_schema_version_pinned(self):
        """``CATALOG_SCHEMA_VERSION`` is the public freeze marker for catalog YAML.

        Reset to ``"1.0"`` at 0.3.0 as the designed baseline (schema version
        decoupled from package version). MINOR is additive; MAJOR is a breaking
        reshape that must pair with a migration tool and a release-note callout.
        If this test fails, you're moving the marker on purpose.
        """
        assert CATALOG_SCHEMA_VERSION == "1.0"


class TestLoadCatalogEntry:
    """Tests for loading individual catalog entries."""

    def test_load_generic_dmm(self):
        """Load the generic DMM catalog entry."""
        path = CATALOG_DIR / "generic_dmm.yaml"
        entry = load_catalog_entry(path)

        assert entry.id == "generic_dmm"
        assert entry.manufacturer == "Generic"
        assert entry.type == "dmm"
        assert len(entry.capabilities) > 0

        dc_v_caps = [
            c
            for c in entry.capabilities
            if c.function == MeasurementFunction.DC_VOLTAGE and c.direction == Direction.INPUT
        ]
        assert len(dc_v_caps) >= 1
        cap = dc_v_caps[0]
        assert "voltage" in cap.signals

    def test_load_generic_psu(self):
        """Load the generic PSU catalog entry."""
        path = CATALOG_DIR / "generic_psu.yaml"
        entry = load_catalog_entry(path)

        assert entry.id == "generic_psu"
        assert entry.type == "psu"
        assert len(entry.capabilities) > 0

        dc_v_out = [
            c
            for c in entry.capabilities
            if c.function == MeasurementFunction.DC_VOLTAGE and c.direction == Direction.OUTPUT
        ]
        assert len(dc_v_out) >= 1

    def test_load_generic_oscilloscope(self):
        """Load the generic oscilloscope catalog entry."""
        path = CATALOG_DIR / "generic_oscilloscope.yaml"
        entry = load_catalog_entry(path)

        assert entry.id == "generic_oscilloscope"
        assert entry.type == "scope"

        waveform_caps = [
            c for c in entry.capabilities if c.function == MeasurementFunction.WAVEFORM
        ]
        assert len(waveform_caps) >= 1


class TestLoadCatalogFromDirectory:
    """Tests for loading all catalog entries from a directory."""

    def test_load_all_entries(self):
        """Load all demo catalog entries."""
        entries = load_catalog_from_directory(CATALOG_DIR)

        assert len(entries) >= 3
        assert "generic_dmm" in entries

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
                    range: {min: 0.001, max: 100, unit: V}
                    accuracy: {pct_reading: 0.01}
                channels: ["1"]
        """

    def test_variant_inherits_capabilities(self, tmp_path):
        """Variant without capabilities: gets base's."""
        _write_yaml(tmp_path / "base_dmm.yaml", self._base_yaml())
        _write_yaml(
            tmp_path / "variant_dmm.yaml",
            """\
            id: variant_dmm
            model: "1001"
            name: "Acme 1001 DMM"
            base: base_dmm
            channels:
              "1":
                terminals: [hi, lo]
                connector: bnc
                ground: shared
        """,
        )
        entry = load_catalog_entry(tmp_path / "variant_dmm.yaml", catalog_dir=tmp_path)
        assert entry.id == "variant_dmm"
        assert len(entry.capabilities) == 1
        assert entry.capabilities[0].function == MeasurementFunction.DC_VOLTAGE

    def test_variant_overrides_channels(self, tmp_path):
        """Variant with channels: replaces base's."""
        _write_yaml(tmp_path / "base_dmm.yaml", self._base_yaml())
        _write_yaml(
            tmp_path / "variant_dmm.yaml",
            """\
            id: variant_dmm
            model: "1001"
            base: base_dmm
            channels:
              "A":
                terminals: [signal]
                connector: bnc
                ground: earth
        """,
        )
        entry = load_catalog_entry(tmp_path / "variant_dmm.yaml", catalog_dir=tmp_path)
        assert list(entry.channels.keys()) == ["A"]

    def test_variant_merges_capabilities(self, tmp_path):
        """Variant capabilities merge with base.

        New functions appended, matching functions merged.
        """
        _write_yaml(tmp_path / "base_dmm.yaml", self._base_yaml())
        _write_yaml(
            tmp_path / "variant_dmm.yaml",
            """\
            id: variant_dmm
            model: "2000"
            base: base_dmm
            capabilities:
              - function: ac_voltage
                direction: input
                signals:
                  voltage:
                    range: {min: 0.01, max: 750, unit: V}
                channels: ["1"]
        """,
        )
        entry = load_catalog_entry(tmp_path / "variant_dmm.yaml", catalog_dir=tmp_path)
        assert len(entry.capabilities) == 2
        funcs = {c.function for c in entry.capabilities}
        assert MeasurementFunction.DC_VOLTAGE in funcs  # inherited from base
        assert MeasurementFunction.AC_VOLTAGE in funcs  # added by variant

    def test_variant_appends_signal_bands(self, tmp_path):
        """A variant redeclaring a matching capability/signal appends its
        bands to the base's (deep-merge of deltas), keeping base-only keys."""
        _write_yaml(
            tmp_path / "base_dmm.yaml",
            """\
            id: base_dmm
            manufacturer: Acme
            model: "1000"
            type: dmm
            channels:
              "1": {terminals: [hi, lo], connector: binding_post, ground: shared}
            capabilities:
              - function: dc_voltage
                direction: input
                signals:
                  voltage:
                    range: {min: 0.001, max: 100, unit: V}
                    bands:
                      - when: {frequency: {min: 50, max: 50, unit: Hz}}
                        accuracy: {pct_reading: 0.01}
                conditions:
                  frequency: {range: {min: 50, max: 60, unit: Hz}}
                channels: ["1"]
        """,
        )
        _write_yaml(
            tmp_path / "variant_dmm.yaml",
            """\
            id: variant_dmm
            model: "2000"
            base: base_dmm
            capabilities:
              - function: dc_voltage
                direction: input
                signals:
                  voltage:
                    bands:
                      - when: {frequency: {min: 60, max: 60, unit: Hz}}
                        accuracy: {pct_reading: 0.02}
        """,
        )
        entry = load_catalog_entry(tmp_path / "variant_dmm.yaml", catalog_dir=tmp_path)
        (cap,) = entry.capabilities
        voltage = cap.signals["voltage"]
        assert voltage.bands is not None
        assert len(voltage.bands) == 2  # base band + variant band appended
        assert voltage.range is not None  # base-only key survived the delta merge

    def test_variant_inherits_header_fields(self, tmp_path):
        """manufacturer, type inherited from base."""
        _write_yaml(tmp_path / "base_dmm.yaml", self._base_yaml())
        _write_yaml(
            tmp_path / "variant_dmm.yaml",
            """\
            id: variant_dmm
            model: "1001"
            base: base_dmm
        """,
        )
        entry = load_catalog_entry(tmp_path / "variant_dmm.yaml", catalog_dir=tmp_path)
        assert entry.manufacturer == "Acme"
        assert entry.type == "dmm"

    def test_circular_inheritance_raises(self, tmp_path):
        """Circular base references raise ValueError."""
        _write_yaml(
            tmp_path / "a.yaml",
            """\
            id: a
            manufacturer: X
            model: "A"
            name: A
            type: dmm
            base: b
        """,
        )
        _write_yaml(
            tmp_path / "b.yaml",
            """\
            id: b
            manufacturer: X
            model: "B"
            name: B
            type: dmm
            base: a
        """,
        )
        with pytest.raises(ValueError, match="[Cc]ircular"):
            load_catalog_entry(tmp_path / "a.yaml", catalog_dir=tmp_path)

    def test_missing_base_raises(self, tmp_path):
        """Non-existent base raises ValueError."""
        _write_yaml(
            tmp_path / "orphan.yaml",
            """\
            id: orphan
            manufacturer: X
            model: "O"
            name: Orphan
            type: dmm
            base: does_not_exist
        """,
        )
        with pytest.raises(ValueError, match="not found"):
            load_catalog_entry(tmp_path / "orphan.yaml", catalog_dir=tmp_path)

    def test_recursive_inheritance(self, tmp_path):
        """A → B → C chain merges correctly."""
        _write_yaml(
            tmp_path / "c.yaml",
            """\
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
                    range: {min: 0.001, max: 100, unit: V}
                channels: ["1"]
        """,
        )
        _write_yaml(
            tmp_path / "b.yaml",
            """\
            id: b
            model: "B"
            name: "Acme B"
            base: c
            channels:
              "1":
                terminals: [hi, lo, sense_hi, sense_lo]
                connector: binding_post
                ground: shared
        """,
        )
        _write_yaml(
            tmp_path / "a.yaml",
            """\
            id: a
            model: "A"
            name: "Acme A"
            base: b
        """,
        )
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
    return sorted(
        p
        for p in CATALOG_DIR.glob("**/*.yaml")
        if not p.name.startswith("_") and ".variants." not in p.name
    )


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


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestCatalogValidation:
    """Tests that Pydantic validation catches bad YAML."""

    def test_top_level_capabilities_rejected(self, tmp_path):
        """capabilities as extra key alongside model fields is rejected by Pydantic."""
        _write_yaml(
            tmp_path / "bad.yaml",
            """\
            id: bad
            manufacturer: X
            model: "1"
            name: Bad
            type: dmm
            capabilities:
              - function: dc_voltage
                direction: input
        """,
        )
        # This should load fine — capabilities is a valid field at root
        entry = load_catalog_entry(tmp_path / "bad.yaml")
        assert entry.id == "bad"
        assert len(entry.capabilities) == 1

    def test_unknown_key_raises(self, tmp_path):
        """Unknown key raises ValidationError."""
        _write_yaml(
            tmp_path / "bad.yaml",
            """\
            id: bad
            manufacturer: X
            model: "1"
            name: Bad
            type: dmm
            bogus_field: 42
        """,
        )
        with pytest.raises(Exception, match="bogus_field"):
            load_catalog_entry(tmp_path / "bad.yaml")

    def test_interfaces_field_loads(self, tmp_path):
        """interfaces field is accepted and parsed."""
        _write_yaml(
            tmp_path / "iface.yaml",
            """\
            id: iface
            manufacturer: X
            model: "1"
            name: Test
            type: dmm
            interfaces: [usb, gpib]
        """,
        )
        entry = load_catalog_entry(tmp_path / "iface.yaml")
        assert entry.interfaces == ["usb", "gpib"]
