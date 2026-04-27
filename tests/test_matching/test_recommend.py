"""Tests for catalog recommendation mode of litmus_match."""

from pathlib import Path
from unittest.mock import patch

from litmus.matching.service import recommend_from_catalog
from litmus.models.capability import (
    AccuracySpec,
    Condition,
    InstrumentCapability,
    RangeSpec,
    ResolutionSpec,
    Signal,
    SpecBand,
)
from litmus.models.catalog import InstrumentCatalogEntry
from litmus.models.enums import Direction, MeasurementFunction


def _make_entry(
    entry_id: str,
    manufacturer: str,
    model: str,
    type: str,
    capabilities: list[InstrumentCapability],
) -> InstrumentCatalogEntry:
    return InstrumentCatalogEntry(
        id=entry_id,
        manufacturer=manufacturer,
        model=model,
        name=f"{manufacturer} {model}",
        type=type,
        capabilities=capabilities,
    )


def _make_cap(
    function: str,
    direction: str,
    range_max: float | None = None,
    range_min: float | None = None,
    units: str = "V",
    channels: list[str] | None = None,
) -> InstrumentCapability:
    params = {}
    param_name = function.replace("dc_", "").replace("ac_", "")
    if range_max is not None or range_min is not None:
        params[param_name] = Signal(
            range=RangeSpec(min=range_min or 0, max=range_max, units=units),
        )
    return InstrumentCapability(
        function=MeasurementFunction(function),
        direction=Direction(direction),
        signals=params,
        channels=channels or [],
    )


FAKE_DMM = _make_entry(
    "keysight_34461a",
    "Keysight",
    "34461A",
    "dmm",
    [
        _make_cap("dc_voltage", "input", range_max=1000, range_min=0.0001),
        _make_cap("dc_current", "input", range_max=3, range_min=0, units="A"),
        _make_cap("resistance", "input", range_max=1e9, range_min=0, units="Ohm"),
    ],
)

FAKE_PSU = _make_entry(
    "keysight_e36312a",
    "Keysight",
    "E36312A",
    "psu",
    [
        _make_cap("dc_voltage", "output", range_max=25),
        _make_cap("dc_current", "output", range_max=1, units="A"),
    ],
)

FAKE_SCOPE = _make_entry(
    "tektronix_mso44",
    "Tektronix",
    "MSO44",
    "scope",
    [_make_cap("dc_voltage", "input", range_max=50, channels=["1", "2", "3", "4"])],
)


def _patch_catalog(entries: dict[str, InstrumentCatalogEntry]):
    """Patch catalog loading to return given entries."""
    return patch(
        "litmus.store.load_catalog_from_directory",
        return_value=entries,
    )


def _patch_dirs():
    return patch(
        "litmus.store.find_catalog_dirs",
        return_value=[Path("/fake/catalog")],
    )


