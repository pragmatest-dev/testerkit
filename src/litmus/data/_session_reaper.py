"""Spine-derived session reaper — the stateless half of P3.

A session is alive while a durable spine event tagged its ``session_id`` is
recent; when none has landed for longer than its will's ``lease + grace``, the
reaper emits an additive synthetic ``SessionEnded{reason, derived=True}`` —
operator-visible, never silent.

It holds **no in-memory state**: recency = ``max(occurred_at)`` over the
session's events, the will = fields on the durable ``SessionStarted`` (read from
the events table's ``json`` column). So a daemon spin re-derives the exact same
verdict (daemon-down tolerance), and an index wipe rebuilds it from the IPC
outbox. The daemon idles (300s) well before any lease (>=900s), so most reaps
fire on the NEXT daemon spin, not a live sweep — correctness survives the gap,
only timeliness is lazy.

Runs on the events daemon (it spins on *any* event and owns the table): a
low-frequency periodic backstop + a final scan at shutdown. Emit goes through a
**per-reap** loopback ``EventStore`` — created only when there's something to
reap and closed straight after, so its transient self-PID ref never pins the
daemon's idle-shutdown.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from uuid import UUID

import duckdb

logger = logging.getLogger(__name__)

# Daemon floor when a SessionStarted predates the will (defensive — pre-release
# every SessionStarted carries it). Mirrors the SessionOptions defaults.
_LEASE_FLOOR = 900.0
_GRACE_FLOOR = 300.0

# Open sessions (SessionStarted, no SessionEnded) whose newest event is older
# than their will's lease + grace. lease/grace/reason read from the will on the
# SessionStarted ``json``; recency = max(occurred_at) across all the session's
# events. ``?`` params: lease floor, grace floor, now.
_SCAN_SQL = """
WITH started AS (
    SELECT session_id,
           COALESCE(
               TRY_CAST(json_extract_string(json, '$.idle_lease_seconds') AS DOUBLE), ?
           ) AS lease,
           COALESCE(
               TRY_CAST(json_extract_string(json, '$.abandon_grace_seconds') AS DOUBLE), ?
           ) AS grace,
           COALESCE(json_extract_string(json, '$.abandon_reason'), 'abandoned') AS reason
    FROM events
    WHERE event_type = 'session.started' AND session_id IS NOT NULL
),
ended AS (
    SELECT DISTINCT session_id FROM events WHERE event_type = 'session.ended'
),
recency AS (
    SELECT session_id, max(occurred_at) AS last_event FROM events GROUP BY session_id
)
SELECT s.session_id, s.reason
FROM started s
JOIN recency r ON r.session_id = s.session_id
WHERE s.session_id NOT IN (SELECT session_id FROM ended)
  AND date_diff('second', r.last_event, ?::TIMESTAMPTZ) > s.lease + s.grace
"""


def find_abandoned(conn: duckdb.DuckDBPyConnection, now: datetime) -> list[tuple[str, str]]:
    """Return ``(session_id, abandon_reason)`` for every open session past its
    will's lease + grace as of ``now``. Pure read — no emission."""
    return [
        (str(sid), str(reason))
        for sid, reason in conn.execute(_SCAN_SQL, [_LEASE_FLOOR, _GRACE_FLOOR, now]).fetchall()
    ]


def reap_abandoned_sessions(
    conn: duckdb.DuckDBPyConnection, events_dir: Path, *, now: datetime
) -> int:
    """Emit a synthetic ``SessionEnded`` for each open session past lease+grace.

    Returns the number reaped. Idempotent: once a session has a ``SessionEnded``
    the scan no longer flags it, so each abandoned session is reaped exactly
    once and the set converges to empty.
    """
    try:
        abandoned = find_abandoned(conn, now)
    except duckdb.Error as exc:  # a bad scan must not kill the daemon
        logger.warning("Session reap scan failed: %s", exc)
        return 0
    if not abandoned:
        return 0

    # Lazy, per-reap loopback EventStore: only constructed when there's something
    # to seal, and closed straight after so its transient self-ref doesn't pin
    # idle-shutdown. emit() writes the durable IPC outbox (no drift) + pushes.
    from litmus.data.event_store import EventStore
    from litmus.data.events import SessionEnded

    es = EventStore(_data_dir=events_dir.parent)
    try:
        for session_id, reason in abandoned:
            es.emit(SessionEnded(session_id=UUID(session_id), reason=reason, derived=True))
            logger.info("Reaped abandoned session %s (reason=%s)", session_id, reason)
        es.flush()
    finally:
        es.close()
    return len(abandoned)
