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
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyarrow as pa

from litmus.data.channels import flight_manager
from litmus.data.channels.client import ChannelClient, channel_query_client
from litmus.data.channels.models import (
    ChannelSample,
    SubscribePolicy,
    encode_value,
    sample_schema,
)
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


def declare(
    name: str,
    *,
    units: str | None = None,
    instrument_role: str = "",
    resource: str = "",
    attributes: dict[str, Any] | None = None,
    namespace: str | None = None,
) -> None:
    """Declare a channel's identity for this session before writing data.

    Sets ``units``/``instrument_role``/``resource``/``attributes`` once; the
    value type is locked by the first write. Optional — a first :func:`write`
    auto-registers with defaults — but it's how you attach units to a channel.
    Identity is immutable within the session: a conflicting unit raises.
    """
    full_name = f"{namespace}.{name}" if namespace else name
    _resolve_store().declare(
        full_name,
        units=units,
        instrument_role=instrument_role,
        resource=resource,
        attributes=attributes,
        run_id=_resolve_run_id(),
    )


def write_many(
    name: str,
    samples: Sequence[tuple[Any, datetime | None]],
    *,
    namespace: str | None = None,
) -> str:
    """Append a batch of samples to a channel in one call — :func:`write`, batched.

    ``samples`` is a list of ``(value, sampled_at)`` pairs. Each pair is one
    sample, exactly what :func:`write` takes: ``value`` is any shape ``write``
    accepts (scalar, array, dict) and ``sampled_at`` is its hardware instant or
    ``None``. The N samples ride one message instead of N — use it for a buffer
    of readings you already have in hand. For one waveform (uniform spacing, no
    per-point time) use :func:`write` with ``sample_interval``.

    Args:
        name: Channel identifier (e.g., ``"dmm.voltage"``).
        samples: ``(value, sampled_at)`` pairs, one per sample.
        namespace: Optional prefix sugar; the effective channel becomes
            ``"{namespace}.{name}"``.

    Returns:
        The ``channel://`` URI for this channel.
    """
    full_name = f"{namespace}.{name}" if namespace else name
    return _resolve_store().write_many(
        full_name, samples, source="stream", run_id=_resolve_run_id()
    )