class TestRecommendFromCatalog:
    def test_finds_dmm_for_voltage_input(self):
        catalog = {"keysight_34461a": FAKE_DMM, "keysight_e36312a": FAKE_PSU}
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(
                [{"function": "dc_voltage", "direction": "input", "range_max": 50, "units": "V"}]
            )
        ids = [r["catalog_id"] for r in result["recommendations"]]
        assert "keysight_34461a" in ids
        # PSU is output, should not match input requirement
        assert "keysight_e36312a" not in ids

    def test_filters_by_range(self):
        catalog = {"keysight_34461a": FAKE_DMM}
        with _patch_dirs(), _patch_catalog(catalog):
            # DMM max current is 3A, asking for 10A should not match
            result = recommend_from_catalog(
                [{"function": "dc_current", "direction": "input", "range_max": 10, "units": "A"}]
            )
        assert result["recommendations"] == []

    def test_direction_match_output(self):
        catalog = {"keysight_e36312a": FAKE_PSU}
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(
                [{"function": "dc_voltage", "direction": "output", "range_max": 12, "units": "V"}]
            )
        assert len(result["recommendations"]) == 1
        assert result["recommendations"][0]["catalog_id"] == "keysight_e36312a"

    def test_coverage_summary(self):
        catalog = {
            "keysight_34461a": FAKE_DMM,
            "keysight_e36312a": FAKE_PSU,
        }
        reqs = [
            {"function": "dc_voltage", "direction": "input", "range_max": 50, "units": "V"},
            {"function": "dc_voltage", "direction": "output", "range_max": 12, "units": "V"},
        ]
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(reqs)

        coverage = result["coverage"]
        assert "keysight_34461a" in coverage["0:dc_voltage:input"]
        assert "keysight_e36312a" in coverage["1:dc_voltage:output"]

    def test_empty_catalog(self):
        with _patch_dirs(), _patch_catalog({}):
            result = recommend_from_catalog([{"function": "dc_voltage", "direction": "input"}])
        assert result["recommendations"] == []
        assert result["coverage"]["0:dc_voltage:input"] == []

    def test_sorted_by_coverage(self):
        """Instruments satisfying more requirements should appear first."""
        # DMM satisfies voltage input + current input
        # Scope satisfies only voltage input
        catalog = {
            "keysight_34461a": FAKE_DMM,
            "tektronix_mso44": FAKE_SCOPE,
        }
        reqs = [
            {"function": "dc_voltage", "direction": "input", "range_max": 50, "units": "V"},
            {"function": "dc_current", "direction": "input", "range_max": 2, "units": "A"},
        ]
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(reqs)

        # DMM covers both, scope covers only one — DMM should be first
        assert result["recommendations"][0]["catalog_id"] == "keysight_34461a"
        assert len(result["recommendations"][0]["satisfies"]) == 2
        assert result["recommendations"][1]["catalog_id"] == "tektronix_mso44"
        assert len(result["recommendations"][1]["satisfies"]) == 1


# --- Fixtures for accuracy/resolution/condition-aware tests ---

PRECISE_DMM = _make_entry(
    "precise_dmm",
    "Acme",
    "P100",
    "dmm",
    [
        InstrumentCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=1000, units="V"),
                    accuracy=AccuracySpec(pct_reading=0.01, pct_range=0.002),
                    resolution=ResolutionSpec(digits=6.5),
                )
            },
        )
    ],
)

ROUGH_DMM = _make_entry(
    "rough_dmm",
    "Acme",
    "R200",
    "dmm",
    [
        InstrumentCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=1000, units="V"),
                    accuracy=AccuracySpec(pct_reading=0.1, pct_range=0.05),
                    resolution=ResolutionSpec(digits=5.5),
                )
            },
        )
    ],
)

AC_DMM = _make_entry(
    "ac_dmm",
    "Acme",
    "AC300",
    "dmm",
    [
        InstrumentCapability(
            function=MeasurementFunction.AC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=750, units="V"),
                    accuracy=AccuracySpec(pct_reading=0.1),
                    specs=[
                        SpecBand(
                            when={"frequency": RangeSpec(min=20, max=50000, units="Hz")},
                            accuracy=AccuracySpec(pct_reading=0.05),
                        ),
                        SpecBand(
                            when={"frequency": RangeSpec(min=50000, max=300000, units="Hz")},
                            accuracy=AccuracySpec(pct_reading=0.2),
                        ),
                    ],
                )
            },
            conditions={"frequency": Condition(range=RangeSpec(min=3, max=300000, units="Hz"))},
        )
    ],
)


class TestAccuracyFiltering:
    def test_precise_dmm_matches_tight_accuracy(self):
        """Require 0.05% pct_reading — precise DMM (0.01%) passes, rough (0.1%) fails."""
        catalog = {"precise_dmm": PRECISE_DMM, "rough_dmm": ROUGH_DMM}
        reqs = [
            {
                "function": "dc_voltage",
                "direction": "input",
                "range_max": 100,
                "units": "V",
                "accuracy": {"pct_reading": 0.05},
            }
        ]
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(reqs)
        ids = [r["catalog_id"] for r in result["recommendations"]]
        assert "precise_dmm" in ids
        assert "rough_dmm" not in ids

    def test_both_match_loose_accuracy(self):
        """Require 0.5% pct_reading — both pass."""
        catalog = {"precise_dmm": PRECISE_DMM, "rough_dmm": ROUGH_DMM}
        reqs = [
            {
                "function": "dc_voltage",
                "direction": "input",
                "range_max": 100,
                "units": "V",
                "accuracy": {"pct_reading": 0.5},
            }
        ]
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(reqs)
        ids = [r["catalog_id"] for r in result["recommendations"]]
        assert "precise_dmm" in ids
        assert "rough_dmm" in ids


