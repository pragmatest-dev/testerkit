"""Tests for SpecBand YAML parsing in catalog loader."""

from pathlib import Path

from litmus.models.config import Signal
from litmus.store import load_catalog_entry


def test_parse_parameter_with_specs():
    """SpecBand list parsed from YAML dict."""
    data = {
        "range": {"min": 0.1, "max": 750, "units": "V"},
        "accuracy": {"pct_reading": 0.07, "pct_range": 0.02},
        "specs": [
            {
                "when": {"frequency": {"min": 3, "max": 5, "units": "Hz"}},
                "accuracy": {"pct_reading": 0.35, "pct_range": 0.03},
            },
            {
                "when": {"frequency": {"min": 5, "max": 300, "units": "Hz"}},
                "accuracy": {"pct_reading": 0.07, "pct_range": 0.02},
            },
        ],
    }
    param = Signal(**data)
    assert param.specs is not None
    assert len(param.specs) == 2
    assert param.specs[0].when["frequency"].min == 3
    assert param.specs[0].accuracy.pct_reading == 0.35
    assert param.specs[1].when["frequency"].max == 300


def test_parse_parameter_without_specs():
    """Backward compat: no specs field → specs=None."""
    data = {
        "range": {"min": 0, "max": 100, "units": "V"},
        "accuracy": {"pct_reading": 0.01},
    }
    param = Signal(**data)
    assert param.specs is None


def test_parse_34461a_ac_voltage_bands():
    """Real catalog entry round-trip: 34461A ac_voltage has frequency bands."""
    catalog_dir = Path(__file__).resolve().parents[2] / "catalog"
    path = catalog_dir / "keysight_34461a.yaml"
    if not path.exists():
        return  # Skip if catalog not present

    entry = load_catalog_entry(path, catalog_dir=catalog_dir)

    # Find ac_voltage capability
    ac_cap = None
    for cap in entry.capabilities:
        if cap.function.value == "ac_voltage":
            ac_cap = cap
            break

    assert ac_cap is not None, "ac_voltage capability not found"
    voltage_param = ac_cap.signals.get("voltage")
    assert voltage_param is not None
    assert voltage_param.specs is not None
    assert len(voltage_param.specs) == 4

    # First band: 3-5 Hz, worst accuracy
    band0 = voltage_param.specs[0]
    assert band0.when["frequency"].min == 3
    assert band0.when["frequency"].max == 5
    assert band0.accuracy.pct_reading == 0.35

    # Frequency should be in conditions dict
    freq_cond = ac_cap.conditions.get("frequency")
    assert freq_cond is not None
