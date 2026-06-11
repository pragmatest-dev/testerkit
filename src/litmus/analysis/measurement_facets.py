"""Filter facets for the parametric viewer — model-driven registry.

The ``/explore`` page lets users scope cross-run measurement queries by
filtering on columns of the ``measurements`` view. This module is the
single source of truth for *which columns are facetable* and *what kind
of facet each column wants* — closed enum sets vs open string sets vs
date ranges.

Closed sets (``Outcome``, ``Comparator``) come straight from the
Pydantic models in ``litmus.data.models`` / ``litmus.models.enums`` —
no DB query needed; the universe is known at import time. Open sets
(``part_id``, ``station_id``, ``dut_serial``, ``step_name``,
``measurement_name``, ``test_phase``) require a ``SELECT DISTINCT``
against the current filter set so the dropdowns reflect what the user
can actually pick from given their other selections.

The Pydantic types here also describe every analytics result row that
crosses a boundary — ``ParametricRow`` / ``HistogramRow`` /
``FacetOption`` / ``SummaryCounts`` / ``FilterSet``. No
``dict[str, Any]`` enters or exits ``MeasurementsQuery`` with these in
place.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from litmus.data.models import Outcome
from litmus.models.enums import Comparator


class FacetKind(StrEnum):
    """How a facet's options are sourced and displayed in the UI."""

    ENUM = "enum"
    STRING = "string"
    DATE = "date"


class FacetSpec(BaseModel):
    """Self-describing definition of one filter facet."""

    column: str
    kind: FacetKind
    enum_class: type[StrEnum] | None = None
    label: str
    description: str = ""

    @model_validator(mode="after")
    def _enum_class_required_for_enum_kind(self) -> FacetSpec:
        if self.kind is FacetKind.ENUM and self.enum_class is None:
            raise ValueError(f"{self.column}: ENUM facet requires enum_class")
        if self.kind is not FacetKind.ENUM and self.enum_class is not None:
            raise ValueError(f"{self.column}: enum_class only valid for ENUM facets")
        return self


class FacetOption(BaseModel):
    """One pickable value within a facet, with the row count it covers."""

    value: str
    count: int


class SummaryCounts(BaseModel):
    """Cardinality stats for the cardinality badge — single query result."""

    total_rows: int
    distinct_runs: int
    distinct_measurements: int
    distinct_parts: int


class ParametricRow(BaseModel):
    """One long-format row from a scatter / line / bar parametric query.

    ``x`` widens to accept datetime / date because measurements view
    columns include timestamps; the chart layer coerces these to
    epoch ms for ECharts ``time`` axes.
    """

    x: float | str | datetime | date | None = None
    y: float
    group: str = ""


class HistogramRow(BaseModel):
    """One bin in a histogram result. ``x`` is the bin midpoint."""

    bin: int
    x: float
    y: int
    group: str = ""


