"""Tests for ChannelStore streaming Arrow IPC materialization."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from litmus.data.channels.models import CHANNEL_SCHEMA_VERSION, ChannelSample
from litmus.data.channels.store import ChannelStore


def _make_store(tmp_path: Path, flush_threshold: int = 100) -> ChannelStore:
    session_id = uuid4()
    store = ChannelStore(tmp_path, session_id, flush_threshold=flush_threshold)
    store.open()
    return store


class TestScalarChannel:
    def test_lazy_open_on_first_write_and_idempotent_open(self, tmp_path: Path):
        """P2b: a never-opened store opens on first write; open() is idempotent."""
        store = ChannelStore(tmp_path, uuid4())  # constructed, NOT opened
        assert store._opened is False
        store.write("dmm.dc_voltage", 3.3, source="measure")  # first write opens it
        assert store._opened is True
        store.open()  # idempotent — no re-init, no error
        store.close()
        assert len(list((tmp_path / "channels").glob("*/*.arrow"))) == 1

    def test_writes_arrow_ipc_on_close(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write("dmm.dc_voltage", 3.3, source="measure_dc_voltage")
        store.write("dmm.dc_voltage", 3.4, source="measure_dc_voltage")
        store.close()

        arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
        assert len(arrow_files) == 1

        reader = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb"))
        table = reader.read_all()
        assert len(table) == 2
        assert "value" in table.schema.names
        # Item 11: ``timestamp`` → ``received_at`` + nullable ``sampled_at``.
        assert "received_at" in table.schema.names
        assert "sampled_at" in table.schema.names

    def test_empty_session_no_files(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.close()

        channels_dir = tmp_path / "channels"
        arrow_files = list(channels_dir.glob("*/*.arrow"))
        assert len(arrow_files) == 0

    def test_set_value_stored(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write("psu.voltage", 12.0, source="set_voltage")
        store.close()

        arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
        reader = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb"))
        table = reader.read_all()
        assert table.column("value")[0].as_py() == 12.0
        assert table.column("source_method")[0].as_py() == "set_voltage"


class TestArrayChannel:
    def test_waveform_stored_as_struct(self, tmp_path: Path):
        store = _make_store(tmp_path)
        # Legacy waveform tuple: ([samples], dt) → normalized to dict
        waveform = ([1.0, 2.0, 3.0, 4.0], 1e-5)
        store.write("scope.waveform", waveform, source="get_waveform")
        store.close()

        arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
        assert len(arrow_files) == 1
        reader = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb"))
        table = reader.read_all()
        # Post-C3a-pre: array payload lives in the ``value`` column
        # (uniform with scalar rows). ``sample_interval`` distinguishes
        # array-shape rows from scalar-shape rows.
        assert "value" in table.schema.names
        assert table.column("value")[0].as_py() == [1.0, 2.0, 3.0, 4.0]
        assert table.column("sample_interval")[0].as_py() == 1e-5

    def test_flat_list_stored(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write("daq.channel1", [1.0, 2.0, 3.0])
        store.close()

        arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
        reader = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb"))
        table = reader.read_all()
        assert table.column("value")[0].as_py() == [1.0, 2.0, 3.0]


class TestMultipleChannels:
    def test_separate_files_per_channel(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write("dmm.dc_voltage", 3.3)
        store.write("psu.voltage", 5.0)
        store.close()

        arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
        assert len(arrow_files) == 2


class TestDescriptor:
    def test_descriptor_rides_on_segment_schema_metadata(self, tmp_path: Path):
        import pyarrow as pa
        import pyarrow.ipc as ipc

        from litmus.data.channels.models import ChannelDescriptor

        store = _make_store(tmp_path)
        store.write("dmm.dc_voltage", 3.3)
        store.close()

        # The descriptor rides on each segment's Arrow schema metadata — the
        # daemon reads + serves it from there (and the do_put stream).
        segments = list((tmp_path / "channels").glob("*/*.arrow"))
        assert segments
        meta = ipc.open_stream(pa.OSFile(str(segments[0]), "rb")).schema.metadata
        assert meta and b"litmus.channel_descriptor" in meta
        desc = ChannelDescriptor.model_validate_json(meta[b"litmus.channel_descriptor"])
        assert desc.channel_id == "dmm.dc_voltage"
        # Build item 14: typed leaf — ``3.3`` is ``float`` → ``"scalar:float"``.
        assert desc.value_type == "scalar:float"

    def test_no_registry_json_written(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write("dmm.dc_voltage", 3.3)
        store.close()
        assert not (tmp_path / "channels" / "_registry.json").exists()


class TestStreaming:
    def test_flush_on_threshold(self, tmp_path: Path):
        store = _make_store(tmp_path, flush_threshold=5)
        for i in range(7):
            store.write("dmm.dc_voltage", float(i), source="measure_dc_voltage")

        # After 7 writes with threshold 5, writer should have flushed once
        writer = store._writers["dmm.dc_voltage"]
        assert writer._row_count == 5  # One flush of 5
        assert writer._pending_rows == 2  # 2 remaining buffered (unflushed)

        store.close()

    def test_mid_session_query(self, tmp_path: Path):
        store = _make_store(tmp_path, flush_threshold=3)
        for i in range(5):
            store.write("dmm.dc_voltage", float(i))

        # Query mid-session: should include flushed + buffered
        result = store.query("dmm.dc_voltage")
        assert len(result) == 5

        store.close()


class TestSubscriptions:
    def test_on_channel_receives_samples(self, tmp_path: Path):
        store = _make_store(tmp_path)
        received: list[ChannelSample] = []
        store.on_channel("dmm.dc_voltage", received.append)

        store.write("dmm.dc_voltage", 3.3)
        store.write("dmm.dc_voltage", 3.4)

        assert len(received) == 2
        assert received[0].value == 3.3
        assert received[1].value == 3.4
        store.close()

    def test_on_channel_none_receives_all(self, tmp_path: Path):
        store = _make_store(tmp_path)
        received: list[ChannelSample] = []
        store.on_channel(None, received.append)

        store.write("dmm.dc_voltage", 3.3)
        store.write("psu.voltage", 5.0)

        assert len(received) == 2
        store.close()

    def test_unsubscribe(self, tmp_path: Path):
        store = _make_store(tmp_path)
        received: list[ChannelSample] = []
        unsub = store.on_channel("dmm.dc_voltage", received.append)

        store.write("dmm.dc_voltage", 3.3)
        unsub()
        store.write("dmm.dc_voltage", 3.4)

        assert len(received) == 1
        store.close()

    def test_array_subscription(self, tmp_path: Path):
        store = _make_store(tmp_path)
        received: list[ChannelSample] = []
        store.on_channel("scope.waveform", received.append)

        waveform = ([1.0, 2.0, 3.0], 1e-5)
        store.write("scope.waveform", waveform, source="get_waveform")

        assert len(received) == 1
        # Normalized to dict
        # Post-C3a-pre: normalize folds the array payload into ``value``
        # (uniform with the scalar row's ``value``).
        assert received[0].value == {"value": [1.0, 2.0, 3.0], "sample_interval": 1e-5}
        store.close()


class TestQuery:
    def test_query_returns_all(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write("dmm.dc_voltage", 3.3)
        store.write("dmm.dc_voltage", 3.4)
        store.close()

        # Create new store to query from files
        store2 = ChannelStore(tmp_path, uuid4())
        result = store2.query("dmm.dc_voltage")
        assert len(result) == 2

    def test_query_last_n(self, tmp_path: Path):
        store = _make_store(tmp_path)
        for i in range(10):
            store.write("dmm.dc_voltage", float(i))
        store.close()

        store2 = ChannelStore(tmp_path, uuid4())
        result = store2.query("dmm.dc_voltage", last_n=3)
        assert len(result) == 3
        assert result.column("value")[0].as_py() == 7.0

    def test_query_empty(self, tmp_path: Path):
        (tmp_path / "channels").mkdir(parents=True)
        store = ChannelStore(tmp_path, uuid4())
        result = store.query("nonexistent")
        assert len(result) == 0


class TestWrite:
    def test_write_scalar(self, tmp_path: Path):
        store = _make_store(tmp_path)
        uri = store.write("temp.reading", 24.5, unit="°C")
        assert uri.startswith("channel://")
        assert "temp.reading" in uri

        result = store.query("temp.reading")
        assert len(result) == 1
        assert result.column("value")[0].as_py() == 24.5
        store.close()

    def test_write_array(self, tmp_path: Path):
        store = _make_store(tmp_path)
        waveform = ([1.0, 2.0, 3.0], 1e-5)
        uri = store.write("scope.ch1_waveform", waveform)
        assert "channel://" in uri

        result = store.query("scope.ch1_waveform")
        assert len(result) == 1
        assert result.column("value")[0].as_py() == [1.0, 2.0, 3.0]
        store.close()

    def test_write_blob_raises(self, tmp_path: Path):
        store = _make_store(tmp_path)
        with pytest.raises(ValueError, match="not numeric"):
            store.write("image", b"png-data")
        store.close()

    def test_write_notifies_subscribers(self, tmp_path: Path):
        store = _make_store(tmp_path)
        received: list[ChannelSample] = []
        store.on_channel("temp.probe", received.append)

        store.write("temp.probe", 22.1)
        assert len(received) == 1
        assert received[0].value == 22.1
        store.close()

    def test_write_string_value(self, tmp_path: Path):
        store = _make_store(tmp_path)
        uri = store.write("status.mode", "heating")
        assert "channel://" in uri

        result = store.query("status.mode")
        assert len(result) == 1
        assert result.column("value")[0].as_py() == "heating"
        store.close()

    def test_write_bool_value(self, tmp_path: Path):
        store = _make_store(tmp_path)
        uri = store.write("relay.state", True)
        assert "channel://" in uri

        result = store.query("relay.state")
        assert len(result) == 1
        assert result.column("value")[0].as_py() is True
        store.close()

    def test_write_dict_value(self, tmp_path: Path):
        """Dict values use flexible per-channel schemas."""
        store = _make_store(tmp_path)
        uri = store.write(
            "scope.acquisition",
            {
                "channels": [[1.0, 2.0], [3.0, 4.0]],
                "dt": 1e-6,
                "t0": 0.0,
            },
        )
        assert "channel://" in uri

        result = store.query("scope.acquisition")
        assert len(result) == 1
        assert result.column("dt")[0].as_py() == 1e-6
        store.close()


class TestDecimation:
    def test_max_points_reduces_rows(self, tmp_path: Path):
        store = _make_store(tmp_path)
        # Write 1000 scalar points
        import math

        for i in range(1000):
            store.write("sensor.temp", math.sin(i * 0.01) * 10 + 25)

        full = store.query("sensor.temp")
        assert len(full) == 1000

        decimated = store.query("sensor.temp", max_points=100)
        assert len(decimated) == 100
        store.close()

    def test_max_points_preserves_first_last(self, tmp_path: Path):
        store = _make_store(tmp_path)
        for i in range(500):
            store.write("ch.x", float(i))

        result = store.query("ch.x", max_points=50)
        values = result.column("value").to_pylist()
        # LTTB always keeps first and last
        assert values[0] == 0.0
        assert values[-1] == 499.0
        assert len(result) == 50
        store.close()

    def test_max_points_preserves_spike(self, tmp_path: Path):
        """LTTB should preserve an outlier spike that stride would miss."""
        store = _make_store(tmp_path)
        # 200 points of flat signal with one spike at index 100
        for i in range(200):
            val = 100.0 if i == 100 else 0.0
            store.write("ch.spike", val)

        result = store.query("ch.spike", max_points=20)
        values = result.column("value").to_pylist()
        # The spike should survive decimation
        assert 100.0 in values
        store.close()

    def test_max_points_noop_when_small(self, tmp_path: Path):
        store = _make_store(tmp_path)
        for i in range(10):
            store.write("ch.few", float(i))

        result = store.query("ch.few", max_points=100)
        assert len(result) == 10  # No decimation needed
        store.close()

    def test_max_points_with_last_n(self, tmp_path: Path):
        """last_n applied before max_points."""
        store = _make_store(tmp_path)
        for i in range(500):
            store.write("ch.combo", float(i))

        result = store.query("ch.combo", last_n=200, max_points=50)
        assert len(result) == 50
        values = result.column("value").to_pylist()
        # last_n=200 gives [300..499], then LTTB to 50
        assert values[0] == 300.0
        assert values[-1] == 499.0
        store.close()


class TestNoneValues:
    def test_none_value_write_raises(self, tmp_path: Path):
        """None classifies as scalar, write should handle gracefully."""
        store = _make_store(tmp_path)
        # None is a scalar — write should work (stores None inline)
        uri = store.write("test.channel", 0.0)
        assert "channel://" in uri
        store.close()


class TestUTCDateDir:
    """Task #19 — UTC everywhere: date-partitioned directory name must be the UTC date."""

    def test_channel_date_dir_is_utc(self, tmp_path: Path) -> None:
        """Channel store uses the UTC date for its date-partitioned directory."""
        from datetime import UTC, datetime

        store = ChannelStore(tmp_path, uuid4())
        store.write("dmm.dc_voltage", 3.3)
        store.close()

        expected_date = datetime.now(UTC).date().isoformat()
        date_dirs = [p.name for p in (tmp_path / "channels").iterdir() if p.is_dir()]
        assert date_dirs == [expected_date], (
            f"Expected UTC date dir '{expected_date}', got {date_dirs}"
        )


