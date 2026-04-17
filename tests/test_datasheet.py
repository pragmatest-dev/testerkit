"""Tests for catalog datasheet generation."""

from pathlib import Path

import pytest

from litmus.reports.datasheet import (
    build_signal_render,
    fmt_accuracy,
    fmt_attr,
    fmt_range,
    fmt_resolution,
    fmt_si,
    generate_datasheet,
    load_datasheet_data,
)
from litmus.store import load_catalog_entry

CATALOG_DIR = Path(__file__).parent.parent / "demo" / "catalog"


class TestFmtSi:
    def test_megahertz(self):
        assert fmt_si(1000000, "Hz") == "1 MHz"

    def test_gigahertz(self):
        assert fmt_si(54000000000, "Hz") == "54 GHz"

    def test_kilohertz(self):
        assert fmt_si(250000, "Hz") == "250 kHz"

    def test_millivolt(self):
        assert fmt_si(0.001, "V") == "1 mV"

    def test_zero(self):
        assert fmt_si(0, "Hz") == "0 Hz"

    def test_none(self):
        assert fmt_si(None, "Hz") == "—"

    def test_no_units(self):
        assert fmt_si(1000, "") == "1000"

    def test_non_si_units(self):
        # Units like degC should not get SI prefixed
        assert fmt_si(55, "degC") == "55 degC"

    def test_plain_hz(self):
        assert fmt_si(100, "Hz") == "100 Hz"


class TestFmtAccuracy:
    def test_pct_reading_and_range(self):
        result = fmt_accuracy({"pct_reading": 0.05, "pct_range": 0.01})
        assert "0.05% rdg" in result
        assert "0.01% rng" in result

    def test_absolute_with_units(self):
        result = fmt_accuracy({"absolute": 0.6, "units": "dB"})
        assert "0.6 dB" in result

    def test_none(self):
        assert fmt_accuracy(None) == "—"

    def test_empty(self):
        assert fmt_accuracy({}) == "—"


class TestFmtRange:
    def test_basic_range(self):
        result = fmt_range({"min": 0.1, "max": 1000, "units": "V"})
        assert "0.1" in result or "100 m" in result
        assert "1 kV" in result or "1000" in result

    def test_hz_range(self):
        result = fmt_range({"min": 250000, "max": 20000000000, "units": "Hz"})
        assert "250 k" in result
        assert "20 G" in result

    def test_none(self):
        assert fmt_range(None) == "—"


class TestFmtResolution:
    def test_digits(self):
        assert fmt_resolution({"digits": 6.5}) == "6.5 digits"

    def test_value_with_units(self):
        result = fmt_resolution({"value": 0.001, "units": "V"})
        assert "1 mV" in result

    def test_none(self):
        assert fmt_resolution(None) == "—"

    def test_empty(self):
        assert fmt_resolution({}) == "—"


class TestFmtAttr:
    def test_numeric_with_si_units(self):
        result = fmt_attr({"value": 1000000, "units": "Hz"})
        assert result == "1 MHz"

    def test_string_value(self):
        assert fmt_attr({"value": "enabled"}) == "enabled"

    def test_range(self):
        result = fmt_attr({"range": {"min": 0, "max": 100, "units": "V"}})
        assert "V" in result

    def test_options(self):
        result = fmt_attr({"options": ["fast", "slow"]})
        assert "fast" in result and "slow" in result

    def test_none(self):
        assert fmt_attr(None) == "—"