class FilterSet(BaseModel):
    """Current filter state — URL-shareable, validated against ``MEASUREMENT_FACETS``.

    Splits enum / string filters because they take different SQL
    treatment downstream: enum filters can be passed straight through
    (their values are the enum.value strings) while string filters
    need cross-filtering when populating their own DISTINCT options.
    """

    string_filters: dict[str, list[str]] = Field(default_factory=dict)
    enum_filters: dict[str, list[str]] = Field(default_factory=dict)
    since: date | None = None
    until: date | None = None

    @model_validator(mode="after")
    def _validate_against_registry(self) -> FilterSet:
        for col in self.string_filters:
            spec = _spec_by_column(col)
            if spec is None or spec.kind is not FacetKind.STRING:
                raise ValueError(f"unknown or non-string facet column: {col}")
        for col in self.enum_filters:
            spec = _spec_by_column(col)
            if spec is None or spec.kind is not FacetKind.ENUM:
                raise ValueError(f"unknown or non-enum facet column: {col}")
        return self

    def is_empty(self) -> bool:
        return (
            not self.string_filters
            and not self.enum_filters
            and self.since is None
            and self.until is None
        )

    def to_url_params(self) -> list[tuple[str, str]]:
        """Encode as repeated query keys for ``urlencode([(k, v), ...])``."""
        params: list[tuple[str, str]] = []
        for col, values in self.string_filters.items():
            for v in values:
                params.append((col, v))
        for col, values in self.enum_filters.items():
            for v in values:
                params.append((col, v))
        if self.since is not None:
            params.append(("since", self.since.isoformat()))
        if self.until is not None:
            params.append(("until", self.until.isoformat()))
        return params

    @classmethod
    def from_url_params(cls, params: dict[str, list[str]]) -> FilterSet:
        """Decode a query-string dict — unknown columns are dropped silently.

        We drop rather than raise so a stale URL gracefully degrades
        to a partially-empty filter rather than a 500. Callers can
        distinguish "user typed garbage" from "schema changed under us"
        by comparing the input keys to ``MEASUREMENT_FACETS``.
        """
        string_filters: dict[str, list[str]] = {}
        enum_filters: dict[str, list[str]] = {}
        since: date | None = None
        until: date | None = None
        for key, values in params.items():
            if key == "since" and values:
                try:
                    since = date.fromisoformat(values[0])
                except ValueError:
                    pass
                continue
            if key == "until" and values:
                try:
                    until = date.fromisoformat(values[0])
                except ValueError:
                    pass
                continue
            spec = _spec_by_column(key)
            if spec is None:
                continue
            if spec.kind is FacetKind.STRING:
                string_filters[key] = list(values)
            elif spec.kind is FacetKind.ENUM:
                assert spec.enum_class is not None
                allowed = {m.value for m in spec.enum_class.__members__.values()}
                enum_filters[key] = [v for v in values if v in allowed]
        return cls(
            string_filters=string_filters,
            enum_filters=enum_filters,
            since=since,
            until=until,
        )


# ---------------------------------------------------------------------------
# The registry — single source of truth for facetable measurement columns.
# ---------------------------------------------------------------------------

MEASUREMENT_FACETS: list[FacetSpec] = [
    FacetSpec(
        column="run_outcome",
        kind=FacetKind.ENUM,
        enum_class=Outcome,
        label="Run outcome",
        description="Did the run pass overall?",
    ),
    FacetSpec(
        column="measurement_outcome",
        kind=FacetKind.ENUM,
        enum_class=Outcome,
        label="Measurement outcome",
        description="Did this measurement meet its limit?",
    ),
    FacetSpec(
        column="limit_comparator",
        kind=FacetKind.ENUM,
        enum_class=Comparator,
        label="Limit comparator",
        description="How the measurement is checked against its limits",
    ),
    FacetSpec(
        column="part_id",
        kind=FacetKind.STRING,
        label="Part",
        description="Which part the DUT is (e.g. PN-100)",
    ),
    FacetSpec(
        column="station_id",
        kind=FacetKind.STRING,
        label="Station",
        description="Which station ran the test",
    ),
    FacetSpec(
        column="test_phase",
        kind=FacetKind.STRING,
        label="Test phase",
        description="Production / qual / development",
    ),
    FacetSpec(
        column="step_name",
        kind=FacetKind.STRING,
        label="Step",
        description="Test step name",
    ),
    FacetSpec(
        column="measurement_name",
        kind=FacetKind.STRING,
        label="Measurement",
        description="Named measurement (e.g. vout)",
    ),
    FacetSpec(
        column="dut_serial",
        kind=FacetKind.STRING,
        label="DUT serial",
        description="Specific unit",
    ),
    FacetSpec(
        column="run_started_at",
        kind=FacetKind.DATE,
        label="Date range",
        description="When the run started",
    ),
]


_BY_COLUMN: dict[str, FacetSpec] = {f.column: f for f in MEASUREMENT_FACETS}


def _spec_by_column(column: str) -> FacetSpec | None:
    """Look up a facet by column name, or ``None`` if not in the registry."""
    return _BY_COLUMN.get(column)


def facets_by_kind(kind: FacetKind) -> list[FacetSpec]:
    """Return every facet of the given kind, in registry order."""
    return [f for f in MEASUREMENT_FACETS if f.kind is kind]
