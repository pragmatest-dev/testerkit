"""Unit tests for gold layer SQL correctness.

Creates synthetic silver Parquet using MeasurementRow (the real model),
runs MetricsStore queries, and cross-validates against the Python metrics module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from litmus.analysis.metrics import calculate_cpk, calculate_fpy
from litmus.analysis.metrics_store import MetricsStore
from litmus.data.backends._row_helpers import MeasurementRow
from litmus.data.schemas import _build_write_schema, table_from_rows


def _row(
    *,
    run_id: str = "run-1",
    dut_serial: str = "SN001",
    run_outcome: str = "pass",
    run_started_at: str = "2026-01-01T10:00:00",
    run_ended_at: str = "2026-01-01T10:05:00",
    step_name: str = "test_voltage",
    measurement_name: str = "vout",
    value: float | None = 3.3,
    outcome: str = "pass",
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


def _write_silver(runs_dir: Path, rows: list[MeasurementRow]) -> None:
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
            run_outcome="pass",
            run_started_at="2026-01-01T10:00:00",
            run_ended_at="2026-01-01T10:05:00",
            value=3.3,
            outcome="pass",
        ),
        _row(
            run_id="run-2",
            dut_serial="SN002",
            run_outcome="fail",
            run_started_at="2026-01-01T11:00:00",
            run_ended_at="2026-01-01T11:03:00",
            value=2.5,
            outcome="fail",
        ),
        _row(
            run_id="run-3",
            dut_serial="SN001",
            run_outcome="pass",
            run_started_at="2026-01-01T12:00:00",
            run_ended_at="2026-01-01T12:04:00",
            value=3.31,
            outcome="pass",
        ),
        _row(
            run_id="run-4",
            dut_serial="SN002",
            run_outcome="pass",
            run_started_at="2026-01-01T13:00:00",
            run_ended_at="2026-01-01T13:06:00",
            value=3.29,
            outcome="pass",
        ),
    ]
    _write_silver(runs_dir, rows)
    return tmp_path


class TestYieldSummary:
    def test_basic_counts(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.yield_summary(phase="all")
        assert len(rows) >= 1
        total_runs = sum(r["total_runs"] for r in rows)
        assert total_runs == 4

    def test_fpy_matches_python(self, results_dir: Path):
        """Gold FPY must match metrics.calculate_fpy on same data."""
        store = MetricsStore(_results_dir=results_dir)
        rows = store.yield_summary(phase="all")
        fp_total = sum(r["first_pass_total"] for r in rows)
        fp_passed = sum(r["first_pass_passed"] for r in rows)
        gold_fpy = fp_passed / fp_total if fp_total else 0.0

        python_runs = [
            {"dut_serial": "SN001", "run_outcome": "pass", "run_started_at": "2026-01-01T10:00:00"},
            {"dut_serial": "SN002", "run_outcome": "fail", "run_started_at": "2026-01-01T11:00:00"},
            {"dut_serial": "SN001", "run_outcome": "pass", "run_started_at": "2026-01-01T12:00:00"},
            {"dut_serial": "SN002", "run_outcome": "pass", "run_started_at": "2026-01-01T13:00:00"},
        ]
        python_fpy = calculate_fpy(python_runs)
        assert gold_fpy == pytest.approx(python_fpy, abs=0.01)

    def test_final_yield(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.yield_summary(phase="all")
        final_passed = sum(r["final_passed"] for r in rows)
        unique_serials = sum(r["unique_serials"] for r in rows)
        final_yield = final_passed / unique_serials if unique_serials else 0.0
        assert final_yield == pytest.approx(1.0)

    def test_phase_filter(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.yield_summary()
        assert all(r["phase"] != "development" for r in rows)

    def test_duration_stats(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.yield_summary(phase="all")
        for r in rows:
            assert r["avg_duration_s"] is not None
            assert r["avg_duration_s"] > 0


class TestPareto:
    def test_failure_count(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.pareto(phase="all")
        assert len(rows) == 1
        assert rows[0]["fail_count"] == 1
        assert rows[0]["measurement_name"] == "vout"

    def test_no_failures(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        _write_silver(
            runs_dir,
            [
                _row(run_id="r1", value=3.3, outcome="pass"),
            ],
        )
        store = MetricsStore(_results_dir=tmp_path)
        assert store.pareto(phase="all") == []


class TestCpk:
    def test_cpk_matches_python(self, tmp_path: Path):
        """Gold Cpk must match metrics.calculate_cpk on same data."""
        runs_dir = tmp_path / "runs"
        values = [3.3, 3.31, 3.29, 3.32, 3.28, 3.30, 3.33, 3.27, 3.31, 3.29]
        rows = [
            _row(run_id=f"r{i}", dut_serial=f"SN{i:03d}", value=v, outcome="pass")
            for i, v in enumerate(values)
        ]
        _write_silver(runs_dir, rows)

        store = MetricsStore(_results_dir=tmp_path)
        gold_rows = store.cpk(phase="all", min_samples=5)
        assert len(gold_rows) >= 1
        gold_cpk = gold_rows[0]["cpk"]

        python_result = calculate_cpk(values, lsl=3.0, usl=3.6, min_samples=5)
        assert gold_cpk == pytest.approx(python_result["cpk"], abs=0.01)

    def test_min_samples_filter(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        _write_silver(
            runs_dir,
            [
                _row(run_id="r1", value=3.3, outcome="pass"),
                _row(run_id="r2", dut_serial="SN002", value=3.31, outcome="pass"),
            ],
        )
        store = MetricsStore(_results_dir=tmp_path)
        assert store.cpk(phase="all", min_samples=10) == []


class TestTrend:
    def test_trend_data(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.trend(phase="all")
        assert len(rows) >= 1
        for r in rows:
            assert "period" in r
            assert "total" in r
            assert "yield_pct" in r

    def test_weekly_period(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.trend(phase="all", period="week")
        assert len(rows) >= 1


class TestRetest:
    def test_retest_detection(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.retest(phase="all")
        assert len(rows) >= 1
        total_serials = sum(r["total_serials"] for r in rows)
        retested = sum(r["retested_count"] for r in rows)
        assert total_serials == 2
        assert retested == 2


class TestTimeLoss:
    def test_time_breakdown(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.time_loss(phase="all")
        assert len(rows) >= 1
        total = sum(r["total_time_s"] or 0 for r in rows)
        fail = sum(r["fail_time_s"] or 0 for r in rows)
        assert total > 0
        assert fail > 0


class TestEmptyDataset:
    def test_all_methods_return_empty(self, tmp_path: Path):
        store = MetricsStore(_results_dir=tmp_path)
        assert store.yield_summary() == []
        assert store.pareto() == []
        assert store.cpk() == []
        assert store.trend() == []
        assert store.retest() == []
        assert store.time_loss() == []


class TestFilters:
    def test_product_filter(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.yield_summary(product="PN-100", phase="all")
        assert all(r["product"] == "PN-100" for r in rows)

    def test_station_filter(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.yield_summary(station="STA-01", phase="all")
        assert all(r["station"] == "STA-01" for r in rows)

    def test_nonexistent_product(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        assert store.yield_summary(product="NOPE", phase="all") == []

    def test_date_filter(self, results_dir: Path):
        store = MetricsStore(_results_dir=results_dir)
        rows = store.yield_summary(since="2026-01-01", until="2026-01-01", phase="all")
        assert len(rows) >= 1