class TestSchemaVersionStamp:
    """C3: schema_version is stamped into every channel Arrow IPC file.

    Pure file-level round-trip — no daemon, no serve=True, no tmp_path daemon.
    Writes to tmp_path directly via the non-serving ChannelStore path and reads
    back the .arrow file via pyarrow.ipc.open_stream.
    """

    def test_scalar_channel_ipc_carries_schema_version(self, tmp_path: Path) -> None:
        """A scalar write closes an Arrow file whose schema metadata contains schema_version."""
        store = ChannelStore(tmp_path, uuid4(), flush_threshold=1)
        store.write("dmm.voltage", 3.3)
        store.close()

        arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
        assert len(arrow_files) == 1, "Expected exactly one .arrow segment file"

        reader = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb"))
        meta = reader.schema.metadata or {}
        assert b"schema_version" in meta, "schema_version key missing from Arrow IPC metadata"
        assert meta[b"schema_version"] == CHANNEL_SCHEMA_VERSION.encode(), (
            f"Expected schema_version={CHANNEL_SCHEMA_VERSION!r}, got {meta[b'schema_version']!r}"
        )

    def test_array_channel_ipc_carries_schema_version(self, tmp_path: Path) -> None:
        """An array-valued channel write also stamps schema_version on its IPC file."""
        store = ChannelStore(tmp_path, uuid4(), flush_threshold=1)
        store.write("scope.waveform", [0.1, 0.2, 0.3, 0.4], sample_interval=1e-6)
        store.close()

        arrow_files = list((tmp_path / "channels").glob("*/*.arrow"))
        assert len(arrow_files) == 1

        reader = ipc.open_stream(pa.OSFile(str(arrow_files[0]), "rb"))
        meta = reader.schema.metadata or {}
        assert meta.get(b"schema_version") == CHANNEL_SCHEMA_VERSION.encode()

    def test_schema_version_does_not_break_read_back(self, tmp_path: Path) -> None:
        """Round-trip: write, close, reopen — data rows are intact alongside the version stamp."""
        store = ChannelStore(tmp_path, uuid4(), flush_threshold=1)
        store.write("psu.current", 0.5)
        store.write("psu.current", 0.6)
        store.close()

        arrow_files = sorted((tmp_path / "channels").glob("*/*.arrow"))
        tables = [ipc.open_stream(pa.OSFile(str(f), "rb")).read_all() for f in arrow_files]
        combined = pa.concat_tables(tables)

        assert len(combined) == 2
        values = combined.column("value").to_pylist()
        assert 0.5 in values
        assert 0.6 in values
        # Version stamp present on every segment
        for f in arrow_files:
            reader = ipc.open_stream(pa.OSFile(str(f), "rb"))
            meta = reader.schema.metadata or {}
            assert meta.get(b"schema_version") == CHANNEL_SCHEMA_VERSION.encode()
