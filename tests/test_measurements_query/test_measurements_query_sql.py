"""Unit tests for MeasurementsQuery SQL correctness.

Uses the canonical singleton runs daemon (the only one a Litmus
process should ever talk to). Each fixture writes synthetic
measurement parquets into the canonical runs dir under a unique
``dut_part_number`` so every aggregation can scope to this test's
own data via the existing ``product=`` filter — passing past whatever
other rows the canonical store may hold.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from litmus.analysis.measurement_facets import FilterSet, HistogramRow, ParametricRow
from litmus.analysis.measurements_query import MeasurementsQuery
from litmus.analysis.metrics import calculate_cpk, calculate_fpy
from litmus.data.backends._row_helpers import MeasurementRow
from litmus.data.data_dir import resolve_data_dir
from litmus.data.run_store import RunStore
from litmus.data.schemas import _build_write_schema, table_from_rows


def _row(
    *,
    run_id: str,
    dut_part_number: str,
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
    station_name: str = "STA-MQS",
    test_phase: str = "production",
) -> MeasurementRow:
    """Build a MeasurementRow with sensible defaults."""
    return MeasurementRow(
        record_type="measurement",
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


def _write_measurements(
    runs_dir: Path,
    rows: list[MeasurementRow],
    *,
    filename: str = "20260101T100000Z_SN001.parquet",
    notify: bool = True,
) -> Path:
    """Write MeasurementRows to a parquet file and (optionally) notify the canonical daemon."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    flat_rows = [r.to_flat_dict() for r in rows]
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
    """4 runs, 2 serials under a unique product.

    SN001: run-1 pass, run-3 pass (first pass = pass)
    SN002: run-2 fail, run-4 pass (first pass = fail, final = pass)
    """
    product = f"TEST-MQS-{uuid4().hex[:8]}"  # unique to this fixture
    canonical_runs = resolve_data_dir() / "runs" / "test-mqs" / "2026-01-01"
    rows = [
        _row(
            run_id=f"mqs-{uuid4()}",
            dut_part_number=product,
            dut_serial="SN001",
            run_outcome="passed",
            run_started_at="2026-01-01T10:00:00",
            run_ended_at="2026-01-01T10:05:00",
            value=3.3,
            outcome="passed",
        ),
        _row(
            run_id=f"mqs-{uuid4()}",
            dut_part_number=product,
            dut_serial="SN002",
            run_outcome="failed",
            run_started_at="2026-01-01T11:00:00",
            run_ended_at="2026-01-01T11:03:00",
            value=2.5,
            outcome="failed",
        ),
        _row(
            run_id=f"mqs-{uuid4()}",
            dut_part_number=product,
            dut_serial="SN001",
            run_outcome="passed",
            run_started_at="2026-01-01T12:00:00",
            run_ended_at="2026-01-01T12:04:00",
            value=3.31,
            outcome="passed",
        ),
        _row(
            run_id=f"mqs-{uuid4()}",
            dut_part_number=product,
            dut_serial="SN002",
            run_outcome="passed",
            run_started_at="2026-01-01T13:00:00",
            run_ended_at="2026-01-01T13:06:00",
            value=3.29,
            outcome="passed",
        ),
    ]
    _write_measurements(canonical_runs, rows, filename=f"{product}_main.parquet")
    return {"product": product, "station": "STA-MQS"}


# Helper: every aggregation accepts ``product`` and ``station``,
# so scope every test query by the fixture's unique product.


