"""Unit tests for litmus.analysis.metrics — pure computation, no I/O."""

import pytest

from litmus.analysis.metrics import (
    calculate_final_yield,
    calculate_fpy,
    calculate_ppk,
    calculate_rty,
    pareto_analysis,
    timing_stats,
    trend_by_period,
)

# ---------------------------------------------------------------------------
# FPY
# ---------------------------------------------------------------------------


class TestFPY:
    def test_all_pass(self):
        runs = [
            {
                "uut_serial_number": "A",
                "run_outcome": "passed",
                "run_started_at": "2026-01-01T00:00:00Z",
            },
            {
                "uut_serial_number": "B",
                "run_outcome": "passed",
                "run_started_at": "2026-01-01T00:01:00Z",
            },
        ]
        assert calculate_fpy(runs) == 1.0

    def test_all_fail(self):
        runs = [
            {
                "uut_serial_number": "A",
                "run_outcome": "failed",
                "run_started_at": "2026-01-01T00:00:00Z",
            },
        ]
        assert calculate_fpy(runs) == 0.0

    def test_retest_pass_after_fail(self):
        """First run fails, second passes — FPY should be 0 for that serial."""
        runs = [
            {
                "uut_serial_number": "A",
                "run_outcome": "failed",
                "run_started_at": "2026-01-01T00:00:00Z",
            },
            {
                "uut_serial_number": "A",
                "run_outcome": "passed",
                "run_started_at": "2026-01-01T01:00:00Z",
            },
        ]
        assert calculate_fpy(runs) == 0.0

    def test_mixed(self):
        runs = [
            {
                "uut_serial_number": "A",
                "run_outcome": "passed",
                "run_started_at": "2026-01-01T00:00:00Z",
            },
            {
                "uut_serial_number": "B",
                "run_outcome": "failed",
                "run_started_at": "2026-01-01T00:01:00Z",
            },
            {
                "uut_serial_number": "C",
                "run_outcome": "passed",
                "run_started_at": "2026-01-01T00:02:00Z",
            },
        ]
        assert calculate_fpy(runs) == pytest.approx(2 / 3)

    def test_empty(self):
        assert calculate_fpy([]) == 0.0


# ---------------------------------------------------------------------------
# Final Yield
# ---------------------------------------------------------------------------


class TestFinalYield:
    def test_retest_pass(self):
        """First run fails, second passes — final yield should be 100%."""
        runs = [
            {
                "uut_serial_number": "A",
                "run_outcome": "failed",
                "run_started_at": "2026-01-01T00:00:00Z",
            },
            {
                "uut_serial_number": "A",
                "run_outcome": "passed",
                "run_started_at": "2026-01-01T01:00:00Z",
            },
        ]
        assert calculate_final_yield(runs) == 1.0

    def test_final_fail(self):
        runs = [
            {
                "uut_serial_number": "A",
                "run_outcome": "passed",
                "run_started_at": "2026-01-01T00:00:00Z",
            },
            {
                "uut_serial_number": "A",
                "run_outcome": "failed",
                "run_started_at": "2026-01-01T01:00:00Z",
            },
        ]
        assert calculate_final_yield(runs) == 0.0


# ---------------------------------------------------------------------------
# RTY
# ---------------------------------------------------------------------------


class TestRTY:
    def test_single_phase(self):
        assert calculate_rty({"phase1": 0.9}) == pytest.approx(0.9)

    def test_multi_phase(self):
        assert calculate_rty({"phase1": 0.9, "phase2": 0.8}) == pytest.approx(0.72)

    def test_empty(self):
        assert calculate_rty({}) == 0.0


# ---------------------------------------------------------------------------
# Ppk
# ---------------------------------------------------------------------------


class TestPpk:
    def test_centered_process(self):
        """Perfectly centered with tight distribution."""
        values = [10.0] * 50  # zero variance edge case
        result = calculate_ppk(values, lsl=9.0, usl=11.0)
        assert result["warning"] == "zero variance"
        assert result["ppk"] is None

    def test_normal_process(self):
        import random

        random.seed(42)
        values = [10.0 + random.gauss(0, 0.1) for _ in range(100)]
        result = calculate_ppk(values, lsl=9.5, usl=10.5)
        assert result["ppk"] is not None
        assert result["ppk"] > 1.0  # should be capable
        assert result["pp"] is not None
        assert result["n"] == 100
        assert result["warning"] is None

    def test_one_sided_upper(self):
        values = [5.0, 5.1, 4.9, 5.05, 4.95]
        result = calculate_ppk(values, lsl=None, usl=6.0)
        assert result["ppk"] is not None
        assert result["pp"] is None  # one-sided

    def test_insufficient_data(self):
        result = calculate_ppk([1.0], lsl=0.0, usl=2.0)
        assert result["ppk"] is None
        assert result["warning"] == "insufficient data"

    def test_below_min_samples(self):
        values = [1.0, 1.1, 0.9, 1.05, 0.95]
        result = calculate_ppk(values, lsl=0.0, usl=2.0, min_samples=30)
        assert "only 5 samples" in result["warning"]

    def test_no_limits(self):
        result = calculate_ppk([1.0, 2.0, 3.0], lsl=None, usl=None)
        assert result["ppk"] is None
        assert result["warning"] == "no limits defined"


