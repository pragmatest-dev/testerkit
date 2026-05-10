"""Tests for explicit Arrow schema on the Parquet write path."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from litmus.data.backends.parquet import ParquetBackend
from litmus.data.models import DUT, Measurement, Outcome, TestRun, TestStep, TestVector
from litmus.data.schemas import (
    _INSTR_ARRAY_TYPES,
    RUN_ROW_SCHEMA,
    _build_write_schema,
    table_from_rows,
)


class TestBuildWriteSchemaFixed:
    """Fixed canonical columns get types from RUN_ROW_SCHEMA."""

    def test_canonical_columns_match_schema(self):
        row = {
            "run_id": "r1",
            "step_name": "test_v",
            "measurement_value": 3.3,
            "measurement_units": "V",
            "measurement_outcome": "PASS",
            "step_index": 0,
        }
        schema = _build_write_schema([row])
        for field in schema:
            if field.name in {f.name for f in RUN_ROW_SCHEMA}:
                expected = RUN_ROW_SCHEMA.field(field.name).type
                assert field.type == expected, (
                    f"{field.name}: got {field.type}, expected {expected}"
                )


class TestBuildWriteSchemaDynamic:
    """Dynamic in_*/out_*/custom_* columns are inferred from values."""

    def test_float_dynamic_column(self):
        rows = [{"run_id": "r1", "in_voltage": 5.0}]
        schema = _build_write_schema(rows)
        assert schema.field("in_voltage").type == pa.float64()

    def test_string_dynamic_column(self):
        rows = [{"run_id": "r1", "in_mode": "fast"}]
        schema = _build_write_schema(rows)
        assert schema.field("in_mode").type == pa.string()

    def test_custom_string_column(self):
        rows = [{"run_id": "r1", "custom_tag": "foo"}]
        schema = _build_write_schema(rows)
        assert schema.field("custom_tag").type == pa.string()

    def test_int_promoted_to_float64(self):
        rows = [{"run_id": "r1", "in_count": 42}]
        schema = _build_write_schema(rows)
        assert schema.field("in_count").type == pa.float64()


class TestAllNoneColumn:
    """All-None columns in canonical schema still get correct types."""

    def test_low_limit_all_none(self):
        rows = [
            {"run_id": "r1", "limit_low": None, "measurement_value": 1.0},
            {"run_id": "r2", "limit_low": None, "measurement_value": 2.0},
        ]
        schema = _build_write_schema(rows)
        assert schema.field("limit_low").type == pa.float64()

    def test_value_all_none(self):
        rows = [{"run_id": "r1", "measurement_value": None}]
        schema = _build_write_schema(rows)
        assert schema.field("measurement_value").type == pa.float64()

    def test_dynamic_all_none_defaults_to_string(self):
        rows = [{"run_id": "r1", "in_unknown": None}]
        schema = _build_write_schema(rows)
        assert schema.field("in_unknown").type == pa.string()


class TestInstrArrayColumns:
    """Instrument array columns get known list types."""

    def test_instr_name_is_list_string(self):
        rows = [{"run_id": "r1", "step_instruments_name": ["DMM1"]}]
        schema = _build_write_schema(rows)
        assert schema.field("step_instruments_name").type == pa.list_(pa.string())

    def test_instr_mocked_is_list_bool(self):
        rows = [{"run_id": "r1", "step_instruments_mocked": [True, False]}]
        schema = _build_write_schema(rows)
        assert schema.field("step_instruments_mocked").type == pa.list_(pa.bool_())

    def test_all_instr_keys_in_type_map(self):
        from litmus.execution.logger import INSTRUMENT_ARRAY_KEYS

        for key in INSTRUMENT_ARRAY_KEYS:
            assert key in _INSTR_ARRAY_TYPES


class TestWriteRejectsTypeMismatch:
    """Explicit schema makes Arrow reject invalid data at construction."""

    def test_string_in_float_column_raises(self):
        rows = [{"run_id": "r1", "measurement_value": "not_a_number"}]
        schema = _build_write_schema(rows)
        with pytest.raises(pa.ArrowInvalid):
            table_from_rows(rows, schema)

    def test_mixed_type_error_message(self):
        rows = [
            {"run_id": "r1", "in_voltage": 5.0},
            {"run_id": "r2", "in_voltage": "high"},
        ]
        schema = _build_write_schema(rows)
        with pytest.raises(pa.ArrowInvalid, match="mixed types"):
            table_from_rows(rows, schema)


class TestRoundTripExplicitSchema:
    """Full round-trip: save_test_run → read back → verify types."""

    def test_column_types_preserved(self, tmp_path):
        m = Measurement(
            name="voltage",
            value=3.3,
            units="V",
            limit_low=3.0,
            limit_high=3.6,
            outcome=Outcome.PASSED,
        )
        v = TestVector(index=0, measurements=[m])
        s = TestStep(name="test_v", vectors=[v])
        run = TestRun(
            dut=DUT(serial="SN001"),
            steps=[s],
            station_id="bench_1",
            outcome=Outcome.PASSED,
        )

        backend = ParquetBackend(data_dir=tmp_path)
        path = backend.save_test_run(run)

        table = pq.read_table(path)
        assert table.schema.field("measurement_value").type == pa.float64()
        assert table.schema.field("limit_low").type == pa.float64()
        assert table.schema.field("run_id").type == pa.string()
        assert table.schema.field("step_index").type == pa.int64()

    def test_dynamic_columns_round_trip(self, tmp_path):
        """Dynamic in_* columns survive the full write path through RecordBatch."""
        m = Measurement(
            name="voltage",
            value=3.3,
            units="V",
            outcome=Outcome.PASSED,
        )
        v = TestVector(
            index=0,
            measurements=[m],
            params={"voltage": 5.0, "mode": "fast"},
        )
        s = TestStep(name="test_v", vectors=[v])
        run = TestRun(
            dut=DUT(serial="SN001"),
            steps=[s],
            station_id="bench_1",
            outcome=Outcome.PASSED,
        )

        backend = ParquetBackend(data_dir=tmp_path)
        path = backend.save_test_run(run)

        table = pq.read_table(path)
        assert "in_voltage" in table.column_names
        assert "in_mode" in table.column_names
        assert table.schema.field("in_voltage").type == pa.float64()
        assert table.schema.field("in_mode").type == pa.string()
        # Unified schema: 1 run row + 1 measurement row + 1 step row.
        # The measurement and step rows carry the denormalized ``in_*``
        # columns; the run row carries NULL there (no vector context).
        rows = table.to_pylist()
        assert len(rows) == 3
        non_run = [r for r in rows if r["record_type"] != "run"]
        assert all(r["in_voltage"] == 5.0 for r in non_run)
        assert all(r["in_mode"] == "fast" for r in non_run)
        run_rows = [r for r in rows if r["record_type"] == "run"]
        assert len(run_rows) == 1
        assert run_rows[0].get("in_voltage") is None
        assert run_rows[0].get("in_mode") is None
