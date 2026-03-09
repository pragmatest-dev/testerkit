"""Tests for StationConnection and litmus.connect()."""

import json
from pathlib import Path
from uuid import UUID

import pyarrow.ipc as ipc
import pytest

from litmus.connect import StationConnection
from litmus.schemas import StationConfig, StationInstrumentConfig


def _read_events_from_ipc(path: Path) -> list[dict]:
    """Read all events from an Arrow IPC file, parsing the json column."""
    reader = ipc.open_file(str(path))
    events: list[dict] = []
    for i in range(reader.num_record_batches):
        batch = reader.get_batch(i)
        json_col = batch.column("json")
        for j in range(batch.num_rows):
            events.append(json.loads(json_col[j].as_py()))
    return events


def _make_station(**instruments) -> StationConfig:
    inst_configs = {}
    for role, resource in instruments.items():
        inst_configs[role] = StationInstrumentConfig(
            type="generic", resource=resource, mock=True,
        )
    return StationConfig(id="test-station", name="Test Station", instruments=inst_configs)


@pytest.fixture(autouse=True)
def _use_tmp_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("LITMUS_HOME", str(tmp_path / "litmus_home"))


class TestStationConnection:
    def test_context_manager(self, tmp_path):
        station = _make_station(dmm="GPIB::16::INSTR")
        with StationConnection(station, results_dir=tmp_path / "results", mock=True) as conn:
            assert conn.session_id is not None
            assert isinstance(conn.session_id, UUID)

    def test_start_stop(self, tmp_path):
        station = _make_station(dmm="GPIB::16::INSTR")
        conn = StationConnection(station, results_dir=tmp_path / "results", mock=True)
        conn.start()
        assert conn.event_log is not None
        conn.stop()
        assert conn.event_log is None

    def test_instrument_connect_release(self, tmp_path):
        station = _make_station(dmm="GPIB::16::INSTR")
        conn = StationConnection(station, results_dir=tmp_path / "results", mock=True)
        conn.start()

        dmm = conn.instrument("dmm")
        assert dmm is not None
        assert "dmm" in conn.instruments

        conn.release("dmm")
        assert "dmm" not in conn.instruments
        conn.stop()

    def test_instrument_not_found(self, tmp_path):
        station = _make_station(dmm="GPIB::16::INSTR")
        conn = StationConnection(station, results_dir=tmp_path / "results", mock=True)
        conn.start()

        with pytest.raises(KeyError, match="psu"):
            conn.instrument("psu")

        conn.stop()

    def test_events_emitted(self, tmp_path):
        station = _make_station(dmm="GPIB::16::INSTR")
        with StationConnection(station, results_dir=tmp_path / "results", mock=True) as conn:
            conn.instrument("dmm")
            conn.release("dmm")
            log_path = conn.event_log.path

        events = _read_events_from_ipc(log_path)
        event_types = [e["event_type"] for e in events]
        assert "session.started" in event_types
        assert "fixture.instrument_connected" in event_types
        assert "fixture.instrument_disconnected" in event_types
        assert "session.ended" in event_types

    def test_stop_releases_all_instruments(self, tmp_path):
        station = _make_station(dmm="GPIB::16::INSTR", psu="GPIB::17::INSTR")
        conn = StationConnection(station, results_dir=tmp_path / "results", mock=True)
        conn.start()
        conn.instrument("dmm")
        conn.instrument("psu")
        assert len(conn.instruments) == 2
        conn.stop()
        assert len(conn.instruments) == 0

    def test_auto_start_on_instrument(self, tmp_path):
        station = _make_station(dmm="GPIB::16::INSTR")
        conn = StationConnection(station, results_dir=tmp_path / "results", mock=True)
        # Don't call start() explicitly
        dmm = conn.instrument("dmm")
        assert dmm is not None
        assert conn.event_log is not None
        conn.stop()

    def test_context_manager_error_outcome(self, tmp_path):
        station = _make_station()
        log_path = None
        with pytest.raises(ValueError):
            with StationConnection(
                station, results_dir=tmp_path / "results", mock=True,
            ) as conn:
                log_path = conn.event_log.path
                raise ValueError("test error")

        events = _read_events_from_ipc(log_path)
        ended = [e for e in events if e["event_type"] == "session.ended"]
        assert ended[0]["outcome"] == "error"


class TestSessionStartedFields:
    def test_pid_field(self, tmp_path):
        import os

        station = _make_station()
        with StationConnection(
            station, results_dir=tmp_path / "results", mock=True,
        ) as conn:
            log_path = conn.event_log.path

        events = _read_events_from_ipc(log_path)
        started = events[0]
        assert started["pid"] == os.getpid()

    def test_dut_serial_defaults_empty(self, tmp_path):
        station = _make_station()
        with StationConnection(
            station, results_dir=tmp_path / "results", mock=True,
        ) as conn:
            log_path = conn.event_log.path

        events = _read_events_from_ipc(log_path)
        started = events[0]
        assert started["dut_serial"] == ""
