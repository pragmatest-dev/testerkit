"""Unit tests for MeasurementsQuery SQL correctness.

Uses the canonical singleton runs daemon (the only one a Litmus
process should ever talk to). Each fixture writes synthetic
measurement parquets into the canonical runs dir under a unique
``uut_part_number`` so every aggregation can scope to this test's
own data via the existing ``part=`` filter — passing past whatever
other rows the canonical store may hold.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from litmus.analysis.measurement_facets import (
    ColumnSchema,
    FieldRef,
    FilterSet,
    HistogramRow,
    LimitBandRow,
    ParametricRow,
)
from litmus.analysis.measurements_query import MeasurementsQuery
from litmus.analysis.metrics import calculate_fpy, calculate_ppk
from litmus.data.backends._row_helpers import RunParquetRow
from litmus.data.data_dir import resolve_data_dir
from litmus.data.run_store import RunStore
from litmus.data.schemas import _build_write_schema, table_from_rows


def _meas_struct(
    *,
    name: str = "vout",
    value: float | None = 3.3,
    outcome: str = "passed",
    limit_low: float | None = None,
    limit_high: float | None = None,
    characteristic_id: str | None = None,
    uut_pin: str | None = None,
) -> dict:
    """Build one nested measurement struct (the at-rest measurement shape)."""
    return {
        "name": name,
        "value": value,
        "unit": None,
        "outcome": outcome,
        "timestamp": None,
        "limit_low": limit_low,
        "limit_high": limit_high,
        "limit_nominal": None,
        "limit_comparator": None,
        "characteristic_id": characteristic_id,
        "spec_ref": None,
        "uut_pin": uut_pin,
        "fixture_connection": None,
        "instrument_name": None,
        "instrument_resource": None,
        "instrument_channel": None,
        "ref": None,
    }


def _row(
    *,
    run_id: str,
    uut_part_number: str,
    uut_serial: str = "SN001",
    run_outcome: str = "passed",
    run_started_at: str = "2026-01-01T10:00:00",
    run_ended_at: str = "2026-01-01T10:05:00",
    step_name: str = "test_voltage",
    measurement_name: str = "vout",
    value: float | None = 3.3,
    outcome: str = "passed",
    limit_low: float | None = 3.0,
    limit_high: float | None = 3.6,
    characteristic_id: str | None = None,
    uut_pin: str | None = None,
    station_name: str = "STA-MQS",
    test_phase: str = "production",
    step_index: int = 0,
) -> RunParquetRow:
    """Build a vector RunParquetRow carrying one nested measurement."""
    return RunParquetRow(
        record_type="vector",
        # A ``vector`` record ALWAYS has a concrete vector_index (0..N); NULL
        # marks the logical step grain. Without this the row leaks into the
        # logical ``steps`` view and inflates step-based aggregates (RTY).
        vector_index=0,
        session_id="sess-1",
        run_id=run_id,
        run_started_at=datetime.fromisoformat(run_started_at).replace(tzinfo=UTC),
        run_ended_at=datetime.fromisoformat(run_ended_at).replace(tzinfo=UTC),
        uut_serial_number=uut_serial,
        uut_part_number=uut_part_number,
        part_id=uut_part_number,
        station_id=station_name,
        station_name=station_name,
        test_phase=test_phase,
        step_name=step_name,
        step_index=step_index,
        vector_outcome=outcome,
        run_outcome=run_outcome,
        measurements=[
            _meas_struct(
                name=measurement_name,
                value=value,
                outcome=outcome,
                limit_low=limit_low,
                limit_high=limit_high,
                characteristic_id=characteristic_id,
                uut_pin=uut_pin,
            )
        ],
    )


def _write_measurements(
    runs_dir: Path,
    rows: list[RunParquetRow],
    *,
    filename: str = "20260101T100000Z_SN001.parquet",
    notify: bool = True,
) -> Path:
    """Write vector RunParquetRows to a parquet file and (optionally) notify the daemon."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    flat_rows = [r.to_flat_dict(at_rest=True) for r in rows]
    schema = _build_write_schema(flat_rows)
    table = table_from_rows(flat_rows, schema)
    path = runs_dir / filename
    pq.write_table(table, path)
    if notify:
        notifier = RunStore()
        try:
            notifier.notify_new_run(path)
        finally:
            notifier.close()
    return path


@pytest.fixture(scope="module")
def fixture_data() -> dict[str, str]:
    """4 runs, 2 serials under a unique part.

    SN001: run-1 pass, run-3 pass (first pass = pass)
    SN002: run-2 fail, run-4 pass (first pass = fail, final = pass)
    """
    part = f"TEST-MQS-{uuid4().hex[:8]}"  # unique to this fixture
    canonical_runs = resolve_data_dir() / "runs" / "test-mqs" / "2026-01-01"
    rows = [
        _row(
            run_id=f"mqs-{uuid4()}",
            uut_part_number=part,
            uut_serial="SN001",
            run_outcome="passed",
            run_started_at="2026-01-01T10:00:00",
            run_ended_at="2026-01-01T10:05:00",
            value=3.3,
            outcome="passed",
        ),
        _row(
            run_id=f"mqs-{uuid4()}",
            uut_part_number=part,
            uut_serial="SN002",
            run_outcome="failed",
            run_started_at="2026-01-01T11:00:00",
            run_ended_at="2026-01-01T11:03:00",
            value=2.5,
            outcome="failed",
        ),
        _row(
            run_id=f"mqs-{uuid4()}",
            uut_part_number=part,
            uut_serial="SN001",
            run_outcome="passed",
            run_started_at="2026-01-01T12:00:00",
            run_ended_at="2026-01-01T12:04:00",
            value=3.31,
            outcome="passed",
        ),
        _row(
            run_id=f"mqs-{uuid4()}",
            uut_part_number=part,
            uut_serial="SN002",
            run_outcome="passed",
            run_started_at="2026-01-01T13:00:00",
            run_ended_at="2026-01-01T13:06:00",
            value=3.29,
            outcome="passed",
        ),
    ]
    _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")
    return {"part": part, "station": "STA-MQS"}


# Helper: every aggregation accepts ``part`` and ``station``,
# so scope every test query by the fixture's unique part.


