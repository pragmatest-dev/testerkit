"""End-to-end test for the runs daemon's live event subscriber.

The runs daemon is supposed to:

* Subscribe to the EventStore (cross-process) on startup.
* On ``run.started``, UPSERT a partial row into the ``runs`` table
  (``ended_at IS NULL``, ``outcome IS NULL``).
* On ``run.ended``, UPSERT the matching row to set ``ended_at`` and
  ``outcome``.

Without this, the operator UI's "Running" chip never appears for an
in-flight run — which is exactly what was reported. These tests pin
the wiring end-to-end: real events daemon, real runs daemon, real
Flight RPCs, real watcher loop. No mocks of subscription plumbing.

Latency budget per assertion: the test process buffers events with a
1s flush timer + the daemon's watcher polls every 500ms, so each
upsert takes up to ~2s to land. We poll with a generous timeout
rather than sleeping a fixed duration.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from litmus.analysis.runs_query import RunRow, RunsQuery
from litmus.data.event_store import EventStore
from litmus.data.events import RunEnded, RunStarted, SessionStarted

_LANDING_TIMEOUT_S = 10.0


@pytest.fixture
def aborted_run_cleanup():
    """Track in-flight runs and emit ``RunEnded(aborted)`` at teardown.

    Tests that exercise the in-flight path (RunStarted with no RunEnded)
    leak zombie runs into the events DB unless they finalize on the way
    out. Real abandoned runs are operator-visible signals — they should
    never be filtered out — but tests that *deliberately* create the
    in-flight state owe the framework a teardown so the events DB
    doesn't accumulate fake zombies across CI cycles.

    Usage::

        def test_something_in_flight(self, aborted_run_cleanup):
            store = EventStore()
            session_id, run_id = uuid4(), uuid4()
            _emit_session(store, session_id)
            _emit_run_started(store, session_id=session_id, run_id=run_id, ...)
            aborted_run_cleanup.track(store, session_id, run_id)
            store.flush()
            # ... assertions ...

    On teardown the fixture emits ``RunEnded(aborted)`` for every
    tracked ``(store, session_id, run_id)`` triple, then flushes. Tests
    that emit RunEnded themselves don't need the fixture.
    """

    class _Tracker:
        def __init__(self) -> None:
            self._tracked: list[tuple[EventStore, UUID, UUID]] = []

        def track(self, store: EventStore, session_id: UUID, run_id: UUID) -> None:
            self._tracked.append((store, session_id, run_id))

    tracker = _Tracker()
    yield tracker
    for store, session_id, run_id in tracker._tracked:
        try:
            _emit_run_ended(
                store,
                session_id=session_id,
                run_id=run_id,
                ended_at=datetime.now(UTC),
                outcome="aborted",
            )
            store.flush()
        except Exception:
            # Teardown is best-effort; if the store is already closed or the
            # daemon is unreachable, the zombie persists this run but won't
            # block the test result.
            pass


def _wait_for_run(
    run_id: str,
    *,
    predicate,
    timeout: float = _LANDING_TIMEOUT_S,
) -> RunRow | None:
    """Poll ``RunsQuery.get(run_id)`` until ``predicate`` matches.

    Returns the matching ``RunRow`` on success, or ``None`` on timeout.
    Uses direct primary-key lookup (``get``) rather than
    ``list_recent`` because the canonical singleton store can hold
    many concurrent in-flight runs from other tests / the UI; a
    LIMIT-50 sorted scan can miss this test's run when the pool is
    busy. ``get(run_id)`` is O(1) and finds it regardless.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with RunsQuery() as q:
            row = q.get(run_id)
        if row is not None and predicate(row):
            return row
        time.sleep(0.2)
    return None


def _emit_session(store: EventStore, session_id: UUID) -> None:
    """Emit the ``session.started`` event the test runner would normally emit."""
    store.emit(
        SessionStarted(
            session_id=session_id,
            station_id="bench-01",
            station_name="Bench 1",
            station_hostname="bench-01.local",
            session_type="test",
            pid=os.getpid(),
        )
    )


def _emit_run_started(
    store: EventStore,
    *,
    session_id: UUID,
    run_id: UUID,
    started_at: datetime,
) -> None:
    """Emit a minimal ``run.started`` — what the pytest plugin emits at session start."""
    store.emit(
        RunStarted(
            session_id=session_id,
            run_id=run_id,
            occurred_at=started_at,
            station_id="bench-01",
            station_name="Bench 1",
            station_hostname="bench-01.local",
            dut_serial="SN-LIVE-001",
            dut_part_number="PN-100",
            test_phase="production",
            project_name="demo",
            pid=os.getpid(),
        )
    )


def _emit_run_ended(
    store: EventStore,
    *,
    session_id: UUID,
    run_id: UUID,
    ended_at: datetime,
    outcome: str,
) -> None:
    """Emit ``run.ended`` — the pytest plugin's session-end finalize."""
    store.emit(
        RunEnded(
            session_id=session_id,
            run_id=run_id,
            occurred_at=ended_at,
            outcome=outcome,  # type: ignore[arg-type]
        )
    )


