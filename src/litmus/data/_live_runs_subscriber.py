"""LiveRunsSubscriber — events-daemon → in-memory run state, for the runs daemon.

Mirrors :class:`~litmus.data.backends.parquet.ParquetSubscriber`:
both consume the canonical event stream and project it into row
shape via a per-run :class:`~litmus.data.backends.parquet.EventAccumulator`.
They differ only in **what they do with the projected state**:

* ``ParquetSubscriber`` (in producer process) — writes the canonical
  parquet at ``RunEnded``.
* ``LiveRunsSubscriber`` (in runs daemon process) — keeps state in
  memory and exposes snapshots so the runs daemon's UNION views
  surface in-flight runs alongside finalized parquets.

The runs daemon owns one :class:`LiveRunsSubscriber` for its
lifetime. The subscriber:

1. Polls for the events daemon's state file with a retry loop —
   never spawns the events daemon itself, only attaches when one
   already exists. The events daemon should be spawned by the
   actual emitter (pytest plugin, ``StationConnection``,
   ``SlotRunner``, the UI's serve-level acquire), not by the runs
   daemon's subscription path.
2. Once attached, dispatches every event into a
   :class:`AccumulatorPool` keyed by ``run_id``.
3. Materializes the pool's snapshot into the runs daemon's
   ``inflight_runs`` / ``inflight_steps`` temp tables on every
   query (Flight pre-query hook).
4. Periodically sweeps the pool for orphaned in-flight runs whose
   producer pid is dead, finalizing them as canonical aborted
   parquets via the same :class:`ParquetSubscriber` path the test
   runner would have used.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from litmus.data._accumulator_pool import AccumulatorPool
from litmus.data._daemon_lifecycle import _pid_alive

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inflight Arrow schemas — mirror ``runs_persisted`` / ``steps_persisted``
# ---------------------------------------------------------------------------

# These describe the shape of the per-query Arrow snapshots that
# back the ``inflight_runs`` / ``inflight_steps`` relations the
# UNION views reference. Registered on the DuckDB connection via
# ``conn.register()`` (read-only metadata — no WAL write, no lock
# contention with the ingest thread). A pre-query hook that wrote
# DELETE+INSERT to TEMP tables previously serialized every read
# behind any concurrent ingest write, wedging the daemon at ~60 %
# CPU on lock-wait.
#
# Outcome here is plain VARCHAR — the persisted side carries the
# strict ``outcome_kind`` ENUM but DuckDB's ``UNION ALL`` permits
# string ↔ enum coercion at read time. Pool snapshots only emit
# strings already in the canonical Outcome enum (validated when
# the accumulator builds the row).
_INFLIGHT_RUNS_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("file_path", pa.string()),
        ("session_id", pa.string()),
        ("slot_id", pa.string()),
        ("dut_serial", pa.string()),
        ("dut_part_number", pa.string()),
        ("dut_lot_number", pa.string()),
        ("station_id", pa.string()),
        ("station_name", pa.string()),
        ("station_hostname", pa.string()),
        ("fixture_id", pa.string()),
        ("outcome", pa.string()),
        ("started_at", pa.timestamp("us", tz="UTC")),
        ("ended_at", pa.timestamp("us", tz="UTC")),
        ("num_measurements", pa.int32()),
        ("num_steps", pa.int32()),
        ("test_phase", pa.string()),
        ("product_id", pa.string()),
        ("operator_id", pa.string()),
        ("project_name", pa.string()),
    ]
)

_INFLIGHT_STEPS_SCHEMA = pa.schema(
    [
        ("run_id", pa.string()),
        ("step_index", pa.int32()),
        ("file_path", pa.string()),
        ("session_id", pa.string()),
        ("slot_id", pa.string()),
        ("step_name", pa.string()),
        ("step_path", pa.string()),
        ("outcome", pa.string()),
        ("started_at", pa.timestamp("us", tz="UTC")),
        ("ended_at", pa.timestamp("us", tz="UTC")),
        ("duration_s", pa.float64()),
        ("has_measurements", pa.bool_()),
        ("measurement_count", pa.int32()),
        ("vector_count", pa.int32()),
        ("markers", pa.string()),
        ("dut_serial", pa.string()),
        ("station_id", pa.string()),
    ]
)

# Allocated once at module load. ``register()`` with these keeps
# the UNION views resolvable when the accumulator pool is empty
# (the common case at idle).
_EMPTY_INFLIGHT_RUNS = pa.Table.from_pylist([], schema=_INFLIGHT_RUNS_SCHEMA)
_EMPTY_INFLIGHT_STEPS = pa.Table.from_pylist([], schema=_INFLIGHT_STEPS_SCHEMA)

_INFLIGHT_MEASUREMENTS_SCHEMA = pa.schema(
    [
        ("record_type", pa.string()),
        ("run_id", pa.string()),
        ("session_id", pa.string()),
        ("slot_id", pa.string()),
        ("run_started_at", pa.timestamp("us", tz="UTC")),
        ("run_ended_at", pa.timestamp("us", tz="UTC")),
        ("run_outcome", pa.string()),
        ("dut_serial", pa.string()),
        ("dut_part_number", pa.string()),
        ("dut_revision", pa.string()),
        ("dut_lot_number", pa.string()),
        ("product_id", pa.string()),
        ("product_name", pa.string()),
        ("product_revision", pa.string()),
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
        ("litmus_version", pa.string()),
        ("env_fingerprint", pa.string()),
        ("step_name", pa.string()),
        ("step_index", pa.int32()),
        ("step_path", pa.string()),
        ("step_outcome", pa.string()),
        ("step_started_at", pa.timestamp("us", tz="UTC")),
        ("step_ended_at", pa.timestamp("us", tz="UTC")),
        ("vector_index", pa.int64()),
        ("vector_attempt", pa.int64()),
        ("vector_outcome", pa.string()),
        ("measurement_name", pa.string()),
        ("measurement_value", pa.float64()),
        ("measurement_outcome", pa.string()),
        ("measurement_units", pa.string()),
        ("measurement_timestamp", pa.timestamp("us", tz="UTC")),
        ("limit_low", pa.float64()),
        ("limit_high", pa.float64()),
        ("limit_nominal", pa.float64()),
        ("limit_comparator", pa.string()),
        ("characteristic_id", pa.string()),
        ("spec_ref", pa.string()),
        ("dut_pin", pa.string()),
        ("fixture_connection", pa.string()),
        ("instrument_name", pa.string()),
        ("instrument_resource", pa.string()),
        ("instrument_channel", pa.string()),
    ]
)
_EMPTY_INFLIGHT_MEASUREMENTS = pa.Table.from_pylist([], schema=_INFLIGHT_MEASUREMENTS_SCHEMA)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _events_daemon_alive(events_dir: Path) -> bool:
    """Return True iff a live events daemon is running for ``events_dir``.

    Reads the events daemon's state file (``_duckdb.json``) and
    checks the recorded pid. **Inspection only, no spawn.** This is
    the gate for attaching: the runs daemon attaches only when an
    events daemon already exists, never spawns one.

    Why no spawn: the events daemon should be spawned by the
    actual emitter (pytest plugin, ``StationConnection``,
    ``SlotRunner``, the UI's serve-level acquire) — those processes
    need to write events anyway. The runs daemon only ever reads,
    so it has no reason to bring the events daemon up by itself.
    """
    state = events_dir / "_duckdb.json"
    if not state.exists():
        return False
    try:
        data = json.loads(state.read_text())
        pid = data.get("pid")
    except (json.JSONDecodeError, OSError):
        return False
    return isinstance(pid, int) and _pid_alive(pid)


def register_empty_inflight(conn: duckdb.DuckDBPyConnection) -> None:
    """Seed the daemon's connection with empty inflight relations.

    Called once at daemon startup, **before** the UNION views
    in :func:`_create_views` are defined. Without this, those
    views fail to compile because ``inflight_runs`` /
    ``inflight_steps`` aren't yet bound to anything.

    The Flight server's pre-query hook
    (:meth:`LiveRunsSubscriber.refresh`) re-registers these names
    on every query with the current accumulator-pool snapshot —
    pure metadata, no WAL write.
    """
    conn.register("inflight_runs", _EMPTY_INFLIGHT_RUNS)
    conn.register("inflight_steps", _EMPTY_INFLIGHT_STEPS)
    conn.register("inflight_measurements", _EMPTY_INFLIGHT_MEASUREMENTS)


# ---------------------------------------------------------------------------
# LiveRunsSubscriber
# ---------------------------------------------------------------------------


class LiveRunsSubscriber:
    """In-memory live-runs projection consumed by the runs daemon.

    Lifecycle: instantiate, ``start()`` at daemon spawn, register
    ``refresh`` as the Flight server's pre-query hook, ``stop()``
    at daemon shutdown.

    Internally:

    * ``self._pool`` — :class:`AccumulatorPool` keyed by run_id.
    * Retry-attach thread — polls every ``poll_interval_seconds``
      and calls ``EventStore.on_event`` once an events daemon
      appears. Exits the loop after the first successful attach.
    * Orphan-sweep thread — every ``orphan_interval_seconds``,
      checks open accumulators for dead producer pids (or
      wall-clock timeout) and finalizes them as aborted parquets
      via :class:`ParquetSubscriber`.
    """

    def __init__(
        self,
        results_dir: Path,
        *,
        poll_interval_seconds: float = 0.5,
        orphan_interval_seconds: float = 30.0,
        orphan_timeout_seconds: float = 3600.0,
    ) -> None:
        self._results_dir = results_dir
        self._poll_interval = poll_interval_seconds
        self._orphan_interval = orphan_interval_seconds
        self._orphan_timeout = orphan_timeout_seconds
        self._pool = AccumulatorPool()
        self._stop_event = threading.Event()
        self._unsubscribe: Callable[[], None] | None = None
        # Held EventStore reference — captured in _try_attach so the sweep
        # can emit synthesized RunEnded events back to the bus when an
        # orphan is finalized. Without this, the parquet side gets the
        # closure but the events DB never sees RunEnded, so the run stays
        # "active" forever in events_for_active_runs.
        self._event_store: Any = None
        self._attach_thread: threading.Thread | None = None
        self._sweep_thread: threading.Thread | None = None
        # Dirty flag — set when an event lands in the pool. ``refresh``
        # only re-registers the inflight Arrow tables when the flag is
        # set, eliminating the per-query ``conn.register()`` cost
        # (~280ms) when nothing has changed.
        self._dirty = True  # initial register required so views resolve

    # -- Lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Begin the attach-retry and orphan-sweep background threads."""
        self._attach_thread = threading.Thread(
            target=self._attach_loop, daemon=True, name="live-runs-attach"
        )
        self._attach_thread.start()
        self._sweep_thread = threading.Thread(
            target=self._sweep_loop, daemon=True, name="live-runs-sweep"
        )
        self._sweep_thread.start()

    def stop(self) -> None:
        """Signal both threads to exit and unsubscribe if attached."""
        self._stop_event.set()
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception as exc:  # noqa: BLE001 — defensive on shutdown
                logger.debug("cleanup failed (non-fatal): %s", exc)
            self._unsubscribe = None
        if self._event_store is not None:
            try:
                self._event_store.close()
            except Exception as exc:  # noqa: BLE001 — defensive on shutdown
                logger.debug("event_store close failed (non-fatal): %s", exc)
            self._event_store = None

    # -- Read path — runs daemon's pre-query hook --------------------------

    def refresh(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Re-bind ``inflight_runs`` / ``inflight_steps`` IF the pool changed.

        Flight server's pre-query hook calls this on every ``do_get``.
        ``self._dirty`` is set by ``_on_event`` whenever the
        AccumulatorPool absorbs an event (RunStarted/RunEnded/etc.),
        and is cleared here after a successful re-register. Steady
        state with no in-flight runs: ``_dirty`` stays False, zero
        ``conn.register()`` calls, pre_query_hook ≈ 0ms.

        Initial value of ``self._dirty`` is ``True`` so the first
        query after daemon startup gets a real register (the
        ``register_empty_inflight`` call at startup creates the
        sentinels; this hook keeps them current).
        """
        if not self._dirty:
            return

        # Clear before the register calls so any event that arrives
        # during conn.register() sets _dirty=True and is caught on the
        # next query — rather than being silently overwritten by a
        # trailing _dirty=False after the work is already done.
        self._dirty = False

        run_rows = self._pool.snapshot_run_rows()
        if run_rows:
            conn.register(
                "inflight_runs",
                pa.Table.from_pylist(run_rows, schema=_INFLIGHT_RUNS_SCHEMA),
            )
        else:
            conn.register("inflight_runs", _EMPTY_INFLIGHT_RUNS)

        step_rows = self._pool.snapshot_step_rows()
        if step_rows:
            conn.register(
                "inflight_steps",
                pa.Table.from_pylist(step_rows, schema=_INFLIGHT_STEPS_SCHEMA),
            )
        else:
            conn.register("inflight_steps", _EMPTY_INFLIGHT_STEPS)

        meas_rows = self._pool.snapshot_measurement_rows()
        if meas_rows:
            conn.register(
                "inflight_measurements",
                pa.Table.from_pylist(meas_rows, schema=_INFLIGHT_MEASUREMENTS_SCHEMA),
            )
        else:
            conn.register("inflight_measurements", _EMPTY_INFLIGHT_MEASUREMENTS)

    # -- Internals ----------------------------------------------------------

    def _attach_loop(self) -> None:
        """Poll for a live events daemon; attach the subscription on first sight."""
        events_dir = self._results_dir / "events"
        while not self._stop_event.is_set():
            if _events_daemon_alive(events_dir):
                if self._try_attach():
                    logger.info("LiveRunsSubscriber attached to events daemon")
                    return
            self._stop_event.wait(timeout=self._poll_interval)

    def _try_attach(self) -> bool:
        """Open an EventStore and subscribe; return True on success."""
        try:
            from litmus.data.event_store import EventStore

            event_store = EventStore(_results_dir=self._results_dir)
        except Exception as exc:  # noqa: BLE001
            logger.debug("LiveRunsSubscriber attach failed (will retry): %s", exc)
            return False

        try:
            # Live view only needs in-flight runs reconstructed; finalized
            # runs already live in parquet. ``replay="active_runs"`` bounds
            # the catch-up by active-run count rather than total event-log
            # size, so attach time stays flat as the event log grows.
            #
            # No ``since`` window: abandoned runs (RunStarted with no
            # RunEnded) are operator-visible signals, not noise to filter.
            # Test code that creates such runs as fixtures owns its own
            # teardown to emit a closing RunEnded.
            self._unsubscribe = event_store.on_event(
                self._on_event,
                replay="active_runs",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventStore.on_event failed (will retry): %s", exc)
            try:
                event_store.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("cleanup failed (non-fatal): %s", exc)
            return False
        # Hold the store so the sweep can emit synthesized RunEnded events
        # back to the bus when an orphan is finalized.
        self._event_store = event_store
        return True

    def _on_event(self, evt: dict[str, Any]) -> None:
        """Route one event into the pool. Best-effort; never propagate."""
        try:
            self._pool.dispatch(evt)
            # Mark inflight snapshot dirty so the next pre_query_hook
            # re-registers the Arrow tables. Steady-state queries with
            # no in-flight events skip the conn.register() cost entirely.
            self._dirty = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LiveRunsSubscriber pool dispatch failed for %s: %s",
                evt.get("event_type"),
                exc,
            )

    def _sweep_loop(self) -> None:
        """Periodic orphan finalization."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._orphan_interval)
            if self._stop_event.is_set():
                return
            try:
                self._sweep_once()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Orphan sweep iteration failed: %s", exc)

    def _sweep_once(self) -> None:
        """One pass of the orphan sweep.

        For each open accumulator (``RunStarted`` seen, ``RunEnded``
        not seen):

        * **pid liveness (primary)** — ``os.kill(pid, 0)``. Dead
          producer pid → finalize immediately (within ~30s of
          process death).
        * **wall-clock timeout (fallback)** — most recent event
          older than ``orphan_timeout_seconds``. Belt-and-suspenders
          for cases where pid liveness can't be checked
          (containerized, namespaced, etc.).

        On finalize: emit a synthesized ``RunEnded(outcome="aborted")``
        back to the EventStore so the events DB records the closure
        (otherwise ``events_for_active_runs`` would treat the run as
        live forever). Then write the canonical parquet via
        :class:`ParquetSubscriber`'s write path (so the file on disk
        is indistinguishable from a clean producer-side abort) and
        evict the accumulator. The daemon's existing parquet-ingest
        path picks the file up and moves the row to ``runs_persisted``.
        """
        output_dir = self._results_dir
        now = datetime.now(UTC)

        for run_id, acc, pid, last_event_at in self._pool.open_runs():
            is_orphan = False
            reason = ""
            if pid is not None:
                alive = _check_pid_liveness(pid)
                if alive is False:
                    is_orphan = True
                    reason = f"producer pid {pid} no longer exists"
            if not is_orphan and last_event_at is not None:
                if (now - last_event_at).total_seconds() > self._orphan_timeout:
                    is_orphan = True
                    reason = f"no events for {self._orphan_timeout:.0f}s"
            if not is_orphan:
                continue
            try:
                logger.info("Finalizing orphan run %s as aborted (%s)", run_id, reason)
                self._emit_synthetic_run_ended(acc, now)
                _write_orphan_parquet(acc, output_dir)
                self._pool.evict(run_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to finalize orphan run %s: %s", run_id, exc)

    def _emit_synthetic_run_ended(self, acc: Any, occurred_at: datetime) -> None:
        """Emit a ``RunEnded(outcome="aborted")`` for an orphan run.

        Pulls ``session_id`` and ``run_id`` from the accumulator's cached
        ``RunStarted`` event so the closure event matches the original
        run identity. Best-effort: if the EventStore reference isn't
        held (attach hadn't completed) or the emit fails, we log and
        continue with the parquet write — the events-DB closure is
        important for query consistency, but not so important that it
        should block the parquet-side cleanup.
        """
        if self._event_store is None:
            logger.debug("No EventStore reference; skipping synthesized RunEnded emit")
            return
        run_started = getattr(acc, "_run_started", None)
        if run_started is None:
            logger.debug("Accumulator has no RunStarted; skipping synthesized RunEnded")
            return
        try:
            from litmus.data.events import RunEnded

            self._event_store.emit(
                RunEnded(
                    session_id=run_started.session_id,
                    run_id=run_started.run_id,
                    occurred_at=occurred_at,
                    outcome="aborted",
                )
            )
        except Exception as exc:  # noqa: BLE001 — best-effort sweep emit
            logger.warning(
                "Failed to emit synthesized RunEnded for orphan run %s: %s",
                run_started.run_id,
                exc,
            )


# ---------------------------------------------------------------------------
# Helpers used by the sweep
# ---------------------------------------------------------------------------


def _check_pid_liveness(pid: int) -> bool | None:
    """``True`` if pid exists, ``False`` if not, ``None`` if we can't tell."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def _write_orphan_parquet(acc: Any, output_dir: Path) -> None:
    """Write the accumulator's state as the canonical aborted parquet.

    Uses :class:`ParquetSubscriber`'s write path so the file is
    indistinguishable from one a clean producer-side close would
    have produced. Daemon's ingest path moves it to
    ``runs_persisted`` on next ``_on_put`` / poll cycle.
    """
    # Lazy import to avoid producer → daemon import cycle.
    from litmus.data.backends.parquet import ParquetSubscriber

    sub = ParquetSubscriber(output_dir)
    sub.absorb_from_accumulator(acc)
    sub._write(outcome="aborted")
