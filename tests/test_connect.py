"""Tests for StationConnection and litmus.connect().

Storage is the canonical singleton data_dir — every test writes
events to the same shared events daemon. Per-test isolation is by
``session_id`` (the per-process EventStore stamps a unique session
on each ``StationConnection``), not by directory. Tests read back
through the IPC file at ``conn.event_log.path``, which is keyed
by session+pid so tests never see each other's events.

Locks and station/instrument config still use ``tmp_path`` via
the ``LITMUS_HOME`` redirect in ``_use_tmp_dirs`` — those don't
spawn daemons.
"""

import json
from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from litmus.connect import StationConnection
from litmus.data.data_dir import resolve_data_dir
from litmus.models.station import StationConfig, StationInstrumentConfig

# Canonical data dir — resolved through the project's
# ``litmus.yaml`` (at repo root) so storage stays project-local
# (``<repo>/data``) instead of polluting the global
# ``~/.local/share/litmus/data`` store. ``resolve_data_dir``
# walks CWD ancestors for ``litmus.yaml`` and returns its
# ``data_dir`` field; here CWD is the repo root because pytest
# is invoked from there.
_CANONICAL_DATA = resolve_data_dir()


def _read_events_from_ipc(path: Path) -> list[dict]:
    """Read all events from an Arrow IPC stream, parsing the json column."""
    reader = ipc.open_stream(pa.OSFile(str(path), "rb"))
    table = reader.read_all()
    events: list[dict] = []
    json_col = table.column("json")
    for j in range(len(table)):
        events.append(json.loads(json_col[j].as_py()))
    return events


def _make_station(**instruments) -> StationConfig:
    inst_configs = {}
    for role, resource in instruments.items():
        inst_configs[role] = StationInstrumentConfig(
            type="generic",
            resource=resource,
            mock=True,
        )
    return StationConfig(id="test-station", name="Test Station", instruments=inst_configs)


@pytest.fixture(autouse=True)
def _use_tmp_dirs(tmp_path, monkeypatch):
    """autouse — pyright can't see fixture wiring, but pytest does."""
    monkeypatch.setenv("LITMUS_HOME", str(tmp_path / "litmus_home"))


# Re-export so pyright sees the autouse fixture as referenced. Pytest
# resolves fixtures by collection-time discovery, not by name binding,
# so this is purely a static-analysis appeasement.
_ = _use_tmp_dirs


class TestStationConnection:
    def test_context_manager(self):
        station = _make_station(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=True) as conn:
            assert conn.session_id is not None
            assert isinstance(conn.session_id, UUID)

    def test_start_stop(self):
        station = _make_station(dmm="GPIB::16::INSTR")
        conn = StationConnection(station, data_dir=_CANONICAL_DATA, mock=True)
        conn.start()
        assert conn.event_log is not None
        conn.stop()
        assert conn.event_log is None

    def test_instrument_connect_release(self):
        station = _make_station(dmm="GPIB::16::INSTR")
        conn = StationConnection(station, data_dir=_CANONICAL_DATA, mock=True)
        conn.start()

        dmm = conn.instrument("dmm")
        assert dmm is not None
        assert "dmm" in conn.instruments

        conn.release("dmm")
        assert "dmm" not in conn.instruments
        conn.stop()

    def test_instrument_not_found(self):
        station = _make_station(dmm="GPIB::16::INSTR")
        conn = StationConnection(station, data_dir=_CANONICAL_DATA, mock=True)
        conn.start()

        with pytest.raises(KeyError, match="psu"):
            conn.instrument("psu")

        conn.stop()

    def test_events_emitted(self):
        station = _make_station(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=True) as conn:
            conn.instrument("dmm")
            conn.release("dmm")
            assert conn.event_log is not None
            log_path = conn.event_log.path

        events = _read_events_from_ipc(log_path)
        event_types = [e["event_type"] for e in events]
        assert "session.started" in event_types
        assert "fixture.instrument_connected" in event_types
        assert "fixture.instrument_disconnected" in event_types
        assert "session.ended" in event_types

    def test_stop_releases_all_instruments(self):
        station = _make_station(dmm="GPIB::16::INSTR", psu="GPIB::17::INSTR")
        conn = StationConnection(station, data_dir=_CANONICAL_DATA, mock=True)
        conn.start()
        conn.instrument("dmm")
        conn.instrument("psu")
        assert len(conn.instruments) == 2
        conn.stop()
        assert len(conn.instruments) == 0

    def test_auto_start_on_instrument(self):
        station = _make_station(dmm="GPIB::16::INSTR")
        conn = StationConnection(station, data_dir=_CANONICAL_DATA, mock=True)
        # Don't call start() explicitly
        dmm = conn.instrument("dmm")
        assert dmm is not None
        assert conn.event_log is not None
        conn.stop()

    def test_context_manager_error_outcome(self):
        station = _make_station()
        log_path = None
        with pytest.raises(ValueError):
            with StationConnection(
                station,
                data_dir=_CANONICAL_DATA,
                mock=True,
            ) as conn:
                assert conn.event_log is not None
                log_path = conn.event_log.path
                raise ValueError("test error")

        assert log_path is not None
        events = _read_events_from_ipc(log_path)
        ended = [e for e in events if e["event_type"] == "session.ended"]
        assert ended[0]["outcome"] == "errored"


class TestSessionStartedFields:
    def test_pid_field(self):
        import os

        station = _make_station()
        with StationConnection(
            station,
            data_dir=_CANONICAL_DATA,
            mock=True,
        ) as conn:
            assert conn.event_log is not None
            log_path = conn.event_log.path

        events = _read_events_from_ipc(log_path)
        started = events[0]
        assert started["pid"] == os.getpid()

    def test_session_type_interactive(self):
        station = _make_station()
        with StationConnection(
            station,
            data_dir=_CANONICAL_DATA,
            mock=True,
        ) as conn:
            assert conn.event_log is not None
            log_path = conn.event_log.path

        events = _read_events_from_ipc(log_path)
        started = events[0]
        assert started["session_type"] == "interactive"

    def test_session_started_no_run_id(self):
        station = _make_station()
        with StationConnection(
            station,
            data_dir=_CANONICAL_DATA,
            mock=True,
        ) as conn:
            assert conn.event_log is not None
            log_path = conn.event_log.path

        events = _read_events_from_ipc(log_path)
        started = events[0]
        assert started.get("run_id") is None

    def test_session_ended_no_run_id(self):
        station = _make_station()
        with StationConnection(
            station,
            data_dir=_CANONICAL_DATA,
            mock=True,
        ) as conn:
            assert conn.event_log is not None
            log_path = conn.event_log.path

        events = _read_events_from_ipc(log_path)
        ended = [e for e in events if e["event_type"] == "session.ended"]
        assert ended[0].get("run_id") is None

    def test_interactive_no_run_events(self):
        """Interactive sessions emit session events but no run events."""
        station = _make_station(dmm="GPIB::16::INSTR")
        with StationConnection(
            station,
            data_dir=_CANONICAL_DATA,
            mock=True,
        ) as conn:
            conn.instrument("dmm")
            assert conn.event_log is not None
            log_path = conn.event_log.path

        events = _read_events_from_ipc(log_path)
        event_types = {e["event_type"] for e in events}
        assert "session.started" in event_types
        assert "session.ended" in event_types
        assert "run.started" not in event_types
        assert "run.ended" not in event_types
