"""Per-run event projection pool for the runs daemon's live overlay.

Owns the in-memory, in-flight projection of run events that the
runs daemon serves alongside its parquet-finalized rows.

One :class:`~litmus.data.backends.parquet.EventAccumulator`
per active ``run_id`` accumulates events until either:

* The producer's parquet lands and the parquet-ingest path
  inserts the canonical row into ``runs_persisted`` (overlay
  is suppressed for that run_id by the UNION view; eviction
  happens lazily).
* The orphan sweep detects a dead producer (pid no longer
  exists) or wall-clock timeout, synthesizes a
  ``RunEnded(outcome="aborted")`` event into the
  accumulator, and calls ``finalize_to_parquet()`` on it —
  which writes the canonical aborted parquet just as the
  test runner's ``ParquetSubscriber`` would.

The pool is the runs daemon's "head block" in the
Prometheus / LSM-tree / materialized-view pattern: in-memory
projection of recent events, regenerable from the events
daemon's IPC log via replay on daemon respawn.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from litmus.data.backends.parquet import EventAccumulator
from litmus.data.events import (
    InstrumentConnected,
    MeasurementRecorded,
    RunEnded,
    RunStarted,
    SessionStarted,
    StepEnded,
    StepsDiscovered,
    StepStarted,
)

logger = logging.getLogger(__name__)


# Map ``event_type`` strings to Pydantic event classes for dict→typed
# conversion. The runs daemon receives events as dicts from the
# events daemon over Flight; the accumulator works with typed events.
_EVENT_CLASSES: dict[str, type] = {
    "session.started": SessionStarted,
    "run.started": RunStarted,
    "run.ended": RunEnded,
    "test.steps_discovered": StepsDiscovered,
    "test.step_started": StepStarted,
    "test.step_ended": StepEnded,
    "test.measurement": MeasurementRecorded,
    "instrument.connected": InstrumentConnected,
}


class AccumulatorPool:
    """Thread-safe pool of per-run :class:`EventAccumulator` instances.

    All public methods are safe to call from the events-subscription
    watcher thread (which feeds events) and from the Flight server
    threads (which read snapshots) concurrently.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._accs: dict[str, EventAccumulator] = {}  # run_id → accumulator
        # Producer pid per session_id, captured from SessionStarted.
        # Used by the orphan sweep for liveness checks.
        self._session_pid: dict[str, int] = {}
        # Most recent event timestamp per run_id — wall-clock fallback
        # for the orphan sweep when pid liveness check is unavailable.
        self._last_event_at: dict[str, datetime] = {}
        # session_id per run_id, so the orphan sweep can resolve a
        # run_id back to its producer pid.
        self._run_session: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Write path — fed by the events-daemon subscription
    # ------------------------------------------------------------------

    def dispatch(self, evt_dict: dict[str, Any]) -> None:
        """Route one event (dict from the events daemon) into the pool.

        ``SessionStarted`` is captured for pid tracking but not
        routed to a per-run accumulator (it has no ``run_id``).
        Run-bearing events route into the accumulator keyed by
        ``run_id``, creating it on first sight.
        """
        et = evt_dict.get("event_type")
        cls = _EVENT_CLASSES.get(str(et) if et else "")
        if cls is None:
            return
        try:
            typed = cls.model_validate(evt_dict)
        except Exception as exc:  # noqa: BLE001 — bad event must not kill watcher
            logger.debug("Pool skipping unparseable event %s: %s", et, exc)
            return

        if isinstance(typed, SessionStarted):
            session_id = str(typed.session_id) if typed.session_id else None
            if session_id and typed.pid:
                with self._lock:
                    self._session_pid[session_id] = typed.pid
            return

        run_id = str(getattr(typed, "run_id", None) or "")
        if not run_id:
            return
        session_id = str(getattr(typed, "session_id", None) or "")
        with self._lock:
            acc = self._accs.setdefault(run_id, EventAccumulator())
            acc.on_event(typed)
            self._last_event_at[run_id] = datetime.now(UTC)
            if session_id:
                self._run_session[run_id] = session_id

    # ------------------------------------------------------------------
    # Read path — snapshot for the runs daemon's UNION views
    # ------------------------------------------------------------------

    def snapshot_run_rows(self) -> list[dict[str, Any]]:
        """Return one run row per active accumulator (None entries dropped)."""
        with self._lock:
            return [r for a in self._accs.values() if (r := a.snapshot_run_row())]

    def snapshot_step_rows(self) -> list[dict[str, Any]]:
        """Flatten step rows from every active accumulator."""
        with self._lock:
            return [r for a in self._accs.values() for r in a.snapshot_step_rows()]

    def snapshot_measurement_rows(self) -> list[dict[str, Any]]:
        """Flatten measurement rows from every active accumulator."""
        with self._lock:
            return [r for a in self._accs.values() for r in a.snapshot_measurement_rows()]

    # ------------------------------------------------------------------
    # Orphan sweep + lifecycle
    # ------------------------------------------------------------------

    def open_runs(self) -> list[tuple[str, EventAccumulator, int | None, datetime | None]]:
        """Return ``(run_id, accumulator, pid_or_None, last_event_or_None)`` for open runs.

        Open = ``RunStarted`` seen, ``RunEnded`` not seen.
        The orphan sweep iterates this and decides per-entry whether
        to finalize.
        """
        out: list[tuple[str, EventAccumulator, int | None, datetime | None]] = []
        with self._lock:
            for run_id, acc in self._accs.items():
                if not acc._run_started or acc._run_ended is not None:
                    continue
                session_id = self._run_session.get(run_id)
                pid = self._session_pid.get(session_id) if session_id else None
                last = self._last_event_at.get(run_id)
                out.append((run_id, acc, pid, last))
        return out

    def evict(self, run_id: str) -> EventAccumulator | None:
        """Drop the accumulator for ``run_id`` and return it (or ``None``)."""
        with self._lock:
            self._last_event_at.pop(run_id, None)
            self._run_session.pop(run_id, None)
            return self._accs.pop(run_id, None)

    def has(self, run_id: str) -> bool:
        """Whether the pool currently holds an accumulator for ``run_id``."""
        with self._lock:
            return run_id in self._accs
