"""Unit tests for MeasurementsQuery SQL correctness.

Creates synthetic measurement Parquet using MeasurementRow (the real
model), runs MeasurementsQuery queries, and cross-validates against
the Python metrics module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from litmus.analysis.measurement_facets import FilterSet, HistogramRow, ParametricRow
from litmus.analysis.measurements_query import MeasurementsQuery
from litmus.analysis.metrics import calculate_cpk, calculate_fpy
from litmus.data.backends._row_helpers import MeasurementRow
from litmus.data.schemas import _build_write_schema, table_from_rows


def _row(
    *,
    run_id: str = "run-1",
    dut_serial: str = "SN001",
    run_outcome: str = "passed",
    run_started_at: str = "2026-01-01T10:00:00",
    run_ended_at: str = "2026-01-01T10:05:00",
    step_name: str = "test_voltage",
    measurement_name: str = "vout",
    value: float | None = 3.3,
    outcome: str = "passed",
    limit_low: float | None = 3.0,
    limit_high: float | None = 3.6,
    dut_part_number: str = "PN-100",
    station_name: str = "STA-01",
    test_phase: str = "production",
) -> MeasurementRow:
    """Build a MeasurementRow with sensible defaults."""
    return MeasurementRow(
        session_id="sess-1",
        run_id=run_id,
        run_started_at=datetime.fromisoformat(run_started_at).replace(tzinfo=UTC),
        run_ended_at=datetime.fromisoformat(run_ended_at).replace(tzinfo=UTC),
        dut_serial=dut_serial,
        dut_part_number=dut_part_number,
        product_id=dut_part_number,
        station_id=station_name,
        station_name=station_name,
        test_phase=test_phase,
        step_name=step_name,
        step_index=0,
        measurement_name=measurement_name,
        measurement_value=value,
        measurement_outcome=outcome,
        limit_low=limit_low,
        limit_high=limit_high,
        run_outcome=run_outcome,
    )


def _write_measurements(runs_dir: Path, rows: list[MeasurementRow]) -> None:
    """Write MeasurementRows to a Parquet file in runs_dir."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    date_dir = runs_dir / "2026-01-01"
    date_dir.mkdir(exist_ok=True)
    flat_rows = [r.to_flat_dict() for r in rows]
    schema = _build_write_schema(flat_rows)
    table = table_from_rows(flat_rows, schema)
    pq.write_table(table, date_dir / "20260101T100000Z_SN001.parquet")


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    """Create results dir with synthetic measurement data.

    4 runs, 2 serials:
    - SN001: run-1 pass, run-3 pass (first pass = pass)
    - SN002: run-2 fail, run-4 pass (first pass = fail, final = pass)
    """
    runs_dir = tmp_path / "runs"
    rows = [
        _row(
            run_id="run-1",
            dut_serial="SN001",
            run_outcome="passed",
            run_started_at="2026-01-01T10:00:00",
            run_ended_at="2026-01-01T10:05:00",
            value=3.3,
            outcome="passed",
        ),
        _row(
            run_id="run-2",
            dut_serial="SN002",
            run_outcome="failed",
            run_started_at="2026-01-01T11:00:00",
            run_ended_at="2026-01-01T11:03:00",
            value=2.5,
            outcome="failed",
        ),
        _row(
            run_id="run-3",
            dut_serial="SN001",
            run_outcome="passed",
            run_started_at="2026-01-01T12:00:00",
            run_ended_at="2026-01-01T12:04:00",
            value=3.31,
            outcome="passed",
        ),
        _row(
            run_id="run-4",
            dut_serial="SN002",
            run_outcome="passed",
            run_started_at="2026-01-01T13:00:00",
            run_ended_at="2026-01-01T13:06:00",
            value=3.29,
            outcome="passed",
        ),
    ]
    _write_measurements(runs_dir, rows)
    return tmp_path


