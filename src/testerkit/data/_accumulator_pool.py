"""Per-run event projection pool for the runs daemon's materializer.

Owns the in-memory, in-flight projection of run events that the
runs daemon serves alongside its parquet-finalized rows AND uses to
write parquets at ``RunEnded`` time.

One :class:`~testerkit.data.backends._event_accumulator.EventAccumulator`
per active ``run_id`` accumulates events until either:

* The daemon's dispatch handler sees ``RunEnded`` (real, or
  synthesized by the orphan sweep). It writes the canonical
  parquet via :func:`~testerkit.data.backends.parquet.materialize_run_to_parquet`,
  ingests into ``runs_materialized`` / ``steps_materialized`` /
  ``measurements_materialized``, then emits ``RunMaterialized``
  to the events bus. Receipt of ``RunMaterialized`` evicts the
  pool entry.
* The orphan sweep detects a dead producer (pid no longer
  exists) or wall-clock timeout, synthesizes a
  ``RunEnded(outcome="aborted")`` event into the events bus.
  The synthetic event flows through the same dispatch path
  above — same materialize → emit → evict sequence.

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

import pyarrow as pa

from testerkit.data.backends._event_accumulator import EventAccumulator
from testerkit.data.events import (
    InstrumentConnected,
    InstrumentReserved,
    MeasurementRecorded,
    Observation,
    RunEnded,
    RunMaterialized,
    RunStarted,
    SessionEnded,
    SessionStarted,
    StepEnded,
    StepsDiscovered,
    StepStarted,
    VectorEnded,
    VectorStarted,
)

logger = logging.getLogger(__name__)


# Map ``event_type`` strings to Pydantic event classes for dict→typed
# conversion. The runs daemon receives events as dicts from the
# events daemon over Flight; the accumulator works with typed events.
_EVENT_CLASSES: dict[str, type] = {
    "session.started": SessionStarted,
    "session.ended": SessionEnded,
    "run.started": RunStarted,
    "run.ended": RunEnded,
    "run.materialized": RunMaterialized,
    "test.steps_discovered": StepsDiscovered,
    "test.step_started": StepStarted,
    "test.step_ended": StepEnded,
    "test.measurement": MeasurementRecorded,
    "test.observation": Observation,
    "test.vector_started": VectorStarted,
    "test.vector_ended": VectorEnded,
    "fixture.instrument_connected": InstrumentConnected,
    "instrument.reserved": InstrumentReserved,
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
        # Producer pid per session_id, captured from SessionStarted and cleared
        # on SessionEnded. The RUN orphan sweep resolves a run back to its
        # producer pid through this (pid-death force-closes a run).
        self._session_pid: dict[str, int] = {}
        # Most recent event timestamp per run_id — wall-clock fallback
        # for the orphan sweep when pid liveness check is unavailable.
        self._last_event_at: dict[str, datetime] = {}
        # session_id per run_id, so the orphan sweep can resolve a
        # run_id back to its producer pid.
        self._run_session: dict[str, str] = {}
        # Monotonic generation counter — bumped under ``_lock`` on every
        # state change (dispatch / evict). The overlay-sync thread uses it
        # only as a cheap "has anything changed?" wake/idle signal.
        self._generation = 0
        # Per-run delta since the last ``take_delta`` drain. The overlay is
        # maintained incrementally by ONE background sync thread off the
        # read path (no per-query refresh): ``dispatch`` dirties a run,
        # ``evict`` evicts it. The sync thread drains this and rewrites only
        # the affected runs' overlay rows — O(changed runs), not O(pool).
        self._dirty: set[str] = set()
        self._evicted: set[str] = set()

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

        if isinstance(typed, SessionEnded):
            # A cleanly-ended session is no longer open — drop it so the orphan
            # sweep can't self-heal (re-emit SessionEnded for) it.
            session_id = str(typed.session_id) if typed.session_id else None
            if session_id:
                self.mark_session_ended(session_id)
            return

        if isinstance(typed, RunMaterialized):
            # Materialization signal — handled by the daemon's _on_event
            # wrapper (it evicts the pool entry). Don't route into an
            # accumulator: there's nothing for the accumulator to absorb,
            # and creating one on first sight would leak state for a run
            # that just finished.
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
            # Mark this run dirty + bump generation under the same lock as
            # the state change, so the sync thread's next drain snapshots
            # this event. Atomic with the dispatch.
            self._dirty.add(run_id)
            self._generation += 1

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

    def mark_session_ended(self, session_id: str) -> None:
        """Forget a session's producer pid (on SessionEnded). Keeps the run
        sweep's pid map from carrying a closed session's producer."""
        with self._lock:
            self._session_pid.pop(session_id, None)

    def evict(self, run_id: str) -> EventAccumulator | None:
        """Drop the accumulator for ``run_id`` and return it (or ``None``)."""
        with self._lock:
            self._last_event_at.pop(run_id, None)
            self._run_session.pop(run_id, None)
            acc = self._accs.pop(run_id, None)
            if acc is not None:
                # Evicted runs must have their overlay rows removed; drop
                # any pending dirty mark (the run is gone, not changed).
                self._dirty.discard(run_id)
                self._evicted.add(run_id)
                self._generation += 1
            return acc

    def take_delta(
        self,
    ) -> tuple[set[str], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]] | None:
        """Drain the per-run delta since the last call; ``None`` if nothing changed.

        Returns ``(touched_run_ids, run_rows, step_rows, meas_rows)``:

        * ``touched_run_ids`` — every run whose overlay rows must be cleared
          (dirty runs to be re-inserted + evicted runs to be removed).
        * ``run_rows`` / ``step_rows`` / ``meas_rows`` — the fresh snapshot
          rows for the still-present dirty runs (flattened, each carries
          ``run_id``). Evicted / not-yet-started runs contribute no rows, so
          a clear-then-reinsert leaves them gone.

        Snapshot + clear happen under the pool lock, so a run that changes
        after the drain is simply re-dirtied for the next drain — never lost.
        The overlay-sync thread is the sole caller; queries never refresh.
        """
        with self._lock:
            if not self._dirty and not self._evicted:
                return None
            dirty = self._dirty
            evicted = self._evicted
            self._dirty = set()
            self._evicted = set()
            run_rows: list[dict[str, Any]] = []
            step_rows: list[dict[str, Any]] = []
            meas_rows: list[dict[str, Any]] = []
            for rid in dirty:
                acc = self._accs.get(rid)
                if acc is None:
                    evicted.add(rid)  # vanished between dispatch and drain
                    continue
                if rr := acc.snapshot_run_row():
                    run_rows.append(rr)
                step_rows.extend(acc.snapshot_step_rows())
                meas_rows.extend(acc.snapshot_measurement_rows())
            return dirty | evicted, run_rows, step_rows, meas_rows

    def generation(self) -> int:
        """Current monotonic generation — bumps on every pool state change.

        A cheap read for the lock-free fast path of the inflight refresh:
        compare against the last-refreshed value and skip the snapshot
        build entirely when nothing has changed.
        """
        return self._generation

    def has(self, run_id: str) -> bool:
        """Whether the pool currently holds an accumulator for ``run_id``."""
        with self._lock:
            return run_id in self._accs

    def get(self, run_id: str) -> EventAccumulator | None:
        """Return the accumulator for ``run_id`` without removing it."""
        with self._lock:
            return self._accs.get(run_id)


# ---------------------------------------------------------------------------
# Inflight Arrow schemas — mirror ``runs_materialized`` / ``steps_materialized``
# ---------------------------------------------------------------------------
#
# Describe the shape of the per-query Arrow snapshots that back the
# ``inflight_runs`` / ``inflight_steps`` relations the UNION views
# reference. Registered on the DuckDB connection via ``conn.register()``
# (read-only metadata — no WAL write, no lock contention with the
# ingest thread). A pre-query hook that wrote DELETE+INSERT to TEMP
# tables previously serialized every read behind any concurrent
# ingest write, wedging the daemon at ~60 % CPU on lock-wait.
#
# Outcome here is plain VARCHAR — the materialized side carries the
# strict ``outcome_kind`` ENUM but DuckDB's ``UNION ALL`` permits
# string ↔ enum coercion at read time. Pool snapshots only emit
# strings already in the canonical Outcome enum (validated when
# the accumulator builds the row).
INFLIGHT_RUNS_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("file_path", pa.string()),
        ("session_id", pa.string()),
        ("site_index", pa.int64()),
        ("site_name", pa.string()),
        ("uut_serial_number", pa.string()),
        ("uut_part_number", pa.string()),
        ("uut_revision", pa.string()),
        ("uut_lot_number", pa.string()),
        ("station_id", pa.string()),
        ("station_name", pa.string()),
        ("station_hostname", pa.string()),
        ("station_type", pa.string()),
        ("station_location", pa.string()),
        ("fixture_id", pa.string()),
        ("outcome", pa.string()),
        ("started_at", pa.timestamp("us", tz="UTC")),
        ("ended_at", pa.timestamp("us", tz="UTC")),
        ("num_measurements", pa.int32()),
        ("num_steps", pa.int32()),
        ("test_phase", pa.string()),
        ("part_id", pa.string()),
        ("part_name", pa.string()),
        ("part_revision", pa.string()),
        ("operator_id", pa.string()),
        ("operator_name", pa.string()),
        ("project_name", pa.string()),
        ("git_commit", pa.string()),
        ("git_branch", pa.string()),
        ("git_remote", pa.string()),
    ]
)

INFLIGHT_STEPS_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("step_index", pa.int32()),
        ("file_path", pa.string()),
        ("session_id", pa.string()),
        ("site_index", pa.int64()),
        ("site_name", pa.string()),
        ("step_name", pa.string()),
        ("step_path", pa.string()),
        ("vector_index", pa.int64()),
        ("vector_outer_index", pa.int64()),
        # The vector's OWN 0-based retry (NULL on a logical-step row). Part of
        # the ``vectors_materialized`` grain key (full snowflake, 0.3.1
        # phase 6), so the overlay must carry it too or a live retried vector
        # can't be matched 1:1 against its finalized row.
        ("vector_retry", pa.int64()),
        ("outcome", pa.string()),
        ("started_at", pa.timestamp("us", tz="UTC")),
        ("ended_at", pa.timestamp("us", tz="UTC")),
        ("duration_s", pa.float64()),
        ("step_retry", pa.int64()),
        ("measurement_count", pa.int32()),
        ("markers", pa.string()),
        ("uut_serial_number", pa.string()),
        ("station_id", pa.string()),
        # Two unprefixed maps — replaces the old merged, ``in_``/``out_``-
        # prefixed ``dynamic_attrs`` MAP (projection-normalization, 0.3.1).
        # The overlay has no long-EAV table to join against for live rows, so
        # it keeps building these directly from the accumulator's in-memory
        # dicts (cheap, no join) — see ``_event_accumulator._pack_io_maps``.
        ("inputs_map", pa.map_(pa.string(), pa.string())),
        ("outputs_map", pa.map_(pa.string(), pa.string())),
    ]
)

INFLIGHT_MEASUREMENTS_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("session_id", pa.string()),
        ("site_index", pa.int64()),
        ("site_name", pa.string()),
        ("run_started_at", pa.timestamp("us", tz="UTC")),
        ("run_ended_at", pa.timestamp("us", tz="UTC")),
        ("run_outcome", pa.string()),
        ("uut_serial_number", pa.string()),
        ("uut_part_number", pa.string()),
        ("uut_revision", pa.string()),
        ("uut_lot_number", pa.string()),
        ("part_id", pa.string()),
        ("part_name", pa.string()),
        ("part_revision", pa.string()),
        ("station_id", pa.string()),
        ("station_name", pa.string()),
        ("station_hostname", pa.string()),
        ("station_type", pa.string()),
        ("station_location", pa.string()),
        ("fixture_id", pa.string()),
        ("test_phase", pa.string()),
        ("project_name", pa.string()),
        ("operator_id", pa.string()),
        ("operator_name", pa.string()),
        ("git_commit", pa.string()),
        ("git_branch", pa.string()),
        ("git_remote", pa.string()),
        ("python_version", pa.string()),
        ("testerkit_version", pa.string()),
        ("env_fingerprint", pa.string()),
        ("step_name", pa.string()),
        ("step_index", pa.int32()),
        ("step_path", pa.string()),
        # Enclosing-step retry — a coordinate on measurements_materialized
        # (full snowflake, 0.3.1); carried so a live measurement matches its
        # finalized row and the ``measurements`` view exposes it on both sides.
        ("step_retry", pa.int64()),
        ("step_outcome", pa.string()),
        ("step_started_at", pa.timestamp("us", tz="UTC")),
        ("step_ended_at", pa.timestamp("us", tz="UTC")),
        ("vector_index", pa.int64()),
        ("vector_outer_index", pa.int64()),
        ("vector_retry", pa.int64()),
        ("vector_outcome", pa.string()),
        ("measurement_name", pa.string()),
        ("measurement_value", pa.float64()),
        ("measurement_outcome", pa.string()),
        ("measurement_unit", pa.string()),
        ("measurement_timestamp", pa.timestamp("us", tz="UTC")),
        ("limit_low", pa.float64()),
        ("limit_high", pa.float64()),
        ("limit_nominal", pa.float64()),
        ("limit_comparator", pa.string()),
        ("characteristic_id", pa.string()),
        ("spec_ref", pa.string()),
        ("uut_pin", pa.string()),
        ("fixture_connection", pa.string()),
        ("instrument_name", pa.string()),
        ("instrument_resource", pa.string()),
        ("instrument_channel", pa.string()),
        # Two unprefixed maps — see INFLIGHT_STEPS_SCHEMA above.
        ("inputs_map", pa.map_(pa.string(), pa.string())),
        ("outputs_map", pa.map_(pa.string(), pa.string())),
    ]
)

# Allocated once at module load. ``conn.register()`` with these keeps
# the UNION views resolvable when the pool is empty (the common case
# at idle).
EMPTY_INFLIGHT_RUNS = pa.Table.from_pylist([], schema=INFLIGHT_RUNS_SCHEMA)
EMPTY_INFLIGHT_STEPS = pa.Table.from_pylist([], schema=INFLIGHT_STEPS_SCHEMA)
EMPTY_INFLIGHT_MEASUREMENTS = pa.Table.from_pylist([], schema=INFLIGHT_MEASUREMENTS_SCHEMA)
