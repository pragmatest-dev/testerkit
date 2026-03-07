"""CLI tests for litmus yield commands."""

from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from litmus.cli import main


@pytest.fixture
def results_dir(tmp_path):
    """Create sample results for CLI testing."""
    runs_dir = tmp_path / "runs" / "2026-01-01"
    runs_dir.mkdir(parents=True)

    rows = [
        {
            "run_id": "run-001",
            "run_started_at": datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            "run_ended_at": datetime(2026, 1, 1, 10, 1, tzinfo=UTC),
            "run_outcome": "pass",
            "dut_serial": "SN001",
            "product_id": "prod_a",
            "station_id": "bench_1",
            "dut_lot_number": "LOT01",
            "test_phase": "production",
            "step_name": "test_voltage",
            "measurement_name": "output_voltage",
            "value": 3.3,
            "low_limit": 3.1,
            "high_limit": 3.5,
            "outcome": "pass",
            "units": "V",
        },
        {
            "run_id": "run-002",
            "run_started_at": datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
            "run_ended_at": datetime(2026, 1, 1, 11, 2, tzinfo=UTC),
            "run_outcome": "fail",
            "dut_serial": "SN002",
            "product_id": "prod_a",
            "station_id": "bench_1",
            "dut_lot_number": "LOT01",
            "test_phase": "production",
            "step_name": "test_voltage",
            "measurement_name": "output_voltage",
            "value": 2.8,
            "low_limit": 3.1,
            "high_limit": 3.5,
            "outcome": "fail",
            "units": "V",
        },
    ]

    table = pa.Table.from_pylist(rows)
    pq.write_table(table, runs_dir / "20260101T100000Z.parquet")
    return tmp_path


class TestYieldCLI:
    def test_summary(self, results_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["yield", "summary", "--results-dir", str(results_dir)])
        assert result.exit_code == 0
        assert "First-pass yield" in result.output
        assert "50.0%" in result.output

    def test_pareto(self, results_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["yield", "pareto", "--results-dir", str(results_dir)])
        assert result.exit_code == 0
        assert "output_voltage" in result.output

    def test_cpk(self, results_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["yield", "cpk", "test_voltage", "--results-dir", str(results_dir)])
        assert result.exit_code == 0
        assert "Samples: 2" in result.output

    def test_trend(self, results_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["yield", "trend", "--results-dir", str(results_dir)])
        assert result.exit_code == 0
        assert "2026-01-01" in result.output

    def test_time(self, results_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["yield", "time", "--results-dir", str(results_dir)])
        assert result.exit_code == 0
        assert "Avg:" in result.output

    def test_no_data(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["yield", "summary", "--results-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "No runs found" in result.output
