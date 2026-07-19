"""Power-user file-write surface — items 7 / 8 / 2.

The test-author shape for blob outputs is :meth:`Context.observe` /
the bare ``observe`` fixture, which auto-routes blobs to FileStore
via dispatch. Power users — code that knows it's emitting one
specific file artifact, or wants explicit control — reach for these
forms:

- :func:`write` — one-shot file write (the canonical class method
  exposed as a top-level verb; symmetric with
  :func:`testerkit.channels.write`)
- :func:`stream` — context-managed file sink for streaming writes
  (continuous DAQ, line-delimited logs, TDMS / HDF5 capture). Item 2
  lit in v0.2.0; see :mod:`testerkit.data.files.streaming` for formats.

Both delegate to the active FileStore singleton via
:func:`testerkit.data.files.get_filestore`. The ``session_id`` argument
defaults to the active Context's session when called from inside a
test; pass explicitly when calling from setup code that runs before
a Context is pushed.

The verb name is ``write`` (not ``put``) for two reasons:

1. Symmetric with :func:`testerkit.channels.write`. Test authors see
   one verb for "create a new record in this store" across both
   stores (module name + ``.write``).
2. Semantically accurate. Today's behavior creates a new immutable
   artifact per call (with collision-suffix naming when the name
   repeats). That's append-a-new-record, not HTTP-PUT-style
   idempotent replace. The ``put`` name would mislead.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from uuid import UUID

from testerkit.execution._state import (
    get_current_run_scope,
    no_active_resource_error,
    resolve_session_id,
)

if TYPE_CHECKING:
    from testerkit.data.event_log import EventLog
    from testerkit.data.files.streaming import StreamingSink


def _resolve_session_id(session_id: str | None) -> str:
    """Return the session_id to use for a FileStore write.

    Thin wrapper around :func:`testerkit.execution._state.resolve_session_id`
    that stringifies the result and raises a consistent error when
    nothing is resolvable. Single resolution rule shared with
    ``Context.__init__`` so adding a new source updates one place.

    ``fallback_to_active=True`` is the deliberate opposite of
    ``Context.__init__``. ``files.write`` is a module-level surface
    that users call from inside tests / fixtures / connect blocks; the
    documented contract is "find the active session and write there."
    Without the fallback, callers would have to thread ``session_id=``
    through every helper. ``Context.__init__`` defaults to OFF for the
    opposite reason — fresh Contexts should not inherit ambient state.
    """
    resolved = resolve_session_id(session_id, fallback_to_active=True)
    if resolved is None:
        raise no_active_resource_error("session_id", explicit_arg="session_id")
    return str(resolved)


def write(
    name: str,
    value: Any,
    *,
    namespace: str | None = None,
    session_id: str | None = None,
    vector_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> str:
    """Write ``value`` to FileStore; return its ``file://`` URI.

    Top-level verb wrapping :meth:`FileStore.write`. Symmetric with
    :func:`testerkit.channels.write`. Per §3 of the design doc, this is
    the power-user explicit form of the test-author blob-observation
    path (which auto-routes blobs to FileStore via
    :meth:`Context.observe`). Reach for this when:

    - The code is outside a test (setup / fixture / driver) and
      needs to claim-check an artifact under a specific session
    - The author wants to be explicit about FileStore (e.g.,
      capturing an ndarray that would otherwise route to
      ChannelStore via observe)

    Args:
        name: Artifact name (forms the bulk of the filename).
        value: The value to write (any registry-typed value — see
            :mod:`testerkit.data.files.serializers` for the dispatch
            table and the ``testerkit_serialize`` protocol).
        namespace: Optional prefix sugar; effective artifact name
            becomes ``"{namespace}.{name}"``. Mirrors the
            ``namespace`` parameter on :meth:`Context.observe` and
            on :func:`testerkit.channels.write` — the three power-user
            surfaces share one prefix discipline so an artifact
            recorded via ``observe(name, value, namespace="psu")``
            and one recorded via ``files.write(name, value,
            namespace="psu")`` land at the same effective name.
        session_id: Session this artifact belongs to. ``None``
            resolves from the active Context.
        vector_id: Optional vector context — first 8 chars prefix
            the filename for audit trail.
        attributes: User-supplied metadata bag persisted into the
            sidecar (item 1c).

    Returns:
        ``file://{date}/{session_id}/{filename}`` URI.
    """
    # Lazy: data.files chain pulls PIL / serializers; only loaded
    # when files.write actually runs.
    from testerkit.data.files import get_filestore  # noqa: PLC0415

    full_name = f"{namespace}.{name}" if namespace else name
    sid = _resolve_session_id(session_id)
    _, run_id = _resolve_event_log_and_run_id()  # run provenance for the catalog
    return get_filestore().write(
        name=full_name,
        value=value,
        session_id=sid,
        vector_id=vector_id,
        attributes=attributes,
        run_id=run_id,
    )


def _resolve_event_log_and_run_id() -> tuple[EventLog | None, UUID | None]:
    """Pull the active event_log + run_id from the current run scope.

    Mirrors :meth:`Context._emit_observation`'s plumbing. Both
    returned values may be ``None`` — call sites must tolerate that
    (the sink emits silently when no event_log is available; useful
    for bare unit tests).
    """
    run_scope = get_current_run_scope()
    if run_scope is None:
        return None, None
    event_log = getattr(run_scope, "event_log", None)
    run_id = getattr(getattr(run_scope, "test_run", None), "id", None)
    return event_log, run_id


@contextmanager
def stream(
    name: str,
    *,
    format: str,
    namespace: str | None = None,
    session_id: str | None = None,
    vector_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[StreamingSink]:
    """Context-managed file sink — streams chunks into one growing artifact.

    Build item 2 (C5). Per §3 / §4 of the design doc, this is the
    FileStore-side equivalent of :func:`testerkit.channels.stream` — both
    stores can stream, with different granularity (file streams write
    bytes per chunk; channel streams write one typed sample per call).

    The sink emits :class:`~testerkit.data.events.FileStarted` on open and
    :class:`~testerkit.data.events.FileEnded` on close (carries the final
    ``file://`` URI). Each :meth:`StreamingSink.write` fans out an ephemeral
    frame (carrying ``byte_offset``) to live subscribers — not a durable
    event — so consumers range-read the new window; a cadence-bounded
    :class:`~testerkit.data.events.FileCheckpoint` keeps the session alive
    during long writes.

    Args:
        name: Artifact name (becomes part of the filename).
        format: Streaming format — one of ``"raw"``, ``"jsonl"``,
            ``"tdms"``, ``"h5"`` in v0.2.0. See
            :func:`testerkit.data.files.streaming.registered_formats`.
        namespace: Optional prefix sugar; effective artifact name
            becomes ``"{namespace}.{name}"``. Mirrors
            ``Context.observe`` and :func:`testerkit.channels.stream`
            so the three power-user surfaces share one prefix
            discipline.
        session_id: Session the stream belongs to. ``None`` resolves
            from the active Context.
        vector_id: Optional vector context; first 8 chars prefix the
            filename for audit.
        attributes: User-supplied metadata bag persisted to the
            item-1c sidecar at close.

    Yields:
        A :class:`~testerkit.data.files.streaming.StreamingSink` with
        ``.write(chunk)``, ``.close()``, and (for context-manager
        exit) implicit close. The final URI is the return value of
        :meth:`StreamingSink.close`; sinks expose ``.byte_offset``
        and ``.file_id`` for callers that want to surface them.

    Example::

        with testerkit.files.stream("daq_capture", format="raw") as sink:
            for chunk in daq.read_chunks():
                sink.write(chunk)
        # sink closed; URI emitted via FileEnded event
    """
    # Lazy: see write() — same data.files heavy chain.
    from testerkit.data.files import get_filestore  # noqa: PLC0415
    from testerkit.store import load_project_config  # noqa: PLC0415

    full_name = f"{namespace}.{name}" if namespace else name
    sid = _resolve_session_id(session_id)
    event_log, run_id = _resolve_event_log_and_run_id()
    # Stream liveness cadence — resolved from the project's stream/session config
    # (testerkit.yaml). A long file stream emits a FileCheckpoint per cadence so the
    # session lease renews instead of going silent on the spine.
    _proj = load_project_config()
    sink = get_filestore().open_stream(
        name=full_name,
        format=format,
        session_id=sid,
        vector_id=vector_id,
        attributes=attributes,
        event_log=event_log,
        run_id=run_id,
        checkpoint_cadence=_proj.stream.resolve_cadence(_proj.session.idle_lease_seconds),
    )
    try:
        yield sink
    finally:
        sink.close()


__all__ = ["stream", "write"]
