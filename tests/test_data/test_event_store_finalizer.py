"""Finalizer safety net for EventStore (weakref.finalize path).

Verifies the weakref.finalize safety net on EventStore:
- The finalizer is registered on construction and is alive.
- explicit close() detaches the finalizer (so it never double-runs).
- When the store has no emit/on_event calls (no bound-method cycles), del +
  gc.collect() collects the object and fires the finalizer.

Note on bound-method cycles: calling emit() stores an EventLog with
on_emit/on_flush callbacks that are bound methods of the EventStore.  Those
callbacks keep the store alive via a reference cycle.  After close() clears
_event_logs the cycle is broken; at interpreter exit weakref.finalize also
fires regardless (it is atexit-level).  The GC test here avoids emit() so
the cycle never forms.

Isolation: uses the canonical data dir (resolve_data_dir()) with unique
session_ids, per CLAUDE.md's daemon-spawning constraint.
"""

from __future__ import annotations

import gc
import weakref

from litmus.data.data_dir import resolve_data_dir
from litmus.data.event_store import EventStore


def test_finalizer_registered_on_construction() -> None:
    """EventStore registers a live finalizer in __init__."""
    store = EventStore(_data_dir=resolve_data_dir())
    try:
        assert store._finalizer.alive, "finalizer must be alive after __init__"
    finally:
        store.close()


def test_close_detaches_finalizer() -> None:
    """explicit close() detaches the finalizer so it never double-runs."""
    store = EventStore(_data_dir=resolve_data_dir())
    store.close()
    assert not store._finalizer.alive, "finalizer must be detached after close()"


def test_close_is_idempotent() -> None:
    """close() is safe to call multiple times (idempotent)."""
    store = EventStore(_data_dir=resolve_data_dir())
    store.close()
    store.close()  # must not raise


def test_finalizer_fires_on_gc_when_no_cycles() -> None:
    """When neither emit() nor on_event() was called, no bound-method cycles
    exist.  del + gc.collect() collects the store and fires the finalizer.

    This is the primary GC-path for a constructed-but-unused EventStore (e.g.
    a Jupyter cell that constructs the store and then re-runs the cell, or
    simply forgets close()).
    """
    data_dir = resolve_data_dir()

    fired: list[bool] = []

    def _make_store_ref() -> weakref.ref:
        store = EventStore(_data_dir=data_dir)
        # No emit(), no on_event() — no bound-method back-references
        assert store._watcher_thread is None, "no thread before on_event()"
        weakref.finalize(store, fired.append, True)
        ref = weakref.ref(store)
        return ref

    ref = _make_store_ref()
    gc.collect()

    assert ref() is None, (
        "EventStore was not collected after del + gc.collect() — a reference cycle is preventing GC"
    )
    assert fired == [True], "finalizer must have fired when the store was collected"