class TestYieldSummary:
    def test_basic_counts(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", part=fixture_data["part"])
        assert len(rows) >= 1
        total_runs = sum(r.total_runs for r in rows)
        assert total_runs == 4

    def test_fpy_matches_python(self, fixture_data):
        """Gold FPY must match metrics.calculate_fpy on same data."""
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", part=fixture_data["part"])
        fp_total = sum(r.first_pass_total for r in rows)
        fp_passed = sum(r.first_pass_passed for r in rows)
        gold_fpy = fp_passed / fp_total if fp_total else 0.0

        # fmt: off
        python_runs = [
            {"uut_serial_number": "SN001", "run_outcome": "passed", "run_started_at": "2026-01-01T10:00:00"},  # noqa: E501
            {"uut_serial_number": "SN002", "run_outcome": "failed", "run_started_at": "2026-01-01T11:00:00"},  # noqa: E501
            {"uut_serial_number": "SN001", "run_outcome": "passed", "run_started_at": "2026-01-01T12:00:00"},  # noqa: E501
            {"uut_serial_number": "SN002", "run_outcome": "passed", "run_started_at": "2026-01-01T13:00:00"},  # noqa: E501
        ]
        # fmt: on
        python_fpy = calculate_fpy(python_runs)
        assert gold_fpy == pytest.approx(python_fpy, abs=0.01)

    def test_final_yield(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", part=fixture_data["part"])
        final_passed = sum(r.final_passed for r in rows)
        unique_serials = sum(r.unique_serials for r in rows)
        final_yield = final_passed / unique_serials if unique_serials else 0.0
        assert final_yield == pytest.approx(1.0)

    def test_phase_filter(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(part=fixture_data["part"])
        assert all(r.phase != "development" for r in rows)

    def test_duration_stats(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", part=fixture_data["part"])
        for r in rows:
            assert r.avg_duration_s is not None
            assert r.avg_duration_s > 0


class TestPareto:
    def test_failure_count(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.pareto(phase="all", part=fixture_data["part"])
        assert len(rows) == 1
        assert rows[0].fail_count == 1
        assert rows[0].measurement_name == "vout"

    def test_no_failures(self):
        part = f"TEST-MQS-NF-{uuid4().hex[:8]}"
        canonical_runs = resolve_data_dir() / "runs" / "test-mqs-no-failures" / "2026-01-01"
        _write_measurements(
            canonical_runs,
            [_row(run_id=f"mqs-{uuid4()}", uut_part_number=part, value=3.3, outcome="passed")],
            filename=f"{part}_main.parquet",
        )
        store = MeasurementsQuery()
        assert store.pareto(phase="all", part=part) == []


class TestPpk:
    def test_ppk_matches_python(self):
        """Gold Ppk must match metrics.calculate_ppk on same data."""
        part = f"TEST-MQS-PPK-{uuid4().hex[:8]}"
        canonical_runs = resolve_data_dir() / "runs" / "test-mqs-ppk" / "2026-01-01"
        values = [3.3, 3.31, 3.29, 3.32, 3.28, 3.30, 3.33, 3.27, 3.31, 3.29]
        rows = [
            _row(
                run_id=f"mqs-ppk-{uuid4()}",
                uut_part_number=part,
                uut_serial=f"SN{i:03d}",
                value=v,
                outcome="passed",
            )
            for i, v in enumerate(values)
        ]
        _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")

        store = MeasurementsQuery()
        gold_rows = store.ppk(phase="all", part=part, min_samples=5)
        assert len(gold_rows) >= 1
        gold_ppk = gold_rows[0].ppk

        python_result = calculate_ppk(values, lsl=3.0, usl=3.6, min_samples=5)
        assert gold_ppk == pytest.approx(python_result["ppk"], abs=0.01)

    def test_min_samples_filter(self):
        part = f"TEST-MQS-MIN-{uuid4().hex[:8]}"
        canonical_runs = resolve_data_dir() / "runs" / "test-mqs-min" / "2026-01-01"
        _write_measurements(
            canonical_runs,
            [
                _row(run_id=f"mqs-{uuid4()}", uut_part_number=part, value=3.3, outcome="passed"),
                _row(
                    run_id=f"mqs-{uuid4()}",
                    uut_part_number=part,
                    uut_serial="SN002",
                    value=3.31,
                    outcome="passed",
                ),
            ],
            filename=f"{part}_main.parquet",
        )
        store = MeasurementsQuery()
        assert store.ppk(phase="all", part=part, min_samples=10) == []

    def test_same_name_different_pin_splits(self):
        """Same measurement_name at two pins yields two Ppk rows, not one pooled."""
        part = f"TEST-MQS-PIN-{uuid4().hex[:8]}"
        canonical_runs = resolve_data_dir() / "runs" / "test-mqs-pin" / "2026-01-01"
        rows = []
        for i in range(6):
            rows.append(
                _row(
                    run_id=f"mqs-pin-a-{uuid4()}",
                    uut_part_number=part,
                    uut_serial=f"SNA{i:03d}",
                    value=3.30,
                    uut_pin="TP_VOUT",
                )
            )
            rows.append(
                _row(
                    run_id=f"mqs-pin-b-{uuid4()}",
                    uut_part_number=part,
                    uut_serial=f"SNB{i:03d}",
                    value=1.80,
                    uut_pin="TP_VAUX",
                )
            )
        _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")

        store = MeasurementsQuery()
        ppk_rows = store.ppk(phase="all", part=part, min_samples=5)
        pins = {r.uut_pin for r in ppk_rows}
        assert pins == {"TP_VOUT", "TP_VAUX"}
        assert all(r.measurement_name == "vout" for r in ppk_rows)

    def test_same_name_different_limits_splits(self):
        """Same name with two spec bands is not pooled under the widened union."""
        part = f"TEST-MQS-LIM-{uuid4().hex[:8]}"
        canonical_runs = resolve_data_dir() / "runs" / "test-mqs-lim" / "2026-01-01"
        rows = []
        for i in range(6):
            rows.append(
                _row(
                    run_id=f"mqs-lim-a-{uuid4()}",
                    uut_part_number=part,
                    uut_serial=f"SNA{i:03d}",
                    value=3.30,
                    limit_low=3.234,
                    limit_high=3.366,
                )
            )
            rows.append(
                _row(
                    run_id=f"mqs-lim-b-{uuid4()}",
                    uut_part_number=part,
                    uut_serial=f"SNB{i:03d}",
                    value=3.30,
                    limit_low=3.0,
                    limit_high=3.6,
                )
            )
        _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")

        store = MeasurementsQuery()
        ppk_rows = store.ppk(phase="all", part=part, min_samples=5)
        bands = {(r.lsl, r.usl) for r in ppk_rows}
        assert bands == {(3.234, 3.366), (3.0, 3.6)}


class TestTrend:
    def test_trend_data(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.trend(phase="all", part=fixture_data["part"])
        assert len(rows) >= 1
        for r in rows:
            assert r.period is not None
            assert r.total >= 0
            assert r.yield_pct is not None

    def test_weekly_period(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.trend(phase="all", part=fixture_data["part"], period="week")
        assert len(rows) >= 1


class TestRetest:
    def test_retest_detection(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.retest(phase="all", part=fixture_data["part"])
        assert len(rows) >= 1
        total_serials = sum(r.total_serials for r in rows)
        retested = sum(r.retested_count for r in rows)
        assert total_serials == 2
        assert retested == 2


class TestTimeLoss:
    def test_time_breakdown(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.time_loss(phase="all", part=fixture_data["part"])
        assert len(rows) >= 1
        total = sum(r.total_time_s or 0 for r in rows)
        fail = sum(r.fail_time_s or 0 for r in rows)
        assert total > 0
        assert fail > 0


class TestEmptyDataset:
    def test_all_methods_return_empty_for_unknown_part(self):
        unknown = f"TEST-MQS-NONE-{uuid4().hex[:8]}"
        store = MeasurementsQuery()
        assert store.yield_summary(part=unknown, phase="all") == []
        assert store.pareto(part=unknown, phase="all") == []
        assert store.ppk(part=unknown, phase="all") == []
        assert store.trend(part=unknown, phase="all") == []
        assert store.retest(part=unknown, phase="all") == []
        assert store.time_loss(part=unknown, phase="all") == []


class TestFilters:
    def test_part_filter(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(part=fixture_data["part"], phase="all")
        assert all(r.part == fixture_data["part"] for r in rows)

    def test_station_filter(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(
            station=fixture_data["station"], part=fixture_data["part"], phase="all"
        )
        assert all(r.station == fixture_data["station"] for r in rows)

    def test_nonexistent_part(self):
        store = MeasurementsQuery()
        assert store.yield_summary(part=f"NOPE-{uuid4().hex}", phase="all") == []

    def test_date_filter(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(
            since="2026-01-01",
            until="2026-01-01",
            phase="all",
            part=fixture_data["part"],
        )
        assert len(rows) >= 1


class TestParametric:
    def test_describe_columns_lists_columns(self):
        store = MeasurementsQuery()
        schema = store.describe_columns()
        assert isinstance(schema, ColumnSchema)
        fixed_names = {c.name for c in schema.fixed}
        assert "measurement_value" in fixed_names
        assert "measurement_name" in fixed_names

    def test_scatter_returns_typed_rows(self, fixture_data):
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"uut_part_number": [fixture_data["part"]]})
        rows = store.parametric(y="measurement_value", x="uut_serial_number", filters=filters)
        assert len(rows) == 4
        assert all(isinstance(r, ParametricRow) for r in rows)
        assert all(r.group == "" for r in rows)

    def test_group_by_populates_group_column(self, fixture_data):
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"uut_part_number": [fixture_data["part"]]})
        rows = store.parametric(
            y="measurement_value", x="uut_serial_number", group_by="run_outcome", filters=filters
        )
        groups = {r.group for r in rows}
        assert groups == {"passed", "failed"}

    def test_filters_apply(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.parametric(
            y="measurement_value",
            x="uut_serial_number",
            filters=FilterSet(
                string_filters={"uut_part_number": [fixture_data["part"]]},
                enum_filters={"run_outcome": ["passed"]},
            ),
        )
        assert len(rows) == 3

    def test_filters_multi_value(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.parametric(
            y="measurement_value",
            x="uut_serial_number",
            filters=FilterSet(
                string_filters={"uut_part_number": [fixture_data["part"]]},
                enum_filters={"run_outcome": ["passed", "failed"]},
            ),
        )
        assert len(rows) == 4

    def test_histogram_returns_histogram_rows(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.histogram(
            field="measurement_value",
            bins=4,
            filters=FilterSet(string_filters={"uut_part_number": [fixture_data["part"]]}),
        )
        assert all(isinstance(r, HistogramRow) for r in rows)
        assert sum(r.y for r in rows) == 4

    def test_invalid_column_rejected(self):
        # Regression: malicious string is treated as a (sql-escaped) measurement name,
        # no injection, no error. The query runs safely and the database is intact.
        store = MeasurementsQuery()
        rows = store.parametric(y="evil'; DROP TABLE --", x="measurement_value")
        assert isinstance(rows, list)
        # DB is still queryable — no injection occurred
        assert store.summary_counts() is not None


@pytest.fixture(scope="module")
def dynamic_axis_data() -> dict[str, str]:
    """Two vectors with a swept input ``freq`` (in_*) and recorded ``vout``.

    Exercises the EAV repoint: a dynamic ``in_freq`` axis is resolved by
    joining ``measurements_dynamic`` on the vector key, not the MAP.
    """
    part = f"DYN-{uuid4().hex[:8]}"
    canonical_runs = resolve_data_dir() / "runs" / "dyn" / "2026-03-01"
    run = f"dyn-{uuid4()}"
    rows = [
        RunParquetRow(
            record_type="vector",
            session_id="sess-dyn",
            run_id=run,
            run_started_at=datetime.fromisoformat("2026-03-01T10:00:00").replace(tzinfo=UTC),
            run_ended_at=datetime.fromisoformat("2026-03-01T10:05:00").replace(tzinfo=UTC),
            run_outcome="passed",
            uut_serial_number="SN-DYN",
            uut_part_number=part,
            part_id=part,
            test_phase="production",
            step_name="sweep",
            step_index=0,
            vector_index=v,
            vector_outcome="passed",
            inputs={"freq": freq},
            measurements=[_meas_struct(value=3.30 + 0.01 * v)],
        )
        for v, freq in enumerate((1000.0, 2000.0))
    ]
    _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")
    return {"part": part}


class TestDynamicAxisEAV:
    """The dynamic ``in_*``/``out_*`` axis path resolves via the EAV join."""

    def _scope(self, part: str) -> FilterSet:
        return FilterSet(string_filters={"uut_part_number": [part]})

    def test_dynamic_x_scatter(self, dynamic_axis_data):
        store = MeasurementsQuery()
        rows = store.parametric(
            y="measurement_value",
            x=FieldRef.input("freq"),
            filters=self._scope(dynamic_axis_data["part"]),
        )
        by_x = {r.x: r.y for r in rows}
        assert by_x == {1000.0: pytest.approx(3.30), 2000.0: pytest.approx(3.31)}

    def test_dynamic_y_is_joined(self, dynamic_axis_data):
        store = MeasurementsQuery()
        rows = store.parametric(
            y=FieldRef.input("freq"),
            x="uut_serial_number",
            filters=self._scope(dynamic_axis_data["part"]),
        )
        assert {r.y for r in rows} == {1000.0, 2000.0}

    def test_dynamic_distinct_via_describe(self, dynamic_axis_data):
        store = MeasurementsQuery()
        schema = store.describe_columns()
        field_names = {f.name for f in schema.fields}
        assert "freq" in field_names


@pytest.fixture(scope="module")
def limit_band_data() -> dict[str, str]:
    """Two runs of ``vout`` over two step_index points, with limits that
    tightened between runs and vary per step in the latest run.

    Older run (10:00): both steps limited 3.0–3.6.
    Newer run (12:00): step 0 limited 3.1–3.5, step 1 limited 3.2–3.4.

    ``latest_run_limits`` must return the newer run's per-step bounds.
    """
    part = f"LBR-{uuid4().hex[:8]}"
    canonical_runs = resolve_data_dir() / "runs" / "lbr" / "2026-02-01"
    older = f"lbr-{uuid4()}"
    newer = f"lbr-{uuid4()}"
    rows = [
        _row(
            run_id=older,
            uut_part_number=part,
            run_started_at="2026-02-01T10:00:00",
            step_index=0,
            limit_low=3.0,
            limit_high=3.6,
        ),
        _row(
            run_id=older,
            uut_part_number=part,
            run_started_at="2026-02-01T10:00:00",
            step_index=1,
            limit_low=3.0,
            limit_high=3.6,
        ),
        _row(
            run_id=newer,
            uut_part_number=part,
            run_started_at="2026-02-01T12:00:00",
            step_index=0,
            limit_low=3.1,
            limit_high=3.5,
        ),
        _row(
            run_id=newer,
            uut_part_number=part,
            run_started_at="2026-02-01T12:00:00",
            step_index=1,
            limit_low=3.2,
            limit_high=3.4,
        ),
    ]
    _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")
    return {"part": part}


class TestLatestRunLimits:
    def _scope(self, part: str) -> FilterSet:
        return FilterSet(string_filters={"uut_part_number": [part], "measurement_name": ["vout"]})

    def test_returns_latest_run_per_step_bounds(self, limit_band_data):
        store = MeasurementsQuery()
        rows = store.latest_run_limits(x="step_index", filters=self._scope(limit_band_data["part"]))
        assert all(isinstance(r, LimitBandRow) for r in rows)
        by_x = {r.x: (r.low, r.high) for r in rows}
        # The newer run's bounds, per step — never the older 3.0–3.6.
        assert by_x == {0: (3.1, 3.5), 1: (3.2, 3.4)}

    def test_ordered_by_x(self, limit_band_data):
        store = MeasurementsQuery()
        rows = store.latest_run_limits(x="step_index", filters=self._scope(limit_band_data["part"]))
        assert [r.x for r in rows] == [0, 1]

    def test_no_limits_returns_empty(self):
        part = f"LBR-NONE-{uuid4().hex[:8]}"
        canonical_runs = resolve_data_dir() / "runs" / "lbr-none" / "2026-02-01"
        rows = [
            _row(
                run_id=f"lbrn-{uuid4()}",
                uut_part_number=part,
                run_started_at="2026-02-01T10:00:00",
                limit_low=None,
                limit_high=None,
            ),
        ]
        _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")
        store = MeasurementsQuery()
        result = store.latest_run_limits(
            x="step_index",
            filters=FilterSet(
                string_filters={"uut_part_number": [part], "measurement_name": ["vout"]}
            ),
        )
        assert result == []


@pytest.fixture(scope="module")
def parametric_scope_data() -> dict[str, str]:
    """Two measurement names ('v_rail', 'i_rail') in one run under a unique part.

    Used to assert that parametric(y="v_rail") returns only v_rail rows,
    not i_rail rows.
    """
    part = f"TEST-PSCOPE-{uuid4().hex[:8]}"
    canonical_runs = resolve_data_dir() / "runs" / "pscope" / "2026-03-01"
    run_id = f"pscope-{uuid4()}"
    rows = [
        RunParquetRow(
            record_type="vector",
            session_id="sess-pscope",
            run_id=run_id,
            run_started_at=datetime.fromisoformat("2026-03-01T10:00:00").replace(tzinfo=UTC),
            run_ended_at=datetime.fromisoformat("2026-03-01T10:05:00").replace(tzinfo=UTC),
            run_outcome="passed",
            uut_serial_number=f"SN-{i}",
            uut_part_number=part,
            part_id=part,
            test_phase="production",
            step_name="sweep",
            step_index=0,
            vector_index=i,
            vector_outcome="passed",
            measurements=[
                _meas_struct(name="v_rail", value=3.3 + i * 0.01),
                _meas_struct(name="i_rail", value=0.1 + i * 0.005),
            ],
        )
        for i in range(5)
    ]
    _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")
    return {"part": part}


class TestParametricMeasurementScoping:
    """#2.1 — parametric(y="v_rail") must scope to v_rail rows only."""

    def test_measurement_name_scoped(self, parametric_scope_data):
        """y=FieldRef.measurement("v_rail") returns only v_rail measurement_name rows."""
        part = parametric_scope_data["part"]
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"uut_part_number": [part]})
        rows = store.parametric(
            y=FieldRef.measurement("v_rail"),
            x="vector_index",
            filters=filters,
        )
        assert rows, "expected non-empty result"
        assert all(r.y is not None for r in rows)
        # All y values come from v_rail (3.3–3.34), not i_rail (0.1–0.12)
        assert all(r.y > 1.0 for r in rows), (
            "i_rail rows (y~0.1) leaked into v_rail parametric result"
        )

    def test_different_measurement_names_are_independent(self, parametric_scope_data):
        """y="v_rail" and y="i_rail" return disjoint value ranges."""
        part = parametric_scope_data["part"]
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"uut_part_number": [part]})
        v_rows = store.parametric(
            y=FieldRef.measurement("v_rail"),
            x="vector_index",
            filters=filters,
        )
        i_rows = store.parametric(
            y=FieldRef.measurement("i_rail"),
            x="vector_index",
            filters=filters,
        )
        assert v_rows and i_rows
        v_max = max(r.y for r in v_rows if r.y is not None)
        i_max = max(r.y for r in i_rows if r.y is not None)
        assert v_max > 1.0, "v_rail values should be ~3.3"
        assert i_max < 1.0, "i_rail values should be ~0.1"


class TestDistinctValues:
    def test_no_filter_returns_fixture_serials(self, fixture_data):
        store = MeasurementsQuery()
        # Scope by part so the canonical store's other rows don't pollute.
        filters = FilterSet(string_filters={"uut_part_number": [fixture_data["part"]]})
        opts = store.distinct_values("uut_serial_number", filters=filters)
        values = {o.value for o in opts}
        assert values == {"SN001", "SN002"}

    def test_options_carry_counts(self, fixture_data):
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"uut_part_number": [fixture_data["part"]]})
        opts = store.distinct_values("uut_serial_number", filters=filters)
        # 2 measurements per serial in the fixture
        assert all(o.count == 2 for o in opts)

    def test_cross_filter_excludes_self(self, fixture_data):
        """exclude_self=True means filtering on part_id doesn't narrow itself."""
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"uut_part_number": [fixture_data["part"]]})
        opts = store.distinct_values("uut_part_number", filters=filters, exclude_self=True)
        # Returns all known parts (since we excluded the part_id filter on itself).
        assert fixture_data["part"] in {o.value for o in opts}

    def test_cross_filter_narrows_other(self, fixture_data):
        """A filter on run_outcome narrows the uut_serial options to passing serials."""
        store = MeasurementsQuery()
        filters = FilterSet(
            string_filters={"uut_part_number": [fixture_data["part"]]},
            enum_filters={"run_outcome": ["failed"]},
        )
        opts = store.distinct_values("uut_serial_number", filters=filters)
        # Only SN002 has a failed run for our part
        assert {o.value for o in opts} == {"SN002"}

    def test_invalid_column_rejected(self):
        store = MeasurementsQuery()
        with pytest.raises(ValueError, match="invalid column identifier"):
            store.distinct_values("evil; DROP --")


class TestSummaryCounts:
    def test_filter_counts_match_fixture(self, fixture_data):
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"uut_part_number": [fixture_data["part"]]})
        counts = store.summary_counts(filters=filters)
        assert counts.total_rows == 4
        assert counts.distinct_runs == 4
        assert counts.distinct_measurements == 1  # only "vout"
        assert counts.distinct_parts == 1  # the fixture's unique part

    def test_filter_narrows_counts(self, fixture_data):
        store = MeasurementsQuery()
        filters = FilterSet(
            string_filters={"uut_part_number": [fixture_data["part"]]},
            enum_filters={"run_outcome": ["passed"]},
        )
        counts = store.summary_counts(filters=filters)
        assert counts.total_rows == 3

    def test_unknown_part_returns_zeros(self):
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"uut_part_number": [f"NOPE-{uuid4().hex}"]})
        counts = store.summary_counts(filters=filters)
        assert counts.total_rows == 0
        assert counts.distinct_runs == 0


