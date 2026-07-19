"""Tests for StationConnection and testerkit.connect().

Storage is the canonical singleton data_dir — every test writes
events to the same shared events daemon. Per-test isolation is by
``session_id`` (the per-process EventStore stamps a unique session
on each ``StationConnection``), not by directory. Tests read back
through the IPC file at ``conn.event_log.path``, which is keyed
by session+pid so tests never see each other's events.

Locks and station/instrument config still use ``tmp_path`` via
the ``TESTERKIT_HOME`` redirect in ``_use_tmp_dirs`` — those don't
spawn daemons.
"""

import json
from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from testerkit.connect import StationConnection
from testerkit.data.data_dir import resolve_data_dir
from testerkit.instruments.locks import ResourceInUse
from testerkit.models.station import StationConfig, StationInstrumentConfig

# Canonical data dir — resolved through the project's
# ``testerkit.yaml`` (at repo root) so storage stays project-local
# (``<repo>/data``) instead of polluting the global
# ``~/.local/share/testerkit/data`` store. ``resolve_data_dir``
# walks CWD ancestors for ``testerkit.yaml`` and returns its
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
    monkeypatch.setenv("TESTERKIT_HOME", str(tmp_path / "testerkit_home"))


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

        conn.disconnect("dmm")
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
            conn.disconnect("dmm")
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

    def test_context_manager_emits_session_ended_on_error(self):
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
        assert len(ended) == 1


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

    def test_interactive_session_stamps_patient_will(self):
        from testerkit.data._process import process_uuid

        station = _make_station()
        with StationConnection(
            station,
            data_dir=_CANONICAL_DATA,
            mock=True,
        ) as conn:
            assert conn.event_log is not None
            log_path = conn.event_log.path

        started = _read_events_from_ipc(log_path)[0]
        # The owner stamps its will; an interactive owner declares a patient
        # lease (>= the interactive floor) so a human pause isn't reaped.
        assert started["process_uuid"] == process_uuid()
        assert started["idle_lease_seconds"] >= 3600.0
        assert started["abandon_reason"] == "abandoned"

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


def _make_station_locking(**instruments) -> StationConfig:
    """Station with non-mocked instruments for lock-behaviour tests."""
    inst_configs = {}
    for role, resource in instruments.items():
        inst_configs[role] = StationInstrumentConfig(
            type="generic",
            resource=resource,
            mock=False,
        )
    return StationConfig(id="test-station", name="Test Station", instruments=inst_configs)


@pytest.fixture()
def _patch_driver_loading(monkeypatch):
    """Replace load_and_connect + verify_and_wrap to avoid real hardware."""
    monkeypatch.setattr(
        "testerkit.instruments.pool.load_and_connect",
        lambda *a, **kw: object(),
    )
    monkeypatch.setattr(
        "testerkit.instruments.pool.verify_and_wrap",
        lambda driver, *a, **kw: driver,
    )


class TestReservationAPI:
    """Phase 2a: reserve/release_reservation/reservation on StationConnection."""

    def test_instrument_reserve_false_attaches_without_lock(self):
        """instrument(role, reserve=False) attaches but does not hold a lock."""
        station = _make_station(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=True) as conn:
            dmm = conn.instrument("dmm", reserve=False)
            assert dmm is not None
            assert "dmm" in conn.instruments

    def test_instrument_reserve_true_default(self):
        """instrument(role) with default reserve=True attaches and is callable."""
        station = _make_station(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=True) as conn:
            dmm = conn.instrument("dmm")
            assert dmm is not None

    def test_explicit_reserve_and_release_reservation(self):
        """conn.reserve() and conn.release_reservation() are directly callable."""
        station = _make_station(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=True) as conn:
            conn.instrument("dmm", reserve=False)
            conn.reserve("dmm")
            conn.release_reservation("dmm")
            assert "dmm" in conn.instruments

    def test_reservation_context_manager(self):
        """with conn.reservation(role): enters and exits without error."""
        station = _make_station(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=True) as conn:
            conn.instrument("dmm", reserve=False)
            with conn.reservation("dmm"):
                assert "dmm" in conn.instruments
            assert "dmm" in conn.instruments

    def test_reservation_releases_on_exception(self):
        """reservation() releases even when the body raises."""
        station = _make_station(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=True) as conn:
            conn.instrument("dmm", reserve=False)
            with pytest.raises(ValueError):
                with conn.reservation("dmm"):
                    raise ValueError("test")
            assert "dmm" in conn.instruments

    def test_release_frees_reservation_and_disconnects(self):
        """disconnect() disconnects the driver (reservation is freed as part of this)."""
        station = _make_station(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=True) as conn:
            conn.instrument("dmm")
            conn.disconnect("dmm")
            assert "dmm" not in conn.instruments

    def test_release_all_via_stop(self):
        """stop() calls disconnect_all() which frees all instruments and reservations."""
        station = _make_station(dmm="GPIB::16::INSTR", psu="GPIB::17::INSTR")
        conn = StationConnection(station, data_dir=_CANONICAL_DATA, mock=True)
        conn.start()
        conn.instrument("dmm")
        conn.instrument("psu")
        conn.stop()
        assert len(conn.instruments) == 0


class TestReservationLocking:
    """Phase 2a lock-behaviour tests using real file locks (driver loading patched)."""

    def test_instrument_reserves_by_default(self, _patch_driver_loading):
        """instrument(role) with default reserve=True holds a file lock."""
        station = _make_station_locking(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=False) as conn:
            conn.instrument("dmm")
            assert conn._pool is not None
            assert "dmm" in conn._pool._locks

    def test_instrument_reserve_false_holds_no_lock(self, _patch_driver_loading):
        """instrument(role, reserve=False) does not acquire a file lock."""
        station = _make_station_locking(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=False) as conn:
            conn.instrument("dmm", reserve=False)
            assert conn._pool is not None
            assert "dmm" not in conn._pool._locks

    def test_reservation_context_manager_acquires_and_releases_lock(self, _patch_driver_loading):
        """reservation() acquires the lock on enter and releases on exit."""
        station = _make_station_locking(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=False) as conn:
            conn.instrument("dmm", reserve=False)
            assert conn._pool is not None

            with conn.reservation("dmm"):
                assert "dmm" in conn._pool._locks

            assert "dmm" not in conn._pool._locks

    def test_two_connections_contend_on_same_resource(self, _patch_driver_loading):
        """A second connection cannot reserve a resource already held by a first."""
        station = _make_station_locking(dmm="GPIB::16::INSTR")
        conn1 = StationConnection(station, data_dir=_CANONICAL_DATA, mock=False)
        conn2 = StationConnection(station, data_dir=_CANONICAL_DATA, mock=False)
        conn1.start()
        conn2.start()
        try:
            conn1.instrument("dmm")
            with pytest.raises(ResourceInUse):
                conn2.instrument("dmm", timeout=0)
        finally:
            conn1.stop()
            conn2.stop()

    def test_reentrant_reserve_via_context_manager(self, _patch_driver_loading):
        """Nested reservation() calls do not deadlock; outer release restores state."""
        station = _make_station_locking(dmm="GPIB::16::INSTR")
        with StationConnection(station, data_dir=_CANONICAL_DATA, mock=False) as conn:
            conn.instrument("dmm", reserve=False)
            assert conn._pool is not None

            with conn.reservation("dmm"):
                with conn.reservation("dmm"):
                    assert "dmm" in conn._pool._locks

                assert "dmm" in conn._pool._locks

            assert "dmm" not in conn._pool._locks