class _ChannelSink:
    """Context-manager sink yielded by :func:`stream`.

    Holds the resolved channel_id closed in the context manager so
    the author writes multiple samples without re-naming the channel
    on every call. Closes idempotently — no resource to release;
    Position 2 doesn't emit a per-sample close event.
    """

    # Sink-side batching: accumulate up to this many samples OR this long, then
    # flush as ONE write_many (columnar append + single enqueue). This is what
    # makes stream the fast path — it reaches the batched ceiling instead of the
    # per-sample one. The interval bounds live latency for low-rate streams.
    _FLUSH_ROWS = 1000
    _FLUSH_INTERVAL = 0.005

    def __init__(self, store: Any, channel_id: str, run_id: UUID | None = None) -> None:
        self._store = store
        self._channel_id = channel_id
        self._run_id = run_id  # captured at construction; timer flushes off-thread
        self._closed = False
        self._buf: list[Any] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

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
        """Buffer one sample; flush as a columnar batch at the size cap or
        ``_FLUSH_INTERVAL``. Returns the channel URI."""
        if self._closed:
            raise RuntimeError(
                f"channel sink for {self._channel_id!r} is closed; opening a new "
                "sink (or call channels.write) is required."
            )
        with self._lock:
            self._buf.append(sample)
            if len(self._buf) >= self._FLUSH_ROWS:
                self._flush_locked()
            elif self._timer is None:
                self._timer = threading.Timer(self._FLUSH_INTERVAL, self._timer_flush)
                self._timer.daemon = True
                self._timer.start()
        return make_channel_uri(self._channel_id, str(self._store.session_id))

    def _timer_flush(self) -> None:
        with self._lock:
            self._timer = None
            self._flush_locked()

    def _flush_locked(self) -> None:
        """Flush the buffer as one ``write_many``. Caller holds ``_lock``."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if not self._buf:
            return
        batch = self._buf
        self._buf = []
        self._store.write_many(self._channel_id, batch, source="stream", run_id=self._run_id)

    def close(self) -> None:
        """Flush remaining buffered samples and mark closed. Idempotent."""
        with self._lock:
            self._flush_locked()
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
        table = client.query(
            full_name,
            session_id=session_id,
            start=start,
            end=end,
            last_n=last_n,
            max_points=max_points,
        )
    # ``offset`` is an internal ordering cursor (the window-stitch dedup key),
    # never part of the public read contract — drop it from query results.
    if "offset" in table.column_names:
        table = table.drop_columns(["offset"])
    return table


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


def _history_to_wire_batch(channel_id: str, table: pa.Table) -> pa.RecordBatch | None:
    """Reshape a ``query`` result into one live-shaped RecordBatch.

    ``query`` returns decoded values and drops ``channel_id``/``units``;
    ``window`` delivers the history prefill in the same shape ``live`` uses so
    the consumer's batch handler is identical for prefill and live tail.
    """
    n = table.num_rows
    if n == 0:
        return None
    names = set(table.column_names)

    def _col(name: str, default: object) -> list:
        return table.column(name).to_pylist() if name in names else [default] * n

    return pa.record_batch(
        {
            "channel_id": [channel_id] * n,
            "received_at": _col("received_at", None),
            "sampled_at": _col("sampled_at", None),
            "value": [encode_value(v) for v in _col("value", None)],
            "source_method": [s or "" for s in _col("source_method", "")],
            "units": [""] * n,
            "sample_interval": _col("sample_interval", None),
            "session_id": _col("session_id", None),
            "offset": _col("offset", -1),
        },
        schema=sample_schema(),
    )


def _dedup_against_history(
    batch: pa.RecordBatch, high_water: dict[str, int]
) -> pa.RecordBatch | None:
    """Drop live rows already covered by the history prefill.

    A live row duplicates a history row iff its ``offset`` is at or below the
    per-session high-water mark seen in history. Returns the survivors (or
    ``None`` if every row is a duplicate).
    """
    if not high_water:
        return batch
    sessions = batch.column("session_id").to_pylist()
    seqs = batch.column("offset").to_pylist()
    mask = [s is None or q > high_water.get(s, -1) for s, q in zip(sessions, seqs, strict=True)]
    if all(mask):
        return batch
    if not any(mask):
        return None
    return batch.filter(pa.array(mask))


def window(
    name: str,
    callback: Callable[[pa.RecordBatch], None],
    *,
    dur: float,
    namespace: str | None = None,
    max_hz: float | None = None,
) -> Callable[[], None]:
    """Backfill the last ``dur`` seconds, then continue live (the chart window).

    Delivers the channel's last ``dur`` seconds of history as an initial
    :class:`pyarrow.RecordBatch`, then keeps ``callback`` fed with new samples —
    the "show me the last 30 seconds, live" pattern. History and live arrive in
    the same batch shape and are joined at the seam with no gap (subscribe runs
    before the history read) and no double-counted sample (dedup on the per-sample
    offset). ``max_hz`` throttles the live tail. Returns an unsubscribe callable.
    """
    full_name = f"{namespace}.{name}" if namespace else name

    lock = threading.Lock()
    live_buffer: list[pa.RecordBatch] = []
    high_water: dict[str, int] = {}
    buffering = [True]
    forward = _throttle_batches(callback, 1.0 / max_hz) if max_hz else callback

    def _on_live(batch: pa.RecordBatch) -> None:
        with lock:
            if buffering[0]:
                live_buffer.append(batch)
                return
            deduped = _dedup_against_history(batch, high_water)
        if deduped is not None and deduped.num_rows:
            forward(deduped)

    # Subscribe BEFORE the history read so nothing written in between is lost
    # (subscribe-before-query closes the gap; the offset closes the dup).
    client = ChannelClient(flight_manager.acquire(_channels_dir()))
    unsub_reader = client.on_channel_batch(full_name, _on_live, policy=SubscribePolicy.ALL)

    now = datetime.now(UTC)
    max_points = int(dur * max_hz) if max_hz else None
    history = client.query(full_name, start=now - timedelta(seconds=dur), max_points=max_points)
    if "offset" in history.column_names and history.num_rows:
        for s, q in zip(
            history.column("session_id").to_pylist(),
            history.column("offset").to_pylist(),
            strict=True,
        ):
            if s is not None and q > high_water.get(s, -1):
                high_water[s] = q

    hist_batch = _history_to_wire_batch(full_name, history)
    if hist_batch is not None:
        callback(hist_batch)

    # Flush the buffered live tail (deduped) under the lock, then hand the live
    # stream straight through. Holding the lock here serializes the seam with
    # any reader-thread delivery, so the tail can't interleave with live.
    with lock:
        for b in live_buffer:
            deduped = _dedup_against_history(b, high_water)
            if deduped is not None and deduped.num_rows:
                callback(deduped)
        live_buffer.clear()
        buffering[0] = False

    def unsub() -> None:
        unsub_reader()
        client.close()
        flight_manager.release(_channels_dir())

    return unsub


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
    sink = _ChannelSink(_resolve_store(), full_name, run_id=_resolve_run_id())
    try:
        yield sink
    finally:
        sink.close()


__all__ = [
    "declare",
    "latest",
    "live",
    "query",
    "stream",
    "window",
    "write",
    "write_many",
]
