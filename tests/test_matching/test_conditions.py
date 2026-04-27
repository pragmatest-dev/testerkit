"""Tests for condition-aware matching (SpecBand, accuracy, resolution, compare modes)."""

from litmus.matching.service import (
    CapabilityRequirement,
    StationCapability,
    _accuracy_sufficient,
    _resolution_sufficient,
    capability_satisfies,
    get_spec_at,
)
from litmus.models.capability import (
    AccuracySpec,
    Condition,
    Control,
    InstrumentCapability,
    ListSpec,
    PointSpec,
    RangeSpec,
    ResolutionSpec,
    Signal,
    SpecBand,
)
from litmus.models.enums import Direction, MatchDepth, MeasurementFunction
from litmus.models.product import ProductCharacteristic


def _spec_with_units(cond: object) -> RangeSpec | PointSpec | ListSpec:
    """Narrow a union-typed ``when``-clause entry to a spec type that has ``units``."""
    assert isinstance(cond, RangeSpec | PointSpec | ListSpec), (
        f"expected RangeSpec/PointSpec/ListSpec, got {type(cond).__name__}"
    )
    return cond


# ---------------------------------------------------------------------------
# SpecBand lookup
# ---------------------------------------------------------------------------


def test_spec_band_lookup_finds_matching_band():
    param = Signal(
        range=RangeSpec(min=0.1, max=750, units="V"),
        accuracy=AccuracySpec(pct_reading=0.07, pct_range=0.02),
        bands=[
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
    assert band.accuracy is not None
    assert band.accuracy.pct_reading == 0.07


def test_spec_band_lookup_multi_key_and():
    """Both condition keys must match for a band to apply."""
    param = Signal(
        bands=[
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
        bands=[
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
        bands=[
            SpecBand(
                when={"rate": "SLOW"},
                accuracy=AccuracySpec(pct_reading=0.35),
            ),
        ],
    )
    band = get_spec_at(param, {"rate": "SLOW"})
    assert band is not None
    assert band.accuracy is not None
    assert band.accuracy.pct_reading == 0.35


def test_spec_band_string_no_match():
    """String when-clause does not match a different string value."""
    param = Signal(
        bands=[
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
        bands=[
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
        bands=[
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
        bands=[
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
        bands=[
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
        bands=[
            SpecBand(
                when={"impedance": [50, 600, "HiZ"]},
                accuracy=AccuracySpec(pct_reading=0.10),
            ),
        ],
    )
    assert get_spec_at(param, {"impedance": 50}) is not None
    assert get_spec_at(param, {"impedance": "HiZ"}) is not None
    assert get_spec_at(param, {"impedance": 75}) is None


# ---------------------------------------------------------------------------
# PointSpec when-clause matching
# ---------------------------------------------------------------------------


def test_pointspec_yaml_round_trip():
    """PointSpec parses from dict with value + units."""
    spec = PointSpec(value=100, units="Hz")
    assert spec.value == 100
    assert spec.units == "Hz"


def test_pointspec_match():
    """PointSpec when-clause matches exact value."""
    param = Signal(
        bands=[
            SpecBand(
                when={"frequency": PointSpec(value=1e8, units="Hz")},
                accuracy=AccuracySpec(pct_reading=0.01),
            ),
        ],
    )
    band = get_spec_at(param, {"frequency": 1e8})
    assert band is not None
    assert band.accuracy is not None
    assert band.accuracy.pct_reading == 0.01
    assert get_spec_at(param, {"frequency": 2e8}) is None


def test_pointspec_no_units():
    """PointSpec without units still matches on value."""
    spec = PointSpec(value=50)
    assert spec.units == ""
    param = Signal(
        bands=[
            SpecBand(
                when={"impedance": spec},
                accuracy=AccuracySpec(pct_reading=0.05),
            ),
        ],
    )
    assert get_spec_at(param, {"impedance": 50}) is not None
    assert get_spec_at(param, {"impedance": 75}) is None


# ---------------------------------------------------------------------------
# ListSpec when-clause matching
# ---------------------------------------------------------------------------


def test_listspec_yaml_round_trip():
    """ListSpec parses from dict with values + units."""
    spec = ListSpec(values=[50, 600], units="ohm")
    assert spec.values == [50, 600]
    assert spec.units == "ohm"


def test_listspec_match():
    """ListSpec when-clause matches membership."""
    param = Signal(
        bands=[
            SpecBand(
                when={"impedance": ListSpec(values=[50, 600], units="ohm")},
                accuracy=AccuracySpec(pct_reading=0.05),
            ),
        ],
    )
    assert get_spec_at(param, {"impedance": 50}) is not None
    assert get_spec_at(param, {"impedance": 600}) is not None
    assert get_spec_at(param, {"impedance": 75}) is None


def test_listspec_no_units():
    """ListSpec without units still matches on membership."""
    spec = ListSpec(values=["single", "automatic"])
    assert spec.units == ""
    param = Signal(
        bands=[
            SpecBand(
                when={"mode": spec},
                value=14,
            ),
        ],
    )
    assert get_spec_at(param, {"mode": "single"}) is not None
    assert get_spec_at(param, {"mode": "burst"}) is None


# ---------------------------------------------------------------------------
# Units inheritance on when-clause specs
# ---------------------------------------------------------------------------


def test_units_inheritance_rangespec():
    """RangeSpec in when-clause inherits units from parent condition."""
    cap = InstrumentCapability(
        function=MeasurementFunction.AC_VOLTAGE,
        direction=Direction.INPUT,
        signals={
            "voltage": Signal(
                range=RangeSpec(min=0, max=750, units="V"),
                bands=[
                    SpecBand(
                        when={"frequency": RangeSpec(min=20, max=300)},
                        accuracy=AccuracySpec(pct_reading=0.07),
                    ),
                ],
            ),
        },
        conditions={
            "frequency": Condition(range=RangeSpec(min=20, max=100000, units="Hz")),
        },
    )
    specs = cap.signals["voltage"].bands
    assert specs is not None
    band = specs[0]
    assert _spec_with_units(band.when["frequency"]).units == "Hz"


def test_units_inheritance_pointspec():
    """PointSpec in when-clause inherits units from parent condition."""
    cap = InstrumentCapability(
        function=MeasurementFunction.AC_VOLTAGE,
        direction=Direction.INPUT,
        signals={
            "voltage": Signal(
                range=RangeSpec(min=0, max=750, units="V"),
                bands=[
                    SpecBand(
                        when={"frequency": PointSpec(value=1000)},
                        accuracy=AccuracySpec(pct_reading=0.05),
                    ),
                ],
            ),
        },
        conditions={
            "frequency": Condition(range=RangeSpec(min=20, max=100000, units="Hz")),
        },
    )
    specs = cap.signals["voltage"].bands
    assert specs is not None
    band = specs[0]
    assert _spec_with_units(band.when["frequency"]).units == "Hz"


def test_units_inheritance_listspec():
    """ListSpec in when-clause inherits units from parent control."""
    cap = InstrumentCapability(
        function=MeasurementFunction.AC_VOLTAGE,
        direction=Direction.INPUT,
        signals={
            "voltage": Signal(
                range=RangeSpec(min=0, max=750, units="V"),
                bands=[
                    SpecBand(
                        when={"impedance": ListSpec(values=[50, 600])},
                        accuracy=AccuracySpec(pct_reading=0.05),
                    ),
                ],
            ),
        },
        controls={
            "impedance": Control(range=RangeSpec(min=50, max=600, units="ohm")),
        },
    )
    specs = cap.signals["voltage"].bands
    assert specs is not None
    band = specs[0]
    assert _spec_with_units(band.when["impedance"]).units == "ohm"


def test_units_inheritance_skips_when_already_set():
    """Units inheritance does NOT override explicitly set units."""
    cap = InstrumentCapability(
        function=MeasurementFunction.AC_VOLTAGE,
        direction=Direction.INPUT,
        signals={
            "voltage": Signal(
                range=RangeSpec(min=0, max=750, units="V"),
                bands=[
                    SpecBand(
                        when={"frequency": PointSpec(value=1000, units="kHz")},
                        accuracy=AccuracySpec(pct_reading=0.05),
                    ),
                ],
            ),
        },
        conditions={
            "frequency": Condition(range=RangeSpec(min=20, max=100000, units="Hz")),
        },
    )
    specs = cap.signals["voltage"].bands
    assert specs is not None
    band = specs[0]
    assert _spec_with_units(band.when["frequency"]).units == "kHz"
