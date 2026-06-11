"""CLI tests for litmus yield/metrics commands.

Uses the canonical singleton runs daemon. Synthetic measurement
parquets land at ``canonical/runs/test-yield-cli/`` under a unique
``part_id`` so the CLI's ``--part`` filter can scope cleanly
past whatever else the canonical store holds.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow.parquet as pq
import pytest
from click.testing import CliRunner

from litmus.cli import main
from litmus.data.backends._row_helpers import MeasurementRow
from litmus.data.data_dir import resolve_data_dir
from litmus.data.run_store import RunStore
from litmus.data.schemas import _build_write_schema, table_from_rows


def _row(
    *,
    run_id: str,
    uut_part_number: str,
    uut_serial: str = "SN001",
    run_outcome: str = "passed",
    run_started_at: datetime = datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
    run_ended_at: datetime = datetime(2026, 1, 1, 10, 1, tzinfo=UTC),
    step_name: str = "test_voltage",
    measurement_name: str = "output_voltage",
    value: float = 3.3,
    outcome: str = "passed",
    limit_low: float = 3.1,
    limit_high: float = 3.5,
    station_name: str = "bench_1",
    test_phase: str = "production",
) -> MeasurementRow:
    return MeasurementRow(
        record_type="measurement",
        session_id="sess-1",
        run_id=run_id,
        run_started_at=run_started_at,
        run_ended_at=run_ended_at,
        uut_serial=uut_serial,
        uut_part_number=uut_part_number,
        part_id=uut_part_number,
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
        uut_lot_number="LOT01",
    )


def _write(runs_dir: Path, rows: list[MeasurementRow], filename: str) -> Path:
    flat = [r.to_flat_dict() for r in rows]
    schema = _build_write_schema(flat)
    table = table_from_rows(flat, schema)
    path = runs_dir / filename
    pq.write_table(table, path)
    return path


@pytest.fixture(scope="module")
def fixture_data() -> dict[str, str]:
    """Sample measurement data under a unique part, in canonical."""
    part = f"prod-yield-{uuid4().hex[:8]}"
    canonical_runs = resolve_data_dir() / "runs" / "test-yield-cli" / "2026-01-01"
    canonical_runs.mkdir(parents=True, exist_ok=True)

    rows = [
        _row(
            run_id=f"yld-{uuid4()}",
            uut_part_number=part,
            uut_serial="SN001",
            run_outcome="passed",
            value=3.3,
            outcome="passed",
        ),
        _row(
            run_id=f"yld-{uuid4()}",
            uut_part_number=part,
            uut_serial="SN002",
            run_outcome="failed",
            run_started_at=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
            run_ended_at=datetime(2026, 1, 1, 11, 2, tzinfo=UTC),
            value=2.8,
            outcome="failed",
        ),
    ]
    path = _write(canonical_runs, rows, f"{part}_main.parquet")

    notifier = RunStore()
    try:
        notifier.notify_new_run(path)
    finally:
        notifier.close()

    return {"part": part}


class TestMetricsCLI:
    def test_summary(self, fixture_data):
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", "summary", "--part", fixture_data["part"]])
        assert result.exit_code == 0
        assert "Pass" in result.output or "Runs" in result.output

    def test_summary_json(self, fixture_data):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["metrics", "summary", "--part", fixture_data["part"], "--json"],
        )
        assert result.exit_code == 0
        assert "total_runs" in result.output

    def test_pareto_default_dispatches_to_part_lens(self, fixture_data):
        """Default ``--group-by`` is ``part`` — exits 0 even when the fixture
        only has measurement parquets (no ``_steps.parquet``); content is
        verified separately for the measurement lens which uses the
        always-populated measurements view.
        """
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", "pareto", "--part", fixture_data["part"]])
        assert result.exit_code == 0

    def test_pareto_group_by_measurement(self, fixture_data):
        """Historical lens: ``--group-by measurement`` surfaces measurement names."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "metrics",
                "pareto",
                "--part",
                fixture_data["part"],
                "--group-by",
                "measurement",
            ],
        )
        assert result.exit_code == 0
        assert "output_voltage" in result.output

    def test_cpk(self, fixture_data):
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", "cpk", "--part", fixture_data["part"]])
        assert result.exit_code == 0

    def test_trend(self, fixture_data):
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", "trend", "--part", fixture_data["part"]])
        assert result.exit_code == 0
        assert "2026-01-01" in result.output

    def test_retest(self, fixture_data):
        runner = CliRunner()
        result = runner.invoke(main, ["metrics", "retest", "--part", fixture_data["part"]])
        assert result.exit_code == 0

    def test_time_loss(self, fixture_data):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["metrics", "time-loss", "--part", fixture_data["part"]],
        )
        assert result.exit_code == 0
        assert "Total(s)" in result.output
