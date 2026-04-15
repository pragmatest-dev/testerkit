"""Tests for MeasurementWriter protocol and ParquetMeasurementWriter."""

import pyarrow as pa
import pyarrow.parquet as pq

from litmus.data.backends._protocol import MeasurementWriter
from litmus.data.backends.parquet import ParquetMeasurementWriter


def _make_batch() -> pa.RecordBatch:
    return pa.record_batch(
        {
            "run_id": ["r1"],
            "step_name": ["test_v"],
            "value": [3.3],
            "units": ["V"],
        },
        schema=pa.schema(
            [
                ("run_id", pa.string()),
                ("step_name", pa.string()),
                ("value", pa.float64()),
                ("units", pa.string()),
            ]
        ),
    )


class TestMeasurementWriterProtocol:
    def test_parquet_writer_satisfies_protocol(self):
        writer = ParquetMeasurementWriter()
        assert isinstance(writer, MeasurementWriter)


class TestParquetMeasurementWriter:
    def test_write_and_read_back(self, tmp_path):
        path = tmp_path / "test.parquet"
        writer = ParquetMeasurementWriter()
        batch = _make_batch()

        result = writer.write_batch(batch, path)

        assert result == path
        assert path.exists()
        table = pq.read_table(path)
        assert table.num_rows == 1
        assert table.schema.field("value").type == pa.float64()
        assert table.column("run_id").to_pylist() == ["r1"]

    def test_file_metadata_attached(self, tmp_path):
        path = tmp_path / "test.parquet"
        writer = ParquetMeasurementWriter()
        batch = _make_batch()
        meta = {b"schema_version": b"2.0", b"custom_key": b"custom_val"}

        writer.write_batch(batch, path, file_metadata=meta)

        table = pq.read_table(path)
        assert table.schema.metadata[b"schema_version"] == b"2.0"
        assert table.schema.metadata[b"custom_key"] == b"custom_val"

    def test_notify_callback_called(self, tmp_path):
        path = tmp_path / "test.parquet"
        notified: list = []
        writer = ParquetMeasurementWriter(notify=notified.append)
        batch = _make_batch()

        writer.write_batch(batch, path)

        assert notified == [path]

    def test_no_notify_when_none(self, tmp_path):
        path = tmp_path / "test.parquet"
        writer = ParquetMeasurementWriter(notify=None)
        batch = _make_batch()

        # Should not raise
        writer.write_batch(batch, path)
        assert path.exists()
