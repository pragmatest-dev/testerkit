"""Integration tests for litmus.analysis.query with sample Parquet data."""

from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.analysis.query import (
    deduplicate_runs,
    filter_by_date_range,
    filter_by_lot,
    filter_by_phase,
    filter_by_product,
    filter_by_station,
    load_runs,
)


@pytest.fixture
def sample_results(tmp_path):
    """Create sample Parquet files for testing."""
    runs_dir = tmp_path / "runs" / "2026-01-01"
    runs_dir.mkdir(parents=True)

    rows = [
        {
            "run_id": "run-001",
            "run_started_at": datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            "run_ended_at": datetime(2026, 1, 1, 10, 1, tzinfo=UTC),
            "run_outcome": "passed",
            "dut_serial": "SN001",
            "product_id": "prod_a",
            "station_id": "bench_1",
            "dut_lot_number": "LOT01",
            "test_phase": "production",
            "step_name": "test_voltage",
            "measurement_name": "output_voltage",
            "value": 3.3,
            "limit_low": 3.1,
            "limit_high": 3.5,
            "outcome": "passed",
        },
        {
            "run_id": "run-001",
            "run_started_at": datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            "run_ended_at": datetime(2026, 1, 1, 10, 1, tzinfo=UTC),
            "run_outcome": "passed",
            "dut_serial": "SN001",
            "product_id": "prod_a",
            "station_id": "bench_1",
            "dut_lot_number": "LOT01",
            "test_phase": "production",
            "step_name": "test_current",
            "measurement_name": "input_current",
            "value": 0.5,
            "limit_low": 0.0,
            "limit_high": 1.0,
            "outcome": "passed",
        },
        {
            "run_id": "run-002",
            "run_started_at": datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
            "run_ended_at": datetime(2026, 1, 1, 11, 2, tzinfo=UTC),
            "run_outcome": "failed",
            "dut_serial": "SN002",
            "product_id": "prod_a",
            "station_id": "bench_1",
            "dut_lot_number": "LOT01",
            "test_phase": "production",
            "step_name": "test_voltage",
            "measurement_name": "output_voltage",
            "value": 2.8,
            "limit_low": 3.1,
            "limit_high": 3.5,
            "outcome": "failed",
        },
        {
            "run_id": "run-003",
            "run_started_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            "run_ended_at": datetime(2026, 1, 1, 12, 1, tzinfo=UTC),
            "run_outcome": "passed",
            "dut_serial": "SN003",
            "product_id": "prod_b",
            "station_id": "bench_2",
            "dut_lot_number": "LOT02",
            "test_phase": "development",
            "step_name": "test_voltage",
            "measurement_name": "output_voltage",
            "value": 3.35,
            "limit_low": 3.1,
            "limit_high": 3.5,
            "outcome": "passed",
        },
    ]

    table = pa.Table.from_pylist(rows)
    pq.write_table(table, runs_dir / "20260101T100000Z_SN001.parquet")

    return tmp_path


class TestLoadRuns:
    def test_load(self, sample_results):
        table = load_runs(sample_results)
        assert table.num_rows == 4

    def test_empty_dir(self, tmp_path):
        table = load_runs(tmp_path)
        assert table.num_rows == 0

    def test_mixed_schema_files(self, tmp_path):
        """load_runs handles files with different column types (legacy data)."""
        runs_dir = tmp_path / "runs" / "2026-01-01"
        runs_dir.mkdir(parents=True)

        # File 1: datetime timestamps (direct write path)
        rows1 = pa.table(
            {
                "run_id": ["run-A"],
                "run_started_at": pa.array(
                    [datetime(2026, 1, 1, 10, 0, tzinfo=UTC)],
                    type=pa.timestamp("us", tz="UTC"),
                ),
                "run_outcome": ["passed"],
                "measurement_name": ["v1"],
                "value": [3.3],
            }
        )
        pq.write_table(rows1, runs_dir / "file1.parquet")

        # File 2: string timestamps (tests coercion path)
        rows2 = pa.table(
            {
                "run_id": ["run-B"],
                "run_started_at": ["2026-01-01T11:00:00+00:00"],
                "run_outcome": ["failed"],
                "measurement_name": ["v1"],
                "value": [2.8],
            }
        )
        pq.write_table(rows2, runs_dir / "file2.parquet")

        # File 3: null types (empty run)
        rows3 = pa.table(
            {
                "run_id": ["run-C"],
                "run_started_at": pa.array(
                    [datetime(2026, 1, 1, 12, 0, tzinfo=UTC)],
                    type=pa.timestamp("us", tz="UTC"),
                ),
                "run_outcome": ["passed"],
                "measurement_name": pa.array([None], type=pa.null()),
                "value": pa.array([None], type=pa.null()),
            }
        )
        pq.write_table(rows3, runs_dir / "file3.parquet")

        # Should concatenate without errors
        table = load_runs(tmp_path)
        assert table.num_rows == 3
        assert pa.types.is_timestamp(table.schema.field("run_started_at").type)


class TestDeduplicateRuns:
    def test_dedup(self, sample_results):
        table = load_runs(sample_results)
        runs = deduplicate_runs(table)
        assert len(runs) == 3  # 3 unique run_ids


class TestFilters:
    def test_filter_phase_default(self, sample_results):
        table = load_runs(sample_results)
        filtered = filter_by_phase(table)
        # Should exclude development
        assert filtered.num_rows == 3

    def test_filter_phase_all(self, sample_results):
        table = load_runs(sample_results)
        filtered = filter_by_phase(table, phases=["all"])
        assert filtered.num_rows == 4

    def test_filter_product(self, sample_results):
        table = load_runs(sample_results)
        filtered = filter_by_product(table, "prod_b")
        assert filtered.num_rows == 1

    def test_filter_station(self, sample_results):
        table = load_runs(sample_results)
        filtered = filter_by_station(table, "bench_1")
        assert filtered.num_rows == 3

    def test_filter_lot(self, sample_results):
        table = load_runs(sample_results)
        filtered = filter_by_lot(table, "LOT02")
        assert filtered.num_rows == 1

    def test_filter_date_range(self, sample_results):
        table = load_runs(sample_results)
        filtered = filter_by_date_range(
            table,
            since="2026-01-01T10:30:00+00:00",
            until="2026-01-01T11:30:00+00:00",
        )
        assert filtered.num_rows == 1  # only run-002
