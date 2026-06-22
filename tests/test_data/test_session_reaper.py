"""Spine-derived session reaper scan — the stateless abandonment query.

``find_abandoned`` reads recency (max occurred_at) + the will (lease/grace/reason
off the SessionStarted json) from the events table and returns the sessions past
their lease + grace. No in-memory state — the same durable rows always yield the
same verdict, which is what makes daemon-down tolerance work.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import duckdb

from litmus.data._session_reaper import find_abandoned

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE events ("
        "  event_type VARCHAR, occurred_at TIMESTAMPTZ, session_id VARCHAR, json VARCHAR"
        ")"
    )
    return conn


def _started(
    conn, sid, t=_T0, *, lease: float = 900.0, grace: float = 300.0, reason: str = "abandoned"
) -> None:
    will = (
        f'{{"idle_lease_seconds": {lease}, "abandon_grace_seconds": {grace}, '
        f'"abandon_reason": "{reason}"}}'
    )
    conn.execute("INSERT INTO events VALUES ('session.started', ?, ?, ?)", [t, str(sid), will])


def _activity(conn, sid, t) -> None:
    conn.execute("INSERT INTO events VALUES ('test.measurement', ?, ?, '{}')", [t, str(sid)])


def _ended(conn, sid, t) -> None:
    conn.execute("INSERT INTO events VALUES ('session.ended', ?, ?, '{}')", [t, str(sid)])


def test_open_session_past_lease_plus_grace_is_abandoned():
    conn = _conn()
    sid = uuid4()
    _started(conn, sid)  # lease 900 + grace 300 = 1200s window
    now = _T0 + timedelta(seconds=1500)
    assert find_abandoned(conn, now) == [(str(sid), "abandoned")]


def test_within_window_not_abandoned():
    conn = _conn()
    sid = uuid4()
    _started(conn, sid)
    now = _T0 + timedelta(seconds=1000)  # < 1200
    assert find_abandoned(conn, now) == []


def test_recent_activity_renews_the_lease():
    conn = _conn()
    sid = uuid4()
    _started(conn, sid)
    _activity(conn, sid, _T0 + timedelta(seconds=1400))  # a heartbeat
    now = _T0 + timedelta(seconds=1500)  # only 100s since the activity
    assert find_abandoned(conn, now) == []


def test_cleanly_ended_session_not_reaped():
    conn = _conn()
    sid = uuid4()
    _started(conn, sid)
    _ended(conn, sid, _T0 + timedelta(seconds=10))
    now = _T0 + timedelta(seconds=5000)
    assert find_abandoned(conn, now) == []


def test_will_lease_respected():
    conn = _conn()
    sid = uuid4()
    _started(conn, sid, lease=3600.0)  # patient interactive lease
    now = _T0 + timedelta(seconds=2000)  # past 1200 default but < 3600+300
    assert find_abandoned(conn, now) == []


def test_custom_abandon_reason_carried():
    conn = _conn()
    sid = uuid4()
    _started(conn, sid, reason="ci_timeout")
    now = _T0 + timedelta(seconds=2000)
    assert find_abandoned(conn, now) == [(str(sid), "ci_timeout")]


def test_missing_will_falls_back_to_floor():
    conn = _conn()
    sid = uuid4()
    conn.execute(
        "INSERT INTO events VALUES ('session.started', ?, ?, '{}')", [_T0, str(sid)]
    )  # no will fields → 900 + 300 floor
    assert find_abandoned(conn, _T0 + timedelta(seconds=1000)) == []
    assert find_abandoned(conn, _T0 + timedelta(seconds=1500)) == [(str(sid), "abandoned")]
