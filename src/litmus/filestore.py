"""Power-user file-write surface — items 7 / 8.

The test-author shape for blob outputs is :meth:`Context.observe` /
the bare ``observe`` fixture, which auto-routes blobs to FileStore
via dispatch. Power users — code that knows it's emitting one
specific file artifact, or wants explicit control — reach for these
forms:

- :func:`put` — one-shot file write (the canonical FileStore.put
  API exposed as a top-level verb)
- :func:`stream` — context-managed file sink for streaming writes
  (video, audio, continuous DAQ); **signature-only stub** in C3b —
  the actual sink implementation lands in build item 2 (C5).

Both delegate to the active FileStore singleton via
:func:`litmus.data.files.get_filestore`. The ``session_id`` argument
defaults to the active Context's session when called from inside a
test; pass explicitly when calling from setup code that runs before
a Context is pushed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def _resolve_session_id(session_id: str | None) -> str:
    """Return the session_id to use for a FileStore put.

    Explicit ``session_id`` arg wins. Otherwise resolve from the
    active Context's ``_session_id`` via the ContextVar chain.
    Raises if neither is available.
    """
    if session_id is not None:
        return session_id
    from litmus.execution._state import get_current_context  # noqa: PLC0415

    ctx = get_current_context()
    if ctx is None or getattr(ctx, "_session_id", None) is None:
        raise RuntimeError(
            "litmus.filestore: no active session_id. Call inside an active "
            "Litmus session (a TestHarness has pushed the Context), or pass "
            "session_id=... explicitly."
        )
    return str(ctx._session_id)


def put(
    name: str,
    value: Any,
    *,
    session_id: str | None = None,
    vector_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> str:
    """Write ``value`` to FileStore; return its ``file://`` URI.

    Top-level verb wrapping :meth:`FileStore.put`. Per §3 of the
    design doc, this is the power-user explicit form of the
    test-author blob-observation path (which auto-routes blobs to
    FileStore via :meth:`Context.observe`). Reach for this when:

    - The code is outside a test (setup / fixture / driver) and
      needs to claim-check an artifact under a specific session
    - The author wants to be explicit about FileStore (e.g.,
      capturing an ndarray that would otherwise route to
      ChannelStore via observe)

    Args:
        name: Artifact name (forms the bulk of the filename).
        value: The value to put (any registry-typed value — see
            :mod:`litmus.data.files.serializers` for the dispatch
            table and the ``litmus_serialize`` protocol).
        session_id: Session this artifact belongs to. ``None``
            resolves from the active Context.
        vector_id: Optional vector context — first 8 chars prefix
            the filename for audit trail.
        attributes: User-supplied metadata bag persisted into the
            sidecar (item 1c).

    Returns:
        ``file://{session_id}/{filename}`` URI.
    """
    from litmus.data.files import get_filestore  # noqa: PLC0415

    sid = _resolve_session_id(session_id)
    return get_filestore().put(
        name=name,
        value=value,
        session_id=sid,
        vector_id=vector_id,
        attributes=attributes,
    )


@contextmanager
def stream(
    name: str,
    *,
    format: str,
    session_id: str | None = None,
) -> Iterator[Any]:
    """Context-managed file sink — streams bytes into one growing artifact.

    **Signature-only stub** in C3b — raises ``NotImplementedError`` at
    call time. The actual sink wraps PyAV / soundfile / tifffile /
    nptdms / h5py / pyarrow per format and lands in build item 2 (C5).
    Locked here so callers see the verb shape now; behavior fills in
    when the streaming sink ships.

    Per §3 / §4 of the design doc, this is the FileStore-side
    equivalent of :func:`litmus.channels.stream` — both stores can
    stream, with different granularity (file streams write bytes per
    chunk; channel streams write one typed sample per call).

    Args:
        name: Artifact name; the eventual ``file://`` URI is announced
            via ``StreamEnded`` (item 1b — wires with item 2).
        format: File format (``"mp4"``, ``"wav"``, ``"tdms"``,
            ``"h5"``, etc.) — drives the underlying library choice.
        session_id: Session the stream belongs to. ``None`` resolves
            from the active Context.

    Yields:
        A sink with ``.write(chunk)`` and ``.close()`` (in C5).
    """
    raise NotImplementedError(
        f"litmus.filestore.stream(name={name!r}, format={format!r}) — "
        "the streaming sink lands in build item 2 (C5). For one-shot "
        "file writes today, use litmus.filestore.put(name, value), "
        "which dispatches per format via the serializer registry."
    )
    # unreachable — present to satisfy the generator contract
    yield None