class TestResolveValueTypeMissEmpty:
    """Fix #2: a (role, name) absent from measurements_dynamic yields empty
    results rather than fabricating 'scalar:float' and returning wrong rows."""

    def test_unknown_output_field_returns_empty_parametric(self):
        store = MeasurementsQuery()
        rows = store.parametric(
            y=FieldRef.output(f"no_such_field_{uuid4().hex}"),
            x="measurement_value",
        )
        assert rows == [], "unknown EAV field must return empty, not fabricated rows"

    def test_unknown_input_field_returns_empty_histogram(self):
        store = MeasurementsQuery()
        rows = store.histogram(field=FieldRef.input(f"no_such_field_{uuid4().hex}"))
        assert rows == [], "unknown EAV field must return empty, not fabricated rows"


@pytest.fixture(scope="module")
def distinct_role_data() -> dict[str, str]:
    """Fixture: two parts, each with one EAV output field named 'freq'.

    Part A has 5 rows; part B has 3 rows.
    ``distinct_values(role=)`` filtered to part A must return only part-A names.
    """
    part_a = f"DV-ROLE-A-{uuid4().hex[:8]}"
    part_b = f"DV-ROLE-B-{uuid4().hex[:8]}"
    for part, n_rows in ((part_a, 5), (part_b, 3)):
        canonical_runs = resolve_data_dir() / "runs" / "dv-role" / "2026-04-01"
        run = f"dvr-{uuid4()}"
        rows = [
            RunParquetRow(
                record_type="vector",
                session_id="sess-dvr",
                run_id=run,
                run_started_at=datetime.fromisoformat("2026-04-01T10:00:00").replace(tzinfo=UTC),
                run_ended_at=datetime.fromisoformat("2026-04-01T10:05:00").replace(tzinfo=UTC),
                run_outcome="passed",
                uut_serial_number=f"SN-{i}",
                uut_part_number=part,
                part_id=part,
                test_phase="production",
                step_name="sweep",
                step_index=0,
                vector_index=i,
                vector_outcome="passed",
                outputs={"freq": float(1000 * (i + 1))},
                measurements=[_meas_struct(value=3.3)],
            )
            for i in range(n_rows)
        ]
        _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")
    return {"part_a": part_a, "part_b": part_b}


