"""Replica-aware orphan sweep (GH-64) — the fork-free half.

``_classify_orphan`` is the pure sweep decision, extracted from the daemon's
``daemon_run`` closure so it is importable and unit-testable without a daemon or a
live pool. The :class:`AccumulatorPool` now caches the producer host + uuid and
stamps the inactivity clock from ``occurred_at`` (source time), so the sweep can:

- gate pid liveness on LOCALITY — a replicated/foreign session's pid is
  meaningless in this process and must never be pid-checked here;
- short-circuit a verified-live LOCAL producer even if it has been quiet past the
  timeout (a busy-but-silent run must not be aborted — this also fixes a
  pre-existing local bug);
- measure inactivity from source time, so a replayed backlog / forwarder lag is
  not mistaken for inactivity.

The synthetic-abort SUPERSEDE path (tombstone vs re-hydrate) is a separate OPEN
design decision and is NOT covered here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from testerkit.data._accumulator_pool import AccumulatorPool, OpenRun
from testerkit.data._runs_duckdb_daemon import _classify_orphan
from testerkit.data.backends._event_accumulator import EventAccumulator
from testerkit.data.events import RunStarted, SessionStarted
from testerkit.models.data_options import (
    RUN_ORPHAN_TIMEOUT_ENV,
    RUN_ORPHAN_TIMEOUT_SECONDS,
    resolve_orphan_timeout,
)

HOST = "bench-01"
NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)


def _never(_pid: int) -> bool | None:
    raise AssertionError("pid_liveness must not be called here")


def _open_run(**kw: object) -> OpenRun:
    defaults: dict[str, object] = {
        "run_id": "R1",
        "acc": EventAccumulator(),
        "pid": 1234,
        "station_hostname": HOST,
        "process_uuid": "u-1",
        "last_event_at": NOW - timedelta(seconds=10),
    }
    defaults.update(kw)
    return OpenRun(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# _classify_orphan (pure)                                                      #
# --------------------------------------------------------------------------- #


def test_local_dead_pid_is_orphan():
    run = _open_run()
    is_orphan, reason = _classify_orphan(
        run, now=NOW, orphan_timeout=900, local_hostname=HOST, pid_liveness=lambda _p: False
    )
    assert is_orphan is True
    assert "no longer exists" in reason


def test_local_live_pid_quiet_past_timeout_is_not_orphan():
    # Short-circuit: a verified-live LOCAL producer, silent for an hour, must NOT
    # be aborted (the pre-existing local bug this fixes).
    run = _open_run(last_event_at=NOW - timedelta(hours=1))
    is_orphan, _ = _classify_orphan(
        run, now=NOW, orphan_timeout=900, local_hostname=HOST, pid_liveness=lambda _p: True
    )
    assert is_orphan is False


def test_non_local_session_skips_pid_liveness():
    # Foreign host: the pid is meaningless here, so pid_liveness is never called;
    # within the timeout the run is not an orphan.
    called: list[int] = []

    def _spy(pid: int) -> bool | None:
        called.append(pid)
        return False

    run = _open_run(station_hostname="other-bench", last_event_at=NOW - timedelta(seconds=10))
    is_orphan, _ = _classify_orphan(
        run, now=NOW, orphan_timeout=900, local_hostname=HOST, pid_liveness=_spy
    )
    assert called == []
    assert is_orphan is False


def test_non_local_past_timeout_is_orphan():
    run = _open_run(station_hostname="other-bench", last_event_at=NOW - timedelta(hours=1))
    is_orphan, reason = _classify_orphan(
        run, now=NOW, orphan_timeout=900, local_hostname=HOST, pid_liveness=lambda _p: True
    )
    assert is_orphan is True
    assert "no events" in reason


def test_indeterminate_liveness_falls_back_to_inactivity():
    run = _open_run(last_event_at=NOW - timedelta(hours=1))
    is_orphan, _ = _classify_orphan(
        run, now=NOW, orphan_timeout=900, local_hostname=HOST, pid_liveness=lambda _p: None
    )
    assert is_orphan is True  # can't verify liveness → the stale inactivity clock governs


def test_no_pid_uses_inactivity_only():
    run = _open_run(pid=None, last_event_at=NOW - timedelta(seconds=10))
    is_orphan, _ = _classify_orphan(
        run, now=NOW, orphan_timeout=900, local_hostname=HOST, pid_liveness=_never
    )
    assert is_orphan is False


def test_no_last_event_is_not_orphan():
    run = _open_run(pid=None, last_event_at=None)
    is_orphan, _ = _classify_orphan(run, now=NOW, orphan_timeout=900, local_hostname=HOST)
    assert is_orphan is False


# --------------------------------------------------------------------------- #
# AccumulatorPool identity caching + occurred_at clock                         #
# --------------------------------------------------------------------------- #


def _dispatch(pool: AccumulatorPool, evt: object) -> None:
    pool.dispatch(evt.model_dump())  # type: ignore[attr-defined]


def test_pool_caches_host_uuid_and_stamps_occurred_at():
    pool = AccumulatorPool()
    sid, rid = uuid4(), uuid4()
    _dispatch(
        pool,
        SessionStarted(session_id=sid, pid=4321, station_hostname=HOST, process_uuid="u-9"),
    )
    occurred = datetime(2026, 9, 13, 10, 0, 0, tzinfo=UTC)
    _dispatch(pool, RunStarted(session_id=sid, run_id=rid, occurred_at=occurred))

    (r,) = pool.open_runs()
    assert r.run_id == str(rid)
    assert r.pid == 4321
    assert r.station_hostname == HOST
    assert r.process_uuid == "u-9"
    assert r.last_event_at == occurred  # source time, NOT now()


def test_mark_session_ended_clears_identity():
    pool = AccumulatorPool()
    sid = uuid4()
    _dispatch(pool, SessionStarted(session_id=sid, pid=1, station_hostname=HOST, process_uuid="u"))
    pool.mark_session_ended(str(sid))
    # A run for the now-forgotten session resolves to no producer identity.
    _dispatch(pool, RunStarted(session_id=sid, run_id=uuid4()))
    (r,) = pool.open_runs()
    assert r.pid is None
    assert r.station_hostname is None
    assert r.process_uuid is None


# --------------------------------------------------------------------------- #
# resolve_orphan_timeout                                                       #
# --------------------------------------------------------------------------- #


def test_resolve_orphan_timeout_default(monkeypatch):
    monkeypatch.delenv(RUN_ORPHAN_TIMEOUT_ENV, raising=False)
    assert resolve_orphan_timeout() == RUN_ORPHAN_TIMEOUT_SECONDS


def test_resolve_orphan_timeout_env_override(monkeypatch):
    monkeypatch.setenv(RUN_ORPHAN_TIMEOUT_ENV, "5")
    assert resolve_orphan_timeout() == 5.0


def test_resolve_orphan_timeout_invalid_falls_back(monkeypatch):
    monkeypatch.setenv(RUN_ORPHAN_TIMEOUT_ENV, "not-a-number")
    assert resolve_orphan_timeout() == RUN_ORPHAN_TIMEOUT_SECONDS
