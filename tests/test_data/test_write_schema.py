"""Tests for explicit Arrow schema on the Parquet write path."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from testerkit.data.backends.parquet import ParquetBackend
from testerkit.data.models import UUT, Measurement, Outcome, TestRun, TestStep, TestVector
from testerkit.data.schemas import (
    _INSTRUMENT_LIST,  # noqa: PLC2701
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
            "measurement_unit": "V",
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

    def test_timestamp_all_none(self):
        rows = [
            {"run_id": "r1", "run_started_at": None},
            {"run_id": "r2", "run_started_at": None},
        ]
        schema = _build_write_schema(rows)
        assert schema.field("run_started_at").type == pa.timestamp("us", tz="UTC")

    def test_int_all_none(self):
        rows = [{"run_id": "r1", "step_index": None}]
        schema = _build_write_schema(rows)
        assert schema.field("step_index").type == pa.int64()

    def test_dynamic_all_none_defaults_to_string(self):
        rows = [{"run_id": "r1", "in_unknown": None}]
        schema = _build_write_schema(rows)
        assert schema.field("in_unknown").type == pa.string()


class TestInstrumentsColumn:
    """instruments column is a list<struct> with known type from RUN_ROW_SCHEMA."""

    def test_instruments_field_type_in_schema(self):
        schema_field = RUN_ROW_SCHEMA.field("instruments")
        assert schema_field.type == _INSTRUMENT_LIST

    def test_instruments_struct_has_14_fields(self):
        from testerkit.data.schemas import _INSTRUMENT_STRUCT

        assert len(_INSTRUMENT_STRUCT) == 14

    def test_instruments_struct_has_serial_number_not_serial(self):
        from testerkit.data.schemas import _INSTRUMENT_STRUCT

        field_names = [f.name for f in _INSTRUMENT_STRUCT]
        assert "serial_number" in field_names
        assert "serial" not in field_names


class TestWriteRejectsTypeMismatch:
    """Explicit schema makes Arrow reject invalid data at construction."""

    def test_string_in_int_column_raises(self):
        rows = [{"run_id": "r1", "step_index": "not_a_number"}]
        schema = _build_write_schema(rows)
        with pytest.raises(pa.ArrowInvalid):
            table_from_rows(rows, schema)

    def test_mixed_kind_lanes_do_not_raise(self):
        """Same input name, different kinds across rows → no raise; each value
        routes to its own value_* lane (the nested EAV at-rest shape)."""
        from testerkit.data.backends._row_helpers import encode_lane_structs

        rows = [
            {"run_id": "r1", "inputs": encode_lane_structs({"voltage": 5.0})},
            {"run_id": "r2", "inputs": encode_lane_structs({"voltage": "high"})},
        ]
        schema = _build_write_schema(rows)
        table = table_from_rows(rows, schema)  # does not raise
        assert table.num_rows == 2


class TestRoundTripExplicitSchema:
    """Full round-trip: save_test_run → read back → verify types."""

    def test_column_types_preserved(self, tmp_path):
        m = Measurement(
            name="voltage",
            value=3.3,
            unit="V",
            limit_low=3.0,
            limit_high=3.6,
            outcome=Outcome.PASSED,
        )
        v = TestVector(index=0, measurements=[m])
        s = TestStep(name="test_v", vectors=[v])
        run = TestRun(
            uut=UUT(serial="SN001"),
            steps=[s],
            station_id="bench_1",
            outcome=Outcome.PASSED,
        )

        backend = ParquetBackend(data_dir=tmp_path)
        path = backend.save_test_run(run)

        table = pq.read_table(path)
        assert table.schema.field("run_id").type == pa.string()
        assert table.schema.field("step_index").type == pa.int64()
        # Measurements are nested on the vector row; the struct carries floats.
        assert pa.types.is_list(table.schema.field("measurements").type)
        meas_struct = table.schema.field("measurements").type.value_type
        assert meas_struct.field("value").type == pa.float64()
        assert meas_struct.field("limit_low").type == pa.float64()

    def test_dynamic_columns_round_trip(self, tmp_path):
        """Vector params survive the write path in the nested inputs lanes."""
        from testerkit.data.backends._row_helpers import decode_lane_structs

        m = Measurement(
            name="voltage",
            value=3.3,
            unit="V",
            outcome=Outcome.PASSED,
        )
        v = TestVector(
            index=0,
            measurements=[m],
            params={"voltage": 5.0, "mode": "fast"},
        )
        s = TestStep(name="test_v", vectors=[v])
        run = TestRun(
            uut=UUT(serial="SN001"),
            steps=[s],
            station_id="bench_1",
            outcome=Outcome.PASSED,
        )

        backend = ParquetBackend(data_dir=tmp_path)
        path = backend.save_test_run(run)

        table = pq.read_table(path)
        assert "inputs" in table.column_names
        assert pa.types.is_list(table.schema.field("inputs").type)
        # v2 nested schema: 1 run + 1 step + 1 scope vector. The conditions live
        # ONLY on the scope vector; the step record sheds them (empty inputs).
        # The measurement is nested on the vector, not a separate row.
        rows = table.to_pylist()
        assert len(rows) == 3
        vec_rows = [r for r in rows if r["record_type"] == "vector"]
        assert len(vec_rows) == 1
        assert decode_lane_structs(vec_rows[0]["inputs"]) == {"voltage": 5.0, "mode": "fast"}
        assert [m["name"] for m in vec_rows[0]["measurements"]] == ["voltage"]
        for rt in ("run", "step"):
            rt_rows = [r for r in rows if r["record_type"] == rt]
            assert len(rt_rows) == 1
            assert decode_lane_structs(rt_rows[0]["inputs"]) == {}