class TestDistinctValuesRoleHonorsFilters:
    """Fix #3: distinct_values(role=...) must apply the caller's FilterSet."""

    def test_role_filter_scoped_to_part(self, distinct_role_data):
        store = MeasurementsQuery()
        part_a = distinct_role_data["part_a"]
        filters = FilterSet(string_filters={"uut_part_number": [part_a]})
        opts = store.distinct_values("name", role="output", filters=filters)
        assert len(opts) >= 1
        assert any(o.value == "freq" for o in opts)

    def test_role_no_filter_sees_both_parts(self, distinct_role_data):
        store = MeasurementsQuery()
        opts_unscoped = store.distinct_values("name", role="output")
        names = {o.value for o in opts_unscoped}
        assert "freq" in names

    def test_role_filter_excludes_other_part_data(self, distinct_role_data):
        """Counts differ when filtered: part_a has more rows than part_b."""
        store = MeasurementsQuery()
        part_a = distinct_role_data["part_a"]
        part_b = distinct_role_data["part_b"]
        opts_a = store.distinct_values(
            "name", role="output", filters=FilterSet(string_filters={"uut_part_number": [part_a]})
        )
        opts_b = store.distinct_values(
            "name", role="output", filters=FilterSet(string_filters={"uut_part_number": [part_b]})
        )
        count_a = next((o.count for o in opts_a if o.value == "freq"), 0)
        count_b = next((o.count for o in opts_b if o.value == "freq"), 0)
        assert count_a > count_b, (
            f"part_a has more rows ({count_a}) than part_b ({count_b}) — "
            "filters must be applied in the role= branch"
        )