class TestLiveRunVisibility:
    """End-to-end: events emitted by the test process land in the daemon's runs table."""

    def test_run_started_inserts_partial_row(self, aborted_run_cleanup):
        """``RunStarted`` → daemon UPSERTs a partial row (``ended_at IS NULL``)."""
        store = EventStore()

        # Force the runs daemon to start by opening a query connection;
        # its event subscriber attaches as part of ``daemon_run``.
        with RunsQuery():
            pass

        session_id = uuid4()
        run_id = uuid4()
        started = datetime.now(UTC)

        _emit_session(store, session_id)
        _emit_run_started(
            store,
            session_id=session_id,
            run_id=run_id,
            started_at=started,
        )
        # Register for teardown finalization — this test deliberately leaves
        # the run in flight to exercise the partial-row path; the fixture
        # emits RunEnded(aborted) at teardown so the events DB doesn't grow
        # a zombie on every CI cycle.
        aborted_run_cleanup.track(store, session_id, run_id)
        store.flush()

        row = _wait_for_run(
            str(run_id),
            predicate=lambda r: r.ended_at is None and r.outcome is None,
        )

        assert row is not None, (
            f"Daemon did not surface in-flight run {run_id} within "
            f"{_LANDING_TIMEOUT_S}s of RunStarted. Live-data path is broken."
        )
        assert row.run_id == str(run_id)
        assert row.ended_at is None
        assert row.outcome is None
        assert row.dut_serial == "SN-LIVE-001"
        assert row.station_hostname == "bench-01.local"

    def test_run_ended_finalizes_row(self):
        """``RunStarted`` then ``RunEnded`` → row gets ``outcome`` and ``ended_at``."""
        store = EventStore()

        with RunsQuery():
            pass

        session_id = uuid4()
        run_id = uuid4()
        started = datetime.now(UTC)

        _emit_session(store, session_id)
        _emit_run_started(
            store,
            session_id=session_id,
            run_id=run_id,
            started_at=started,
        )
        store.flush()

        partial = _wait_for_run(
            str(run_id),
            predicate=lambda r: r.ended_at is None,
        )
        assert partial is not None, "RunStarted never landed"

        ended = datetime.now(UTC)
        _emit_run_ended(
            store,
            session_id=session_id,
            run_id=run_id,
            ended_at=ended,
            outcome="passed",
        )
        store.flush()
        store.close()

        finalized = _wait_for_run(
            str(run_id),
            predicate=lambda r: r.outcome == "passed" and r.ended_at is not None,
        )
        assert finalized is not None, (
            "RunEnded never updated the partial row — daemon's _upsert_run_ended "
            "is not firing or the watcher loop is missing the event."
        )

    def test_default_query_excludes_in_flight_partial(self, aborted_run_cleanup):
        """``include_incomplete=False`` (default) filters out partial rows.

        The streaming UPSERT and the parquet path write to the same
        table; the query layer is the gate that hides in-flight rows
        from analytics callers. Pin that the gate works.
        """
        store = EventStore()

        with RunsQuery():
            pass

        session_id = uuid4()
        run_id = uuid4()
        _emit_session(store, session_id)
        _emit_run_started(
            store,
            session_id=session_id,
            run_id=run_id,
            started_at=datetime.now(UTC),
        )
        aborted_run_cleanup.track(store, session_id, run_id)
        store.flush()

        # Wait for the partial row to land via include_incomplete=True.
        partial = _wait_for_run(
            str(run_id),
            predicate=lambda r: r.ended_at is None,
        )
        assert partial is not None, "RunStarted never landed (precondition)"

        # Now confirm the default (``include_incomplete=False``) hides it.
        with RunsQuery() as q:
            visible_ids = [r.run_id for r in q.list_recent(limit=50)]
        assert str(run_id) not in visible_ids


@pytest.mark.parametrize("outcome", ["passed", "failed", "errored"])
def test_run_ended_outcomes(outcome: str):
    """Each canonical ``RunEnded`` outcome lands in the table verbatim."""
    store = EventStore()

    with RunsQuery():
        pass

    session_id = uuid4()
    run_id = uuid4()
    started = datetime.now(UTC)

    _emit_session(store, session_id)
    _emit_run_started(
        store,
        session_id=session_id,
        run_id=run_id,
        started_at=started,
    )
    _emit_run_ended(
        store,
        session_id=session_id,
        run_id=run_id,
        ended_at=datetime.now(UTC),
        outcome=outcome,
    )
    store.flush()
    store.close()

    row = _wait_for_run(
        str(run_id),
        predicate=lambda r: r.outcome == outcome,
    )
    assert row is not None, f"outcome={outcome!r} never landed"
    assert row.outcome == outcome
    assert row.ended_at is not None
