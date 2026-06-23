"""Filter facets and field-reference types for the parametric viewer.

The ``/explore`` page lets users scope cross-run measurement queries by
filtering on columns of the ``measurements`` view. This module is the
single source of truth for *which columns are facetable* and *what kind
of facet each column wants* — closed enum sets vs open string sets vs
date ranges.

Closed sets (``Outcome``, ``Comparator``) come straight from the
Pydantic models in ``litmus.data.models`` / ``litmus.models.enums`` —
no DB query needed; the universe is known at import time. Open sets
(``part_id``, ``station_id``, ``uut_serial``, ``step_name``,
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

# ---------------------------------------------------------------------------
# Field identity — role + name + optional value_type
# ---------------------------------------------------------------------------


class FieldRole(StrEnum):
    """Which role a recorded field plays in a measurement vector."""

    INPUT = "input"
    OUTPUT = "output"
    MEASUREMENT = "measurement"


class FieldRef(BaseModel):
    """Reference to a named field, identified by (role, name).

    Use the classmethod constructors for everyday code::

        FieldRef.measurement("v_rail")
        FieldRef.output("v_rail")
        FieldRef.input("vin")

    The plain constructor also works and is used at wire boundaries::

        FieldRef(role=FieldRole.OUTPUT, name="v_rail")
        FieldRef(role="output", name="v_rail")  # FieldRole coerces from str

    ``value_type`` is an open string (not an enum) — it reflects the
    stored tag (e.g. ``"scalar:float"``, ``"scalar:int"``) and is only
    required when a (role, name) pair has mixed value_types in scope.
    """

    role: FieldRole
    name: str
    value_type: str | None = None

    @classmethod
    def input(cls, name: str, value_type: str | None = None) -> FieldRef:
        return cls(role=FieldRole.INPUT, name=name, value_type=value_type)

    @classmethod
    def output(cls, name: str, value_type: str | None = None) -> FieldRef:
        return cls(role=FieldRole.OUTPUT, name=name, value_type=value_type)

    @classmethod
    def measurement(cls, name: str, value_type: str | None = None) -> FieldRef:
        return cls(role=FieldRole.MEASUREMENT, name=name, value_type=value_type)


# ---------------------------------------------------------------------------
# describe_columns() result models
# ---------------------------------------------------------------------------


class FixedColumnDescriptor(BaseModel):
    """One plottable fixed column from the measurements view."""

    name: str
    column_type: str


class DynamicFieldDescriptor(BaseModel):
    """One role-keyed field discovered in the catalog."""

    role: FieldRole
    name: str
    value_types: list[str]


class ColumnSchema(BaseModel):
    """Return type of ``MeasurementsQuery.describe_columns()``."""

    fixed: list[FixedColumnDescriptor]
    fields: list[DynamicFieldDescriptor]


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
    epoch ms for ECharts ``time`` axes. ``group`` is always coerced to
    str so numeric EAV fields used as group_by axes render as labels.
    """

    x: float | str | datetime | date | None = None
    y: float
    group: str = ""

    @model_validator(mode="before")
    @classmethod
    def _coerce_group(cls, data: object) -> object:
        if isinstance(data, dict) and "group" in data and data["group"] is not None:
            data = dict(data)
            data["group"] = str(data["group"])
        return data


class HistogramRow(BaseModel):
    """One bin in a histogram result. ``x`` is the bin midpoint."""

    bin: int
    x: float
    y: int
    group: str = ""


class YieldRow(BaseModel):
    """One row from :meth:`MeasurementsQuery.yield_summary` or
    :meth:`MeasurementsQuery.yield_overall`."""

    part: str
    station: str
    phase: str
    period: object  # date from DuckDB — typed as object to accept date/str
    total_runs: int
    passed: int
    failed: int
    errored: int
    unique_serials: int
    first_pass_total: int
    first_pass_passed: int
    final_passed: int
    avg_duration_s: float | None = None
    p95_duration_s: float | None = None
    min_duration_s: float | None = None
    max_duration_s: float | None = None
    # Quality metrics — rty from step records; dpmo from measurement records; dppm from runs.
    # rty is None when no step records exist in the matching scope.
    rty: float | None = None  # Rolled Throughput Yield — product of per-step FPY
    dpmo: float | None = None  # Defects Per Million Opportunities (measurement-level)
    dppm: float | None = None  # Defective Parts Per Million (run-level)


class ParetoRow(BaseModel):
    """One row from :meth:`MeasurementsQuery.pareto`.

    Represents one (part, station, step, measurement) failure bucket.
    """

    part: str
    station: str
    step_name: str | None = None
    measurement_name: str | None = None
    total_count: int
    fail_count: int
    fail_rate: float | None = None

    def to_bucket_dict(self) -> dict[str, object]:
        """Normalize to the shared failure-pareto display shape."""
        return {
            "bucket": f"{self.step_name or ''}: {self.measurement_name or ''}",
            "failed_count": self.fail_count,
            "total": self.total_count,
            "fail_rate_pct": self.fail_rate,
        }


class PpkRow(BaseModel):
    """One row from :meth:`MeasurementsQuery.ppk` — one homogeneous population:
    one (part, station, measurement_name, characteristic_id, uut_pin) sharing a
    single spec limit pair. Splitting on characteristic / pin / limits keeps Ppk
    over a single distribution instead of pooling differing specs under a shared
    name."""

    part: str
    station: str
    measurement_name: str
    characteristic_id: str | None = None
    uut_pin: str | None = None
    n: int
    mean: float | None = None
    sigma: float | None = None
    lsl: float | None = None
    usl: float | None = None
    pp: float | None = None
    ppk: float | None = None


class TrendRow(BaseModel):
    """One row from :meth:`MeasurementsQuery.trend` — one (part, station, phase, period)."""

    part: str
    station: str
    phase: str
    period: object  # date from DuckDB — typed as object to accept date/str
    total: int
    passed: int
    yield_pct: float | None = None


class RetestRow(BaseModel):
    """One row from :meth:`MeasurementsQuery.retest` — one (part, station, phase, period)."""

    part: str
    station: str
    phase: str
    period: object  # date from DuckDB — typed as object to accept date/str
    total_serials: int
    retested_count: int
    retest_rate: float | None = None
    avg_retries: float | None = None


class TimeLossRow(BaseModel):
    """One row from :meth:`MeasurementsQuery.time_loss` — one (part, station, phase, period)."""

    part: str
    station: str
    phase: str
    period: object  # date from DuckDB — typed as object to accept date/str
    total_time_s: float | None = None
    pass_time_s: float | None = None
    fail_time_s: float | None = None
    error_time_s: float | None = None


class LimitBandRow(BaseModel):
    """One point of a measurement's limit envelope, keyed by the chart's X.

    The chart layer draws ``low`` and ``high`` as step lines against the
    same X axis as the data — a staircase when limits are condition-indexed,
    a flat band when they don't vary.
    """

    x: float | str | datetime | date | None = None
    low: float | None = None
    high: float | None = None


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
        description="Which part the UUT is (e.g. PN-100)",
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
        column="uut_serial",
        kind=FacetKind.STRING,
        label="UUT serial",
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