class TestBuildSignalRender:
    def test_no_specs(self):
        render = build_signal_render(
            "voltage",
            {
                "range": {"min": 0, "max": 10, "units": "V"},
                "accuracy": None,
                "resolution": None,
            },
        )
        assert render["headline"]["range"] != "—"
        assert render["tables"] == []

    def test_1d_specs(self):
        """Bands with a single when key produce a 1D table."""
        sig = {
            "range": {"min": 9000, "max": 8.5e9, "units": "Hz"},
            "accuracy": None,
            "resolution": None,
            "specs": [
                {
                    "when": {"option": "503"},
                    "range": {"min": 9000, "max": 3e9, "units": "Hz"},
                    "accuracy": None,
                    "resolution": None,
                },
                {
                    "when": {"option": "506"},
                    "range": {"min": 9000, "max": 6e9, "units": "Hz"},
                    "accuracy": None,
                    "resolution": None,
                },
            ],
        }
        render = build_signal_render("frequency", sig)
        assert len(render["tables"]) == 1
        tbl = render["tables"][0]
        assert tbl["kind"] == "1d"
        assert len(tbl["rows"]) == 2
        assert tbl["row_key"] == "Option"

    def test_2d_specs(self):
        """Bands with two when keys produce a 2D matrix."""
        sig = {
            "range": None,
            "accuracy": None,
            "resolution": None,
            "specs": [
                {
                    "when": {"voltage_range": 0.1, "cal": "24h"},
                    "range": None,
                    "accuracy": {"pct_reading": 0.003, "pct_range": 0.003},
                    "resolution": None,
                },
                {
                    "when": {"voltage_range": 0.1, "cal": "1yr"},
                    "range": None,
                    "accuracy": {"pct_reading": 0.005, "pct_range": 0.0035},
                    "resolution": None,
                },
                {
                    "when": {"voltage_range": 1.0, "cal": "24h"},
                    "range": None,
                    "accuracy": {"pct_reading": 0.002, "pct_range": 0.0005},
                    "resolution": None,
                },
                {
                    "when": {"voltage_range": 1.0, "cal": "1yr"},
                    "range": None,
                    "accuracy": {"pct_reading": 0.004, "pct_range": 0.001},
                    "resolution": None,
                },
            ],
        }
        render = build_signal_render("voltage", sig)
        assert len(render["tables"]) == 1
        tbl = render["tables"][0]
        assert tbl["kind"] == "2d"
        assert len(tbl["rows"]) == 2
        assert len(tbl["col_headers"]) == 2

    def test_separate_tables_per_output_field(self):
        """Bands with different output fields produce separate tables (one per output type)."""
        sig = {
            "range": {"min": 0, "max": 100, "units": "V"},
            "accuracy": {"pct_reading": 1.0},
            "resolution": None,
            "specs": [
                {
                    "when": {"freq": "low"},
                    "range": {"min": 0, "max": 50, "units": "V"},
                    "accuracy": None,
                    "resolution": None,
                },
                {
                    "when": {"freq": "high"},
                    "range": None,
                    "accuracy": {"pct_reading": 2.0},
                    "resolution": None,
                },
            ],
        }
        render = build_signal_render("voltage", sig)
        # Different output fields → separate tables
        assert len(render["tables"]) == 2

    def test_mixed_when_key_sets_merge_into_superset(self):
        """Bands with different when-key-sets merge into one superset table."""
        sig = {
            "range": None,
            "accuracy": None,
            "resolution": None,
            "specs": [
                {
                    "when": {"freq": "low"},
                    "range": {"min": 0, "max": 50, "units": "V"},
                    "accuracy": None,
                    "resolution": None,
                },
                {
                    "when": {"power": "high", "temp": "hot"},
                    "accuracy": {"absolute": 1.0, "units": "dB"},
                    "range": None,
                    "resolution": None,
                },
            ],
        }
        render = build_signal_render("voltage", sig)
        # Different output fields (range vs accuracy) → separate tables
        assert len(render["tables"]) == 2


class TestLoadDatasheetData:
    @pytest.fixture(
        params=[
            "generic_dmm.yaml",
            "generic_psu.yaml",
            "generic_oscilloscope.yaml",
        ]
    )
    def catalog_path(self, request):
        path = CATALOG_DIR / request.param
        assert path.exists(), f"Catalog file not found: {path}"
        return path

    def test_loads_successfully(self, catalog_path):
        data = load_datasheet_data(catalog_path)
        assert data.entry.manufacturer is not None
        assert data.summary.capability_count >= 0

    def test_entry_has_expected_keys(self, catalog_path):
        data = load_datasheet_data(catalog_path)
        assert data.entry.id is not None
        assert data.entry.model is not None
        assert data.entry.capabilities is not None

    def test_signal_renders_present(self, catalog_path):
        data = load_datasheet_data(catalog_path)
        for cap_render in data.cap_renders:
            for sig_name in cap_render.get("signal_renders") or {}:
                render = cap_render["signal_renders"][sig_name]
                assert "headline" in render
                assert "tables" in render


class TestGenerateDatasheet:
    @pytest.fixture(
        params=[
            "generic_dmm.yaml",
            "generic_psu.yaml",
            "generic_oscilloscope.yaml",
        ]
    )
    def catalog_path(self, request):
        path = CATALOG_DIR / request.param
        assert path.exists(), f"Catalog file not found: {path}"
        return path

    def test_generates_html(self, catalog_path, tmp_path):
        entry = load_catalog_entry(catalog_path)
        out = generate_datasheet(catalog_path, tmp_path / "test.html")
        assert out.exists()
        html = out.read_text()
        assert "<!DOCTYPE html>" in html
        assert entry.model in html

    def test_html_has_sections(self, catalog_path, tmp_path):
        out = generate_datasheet(catalog_path, tmp_path / "test.html")
        html = out.read_text()
        assert "Capabilities" in html
        assert "Channels" in html or "channels" in html.lower()
