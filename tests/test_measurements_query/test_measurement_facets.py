"""Tests for the measurement_facets registry and Pydantic models.

The registry is the single source of truth for facetable measurement
columns. These tests catch drift between the registry and the actual
``MeasurementRow`` model — a column that's no longer on the row would
produce SQL errors only at runtime; checking at import time is much
cheaper.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

import pytest

from litmus.analysis.measurement_facets import (
    MEASUREMENT_FACETS,
    FacetKind,
    FacetOption,
    FacetSpec,
    FilterSet,
    HistogramRow,
    ParametricRow,
    SummaryCounts,
    facets_by_kind,
)
from litmus.data.backends._row_helpers import MeasurementRow
from litmus.data.models import Outcome
from litmus.models.enums import Comparator


class TestRegistry:
    def test_no_duplicate_columns(self):
        cols = [f.column for f in MEASUREMENT_FACETS]
        assert len(cols) == len(set(cols)), f"duplicate facet columns: {cols}"

    def test_every_column_is_a_real_measurement_field(self):
        model_fields = set(MeasurementRow.model_fields)
        for facet in MEASUREMENT_FACETS:
            assert facet.column in model_fields, (
                f"{facet.column!r} is in MEASUREMENT_FACETS but not on MeasurementRow"
            )

    def test_enum_facets_have_enum_class(self):
        for facet in facets_by_kind(FacetKind.ENUM):
            assert facet.enum_class is not None
            assert issubclass(facet.enum_class, StrEnum)

    def test_non_enum_facets_have_no_enum_class(self):
        for facet in MEASUREMENT_FACETS:
            if facet.kind is not FacetKind.ENUM:
                assert facet.enum_class is None

    def test_outcome_facets_use_outcome_enum(self):
        outcome_facets = [f for f in MEASUREMENT_FACETS if f.column.endswith("_outcome")]
        assert outcome_facets, "expected at least one outcome facet"
        for facet in outcome_facets:
            assert facet.enum_class is Outcome

    def test_comparator_facet_uses_comparator_enum(self):
        comp = next(f for f in MEASUREMENT_FACETS if f.column == "limit_comparator")
        assert comp.enum_class is Comparator


class TestFacetSpec:
    def test_enum_kind_requires_enum_class(self):
        with pytest.raises(ValueError, match="ENUM facet requires enum_class"):
            FacetSpec(column="x", kind=FacetKind.ENUM, label="X")

    def test_string_kind_rejects_enum_class(self):
        with pytest.raises(ValueError, match="enum_class only valid for ENUM"):
            FacetSpec(column="x", kind=FacetKind.STRING, label="X", enum_class=Outcome)


class TestFilterSet:
    def test_empty_default(self):
        fs = FilterSet()
        assert fs.is_empty()
        assert fs.to_url_params() == []

    def test_validates_string_column(self):
        with pytest.raises(ValueError, match="unknown or non-string"):
            FilterSet(string_filters={"not_a_column": ["x"]})

    def test_validates_enum_column(self):
        with pytest.raises(ValueError, match="unknown or non-enum"):
            FilterSet(enum_filters={"uut_part_number": ["PN-100"]})

    def test_url_round_trip_string_filters(self):
        # The Part facet's storage column is ``uut_part_number``; its URL
        # key is the operator-facing ``part`` (matching the metrics page).
        fs = FilterSet(string_filters={"uut_part_number": ["PN-100", "PN-200"]})
        params = fs.to_url_params()
        assert params == [("part", "PN-100"), ("part", "PN-200")]
        # Decode round-trip: URL key ``part`` maps back to ``uut_part_number``.
        decoded = FilterSet.from_url_params({"part": ["PN-100", "PN-200"]})
        assert decoded.string_filters == {"uut_part_number": ["PN-100", "PN-200"]}

    def test_url_round_trip_enum_filters(self):
        fs = FilterSet(enum_filters={"measurement_outcome": ["passed", "failed"]})
        params = fs.to_url_params()
        assert ("measurement_outcome", "passed") in params
        assert ("measurement_outcome", "failed") in params
        decoded = FilterSet.from_url_params({"measurement_outcome": ["passed", "failed"]})
        assert decoded.enum_filters == {"measurement_outcome": ["passed", "failed"]}

    def test_url_round_trip_dates(self):
        fs = FilterSet(since=date(2026, 4, 1), until=date(2026, 4, 30))
        params = dict(fs.to_url_params())
        assert params["since"] == "2026-04-01"
        assert params["until"] == "2026-04-30"
        decoded = FilterSet.from_url_params({"since": ["2026-04-01"], "until": ["2026-04-30"]})
        assert decoded.since == date(2026, 4, 1)
        assert decoded.until == date(2026, 4, 30)

    def test_unknown_column_dropped_on_decode(self):
        decoded = FilterSet.from_url_params({"part": ["PN-100"], "fake_column": ["whatever"]})
        assert decoded.string_filters == {"uut_part_number": ["PN-100"]}
        assert "fake_column" not in decoded.string_filters
        assert "fake_column" not in decoded.enum_filters

    def test_invalid_enum_value_dropped_on_decode(self):
        decoded = FilterSet.from_url_params(
            {"measurement_outcome": ["passed", "not_a_real_outcome"]}
        )
        assert decoded.enum_filters == {"measurement_outcome": ["passed"]}

    def test_invalid_date_dropped_on_decode(self):
        decoded = FilterSet.from_url_params({"since": ["not-a-date"]})
        assert decoded.since is None


class TestRowModels:
    def test_parametric_row_defaults(self):
        row = ParametricRow(y=3.3)
        assert row.y == 3.3
        assert row.x is None
        assert row.group == ""

    def test_parametric_row_accepts_string_x(self):
        row = ParametricRow(x="SN001", y=3.3, group="passed")
        assert row.x == "SN001"

    def test_parametric_row_accepts_numeric_x(self):
        row = ParametricRow(x=1.5, y=3.3)
        assert row.x == 1.5

    def test_histogram_row_required_fields(self):
        row = HistogramRow(bin=0, x=3.15, y=42, group="PN-100")
        assert row.bin == 0
        assert row.x == 3.15
        assert row.y == 42

    def test_summary_counts(self):
        sc = SummaryCounts(
            total_rows=42_317,
            distinct_runs=314,
            distinct_measurements=14,
            distinct_parts=6,
        )
        assert sc.total_rows == 42_317

    def test_facet_option(self):
        opt = FacetOption(value="PN-100", count=12_304)
        assert opt.value == "PN-100"
        assert opt.count == 12_304
