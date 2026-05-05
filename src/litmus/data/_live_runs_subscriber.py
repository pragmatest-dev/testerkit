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
# DuckDB column lists for the inflight temp tables
# ---------------------------------------------------------------------------


_INFLIGHT_RUN_COLS = (
    "run_id, file_path, steps_file_path, session_id, slot_id, "
    "dut_serial, dut_part_number, station_id, station_name, "
    "station_hostname, fixture_id, outcome, started_at, ended_at, "
    "num_measurements, num_steps, test_phase, product_id, "
    "operator_id, project_name"
)


_INFLIGHT_STEP_COLS = (
    "run_id, step_index, file_path, session_id, slot_id, "
    "step_name, step_path, outcome, started_at, ended_at, "
    "duration_s, has_measurements, measurement_count, vector_count, "
    "markers, dut_serial, station_id"
)


# Outcome strings from older / non-canonical events that would fail
# the daemon's ``outcome_kind`` ENUM cast. ``TRY_CAST`` returns NULL
# instead of erroring — same effect as not having received an outcome
# yet, which is the right in-flight semantics anyway.
_RUN_SELECT_EXPR = (
    "run_id, file_path, steps_file_path, session_id, slot_id, "
    "dut_serial, dut_part_number, station_id, station_name, "
    "station_hostname, fixture_id, "
    "TRY_CAST(outcome AS outcome_kind) AS outcome, "
    "started_at, ended_at, "
    "num_measurements, num_steps, test_phase, product_id, "
    "operator_id, project_name"
)


_STEP_SELECT_EXPR = (
    "run_id, step_index, file_path, session_id, slot_id, "
    "step_name, step_path, "
    "TRY_CAST(outcome AS outcome_kind) AS outcome, "
    "started_at, ended_at, "
    "duration_s, has_measurements, measurement_count, vector_count, "
    "markers, dut_serial, station_id"
)


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


def create_inflight_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Create empty TEMP tables matching the persistent schemas.

    The Flight server's pre-query hook (``LiveRunsSubscriber.refresh``)
    repopulates these from the accumulator pool snapshot before each
    query. Schema is copied from the on-disk persistent tables via
    ``WHERE 1=0`` so the UNION views' types align exactly.
    """
    conn.execute(
        "CREATE OR REPLACE TEMP TABLE inflight_runs AS SELECT * FROM runs_persisted WHERE 1=0"
    )
    conn.execute(
        "CREATE OR REPLACE TEMP TABLE inflight_steps AS SELECT * FROM steps_persisted WHERE 1=0"
    )


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
        poll_interval_seconds: float = 5.0,
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
        self._attach_thread: threading.Thread | None = None
        self._sweep_thread: threading.Thread | None = None

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
            except Exception:  # noqa: BLE001 — defensive on shutdown
                pass
            self._unsubscribe = None

    # -- Read path — runs daemon's pre-query hook --------------------------

    def refresh(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Reload the inflight TEMP tables from the pool snapshot.

        Flight server's pre-query hook calls this on every
        ``do_get`` so the UNION views see a current snapshot. Cost
        is O(in-flight rows), typically tiny.

        ``DELETE`` + ``INSERT`` rather than ``CREATE OR REPLACE``
        because the temp tables are referenced by the
        ``runs`` / ``steps`` views — replacing them would
        invalidate the views.
        """
        conn.execute("DELETE FROM inflight_runs")
        conn.execute("DELETE FROM inflight_steps")

        run_rows = self._pool.snapshot_run_rows()
        if run_rows:
            conn.register("_pool_run_rows", pa.Table.from_pylist(run_rows))
            try:
                conn.execute(
                    f"INSERT INTO inflight_runs ({_INFLIGHT_RUN_COLS}) "
                    f"SELECT {_RUN_SELECT_EXPR} FROM _pool_run_rows"
                )
            finally:
                conn.unregister("_pool_run_rows")

        step_rows = self._pool.snapshot_step_rows()
        if step_rows:
            conn.register("_pool_step_rows", pa.Table.from_pylist(step_rows))
            try:
                conn.execute(
                    f"INSERT INTO inflight_steps ({_INFLIGHT_STEP_COLS}) "
                    f"SELECT {_STEP_SELECT_EXPR} FROM _pool_step_rows"
                )
            finally:
                conn.unregister("_pool_step_rows")

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
            self._unsubscribe = event_store.on_event(self._on_event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventStore.on_event failed (will retry): %s", exc)
            try:
                event_store.close()
            except Exception:  # noqa: BLE001
                pass
            return False
        return True

    def _on_event(self, evt: dict[str, Any]) -> None:
        """Route one event into the pool. Best-effort; never propagate."""
        try:
            self._pool.dispatch(evt)
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

        On finalize: synthesize a ``RunEnded(outcome="aborted")``,
        write the canonical parquet via :class:`ParquetSubscriber`'s
        write path (so the file on disk is indistinguishable from a
        clean producer-side abort), and evict the accumulator. The
        daemon's existing parquet-ingest path picks the file up and
        moves the row to ``runs_persisted``.
        """
        output_dir = self._results_dir
        now = datetime.now(UTC)

        for run_id, acc, pid, last_event_at in self._pool.open_runs():
            is_orphan = False
            reason = ""
            if pid is not None:
                alive = _pid_liveness(pid)
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
                _write_orphan_parquet(acc, output_dir)
                self._pool.evict(run_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to finalize orphan run %s: %s", run_id, exc)


# ---------------------------------------------------------------------------
# Helpers used by the sweep
# ---------------------------------------------------------------------------


def _pid_liveness(pid: int) -> bool | None:
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
    sub._run_started = acc._run_started
    sub._instruments = list(acc._instruments)
    sub._measurement_events = list(acc._measurement_events)
    sub._step_starts = dict(acc._step_starts)
    sub._step_ends = dict(acc._step_ends)
    sub._collected_items = list(acc._collected_items)
    sub._markers_by_node = dict(acc._markers_by_node)
    sub._write(outcome="aborted")
