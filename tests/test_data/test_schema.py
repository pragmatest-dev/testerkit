"""Tests for Parquet schema consistency across write paths."""


import pyarrow as pa

from litmus.data.backends.parquet import (
    _enforce_schema,
)
from litmus.data.models import DUT, Measurement, Outcome, TestRun, TestStep, TestVector


def _make_test_run() -> TestRun:
    """Create a minimal TestRun for schema testing."""
    m = Measurement(
        name="voltage",
        value=3.3,
        units="V",
        low_limit=3.0,
        high_limit=3.6,
        outcome=Outcome.PASS,
    )
    v = TestVector(index=0, measurements=[m])
    s = TestStep(name="test_v", vectors=[v])
    return TestRun(
        dut=DUT(serial="SN001"),
        steps=[s],
        station_id="bench_1",
        test_sequence_id="test_seq",
        outcome=Outcome.PASS,
    )


class TestEnforceSchema:
    def test_null_columns_cast(self):
        """Null-typed columns should be cast to canonical types."""
        table = pa.table({
            "run_id": pa.array([None], type=pa.null()),
            "value": pa.array([None], type=pa.null()),
            "step_index": pa.array([None], type=pa.null()),
        })
        result = _enforce_schema(table)
        assert result.schema.field("run_id").type == pa.string()
        assert result.schema.field("value").type == pa.float64()
        assert result.schema.field("step_index").type == pa.int64()

    def test_string_timestamps_converted(self):
        """String timestamps should be parsed to timestamp[us, tz=UTC]."""
        table = pa.table({
            "run_started_at": ["2026-01-01T10:00:00+00:00"],
        })
        result = _enforce_schema(table)
        assert pa.types.is_timestamp(result.schema.field("run_started_at").type)

    def test_dynamic_columns_pass_through(self):
        """Columns not in MEASUREMENT_SCHEMA should not be modified."""
        table = pa.table({
            "in_vin": [3.3],
            "out_temperature": [25.0],
        })
        result = _enforce_schema(table)
        assert result.schema.field("in_vin").type == pa.float64()

    def test_already_correct_types_noop(self):
        """Columns already matching schema should pass through unchanged."""
        table = pa.table({
            "run_id": ["abc"],
            "value": [1.5],
            "step_index": [0],
        })
        result = _enforce_schema(table)
        assert result.schema.field("run_id").type == pa.string()
        assert result.equals(table)