# ---------------------------------------------------------------------------
# Pareto
# ---------------------------------------------------------------------------


class TestPareto:
    def test_basic(self):
        measurements = [
            {"step_name": "s1", "measurement_name": "m1", "measurement_outcome": "failed"},
            {"step_name": "s1", "measurement_name": "m1", "measurement_outcome": "failed"},
            {"step_name": "s2", "measurement_name": "m2", "measurement_outcome": "failed"},
            {"step_name": "s1", "measurement_name": "m1", "measurement_outcome": "passed"},
        ]
        result = pareto_analysis(measurements)
        assert len(result) == 2
        assert result[0]["step_name"] == "s1"
        assert result[0]["count"] == 2
        assert result[0]["pct"] == pytest.approx(66.7, abs=0.1)
        assert result[1]["cumulative_pct"] == 100.0

    def test_no_failures(self):
        measurements = [
            {"step_name": "s1", "measurement_name": "m1", "measurement_outcome": "passed"}
        ]
        assert pareto_analysis(measurements) == []

    def test_top_n(self):
        measurements = [
            {"step_name": f"s{i}", "measurement_name": "m", "measurement_outcome": "failed"}
            for i in range(20)
        ]
        result = pareto_analysis(measurements, top_n=5)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------


class TestTrend:
    def test_daily(self):
        runs = [
            {"run_outcome": "passed", "run_started_at": "2026-01-01T10:00:00Z"},
            {"run_outcome": "failed", "run_started_at": "2026-01-01T11:00:00Z"},
            {"run_outcome": "passed", "run_started_at": "2026-01-02T10:00:00Z"},
        ]
        result = trend_by_period(runs, period="day")
        assert len(result) == 2
        assert result[0]["period"] == "2026-01-01"
        assert result[0]["yield_pct"] == 50.0

    def test_monthly(self):
        runs = [
            {"run_outcome": "passed", "run_started_at": "2026-01-15T10:00:00Z"},
            {"run_outcome": "passed", "run_started_at": "2026-02-15T10:00:00Z"},
        ]
        result = trend_by_period(runs, period="month")
        assert len(result) == 2
        assert result[0]["period"] == "2026-01"

    def test_empty(self):
        assert trend_by_period([]) == []


# ---------------------------------------------------------------------------
# Test Time
# ---------------------------------------------------------------------------


class TestTime:
    def test_run_stats(self):
        runs = [
            {"run_started_at": "2026-01-01T10:00:00Z", "run_ended_at": "2026-01-01T10:01:00Z"},
            {"run_started_at": "2026-01-01T11:00:00Z", "run_ended_at": "2026-01-01T11:02:00Z"},
        ]
        stats = timing_stats(runs, by="run")
        assert stats["count"] == 2
        assert stats["min_s"] == 60.0
        assert stats["max_s"] == 120.0
        assert stats["avg_s"] == 90.0

    def test_step_stats(self):
        rows = [
            {
                "run_id": "r1",
                "step_name": "s1",
                "step_started_at": "2026-01-01T10:00:00Z",
                "step_ended_at": "2026-01-01T10:00:30Z",
            },
            {
                "run_id": "r1",
                "step_name": "s1",
                "step_started_at": "2026-01-01T10:00:00Z",
                "step_ended_at": "2026-01-01T10:00:30Z",
            },  # dup
            {
                "run_id": "r1",
                "step_name": "s2",
                "step_started_at": "2026-01-01T10:00:30Z",
                "step_ended_at": "2026-01-01T10:01:00Z",
            },
        ]
        stats = timing_stats(rows, by="step")
        assert stats["count"] == 2  # 2 unique steps
        assert "per_step" in stats
        assert "s1" in stats["per_step"]
        assert stats["per_step"]["s1"]["avg_s"] == 30.0

    def test_no_timing(self):
        stats = timing_stats([{"run_started_at": None, "run_ended_at": None}], by="run")
        assert stats["count"] == 0
