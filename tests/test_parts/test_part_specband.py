"""Tests for SpecBand matching in part characteristics.

Covers PointSpec and ListSpec when-clauses in band_matches().
"""

from testerkit.models.capability import (
    AccuracySpec,
    ListSpec,
    PointSpec,
    RangeSpec,
    SpecBand,
    band_matches,
)


class TestBandMatchesPartPointSpec:
    """PointSpec when-clauses match by exact value."""

    def test_point_spec_matches_exact(self):
        band = SpecBand(
            when={"frequency": PointSpec(value=1000.0, unit="Hz")},
            value=3.3,
        )
        assert band_matches(band, {"frequency": 1000.0})

    def test_point_spec_rejects_different_value(self):
        band = SpecBand(
            when={"frequency": PointSpec(value=1000.0, unit="Hz")},
            value=3.3,
        )
        assert not band_matches(band, {"frequency": 2000.0})

    def test_point_spec_rejects_missing_key(self):
        band = SpecBand(
            when={"frequency": PointSpec(value=1000.0)},
            value=3.3,
        )
        assert not band_matches(band, {"temperature": 25})


class TestBandMatchesPartListSpec:
    """ListSpec when-clauses match by membership."""

    def test_list_spec_matches_member(self):
        band = SpecBand(
            when={"impedance": ListSpec(values=[50, 600], unit="ohm")},
            value=1.0,
            accuracy=AccuracySpec(pct_reading=1.0),
        )
        assert band_matches(band, {"impedance": 50})

    def test_list_spec_rejects_non_member(self):
        band = SpecBand(
            when={"impedance": ListSpec(values=[50, 600], unit="ohm")},
            value=1.0,
        )
        assert not band_matches(band, {"impedance": 75})

    def test_list_spec_string_values(self):
        band = SpecBand(
            when={"coupling": ListSpec(values=["AC", "DC"])},
            value=5.0,
        )
        assert band_matches(band, {"coupling": "AC"})
        assert not band_matches(band, {"coupling": "GND"})


class TestBandMatchesPartMixed:
    """Mixed when-clauses with multiple spec types."""

    def test_range_and_point_combined(self):
        band = SpecBand(
            when={
                "temperature": RangeSpec(min=0, max=50, unit="degC"),
                "frequency": PointSpec(value=1e6, unit="Hz"),
            },
            value=3.3,
        )
        # Both match
        assert band_matches(band, {"temperature": 25, "frequency": 1e6})
        # Range matches, point fails
        assert not band_matches(band, {"temperature": 25, "frequency": 2e6})
        # Point matches, range fails
        assert not band_matches(band, {"temperature": 100, "frequency": 1e6})

    def test_empty_when_matches_anything(self):
        band = SpecBand(when={}, value=3.3)
        assert band_matches(band, {"anything": 42})
        assert band_matches(band, {})
