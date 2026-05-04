"""CLI tests for litmus yield commands."""

from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from litmus.cli import main
from litmus.data.backends._row_helpers import MeasurementRow
from litmus.data.schemas import _build_write_schema, table_from_rows


def _row(
    *,
    run_id: str = "run-001",
    dut_serial: str = "SN001",
    run_outcome: str = "passed",
    run_started_at: datetime = datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
    run_ended_at: datetime = datetime(2026, 1, 1, 10, 1, tzinfo=UTC),
    step_name: str = "test_voltage",
    measurement_name: str = "output_voltage",
    value: float = 3.3,
    outcome: str = "passed",
    limit_low: float = 3.1,
    limit_high: float = 3.5,
    dut_part_number: str = "prod_a",
    station_name: str = "bench_1",
    test_phase: str = "production",
) -> MeasurementRow:
    return MeasurementRow(
        session_id="sess-1",
        run_id=run_id,
        run_started_at=run_started_at,
        run_ended_at=run_ended_at,
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
        measurement_units="V",
        dut_lot_number="LOT01",
    )


def _write(runs_dir: Path, rows: list[MeasurementRow]) -> None:
    flat = [r.to_flat_dict() for r in rows]
    schema = _build_write_schema(flat)
    table = table_from_rows(flat, schema)
    pq.write_table(table, runs_dir / "20260101T100000Z.parquet")


@pytest.fixture()
def results_dir(tmp_path):
    """Create sample results for CLI testing."""
    runs_dir = tmp_path / "runs" / "2026-01-01"
    runs_dir.mkdir(parents=True)

    rows = [
        _row(
            run_id="run-001",
            dut_serial="SN001",
            run_outcome="passed",
            value=3.3,
            outcome="passed",
        ),
        _row(
            run_id="run-002",
            dut_serial="SN002",
            run_outcome="failed",
            run_started_at=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
            run_ended_at=datetime(2026, 1, 1, 11, 2, tzinfo=UTC),
            value=2.8,
            outcome="failed",
        ),
    ]
    _write(runs_dir, rows)
    return tmp_path


class TestMetricsCLI:
    def test_summary(self, results_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", "summary", "--results-dir", str(results_dir)])
        assert result.exit_code == 0
        assert "Pass" in result.output or "Runs" in result.output

    def test_summary_json(self, results_dir):
        runner = CliRunner()
        result = runner.invoke(
            main, ["metrics", "summary", "--results-dir", str(results_dir), "--json"]
        )
        assert result.exit_code == 0
        assert "total_runs" in result.output

    def test_pareto_default_dispatches_to_product_lens(self, results_dir):
        """Default ``--group-by`` is ``product`` — exits 0 even when the fixture
        only has measurement parquets (no ``_steps.parquet``); content is
        verified separately for the measurement lens which uses the
        always-populated measurements view.
        """
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", "pareto", "--results-dir", str(results_dir)])
        assert result.exit_code == 0

    def test_pareto_group_by_measurement(self, results_dir):
        """Historical lens: ``--group-by measurement`` surfaces measurement names."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["metrics", "pareto", "--results-dir", str(results_dir), "--group-by", "measurement"],
        )
        assert result.exit_code == 0
        assert "output_voltage" in result.output

    def test_cpk(self, results_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", "cpk", "--results-dir", str(results_dir)])
        assert result.exit_code == 0

    def test_trend(self, results_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", "trend", "--results-dir", str(results_dir)])
        assert result.exit_code == 0
        assert "2026-01-01" in result.output

    def test_retest(self, results_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", "retest", "--results-dir", str(results_dir)])
        assert result.exit_code == 0

    def test_time_loss(self, results_dir):
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", "time-loss", "--results-dir", str(results_dir)])
        assert result.exit_code == 0
        assert "Total(s)" in result.output