class TestYieldSummary:
    def test_basic_counts(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.yield_summary(phase="all")
        assert len(rows) >= 1
        total_runs = sum(r["total_runs"] for r in rows)
        assert total_runs == 4

    def test_fpy_matches_python(self, results_dir: Path):
        """Gold FPY must match metrics.calculate_fpy on same data."""
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.yield_summary(phase="all")
        fp_total = sum(r["first_pass_total"] for r in rows)
        fp_passed = sum(r["first_pass_passed"] for r in rows)
        gold_fpy = fp_passed / fp_total if fp_total else 0.0

        # fmt: off
        python_runs = [
            {"dut_serial": "SN001", "run_outcome": "passed", "run_started_at": "2026-01-01T10:00:00"},  # noqa: E501
            {"dut_serial": "SN002", "run_outcome": "failed", "run_started_at": "2026-01-01T11:00:00"},  # noqa: E501
            {"dut_serial": "SN001", "run_outcome": "passed", "run_started_at": "2026-01-01T12:00:00"},  # noqa: E501
            {"dut_serial": "SN002", "run_outcome": "passed", "run_started_at": "2026-01-01T13:00:00"},  # noqa: E501
        ]
        # fmt: on
        python_fpy = calculate_fpy(python_runs)
        assert gold_fpy == pytest.approx(python_fpy, abs=0.01)

    def test_final_yield(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.yield_summary(phase="all")
        final_passed = sum(r["final_passed"] for r in rows)
        unique_serials = sum(r["unique_serials"] for r in rows)
        final_yield = final_passed / unique_serials if unique_serials else 0.0
        assert final_yield == pytest.approx(1.0)

    def test_phase_filter(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.yield_summary()
        assert all(r["phase"] != "development" for r in rows)

    def test_duration_stats(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.yield_summary(phase="all")
        for r in rows:
            assert r["avg_duration_s"] is not None
            assert r["avg_duration_s"] > 0


class TestPareto:
    def test_failure_count(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.pareto(phase="all")
        assert len(rows) == 1
        assert rows[0]["fail_count"] == 1
        assert rows[0]["measurement_name"] == "vout"

    def test_no_failures(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        _write_measurements(
            runs_dir,
            [
                _row(run_id="r1", value=3.3, outcome="passed"),
            ],
        )
        store = MeasurementsQuery(_results_dir=tmp_path)
        assert store.pareto(phase="all") == []


class TestCpk:
    def test_cpk_matches_python(self, tmp_path: Path):
        """Gold Cpk must match metrics.calculate_cpk on same data."""
        runs_dir = tmp_path / "runs"
        values = [3.3, 3.31, 3.29, 3.32, 3.28, 3.30, 3.33, 3.27, 3.31, 3.29]
        rows = [
            _row(run_id=f"r{i}", dut_serial=f"SN{i:03d}", value=v, outcome="passed")
            for i, v in enumerate(values)
        ]
        _write_measurements(runs_dir, rows)

        store = MeasurementsQuery(_results_dir=tmp_path)
        gold_rows = store.cpk(phase="all", min_samples=5)
        assert len(gold_rows) >= 1
        gold_cpk = gold_rows[0]["cpk"]

        python_result = calculate_cpk(values, lsl=3.0, usl=3.6, min_samples=5)
        assert gold_cpk == pytest.approx(python_result["cpk"], abs=0.01)

    def test_min_samples_filter(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        _write_measurements(
            runs_dir,
            [
                _row(run_id="r1", value=3.3, outcome="passed"),
                _row(run_id="r2", dut_serial="SN002", value=3.31, outcome="passed"),
            ],
        )
        store = MeasurementsQuery(_results_dir=tmp_path)
        assert store.cpk(phase="all", min_samples=10) == []


class TestTrend:
    def test_trend_data(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.trend(phase="all")
        assert len(rows) >= 1
        for r in rows:
            assert "period" in r
            assert "total" in r
            assert "yield_pct" in r

    def test_weekly_period(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.trend(phase="all", period="week")
        assert len(rows) >= 1


class TestRetest:
    def test_retest_detection(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.retest(phase="all")
        assert len(rows) >= 1
        total_serials = sum(r["total_serials"] for r in rows)
        retested = sum(r["retested_count"] for r in rows)
        assert total_serials == 2
        assert retested == 2


class TestTimeLoss:
    def test_time_breakdown(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.time_loss(phase="all")
        assert len(rows) >= 1
        total = sum(r["total_time_s"] or 0 for r in rows)
        fail = sum(r["fail_time_s"] or 0 for r in rows)
        assert total > 0
        assert fail > 0


class TestEmptyDataset:
    def test_all_methods_return_empty(self, tmp_path: Path):
        store = MeasurementsQuery(_results_dir=tmp_path)
        assert store.yield_summary() == []
        assert store.pareto() == []
        assert store.cpk() == []
        assert store.trend() == []
        assert store.retest() == []
        assert store.time_loss() == []


class TestFilters:
    def test_product_filter(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.yield_summary(product="PN-100", phase="all")
        assert all(r["product"] == "PN-100" for r in rows)

    def test_station_filter(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.yield_summary(station="STA-01", phase="all")
        assert all(r["station"] == "STA-01" for r in rows)

    def test_nonexistent_product(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        assert store.yield_summary(product="NOPE", phase="all") == []

    def test_date_filter(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.yield_summary(since="2026-01-01", until="2026-01-01", phase="all")
        assert len(rows) >= 1


class TestParametric:
    def test_describe_columns_lists_columns(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        cols = store.describe_columns()
        names = {c["column_name"] for c in cols}
        assert "measurement_value" in names
        assert "measurement_name" in names

    def test_scatter_returns_typed_rows(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.parametric(y="measurement_value", x="dut_serial")
        assert len(rows) == 4
        assert all(isinstance(r, ParametricRow) for r in rows)
        assert all(r.group == "" for r in rows)

    def test_group_by_populates_group_column(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.parametric(y="measurement_value", x="dut_serial", group_by="run_outcome")
        groups = {r.group for r in rows}
        assert groups == {"passed", "failed"}

    def test_filters_apply(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.parametric(
            y="measurement_value",
            x="dut_serial",
            filters=FilterSet(enum_filters={"run_outcome": ["passed"]}),
        )
        assert len(rows) == 3

    def test_filters_multi_value(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.parametric(
            y="measurement_value",
            x="dut_serial",
            filters=FilterSet(enum_filters={"run_outcome": ["passed", "failed"]}),
        )
        assert len(rows) == 4

    def test_histogram_returns_histogram_rows(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.parametric(
            y="measurement_value", x="dut_serial", chart_type="histogram", bins=4
        )
        assert all(isinstance(r, HistogramRow) for r in rows)
        assert sum(r.y for r in rows) == 4

    def test_bar_aggregates(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        rows = store.parametric(y="measurement_value", x="dut_serial", chart_type="bar")
        assert len(rows) == 2
        assert all(isinstance(r, ParametricRow) for r in rows)
        by_serial = {r.x: r.y for r in rows}
        assert by_serial["SN001"] == pytest.approx((3.3 + 3.31) / 2)
        assert by_serial["SN002"] == pytest.approx((2.5 + 3.29) / 2)

    def test_invalid_column_rejected(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        with pytest.raises(ValueError, match="invalid column identifier"):
            store.parametric(y="value; DROP TABLE silver --", x="dut_serial")


class TestDistinctValues:
    def test_no_filter_returns_all(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        opts = store.distinct_values("dut_serial")
        values = {o.value for o in opts}
        assert values == {"SN001", "SN002"}

    def test_options_carry_counts(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        opts = store.distinct_values("dut_serial")
        # 2 measurements per serial in the fixture
        assert all(o.count == 2 for o in opts)

    def test_cross_filter_excludes_self(self, results_dir: Path):
        """exclude_self=True means filtering on station_id doesn't narrow station_id options."""
        store = MeasurementsQuery(_results_dir=results_dir)
        filters = FilterSet(string_filters={"station_id": ["STA-01"]})
        opts = store.distinct_values("station_id", filters=filters, exclude_self=True)
        # All stations still visible because we excluded self
        assert {o.value for o in opts} == {"STA-01"}

    def test_cross_filter_narrows_other(self, results_dir: Path):
        """A filter on run_outcome narrows the dut_serial options to passing serials."""
        store = MeasurementsQuery(_results_dir=results_dir)
        filters = FilterSet(enum_filters={"run_outcome": ["failed"]})
        opts = store.distinct_values("dut_serial", filters=filters)
        # Only SN002 has a failed run in the fixture
        assert {o.value for o in opts} == {"SN002"}

    def test_exclude_self_false_includes_self(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        filters = FilterSet(string_filters={"dut_serial": ["SN001"]})
        opts = store.distinct_values("dut_serial", filters=filters, exclude_self=False)
        assert {o.value for o in opts} == {"SN001"}

    def test_invalid_column_rejected(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        with pytest.raises(ValueError, match="invalid column identifier"):
            store.distinct_values("evil; DROP --")


class TestSummaryCounts:
    def test_no_filter_counts_all(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        counts = store.summary_counts()
        assert counts.total_rows == 4
        assert counts.distinct_runs == 4
        assert counts.distinct_measurements == 1  # only "vout"
        assert counts.distinct_products == 1  # only "PN-100"

    def test_filter_narrows_counts(self, results_dir: Path):
        store = MeasurementsQuery(_results_dir=results_dir)
        filters = FilterSet(enum_filters={"run_outcome": ["passed"]})
        counts = store.summary_counts(filters=filters)
        assert counts.total_rows == 3

    def test_empty_data_returns_zeros(self, tmp_path: Path):
        store = MeasurementsQuery(_results_dir=tmp_path)
        counts = store.summary_counts()
        assert counts.total_rows == 0
        assert counts.distinct_runs == 0
