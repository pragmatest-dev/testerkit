"""Open-session tracking in the accumulator pool — the basis for the orphan
sweep self-healing a crashed (possibly runless) session.

A session with no open run is invisible to ``open_runs`` but must still be
reachable for the sweep, so the pool tracks ``(session_id, pid, hostname)`` from
``SessionStarted`` and drops it on ``SessionEnded``.
"""

from __future__ import annotations

from uuid import uuid4

from litmus.data._accumulator_pool import AccumulatorPool
from litmus.data.events import SessionEnded, SessionStarted


def _started(sid, *, host: str = "h1", pid: int = 999999) -> dict:
    return SessionStarted(session_id=sid, pid=pid, station_hostname=host).model_dump(mode="json")


def _ended(sid) -> dict:
    return SessionEnded(session_id=sid, outcome="aborted").model_dump(mode="json")


class TestOpenSessions:
    def test_present_after_start_without_any_run(self):
        pool = AccumulatorPool()
        sid = uuid4()
        pool.dispatch(_started(sid))
        sessions = pool.open_sessions()
        assert [s[0] for s in sessions] == [str(sid)]
        assert sessions[0][1] == 999999  # pid — the sweep's liveness probe
        assert sessions[0][2] == "h1"  # hostname — the same-host gate

    def test_session_ended_drops_from_open_set(self):
        pool = AccumulatorPool()
        sid = uuid4()
        pool.dispatch(_started(sid))
        pool.dispatch(_ended(sid))
        assert pool.open_sessions() == []  # cleanly ended → not swept

    def test_mark_session_ended_drops_from_open_set(self):
        # The sweep calls this after emitting a synthetic SessionEnded, so a
        # self-healed session isn't re-emitted on the next tick.
        pool = AccumulatorPool()
        sid = uuid4()
        pool.dispatch(_started(sid))
        pool.mark_session_ended(str(sid))
        assert pool.open_sessions() == []
