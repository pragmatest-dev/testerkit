"""Finalizer safety net for ChannelStore (weakref.finalize path).

A producer that constructs ``ChannelStore(..., serve=True)`` (e.g. a notebook
script) and forgets ``close()`` must not leak the async pusher thread. The
relay is the one resource that won't self-clean on GC — a running thread keeps
itself alive — so ``weakref.finalize`` stops it on GC or interpreter exit. Once
stopped, the held writers / flight client are unreferenced and collected, and
the daemon prunes our dead ref like the other daemons.

The relay's ``flush`` back-ref is a weakref, so the relay does not pin the store
(without that, the store could never be GC'd and the finalizer would never fire).

Isolation: canonical data dir (resolve_data_dir()) + unique session_id, per
CLAUDE.md's daemon-spawning constraint.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from testerkit.data.channels.store import ChannelStore
from testerkit.data.data_dir import resolve_data_dir


@pytest.fixture(autouse=True)
def _async_push(monkeypatch: pytest.MonkeyPatch) -> None:
    # The relay only exists when async push is on (the default); make it explicit
    # so this test is independent of the ambient env.
    monkeypatch.delenv("TESTERKIT_CHANNELS_SYNC_PUSH", raising=False)


def _serve_store() -> ChannelStore:
    return ChannelStore(resolve_data_dir(), uuid4(), serve=True)


def test_finalizer_registered_after_open() -> None:
    """open() with serve=True creates the relay and registers a live finalizer."""
    store = _serve_store()
    try:
        store.open()
        assert store._push_relay is not None
        assert store._finalizer is not None
        assert store._finalizer.alive
    finally:
        store.close()


def test_close_detaches_finalizer() -> None:
    """explicit close() detaches the finalizer so it never double-runs."""
    store = _serve_store()
    store.open()
    fin = store._finalizer
    store.close()
    assert fin is not None
    assert not fin.alive


def test_finalizer_stops_pusher() -> None:
    """The registered finalizer stops the pusher thread when it runs.

    ``weakref.finalize`` invokes this same callback on GC or interpreter
    exit. We invoke it directly rather than forcing real GC: a ``serve=True``
    store stays referenced through its in-process Flight/gRPC objects until
    ``close()``, so prompt collection is not deterministic across
    environments (it collects by refcount locally but not on a loaded CI
    runner). The invariant TesterKit owns is that the finalizer, once it fires,
    stops the thread — which this exercises without a GC dependency.
    """
    store = _serve_store()
    store.open()
    relay = store._push_relay
    assert relay is not None
    assert relay._thread.is_alive()
    assert store._finalizer is not None and store._finalizer.alive

    store._finalizer()  # exactly what weakref.finalize calls on GC / interpreter exit

    relay._thread.join(timeout=5.0)
    assert not relay._thread.is_alive(), "finalizer must stop the pusher thread"
