"""Tests for TelemetryStore Arrow IPC materialization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pyarrow.ipc as ipc
import pyarrow.parquet as pq

from litmus.data.events import InstrumentRead, InstrumentSet
from litmus.data.telemetry.store import CHANNEL_SCHEMA, TelemetryStore


def _make_store(tmp_path: Path) -> TelemetryStore:
    session_id = uuid4()
    store = TelemetryStore(tmp_path / "telemetry", session_id)
    store.open()
    return store


def _read_event(
    role: str = "dmm",
    channel: str = "dmm.dc_voltage",
    method: str = "measure_dc_voltage",
    value: float = 3.3,
) -> InstrumentRead:
    return InstrumentRead(
        session_id=uuid4(),
        instrument_role=role,
        channel_id=channel,
        method=method,
        value=value,
    )


def _set_event(
    role: str = "psu",
    channel: str = "psu.voltage",
    attribute: str = "voltage",
    value: float = 5.0,
) -> InstrumentSet:
    return InstrumentSet(
        session_id=uuid4(),
        instrument_role=role,
        channel_id=channel,
        attribute=attribute,
        value=value,
    )


class TestBufferAndWrite:
    def test_writes_arrow_ipc_on_close(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.on_event(_read_event())
        store.on_event(_read_event(value=3.4))
        store.close()

        arrow_files = list((tmp_path / "telemetry").glob("*/*.arrow"))
        assert len(arrow_files) == 1

        reader = ipc.open_file(str(arrow_files[0]))
        table = reader.read_all()
        assert len(table) == 2
        assert table.schema == CHANNEL_SCHEMA

    def test_empty_session_no_files(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.close()

        telemetry_dir = tmp_path / "telemetry"
        arrow_files = list(telemetry_dir.glob("*/*.arrow"))
        assert len(arrow_files) == 0


class TestMultipleChannels:
    def test_separate_files_per_channel(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.on_event(_read_event(channel="dmm.dc_voltage"))
        store.on_event(_set_event(channel="psu.voltage"))
        store.close()

        arrow_files = list((tmp_path / "telemetry").glob("*/*.arrow"))
        assert len(arrow_files) == 2


class TestIndex:
    def test_index_parquet_written(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.on_event(_read_event())
        store.close()

        index_files = list((tmp_path / "telemetry").glob("*/_index_*.parquet"))
        assert len(index_files) == 1

        table = pq.read_table(index_files[0])
        assert len(table) == 1
        assert table.column("channel_id")[0].as_py() == "dmm.dc_voltage"
        assert table.column("row_count")[0].as_py() == 1


class TestSetEvents:
    def test_set_event_stored(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.on_event(_set_event(value=12.0))
        store.close()

        arrow_files = list((tmp_path / "telemetry").glob("*/*.arrow"))
        reader = ipc.open_file(str(arrow_files[0]))
        table = reader.read_all()
        assert table.column("value")[0].as_py() == 12.0
        assert table.column("source_method")[0].as_py() == "voltage"


class TestQuery:
    def test_query_returns_all(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.on_event(_read_event())
        store.on_event(_read_event(value=3.4))
        store.close()

        telemetry_dir = tmp_path / "telemetry"
        result = TelemetryStore.query(telemetry_dir, "dmm.dc_voltage")
        assert len(result) == 2

    def test_query_filters_by_time(self, tmp_path: Path):
        store = _make_store(tmp_path)

        now = datetime.now(UTC)
        e1 = _read_event(value=1.0)
        e1.occurred_at = now - timedelta(hours=2)
        e2 = _read_event(value=2.0)
        e2.occurred_at = now

        store.on_event(e1)
        store.on_event(e2)
        store.close()

        telemetry_dir = tmp_path / "telemetry"
        result = TelemetryStore.query(
            telemetry_dir, "dmm.dc_voltage",
            start=now - timedelta(hours=1),
        )
        assert len(result) == 1
        assert result.column("value")[0].as_py() == 2.0

    def test_query_empty(self, tmp_path: Path):
        telemetry_dir = tmp_path / "telemetry"
        telemetry_dir.mkdir(parents=True)
        result = TelemetryStore.query(telemetry_dir, "nonexistent")
        assert len(result) == 0
