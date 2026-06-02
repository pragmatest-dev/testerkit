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

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


def _resolve_run_id() -> UUID | None:
    """Active run_id for stamping on ``ChannelStarted`` / ``ChannelClosed``.

    Pulled from the active :class:`TestRunLogger` ContextVar at write
    time. ``None`` outside a run (interactive bringup, daemon-driven
    channel writes). ChannelStore tolerates ``None`` — channel-lifecycle
    events stay valid without run context.
    """
    from litmus.execution._state import get_current_logger  # noqa: PLC0415

    logger = get_current_logger()
    return getattr(getattr(logger, "test_run", None), "id", None)


def _resolve_store() -> Any:
    """Return the active ChannelStore from the ContextVar chain.

    Raises if no store is wired — call from inside an active Litmus
    session (TestHarness + ChannelStore constructed) or via the
    pytest plugin which sets the ContextVar during test setup.
    """
    from litmus.execution._state import get_channel_store  # noqa: PLC0415

    store = get_channel_store()
    if store is None:
        raise RuntimeError(
            "litmus.channels: no active ChannelStore. "
            "Call inside an active Litmus session, or construct a "
            "TestHarness with a wired ChannelStore explicitly."
        )
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
