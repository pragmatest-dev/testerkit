"""Tests for condition-aware matching (SpecBand, accuracy, resolution, compare modes)."""

from litmus.config.models import (
    AccuracySpec,
    CompareMode,
    Direction,
    MatchDepth,
    MeasurementFunction,
    RangeSpec,
    ResolutionSpec,
    SignalParameter,
    SpecBand,
)
from litmus.matching.service import (
    CapabilityRequirement,
    StationCapability,
    _accuracy_sufficient,
    _resolution_sufficient,
    capability_satisfies,
    get_spec_at,
)

# ---------------------------------------------------------------------------
# SpecBand lookup
# ---------------------------------------------------------------------------


def test_spec_band_lookup_finds_matching_band():
    param = SignalParameter(
        range=RangeSpec(min=0.1, max=750, units="V"),
        accuracy=AccuracySpec(pct_reading=0.07, pct_range=0.02),
        specs=[
            SpecBand(
                conditions={"frequency": RangeSpec(min=3, max=5, units="Hz")},
                accuracy=AccuracySpec(pct_reading=0.35, pct_range=0.03),
            ),
            SpecBand(
                conditions={"frequency": RangeSpec(min=5, max=300, units="Hz")},
                accuracy=AccuracySpec(pct_reading=0.07, pct_range=0.02),
            ),
        ],
    )
    band = get_spec_at(param, {"frequency": 100})
    assert band is not None
    assert band.accuracy.pct_reading == 0.07


def test_spec_band_lookup_multi_key_and():
    """Both condition keys must match for a band to apply."""
    param = SignalParameter(
        specs=[
            SpecBand(
                conditions={
                    "frequency": RangeSpec(min=1e9, max=2e9, units="Hz"),
                    "offset": RangeSpec(min=1000, max=1000, units="Hz"),
                },
                value=-121,
            ),
        ],
    )
    # Both match
    assert get_spec_at(param, {"frequency": 1.5e9, "offset": 1000}) is not None
    # Only one matches
    assert get_spec_at(param, {"frequency": 1.5e9, "offset": 500}) is None
    # Missing key
    assert get_spec_at(param, {"frequency": 1.5e9}) is None


def test_spec_band_lookup_falls_back_to_default():
    """No matching band → get_spec_at returns None, caller uses top-level."""
    param = SignalParameter(
        accuracy=AccuracySpec(pct_reading=0.07),
        specs=[
            SpecBand(
                conditions={"frequency": RangeSpec(min=3, max=5, units="Hz")},
                accuracy=AccuracySpec(pct_reading=0.35),
            ),
        ],
    )
    # 1000 Hz doesn't match any band
    assert get_spec_at(param, {"frequency": 1000}) is None


# ---------------------------------------------------------------------------
# Accuracy comparison
# ---------------------------------------------------------------------------


def test_accuracy_sufficient_passes():
    inst = AccuracySpec(pct_reading=0.05, pct_range=0.01)
    req = AccuracySpec(pct_reading=0.07, pct_range=0.02)
    assert _accuracy_sufficient(inst, req) is True


def test_accuracy_sufficient_fails():
    inst = AccuracySpec(pct_reading=0.10, pct_range=0.05)
    req = AccuracySpec(pct_reading=0.07, pct_range=0.02)
    assert _accuracy_sufficient(inst, req) is False


# ---------------------------------------------------------------------------
# Resolution comparison
# ---------------------------------------------------------------------------


def test_resolution_sufficient_bits():
    inst = ResolutionSpec(bits=16)
    req = ResolutionSpec(bits=12)
    assert _resolution_sufficient(inst, req) is True
    assert _resolution_sufficient(req, inst) is False


def test_resolution_sufficient_digits():
    inst = ResolutionSpec(digits=6.5)
    req = ResolutionSpec(digits=5.5)
    assert _resolution_sufficient(inst, req) is True
    assert _resolution_sufficient(req, inst) is False


# ---------------------------------------------------------------------------
# CompareMode (higher_better / lower_better)
# ---------------------------------------------------------------------------


def _make_cap(value, compare, function=MeasurementFunction.DC_VOLTAGE):
    return StationCapability(
        function=function,
        direction=Direction.TRANSFORM,
        parameters={
            "gain": SignalParameter(value=value, units="dB", compare=compare),
        },
        name="test",
        instrument_type="amp",
        instrument_name="amp1",
    )


def _make_req(value, compare, function=MeasurementFunction.DC_VOLTAGE):
    return CapabilityRequirement(
        function=function,
        direction=Direction.TRANSFORM,
        parameters={
            "gain": SignalParameter(value=value, units="dB", compare=compare),
        },
        characteristic_name="test",
    )


def test_compare_higher_better():
    cap = _make_cap(16.5, CompareMode.HIGHER_BETTER)
    req = _make_req(12.0, CompareMode.HIGHER_BETTER)
    assert capability_satisfies(cap, req, MatchDepth.ACCURACY) is True


def test_compare_lower_better():
    cap = _make_cap(-121, CompareMode.LOWER_BETTER)
    req = _make_req(-110, CompareMode.LOWER_BETTER)
    assert capability_satisfies(cap, req, MatchDepth.ACCURACY) is True


def test_compare_lower_better_fails():
    cap = _make_cap(-90, CompareMode.LOWER_BETTER)
    req = _make_req(-110, CompareMode.LOWER_BETTER)
    assert capability_satisfies(cap, req, MatchDepth.ACCURACY) is False


# ---------------------------------------------------------------------------
# Full capability_satisfies with depth
# ---------------------------------------------------------------------------


def test_capability_satisfies_with_accuracy_depth():
    """Full tier-4 match: function + direction + range + accuracy."""
    cap = StationCapability(
        function=MeasurementFunction.DC_VOLTAGE,
        direction=Direction.INPUT,
        parameters={
            "voltage": SignalParameter(
                range=RangeSpec(min=0, max=100, units="V"),
                accuracy=AccuracySpec(pct_reading=0.003, pct_range=0.0005),
                resolution=ResolutionSpec(digits=6.5),
            ),
        },
        name="dc_voltage_input",
        instrument_type="dmm",
        instrument_name="dmm1",
    )
    req = CapabilityRequirement(
        function=MeasurementFunction.DC_VOLTAGE,
        direction=Direction.INPUT,
        parameters={
            "voltage": SignalParameter(
                range=RangeSpec(min=0, max=50, units="V"),
                accuracy=AccuracySpec(pct_reading=0.01, pct_range=0.001),
                resolution=ResolutionSpec(digits=5.5),
            ),
        },
        characteristic_name="output_voltage",
    )
    assert capability_satisfies(cap, req, MatchDepth.ACCURACY) is True
    assert capability_satisfies(cap, req, MatchDepth.RESOLUTION) is True


def test_backward_compat_no_specs():
    """Existing catalog entries without specs still match at range depth."""
    cap = StationCapability(
        function=MeasurementFunction.DC_VOLTAGE,
        direction=Direction.INPUT,
        parameters={
            "voltage": SignalParameter(
                range=RangeSpec(min=0, max=1000, units="V"),
            ),
        },
        name="dc_voltage_input",
        instrument_type="dmm",
        instrument_name="dmm1",
    )
    req = CapabilityRequirement(
        function=MeasurementFunction.DC_VOLTAGE,
        direction=Direction.INPUT,
        parameters={
            "voltage": SignalParameter(
                range=RangeSpec(min=0, max=50, units="V"),
            ),
        },
        characteristic_name="output_voltage",
    )
    assert capability_satisfies(cap, req) is True
    assert capability_satisfies(cap, req, MatchDepth.RANGE) is True
