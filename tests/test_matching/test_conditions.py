"""Tests for condition-aware matching (SpecBand, accuracy, resolution, compare modes)."""

from litmus.config.models import (
    AccuracySpec,
    Direction,
    InstrumentCapability,
    MatchDepth,
    MeasurementFunction,
    RangeSpec,
    ResolutionSpec,
    Signal,
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
from litmus.products.models import ProductCharacteristic

# ---------------------------------------------------------------------------
# SpecBand lookup
# ---------------------------------------------------------------------------


def test_spec_band_lookup_finds_matching_band():
    param = Signal(
        range=RangeSpec(min=0.1, max=750, units="V"),
        accuracy=AccuracySpec(pct_reading=0.07, pct_range=0.02),
        specs=[
            SpecBand(
                when={"frequency": RangeSpec(min=3, max=5, units="Hz")},
                accuracy=AccuracySpec(pct_reading=0.35, pct_range=0.03),
            ),
            SpecBand(
                when={"frequency": RangeSpec(min=5, max=300, units="Hz")},
                accuracy=AccuracySpec(pct_reading=0.07, pct_range=0.02),
            ),
        ],
    )
    band = get_spec_at(param, {"frequency": 100})
    assert band is not None
    assert band.accuracy.pct_reading == 0.07


def test_spec_band_lookup_multi_key_and():
    """Both condition keys must match for a band to apply."""
    param = Signal(
        specs=[
            SpecBand(
                when={
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
    param = Signal(
        accuracy=AccuracySpec(pct_reading=0.07),
        specs=[
            SpecBand(
                when={"frequency": RangeSpec(min=3, max=5, units="Hz")},
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
# Full capability_satisfies with depth
# ---------------------------------------------------------------------------


def test_capability_satisfies_with_accuracy_depth():
    """Full tier-4 match: function + direction + range + accuracy."""
    cap = StationCapability(
        capability=InstrumentCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=100, units="V"),
                    accuracy=AccuracySpec(pct_reading=0.003, pct_range=0.0005),
                    resolution=ResolutionSpec(digits=6.5),
                ),
            },
        ),
        instrument_type="dmm",
        instrument_name="dmm1",
    )
    req = CapabilityRequirement(
        capability=ProductCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=50, units="V"),
                    accuracy=AccuracySpec(pct_reading=0.01, pct_range=0.001),
                    resolution=ResolutionSpec(digits=5.5),
                ),
            },
            units="V",
            net="output_voltage",
        ),
        characteristic_name="output_voltage",
    )
    assert capability_satisfies(cap, req, MatchDepth.ACCURACY) is True
    assert capability_satisfies(cap, req, MatchDepth.RESOLUTION) is True


def test_backward_compat_no_specs():
    """Existing catalog entries without specs still match at range depth."""
    cap = StationCapability(
        capability=InstrumentCapability(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.INPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=1000, units="V"),
                ),
            },
        ),
        instrument_type="dmm",
        instrument_name="dmm1",
    )
    req = CapabilityRequirement(
        capability=ProductCharacteristic(
            function=MeasurementFunction.DC_VOLTAGE,
            direction=Direction.OUTPUT,
            signals={
                "voltage": Signal(
                    range=RangeSpec(min=0, max=50, units="V"),
                ),
            },
            units="V",
            net="output_voltage",
        ),
        characteristic_name="output_voltage",
    )
    assert capability_satisfies(cap, req) is True
    assert capability_satisfies(cap, req, MatchDepth.RANGE) is True


# ---------------------------------------------------------------------------
# String when-clause matching
# ---------------------------------------------------------------------------


def test_spec_band_string_match():
    """String when-clause matches exact string value in operating point."""
    param = Signal(
        specs=[
            SpecBand(
                when={"rate": "SLOW"},
                accuracy=AccuracySpec(pct_reading=0.35),
            ),
        ],
    )
    assert get_spec_at(param, {"rate": "SLOW"}) is not None
    assert get_spec_at(param, {"rate": "SLOW"}).accuracy.pct_reading == 0.35


def test_spec_band_string_no_match():
    """String when-clause does not match a different string value."""
    param = Signal(
        specs=[
            SpecBand(
                when={"rate": "SLOW"},
                accuracy=AccuracySpec(pct_reading=0.35),
            ),
        ],
    )
    assert get_spec_at(param, {"rate": "FAST"}) is None


def test_spec_band_mixed_string_and_range():
    """Mixed string + range when-clause: both must match."""
    param = Signal(
        specs=[
            SpecBand(
                when={
                    "rate": "MED",
                    "frequency": RangeSpec(min=20, max=300, units="Hz"),
                },
                accuracy=AccuracySpec(pct_reading=0.10),
            ),
        ],
    )
    # Both match
    assert get_spec_at(param, {"rate": "MED", "frequency": 100}) is not None
    # String mismatch
    assert get_spec_at(param, {"rate": "SLOW", "frequency": 100}) is None
    # Range mismatch
    assert get_spec_at(param, {"rate": "MED", "frequency": 500}) is None


# ---------------------------------------------------------------------------
# Scalar float and bool when-clause matching
# ---------------------------------------------------------------------------


def test_spec_band_float_match():
    """Float when-clause matches exact float value."""
    param = Signal(
        specs=[
            SpecBand(
                when={"impedance": 50.0},
                accuracy=AccuracySpec(pct_reading=0.05),
            ),
        ],
    )
    assert get_spec_at(param, {"impedance": 50.0}) is not None
    assert get_spec_at(param, {"impedance": 75.0}) is None


def test_spec_band_bool_match():
    """Bool when-clause matches exact bool value."""
    param = Signal(
        specs=[
            SpecBand(
                when={"autorange": True},
                accuracy=AccuracySpec(pct_reading=0.10),
            ),
        ],
    )
    assert get_spec_at(param, {"autorange": True}) is not None
    assert get_spec_at(param, {"autorange": False}) is None


# ---------------------------------------------------------------------------
# List when-clause matching (membership)
# ---------------------------------------------------------------------------


def test_spec_band_list_match():
    """List when-clause matches if value is a member."""
    param = Signal(
        specs=[
            SpecBand(
                when={"impedance": [50, 600]},
                accuracy=AccuracySpec(pct_reading=0.05),
            ),
        ],
    )
    assert get_spec_at(param, {"impedance": 50}) is not None
    assert get_spec_at(param, {"impedance": 600}) is not None
    assert get_spec_at(param, {"impedance": 75}) is None


def test_spec_band_list_mixed_types():
    """List with mixed str/float types."""
    param = Signal(
        specs=[
            SpecBand(
                when={"impedance": [50, 600, "HiZ"]},
                accuracy=AccuracySpec(pct_reading=0.10),
            ),
        ],
    )
    assert get_spec_at(param, {"impedance": 50}) is not None
    assert get_spec_at(param, {"impedance": "HiZ"}) is not None
    assert get_spec_at(param, {"impedance": 75}) is None