class TestYieldSummary:
    def test_basic_counts(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", product=fixture_data["product"])
        assert len(rows) >= 1
        total_runs = sum(r["total_runs"] for r in rows)
        assert total_runs == 4

    def test_fpy_matches_python(self, fixture_data):
        """Gold FPY must match metrics.calculate_fpy on same data."""
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", product=fixture_data["product"])
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

    def test_final_yield(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", product=fixture_data["product"])
        final_passed = sum(r["final_passed"] for r in rows)
        unique_serials = sum(r["unique_serials"] for r in rows)
        final_yield = final_passed / unique_serials if unique_serials else 0.0
        assert final_yield == pytest.approx(1.0)

    def test_phase_filter(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(product=fixture_data["product"])
        assert all(r["phase"] != "development" for r in rows)

    def test_duration_stats(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(phase="all", product=fixture_data["product"])
        for r in rows:
            assert r["avg_duration_s"] is not None
            assert r["avg_duration_s"] > 0


class TestPareto:
    def test_failure_count(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.pareto(phase="all", product=fixture_data["product"])
        assert len(rows) == 1
        assert rows[0]["fail_count"] == 1
        assert rows[0]["measurement_name"] == "vout"

    def test_no_failures(self):
        product = f"TEST-MQS-NF-{uuid4().hex[:8]}"
        canonical_runs = resolve_data_dir() / "runs" / "test-mqs-no-failures" / "2026-01-01"
        _write_measurements(
            canonical_runs,
            [_row(run_id=f"mqs-{uuid4()}", dut_part_number=product, value=3.3, outcome="passed")],
            filename=f"{product}_main.parquet",
        )
        store = MeasurementsQuery()
        assert store.pareto(phase="all", product=product) == []


class TestCpk:
    def test_cpk_matches_python(self):
        """Gold Cpk must match metrics.calculate_cpk on same data."""
        product = f"TEST-MQS-CPK-{uuid4().hex[:8]}"
        canonical_runs = resolve_data_dir() / "runs" / "test-mqs-cpk" / "2026-01-01"
        values = [3.3, 3.31, 3.29, 3.32, 3.28, 3.30, 3.33, 3.27, 3.31, 3.29]
        rows = [
            _row(
                run_id=f"mqs-cpk-{uuid4()}",
                dut_part_number=product,
                dut_serial=f"SN{i:03d}",
                value=v,
                outcome="passed",
            )
            for i, v in enumerate(values)
        ]
        _write_measurements(canonical_runs, rows, filename=f"{product}_main.parquet")

        store = MeasurementsQuery()
        gold_rows = store.cpk(phase="all", product=product, min_samples=5)
        assert len(gold_rows) >= 1
        gold_cpk = gold_rows[0]["cpk"]

        python_result = calculate_cpk(values, lsl=3.0, usl=3.6, min_samples=5)
        assert gold_cpk == pytest.approx(python_result["cpk"], abs=0.01)

    def test_min_samples_filter(self):
        product = f"TEST-MQS-MIN-{uuid4().hex[:8]}"
        canonical_runs = resolve_data_dir() / "runs" / "test-mqs-min" / "2026-01-01"
        _write_measurements(
            canonical_runs,
            [
                _row(run_id=f"mqs-{uuid4()}", dut_part_number=product, value=3.3, outcome="passed"),
                _row(
                    run_id=f"mqs-{uuid4()}",
                    dut_part_number=product,
                    dut_serial="SN002",
                    value=3.31,
                    outcome="passed",
                ),
            ],
            filename=f"{product}_main.parquet",
        )
        store = MeasurementsQuery()
        assert store.cpk(phase="all", product=product, min_samples=10) == []


class TestTrend:
    def test_trend_data(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.trend(phase="all", product=fixture_data["product"])
        assert len(rows) >= 1
        for r in rows:
            assert "period" in r
            assert "total" in r
            assert "yield_pct" in r

    def test_weekly_period(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.trend(phase="all", product=fixture_data["product"], period="week")
        assert len(rows) >= 1


class TestRetest:
    def test_retest_detection(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.retest(phase="all", product=fixture_data["product"])
        assert len(rows) >= 1
        total_serials = sum(r["total_serials"] for r in rows)
        retested = sum(r["retested_count"] for r in rows)
        assert total_serials == 2
        assert retested == 2


class TestTimeLoss:
    def test_time_breakdown(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.time_loss(phase="all", product=fixture_data["product"])
        assert len(rows) >= 1
        total = sum(r["total_time_s"] or 0 for r in rows)
        fail = sum(r["fail_time_s"] or 0 for r in rows)
        assert total > 0
        assert fail > 0


class TestEmptyDataset:
    def test_all_methods_return_empty_for_unknown_product(self):
        unknown = f"TEST-MQS-NONE-{uuid4().hex[:8]}"
        store = MeasurementsQuery()
        assert store.yield_summary(product=unknown, phase="all") == []
        assert store.pareto(product=unknown, phase="all") == []
        assert store.cpk(product=unknown, phase="all") == []
        assert store.trend(product=unknown, phase="all") == []
        assert store.retest(product=unknown, phase="all") == []
        assert store.time_loss(product=unknown, phase="all") == []


class TestFilters:
    def test_product_filter(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(product=fixture_data["product"], phase="all")
        assert all(r["product"] == fixture_data["product"] for r in rows)

    def test_station_filter(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(
            station=fixture_data["station"], product=fixture_data["product"], phase="all"
        )
        assert all(r["station"] == fixture_data["station"] for r in rows)

    def test_nonexistent_product(self):
        store = MeasurementsQuery()
        assert store.yield_summary(product=f"NOPE-{uuid4().hex}", phase="all") == []

    def test_date_filter(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.yield_summary(
            since="2026-01-01",
            until="2026-01-01",
            phase="all",
            product=fixture_data["product"],
        )
        assert len(rows) >= 1


class TestParametric:
    def test_describe_columns_lists_columns(self):
        store = MeasurementsQuery()
        cols = store.describe_columns()
        names = {c["column_name"] for c in cols}
        assert "measurement_value" in names
        assert "measurement_name" in names

    def test_scatter_returns_typed_rows(self, fixture_data):
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"product_id": [fixture_data["product"]]})
        rows = store.parametric(y="measurement_value", x="dut_serial", filters=filters)
        assert len(rows) == 4
        assert all(isinstance(r, ParametricRow) for r in rows)
        assert all(r.group == "" for r in rows)

    def test_group_by_populates_group_column(self, fixture_data):
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"product_id": [fixture_data["product"]]})
        rows = store.parametric(
            y="measurement_value", x="dut_serial", group_by="run_outcome", filters=filters
        )
        groups = {r.group for r in rows}
        assert groups == {"passed", "failed"}

    def test_filters_apply(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.parametric(
            y="measurement_value",
            x="dut_serial",
            filters=FilterSet(
                string_filters={"product_id": [fixture_data["product"]]},
                enum_filters={"run_outcome": ["passed"]},
            ),
        )
        assert len(rows) == 3

    def test_filters_multi_value(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.parametric(
            y="measurement_value",
            x="dut_serial",
            filters=FilterSet(
                string_filters={"product_id": [fixture_data["product"]]},
                enum_filters={"run_outcome": ["passed", "failed"]},
            ),
        )
        assert len(rows) == 4

    def test_histogram_returns_histogram_rows(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.parametric(
            y="measurement_value",
            x="dut_serial",
            chart_type="histogram",
            bins=4,
            filters=FilterSet(string_filters={"product_id": [fixture_data["product"]]}),
        )
        assert all(isinstance(r, HistogramRow) for r in rows)
        assert sum(r.y for r in rows) == 4

    def test_bar_aggregates(self, fixture_data):
        store = MeasurementsQuery()
        rows = store.parametric(
            y="measurement_value",
            x="dut_serial",
            chart_type="bar",
            filters=FilterSet(string_filters={"product_id": [fixture_data["product"]]}),
        )
        assert len(rows) == 2
        assert all(isinstance(r, ParametricRow) for r in rows)
        by_serial = {r.x: r.y for r in rows}
        assert by_serial["SN001"] == pytest.approx((3.3 + 3.31) / 2)
        assert by_serial["SN002"] == pytest.approx((2.5 + 3.29) / 2)

    def test_invalid_column_rejected(self):
        store = MeasurementsQuery()
        with pytest.raises(ValueError, match="invalid column identifier"):
            store.parametric(y="value; DROP TABLE silver --", x="dut_serial")


class TestDistinctValues:
    def test_no_filter_returns_fixture_serials(self, fixture_data):
        store = MeasurementsQuery()
        # Scope by product so the canonical store's other rows don't pollute.
        filters = FilterSet(string_filters={"product_id": [fixture_data["product"]]})
        opts = store.distinct_values("dut_serial", filters=filters)
        values = {o.value for o in opts}
        assert values == {"SN001", "SN002"}

    def test_options_carry_counts(self, fixture_data):
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"product_id": [fixture_data["product"]]})
        opts = store.distinct_values("dut_serial", filters=filters)
        # 2 measurements per serial in the fixture
        assert all(o.count == 2 for o in opts)

    def test_cross_filter_excludes_self(self, fixture_data):
        """exclude_self=True means filtering on product_id doesn't narrow itself."""
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"product_id": [fixture_data["product"]]})
        opts = store.distinct_values("product_id", filters=filters, exclude_self=True)
        # Returns all known products (since we excluded the product_id filter on itself).
        assert fixture_data["product"] in {o.value for o in opts}

    def test_cross_filter_narrows_other(self, fixture_data):
        """A filter on run_outcome narrows the dut_serial options to passing serials."""
        store = MeasurementsQuery()
        filters = FilterSet(
            string_filters={"product_id": [fixture_data["product"]]},
            enum_filters={"run_outcome": ["failed"]},
        )
        opts = store.distinct_values("dut_serial", filters=filters)
        # Only SN002 has a failed run for our product
        assert {o.value for o in opts} == {"SN002"}

    def test_invalid_column_rejected(self):
        store = MeasurementsQuery()
        with pytest.raises(ValueError, match="invalid column identifier"):
            store.distinct_values("evil; DROP --")


class TestSummaryCounts:
    def test_filter_counts_match_fixture(self, fixture_data):
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"product_id": [fixture_data["product"]]})
        counts = store.summary_counts(filters=filters)
        assert counts.total_rows == 4
        assert counts.distinct_runs == 4
        assert counts.distinct_measurements == 1  # only "vout"
        assert counts.distinct_products == 1  # the fixture's unique product

    def test_filter_narrows_counts(self, fixture_data):
        store = MeasurementsQuery()
        filters = FilterSet(
            string_filters={"product_id": [fixture_data["product"]]},
            enum_filters={"run_outcome": ["passed"]},
        )
        counts = store.summary_counts(filters=filters)
        assert counts.total_rows == 3

    def test_unknown_product_returns_zeros(self):
        store = MeasurementsQuery()
        filters = FilterSet(string_filters={"product_id": [f"NOPE-{uuid4().hex}"]})
        counts = store.summary_counts(filters=filters)
        assert counts.total_rows == 0
        assert counts.distinct_runs == 0