@pytest.fixture(scope="module")
def mixed_type_field_data() -> dict[str, str]:
    """Two parts that write the same output field name with different value_types.

    part_float writes ``sensor`` as a float (scalar:float).
    part_str   writes ``sensor`` as a string (scalar:str).

    Globally the field is ambiguous (two value_types). Within a filter
    scoped to either part alone it is uniform.
    """
    part_float = f"MT-FLOAT-{uuid4().hex[:8]}"
    part_str = f"MT-STR-{uuid4().hex[:8]}"
    for part, outputs in (
        (part_float, {"sensor": 42.0}),
        (part_str, {"sensor": "ok"}),
    ):
        canonical_runs = resolve_data_dir() / "runs" / "mixed-type" / "2026-05-01"
        run = f"mt-{uuid4()}"
        rows = [
            RunParquetRow(
                record_type="vector",
                session_id="sess-mt",
                run_id=run,
                run_started_at=datetime.fromisoformat("2026-05-01T10:00:00").replace(tzinfo=UTC),
                run_ended_at=datetime.fromisoformat("2026-05-01T10:05:00").replace(tzinfo=UTC),
                run_outcome="passed",
                uut_serial_number="SN-MT",
                uut_part_number=part,
                part_id=part,
                test_phase="production",
                step_name="measure",
                step_index=0,
                vector_index=0,
                vector_outcome="passed",
                outputs=outputs,
                measurements=[_meas_struct(value=3.3)],
            )
        ]
        _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")
    return {"part_float": part_float, "part_str": part_str}