class TestResolutionFiltering:
    def test_requires_6_5_digits(self):
        """Require 6.5 digits — 5.5 digit DMM excluded."""
        catalog = {"precise_dmm": PRECISE_DMM, "rough_dmm": ROUGH_DMM}
        reqs = [
            {
                "function": "dc_voltage",
                "direction": "input",
                "range_max": 100,
                "units": "V",
                "resolution": {"digits": 6.5},
            }
        ]
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(reqs)
        ids = [r["catalog_id"] for r in result["recommendations"]]
        assert "precise_dmm" in ids
        assert "rough_dmm" not in ids


class TestConditionFiltering:
    def test_ac_with_frequency_condition(self):
        """Require AC voltage at 20-50kHz — AC DMM covers that band."""
        catalog = {"ac_dmm": AC_DMM, "rough_dmm": ROUGH_DMM}
        reqs = [
            {
                "function": "ac_voltage",
                "direction": "input",
                "range_max": 10,
                "units": "V",
                "conditions": {"frequency": {"min": 1000, "max": 50000, "units": "Hz"}},
            }
        ]
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(reqs)
        ids = [r["catalog_id"] for r in result["recommendations"]]
        assert "ac_dmm" in ids
        # rough_dmm has no ac_voltage capability
        assert "rough_dmm" not in ids

    def test_ac_with_frequency_and_accuracy(self):
        """Require AC voltage at 20-50kHz with tight accuracy — AC DMM band says 0.05%."""
        catalog = {"ac_dmm": AC_DMM}
        reqs = [
            {
                "function": "ac_voltage",
                "direction": "input",
                "range_max": 10,
                "units": "V",
                "accuracy": {"pct_reading": 0.06},
                "conditions": {"frequency": {"min": 1000, "max": 50000, "units": "Hz"}},
            }
        ]
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(reqs)
        ids = [r["catalog_id"] for r in result["recommendations"]]
        assert "ac_dmm" in ids

    def test_ac_with_too_tight_accuracy_for_band(self):
        """Require 0.01% at high freq band (instrument has 0.2%) — should fail."""
        catalog = {"ac_dmm": AC_DMM}
        reqs = [
            {
                "function": "ac_voltage",
                "direction": "input",
                "range_max": 10,
                "units": "V",
                "accuracy": {"pct_reading": 0.01},
                "conditions": {"frequency": {"min": 100000, "max": 200000, "units": "Hz"}},
            }
        ]
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(reqs)
        assert result["recommendations"] == []


class TestGracefulDegradation:
    def test_function_and_direction_only(self):
        """Just function + direction (no range) still works."""
        catalog = {"keysight_34461a": FAKE_DMM}
        reqs = [{"function": "dc_voltage", "direction": "input"}]
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(reqs)
        assert len(result["recommendations"]) == 1

    def test_mixed_depths(self):
        """One req with accuracy, another without — each uses appropriate depth."""
        catalog = {"precise_dmm": PRECISE_DMM, "rough_dmm": ROUGH_DMM, "keysight_e36312a": FAKE_PSU}
        reqs = [
            {
                "function": "dc_voltage",
                "direction": "input",
                "range_max": 100,
                "units": "V",
                "accuracy": {"pct_reading": 0.05},
            },
            {"function": "dc_voltage", "direction": "output", "range_max": 12, "units": "V"},
        ]
        with _patch_dirs(), _patch_catalog(catalog):
            result = recommend_from_catalog(reqs)
        ids = [r["catalog_id"] for r in result["recommendations"]]
        # Precise DMM matches req 0 (accuracy), rough doesn't
        assert "precise_dmm" in ids
        assert "rough_dmm" not in ids
        # PSU matches req 1 (range only)
        assert "keysight_e36312a" in ids
