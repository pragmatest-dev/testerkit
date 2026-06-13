"""Power-user channel-write surface — items 7 / 8.

The test-author shape is :func:`stream` (a bare verb exposed via the
``stream`` pytest fixture; see :mod:`litmus.pytest_plugin`). Power
users — driver code, multi-channel sweeps, cross-vector recorders —
reach for these explicit forms:

- :func:`write` — one-shot append-a-sample to a named channel
- :func:`stream` — context-managed sink with ``.write(sample)`` /
  ``.close()``

Both route to the active ChannelStore via
:func:`litmus.execution._state.get_channel_store`. The channel kind
is pinned on first write per the C2 kind-registry rule; subsequent
writes that violate the type-stability raise.

Per §3 of the design doc, ``channels.write`` is semantically
identical to the bare ``stream`` verb — one channel write per call.
The split is API clarity: ``stream(...)`` for test-author intent
("push this sample"); ``channels.write(...)`` for power-user
explicit ("write to this specific store, no auto-association").
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow as pa

from litmus.data.channels import flight_manager
from litmus.data.channels.client import ChannelClient, channel_query_client
from litmus.data.channels.models import ChannelSample, SubscribePolicy
from litmus.data.data_dir import resolve_data_dir
from litmus.data.ref import make_channel_uri
from litmus.execution._state import (
    get_channel_store,
    get_current_logger,
    no_active_resource_error,
)

if TYPE_CHECKING:
    from uuid import UUID


def _resolve_run_id() -> UUID | None:
    """Active run_id for stamping on ``ChannelStarted`` / ``ChannelClosed``.

    Pulled from the active :class:`TestRunLogger` ContextVar at write
    time. ``None`` outside a run (interactive bringup, daemon-driven
    channel writes). ChannelStore tolerates ``None`` — channel-lifecycle
    events stay valid without run context.
    """
    logger = get_current_logger()
    return getattr(getattr(logger, "test_run", None), "id", None)


def _resolve_store() -> Any:
    """Return the active ChannelStore from the ContextVar chain.

    Raises if no store is wired — call from inside an active Litmus
    session (TestHarness + ChannelStore constructed) or via the
    pytest plugin which sets the ContextVar during test setup.
    """
    store = get_channel_store()
    if store is None:
        raise no_active_resource_error("ChannelStore")
    return store


def write(name: str, sample: Any, *, namespace: str | None = None) -> str:
    """Append one sample to a channel; return its ``channel://`` URI.

    Per §3 of the design doc, this is the power-user explicit form
    of the test-author ``stream(name, sample)`` verb. Both call into
    the same ``ChannelStore.write`` body. Use ``write`` when you want
    to be explicit about the storage decision (e.g., a driver that
    knows it's recording channel data); use ``stream`` for test-author
    intent code.

    Args:
        name: Channel identifier (e.g., ``"scope.ch1"``).
        sample: One sample to append. Same shape rules as
            ``Context.observe`` for array channels — see
            :func:`litmus.data.ref.classify_value`. Per Position 2 +
            C-3b: blobs route through FileStore at the verb layer,
            not here.
        namespace: Optional prefix sugar; effective channel_id
            becomes ``"{namespace}.{name}"``.

    Returns:
        The ``channel://`` URI for this write's channel.
    """
    full_name = f"{namespace}.{name}" if namespace else name
    return _resolve_store().write(full_name, sample, source="stream", run_id=_resolve_run_id())


class _ChannelSink:
    """Context-manager sink yielded by :func:`stream`.

    Holds the resolved channel_id closed in the context manager so
    the author writes multiple samples without re-naming the channel
    on every call. Closes idempotently — no resource to release;
    Position 2 doesn't emit a per-sample close event.
    """

    def __init__(self, store: Any, channel_id: str) -> None:
        self._store = store
        self._channel_id = channel_id
        self._closed = False

    @property
    def channel_id(self) -> str:
        """The resolved channel_id this sink writes to."""
        return self._channel_id

    @property
    def uri(self) -> str:
        """The ``channel://`` URI for this sink's channel.

        Satisfies the :class:`~litmus.data.ref.Latchable` protocol —
        :meth:`Context.observe` checks for this property and stamps
        the URI without re-writing when handed a sink:

        ::

            with channels.stream("scope.ch1") as sink:
                sink.write(sample)
                observe("capture", sink)   # latches sink.uri on out_*
        """
        return make_channel_uri(self._channel_id, str(self._store.session_id))

    def write(self, sample: Any) -> str:
        """Append one sample to this sink's channel. Returns the URI."""
        if self._closed:
            raise RuntimeError(
                f"channel sink for {self._channel_id!r} is closed; opening a new "
                "sink (or call channels.write) is required."
            )
        return self._store.write(
            self._channel_id, sample, source="stream", run_id=_resolve_run_id()
        )

    def close(self) -> None:
        """Mark the sink closed. Idempotent."""
        self._closed = True


def _channels_dir() -> Path:
    """The project's channels directory (where the daemon serves from)."""
    return resolve_data_dir() / "channels"


def query(
    name: str,
    *,
    namespace: str | None = None,
    session_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    last_n: int | None = None,
    max_points: int | None = None,
) -> pa.Table:
    """One-shot at-rest read of a channel (the pull verb).

    Goes through the daemon's warm index (no per-call disk walk). Poll this in
    your own loop for a sparkline / periodic refresh. ``max_points`` decimates
    (LTTB) for charts. Returns an Arrow table.
    """
    full_name = f"{namespace}.{name}" if namespace else name
    with channel_query_client(_channels_dir()) as client:
        return client.query(
            full_name,
            session_id=session_id,
            start=start,
            end=end,
            last_n=last_n,
            max_points=max_points,
        )


def latest(
    name: str,
    callback: Callable[[ChannelSample], None],
    *,
    namespace: str | None = None,
) -> Callable[[], None]:
    """Subscribe to the **newest sample** of a channel, conflated (the gauge).

    ``callback`` fires with a :class:`ChannelSample` each time a newer sample
    lands; if you fall behind you get the current one, never a backlog. The
    sample's value is whatever the channel carries — a number (DMM) or a whole
    array/waveform (scope). Returns an unsubscribe callable.
    """
    full_name = f"{namespace}.{name}" if namespace else name
    client = ChannelClient(flight_manager.acquire(_channels_dir()))
    unsub_reader = client.on_channel(full_name, callback, policy=SubscribePolicy.LATEST)

    def unsub() -> None:
        unsub_reader()
        client.close()
        flight_manager.release(_channels_dir())

    return unsub


def live(
    name: str,
    callback: Callable[[pa.RecordBatch], None],
    *,
    namespace: str | None = None,
    max_hz: float | None = None,
) -> Callable[[], None]:
    """Subscribe to **every sample** of a channel, batched (the chart edge).

    ``callback`` fires with a coalesced :class:`pyarrow.RecordBatch` — a
    lagging consumer catches up in one batch rather than per-sample. ``max_hz``
    throttles delivery cadence (coalescing in between); ``None`` delivers each
    batch as it arrives. Returns an unsubscribe callable.
    """
    full_name = f"{namespace}.{name}" if namespace else name
    cb = _throttle_batches(callback, 1.0 / max_hz) if max_hz else callback
    client = ChannelClient(flight_manager.acquire(_channels_dir()))
    unsub_reader = client.on_channel_batch(full_name, cb, policy=SubscribePolicy.ALL)

    def unsub() -> None:
        unsub_reader()
        client.close()
        flight_manager.release(_channels_dir())

    return unsub


def _throttle_batches(
    callback: Callable[[pa.RecordBatch], None], interval: float
) -> Callable[[pa.RecordBatch], None]:
    """Deliver to ``callback`` at most every ``interval`` s, coalescing the
    batches that arrived in between into one."""
    lock = threading.Lock()
    pending: list[pa.RecordBatch] = []
    last = [0.0]

    def wrapped(batch: pa.RecordBatch) -> None:
        with lock:
            pending.append(batch)
            now = time.monotonic()
            if now - last[0] < interval:
                return
            combined = pa.Table.from_batches(pending).combine_chunks().to_batches()
            pending.clear()
            last[0] = now
        for b in combined:
            callback(b)

    return wrapped


@contextmanager
def stream(name: str, *, namespace: str | None = None) -> Iterator[_ChannelSink]:
    """Context-managed channel sink — multi-sample append.

    Usage::

        with channels.stream("iv_curve.i") as sink:
            for v in voltages:
                psu.set_voltage(v)
                sink.write(dmm.read_current())

    Differs from repeated :func:`write` calls only in ergonomics —
    the author names the channel once and gets a sink to push samples
    into. Per design doc §3, this is the symmetric power-user form of
    a channel writer; the equivalent for FileStore is
    :func:`litmus.filestore.stream` (signature today, real sink in
    build item 2 / C5).

    Args:
        name: Channel identifier.
        namespace: Optional prefix sugar; effective channel_id
            becomes ``"{namespace}.{name}"``.

    Yields:
        :class:`_ChannelSink` with ``.write(sample)`` and ``.close()``.
    """
    full_name = f"{namespace}.{name}" if namespace else name
    sink = _ChannelSink(_resolve_store(), full_name)
    try:
        yield sink
    finally:
        sink.close()


__all__ = ["latest", "live", "query", "stream", "write"]