class TestResolveValueTypeFilterScoped:
    """Fix #2 — ambiguity check must be scoped by the active FilterSet.

    A field that has two value_types globally (float from part_float,
    str from part_str) must resolve cleanly when the filter limits scope
    to one part, not raise ValueError.
    """

    def test_uniform_under_filter_resolves_without_raise(self, mixed_type_field_data):
        """Globally mixed-type field resolves cleanly when filter scopes to one part."""
        store = MeasurementsQuery()
        part_float = mixed_type_field_data["part_float"]
        filters = FilterSet(string_filters={"uut_part_number": [part_float]})
        # Must not raise — field is uniform (scalar:float) within this filter.
        rows = store.parametric(
            y=FieldRef.output("sensor"),
            x="measurement_value",
            filters=filters,
        )
        assert rows, "expected rows from the float part"
        assert all(r.y == pytest.approx(42.0) for r in rows)

    def test_globally_mixed_raises_without_filter(self, mixed_type_field_data):
        """Without a scoping filter the field is ambiguous and must raise ValueError."""
        store = MeasurementsQuery()
        part_float = mixed_type_field_data["part_float"]
        part_str = mixed_type_field_data["part_str"]
        # Both parts in scope → two value_types → ValueError.
        filters = FilterSet(string_filters={"uut_part_number": [part_float, part_str]})
        with pytest.raises(ValueError, match="value_types in scope"):
            store.parametric(
                y=FieldRef.output("sensor"),
                x="measurement_value",
                filters=filters,
            )


def _step_row(
    *,
    run_id: str,
    uut_part_number: str,
    uut_serial: str = "SN001",
    run_outcome: str = "passed",
    run_started_at: str = "2026-01-01T10:00:00",
    run_ended_at: str = "2026-01-01T10:05:00",
    step_name: str = "test_voltage",
    step_path: str = "test_voltage",
    step_index: int = 0,
    step_outcome: str = "passed",
    station_name: str = "STA-RTY",
    test_phase: str = "production",
) -> RunParquetRow:
    """Build a step RunParquetRow (record_type='step')."""
    return RunParquetRow(
        record_type="step",
        session_id="sess-rty",
        run_id=run_id,
        run_started_at=datetime.fromisoformat(run_started_at).replace(tzinfo=UTC),
        run_ended_at=datetime.fromisoformat(run_ended_at).replace(tzinfo=UTC),
        uut_serial_number=uut_serial,
        uut_part_number=uut_part_number,
        part_id=uut_part_number,
        station_id=station_name,
        station_name=station_name,
        test_phase=test_phase,
        step_name=step_name,
        step_path=step_path,
        step_index=step_index,
        step_outcome=step_outcome,
        run_outcome=run_outcome,
    )


@pytest.fixture(scope="module")
def rty_fixture_data() -> dict[str, str]:
    """3 runs with 2 steps each; one run fails step_b.

    run-A: step_a=passed, step_b=passed  → run outcome=passed
    run-B: step_a=passed, step_b=failed  → run outcome=failed
    run-C: step_a=passed, step_b=passed  → run outcome=passed

    Expected values:
      step_a FPY = 3/3 = 1.0
      step_b FPY = 2/3
      RTY = 1.0 * (2/3) = 2/3 ≈ 0.6667

      total_measurements = 3 (one _row per run), failed_measurements = 1 (run-B)
      DPMO = 1/3 * 1e6 ≈ 333333

      total_runs = 3, failed_runs = 1
      DPPM = 1/3 * 1e6 ≈ 333333
    """
    part = f"TEST-RTY-{uuid4().hex[:8]}"
    canonical_runs = resolve_data_dir() / "runs" / "test-rty" / "2026-01-01"
    run_a = f"rty-{uuid4()}"
    run_b = f"rty-{uuid4()}"
    run_c = f"rty-{uuid4()}"
    rows = [
        # run-A: both steps pass
        _step_row(
            run_id=run_a,
            uut_part_number=part,
            uut_serial="RTY-SN001",
            step_name="step_a",
            step_path="step_a",
            step_index=0,
            step_outcome="passed",
            run_outcome="passed",
        ),
        _step_row(
            run_id=run_a,
            uut_part_number=part,
            uut_serial="RTY-SN001",
            step_name="step_b",
            step_path="step_b",
            step_index=1,
            step_outcome="passed",
            run_outcome="passed",
        ),
        # run-B: step_b fails → run failed
        _step_row(
            run_id=run_b,
            uut_part_number=part,
            uut_serial="RTY-SN002",
            step_name="step_a",
            step_path="step_a",
            step_index=0,
            step_outcome="passed",
            run_outcome="failed",
        ),
        _step_row(
            run_id=run_b,
            uut_part_number=part,
            uut_serial="RTY-SN002",
            step_name="step_b",
            step_path="step_b",
            step_index=1,
            step_outcome="failed",
            run_outcome="failed",
        ),
        # run-C: both steps pass
        _step_row(
            run_id=run_c,
            uut_part_number=part,
            uut_serial="RTY-SN003",
            step_name="step_a",
            step_path="step_a",
            step_index=0,
            step_outcome="passed",
            run_outcome="passed",
        ),
        _step_row(
            run_id=run_c,
            uut_part_number=part,
            uut_serial="RTY-SN003",
            step_name="step_b",
            step_path="step_b",
            step_index=1,
            step_outcome="passed",
            run_outcome="passed",
        ),
        # One vector row per run so yield_summary has measurement rows to aggregate.
        _row(
            run_id=run_a,
            uut_part_number=part,
            uut_serial="RTY-SN001",
            run_outcome="passed",
            step_name="step_b",
        ),
        _row(
            run_id=run_b,
            uut_part_number=part,
            uut_serial="RTY-SN002",
            run_outcome="failed",
            step_name="step_b",
            value=2.5,
            outcome="failed",
        ),
        _row(
            run_id=run_c,
            uut_part_number=part,
            uut_serial="RTY-SN003",
            run_outcome="passed",
            step_name="step_b",
        ),
    ]
    _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")
    return {"part": part, "station": "STA-RTY"}


class TestYieldRTY_DPMO_DPPM:
    """RTY (step-based), DPMO (measurement-based), DPPM (run-based)."""

    def test_rty_is_product_of_step_fpy(self, rty_fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", part=rty_fixture_data["part"])
        assert rows, "expected at least one yield row"
        # Sum across periods (fixture uses a single day).
        rty_val = next((r.rty for r in rows if r.rty is not None), None)
        assert rty_val is not None, "rty must be populated when step data exists"
        # step_a: 3/3=1.0; step_b: 2/3 → RTY = 2/3
        assert rty_val == pytest.approx(2 / 3, rel=0.01)

    def test_dpmo_is_failed_measurements_per_million(self, rty_fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", part=rty_fixture_data["part"])
        assert rows
        dpmo_val = next((r.dpmo for r in rows if r.dpmo is not None), None)
        assert dpmo_val is not None, "dpmo must be populated when measurement data exists"
        # 3 measurement records (1 per run): run_b=failed, run_a/c=passed
        # failed_measurements=1, total_measurements=3 → 1/3 * 1e6 ≈ 333333
        assert dpmo_val == pytest.approx(1_000_000 / 3, rel=0.01)

    def test_dppm_is_failed_runs_per_million(self, rty_fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", part=rty_fixture_data["part"])
        assert rows
        dppm_val = next((r.dppm for r in rows if r.dppm is not None), None)
        assert dppm_val is not None, "dppm must be populated"
        # 1 failed run out of 3 → 1/3 * 1e6 ≈ 333333
        assert dppm_val == pytest.approx(1_000_000 / 3, rel=0.01)

    def test_no_step_data_yields_none_rty(self):
        """No step records → rty is None; measurement-based dpmo is 0 when all pass."""
        part = f"TEST-RTY-NOSTEP-{uuid4().hex[:8]}"
        canonical_runs = resolve_data_dir() / "runs" / "test-rty-nostep" / "2026-01-01"
        _write_measurements(
            canonical_runs,
            [_row(run_id=f"ns-{uuid4()}", uut_part_number=part, value=3.3, outcome="passed")],
            filename=f"{part}_main.parquet",
        )
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", part=part)
        assert rows
        r = rows[0]
        assert r.rty is None
        # 1 measurement, outcome=passed → failed_measurements=0, dpmo=0
        assert r.dpmo == pytest.approx(0.0)


@pytest.fixture(scope="module")
def overall_fixture_data() -> dict[str, str]:
    """SN001 tested on two stations; SN002 tested on one station only.

    SN001 @ station_a: run_x — failed (first run, two steps: step_a pass, step_b fail)
    SN001 @ station_b: run_y — passed (two steps: step_a pass, step_b pass)
    SN002 @ station_a: run_z — passed (two steps: step_a pass, step_b pass)

    Expected overall pooled values:
      unique_serials = 2  (DISTINCT — no double-count across stations)
      first_pass_total = 2, first_pass_passed = 1  → FPY = 0.5
      final_passed = 2  (both pass on their last run)  → Final Yield = 1.0
      total_runs = 3, failed = 1  → DPPM = 1/3 × 1e6 ≈ 333333
      step_a: 3/3=1.0, step_b: 2/3  → RTY = 2/3 ≈ 0.6667
      total_measurements = 3 (one _row per run), failed_measurements = 1 (run_x)
      DPMO = 1/3 × 1e6 ≈ 333333
      run durations: run_x=180s, run_y=240s, run_z=300s
        → min=180, max=300, avg=240, p95≈294
    """
    part = f"TEST-OVERALL-{uuid4().hex[:8]}"
    canonical_runs = resolve_data_dir() / "runs" / "test-overall" / "2026-02-01"
    run_x = f"overall-{uuid4()}"
    run_y = f"overall-{uuid4()}"
    run_z = f"overall-{uuid4()}"
    station_a = f"STA-OVR-A-{part[-4:]}"
    station_b = f"STA-OVR-B-{part[-4:]}"
    rows = [
        # run_x: SN001 @ station_a, failed — step_b fails
        _step_row(
            run_id=run_x,
            uut_part_number=part,
            uut_serial="OVR-SN001",
            step_name="step_a",
            step_path="step_a",
            step_index=0,
            step_outcome="passed",
            run_outcome="failed",
            station_name=station_a,
            run_started_at="2026-02-01T10:00:00",
            run_ended_at="2026-02-01T10:03:00",
        ),
        _step_row(
            run_id=run_x,
            uut_part_number=part,
            uut_serial="OVR-SN001",
            step_name="step_b",
            step_path="step_b",
            step_index=1,
            step_outcome="failed",
            run_outcome="failed",
            station_name=station_a,
            run_started_at="2026-02-01T10:00:00",
            run_ended_at="2026-02-01T10:03:00",
        ),
        # run_y: SN001 @ station_b, passed — both steps pass
        _step_row(
            run_id=run_y,
            uut_part_number=part,
            uut_serial="OVR-SN001",
            step_name="step_a",
            step_path="step_a",
            step_index=0,
            step_outcome="passed",
            run_outcome="passed",
            station_name=station_b,
            run_started_at="2026-02-01T11:00:00",
            run_ended_at="2026-02-01T11:04:00",
        ),
        _step_row(
            run_id=run_y,
            uut_part_number=part,
            uut_serial="OVR-SN001",
            step_name="step_b",
            step_path="step_b",
            step_index=1,
            step_outcome="passed",
            run_outcome="passed",
            station_name=station_b,
            run_started_at="2026-02-01T11:00:00",
            run_ended_at="2026-02-01T11:04:00",
        ),
        # run_z: SN002 @ station_a, passed — both steps pass
        _step_row(
            run_id=run_z,
            uut_part_number=part,
            uut_serial="OVR-SN002",
            step_name="step_a",
            step_path="step_a",
            step_index=0,
            step_outcome="passed",
            run_outcome="passed",
            station_name=station_a,
            run_started_at="2026-02-01T12:00:00",
            run_ended_at="2026-02-01T12:05:00",
        ),
        _step_row(
            run_id=run_z,
            uut_part_number=part,
            uut_serial="OVR-SN002",
            step_name="step_b",
            step_path="step_b",
            step_index=1,
            step_outcome="passed",
            run_outcome="passed",
            station_name=station_a,
            run_started_at="2026-02-01T12:00:00",
            run_ended_at="2026-02-01T12:05:00",
        ),
        # One vector row per run so runs appear in the measurements view.
        _row(
            run_id=run_x,
            uut_part_number=part,
            uut_serial="OVR-SN001",
            run_outcome="failed",
            run_started_at="2026-02-01T10:00:00",
            run_ended_at="2026-02-01T10:03:00",
            station_name=station_a,
            value=2.5,
            outcome="failed",
        ),
        _row(
            run_id=run_y,
            uut_part_number=part,
            uut_serial="OVR-SN001",
            run_outcome="passed",
            run_started_at="2026-02-01T11:00:00",
            run_ended_at="2026-02-01T11:04:00",
            station_name=station_b,
            value=3.3,
            outcome="passed",
        ),
        _row(
            run_id=run_z,
            uut_part_number=part,
            uut_serial="OVR-SN002",
            run_outcome="passed",
            run_started_at="2026-02-01T12:00:00",
            run_ended_at="2026-02-01T12:05:00",
            station_name=station_a,
            value=3.3,
            outcome="passed",
        ),
    ]
    _write_measurements(canonical_runs, rows, filename=f"{part}_main.parquet")
    return {"part": part, "station_a": station_a, "station_b": station_b}


class TestYieldOverall:
    """yield_overall returns one pooled YieldRow; no double-counting across stations."""

    def test_unique_serials_not_doubled(self, overall_fixture_data):
        """COUNT(DISTINCT uut_serial) must be 2, not 3 (SN001 across 2 stations)."""
        store = MeasurementsQuery()
        row = store.yield_overall(phase="all", part=overall_fixture_data["part"])
        assert row is not None
        assert row.unique_serials == 2

    def test_final_yield_gte_fpy(self, overall_fixture_data):
        """Final Yield >= FPY when all serials eventually pass."""
        store = MeasurementsQuery()
        row = store.yield_overall(phase="all", part=overall_fixture_data["part"])
        assert row is not None
        fpy = row.first_pass_passed / row.first_pass_total if row.first_pass_total else 0.0
        final_yield = row.final_passed / row.unique_serials if row.unique_serials else 0.0
        assert fpy == pytest.approx(0.5, abs=0.01)
        assert final_yield == pytest.approx(1.0, abs=0.01)
        assert final_yield >= fpy

    def test_rty_is_pooled_product(self, overall_fixture_data):
        """RTY = EXP(SUM(LN(per-step FPY))) pooled over all filtered runs."""
        store = MeasurementsQuery()
        row = store.yield_overall(phase="all", part=overall_fixture_data["part"])
        assert row is not None
        assert row.rty is not None
        # step_a: 3/3=1.0; step_b: 2/3 → RTY = 2/3
        assert row.rty == pytest.approx(2 / 3, rel=0.01)

    def test_dpmo_pooled(self, overall_fixture_data):
        """DPMO = 1 failed measurement / 3 total measurements × 1e6."""
        store = MeasurementsQuery()
        row = store.yield_overall(phase="all", part=overall_fixture_data["part"])
        assert row is not None
        # 3 measurement records (1 per run): run_x=failed, run_y/z=passed
        # failed_measurements=1, total_measurements=3 → 1/3 * 1e6 ≈ 333333
        assert row.dpmo == pytest.approx(1_000_000 / 3, rel=0.01)

    def test_dppm_pooled(self, overall_fixture_data):
        """DPPM = 1 failed run / 3 total runs × 1e6."""
        store = MeasurementsQuery()
        row = store.yield_overall(phase="all", part=overall_fixture_data["part"])
        assert row is not None
        assert row.dppm == pytest.approx(1_000_000 / 3, rel=0.01)

    def test_duration_min_max(self, overall_fixture_data):
        """min_duration_s and max_duration_s reflect true MIN/MAX over all runs."""
        store = MeasurementsQuery()
        row = store.yield_overall(phase="all", part=overall_fixture_data["part"])
        assert row is not None
        assert row.min_duration_s == pytest.approx(180.0, abs=1.0)  # run_x: 3 min
        assert row.max_duration_s == pytest.approx(300.0, abs=1.0)  # run_z: 5 min

    def test_returns_none_for_unknown_part(self):
        store = MeasurementsQuery()
        result = store.yield_overall(phase="all", part=f"NOPE-{uuid4().hex}")
        assert result is None

    def test_part_station_period_are_all(self, overall_fixture_data):
        store = MeasurementsQuery()
        row = store.yield_overall(phase="all", part=overall_fixture_data["part"])
        assert row is not None
        assert row.part == "all"
        assert row.station == "all"
        assert row.period == "all"
