"""FileStore — durable file artifact storage, session-scoped.

Holds non-numeric blobs: images, video, vendor files (.tdms), NPZ
waveforms, JSON exports, anything that fits the file-shaped pattern
(one artifact, one URI). Companion to ChannelStore for typed numeric
streaming; not for ChannelStore-shaped data (rejected by ChannelStore
via ``classify_value``).

URI format:    ``file://{session_id}/{filename}``
On disk:       ``{data_dir}/files/{date}/{session_id}/{filename}``

Filename convention:
- with ``vector_id``:  ``{vector_id_short}_{name}.{ext}``
- without:             ``{name}.{ext}``
- on collision:        ``_2``, ``_3``, … suffix (preserves claim-check
  immutability — repeated puts create distinct files; the vector's
  ``out_<name>`` last-write-wins separately at materialization)

Type dispatch + MIME convention live in
:mod:`litmus.data.files.serializers` (build items 12 + 13). Custom
types either expose ``litmus_serialize(dest)`` (the protocol) or
register via :func:`register_serializer`.

Forward-compatible parameter ``attributes`` is accepted but not
yet persisted — that lands in item 1c. Streaming sink lands in 1b.
Migration of the two legacy ``_ref`` dirs lands in 1d.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from litmus.data.data_dir import resolve_data_dir
from litmus.data.files.serializers import find_serializer

# Truncate vector_id to N chars for filename prefix (audit trail
# without bloating). Matches existing ``VECTOR_ID_LENGTH`` convention
# in ``data/backends/_row_helpers.py``.
_VECTOR_ID_LENGTH = 8


class FileStore:
    """Durable file artifact storage, session-scoped.

    Typical use is indirect — ``observe()`` / ``verify()`` route
    non-channel-shaped values here automatically. Direct use is for
    power-user cases (the streaming sink lands in build item 1b; for
    one-shot puts, callers can go through this class today).

    Holds non-numeric blobs only — not channel-shaped numerics (those
    go to ChannelStore). The dispatch decision is the caller's; this
    store does not type-check the incoming value beyond what its
    serializer can handle.

    Attributes/metadata persistence is **not** implemented in this
    initial cut. The ``attributes`` parameter is accepted for forward
    compatibility; it will be wired in build item 1c (MIME typing +
    attributes persistence).
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = resolve_data_dir(data_dir)
        self._files_dir = self._data_dir / "files"

    def put(
        self,
        name: str,
        value: Any,
        *,
        session_id: str,
        vector_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        """Write ``value`` to FileStore; return its ``file://`` URI.

        Args:
            name: Logical name for the artifact (e.g.
                ``"scope.ch1.capture"``). Forms the bulk of the
                filename. Should not include extension; extension is
                chosen by the registry (build item 12).
            value: The value to write. Routed through
                :func:`litmus.data.files.serializers.find_serializer`
                — see that module for the built-in convention table
                and the ``litmus_serialize`` protocol /
                :func:`register_serializer` extension points.
                Last-resort fallback is pickle with a
                ``RuntimeWarning`` naming the type.
            session_id: Session this artifact belongs to. Required.
                Callers (verb layer) pull from the active-session
                ContextVar.
            vector_id: Optional vector context. When provided, the
                first 8 chars prefix the filename for audit
                (matches existing convention).
            attributes: Forward-compat parameter. Not persisted in
                this cut; wired in item 1c.

        Returns:
            URI of the form ``file://{session_id}/{filename}``.
            The filename reflects the actual on-disk name (including
            any collision-avoidance ``_2`` / ``_3`` suffix).
        """
        # Accepted for forward compat; persistence wired in item 1c.
        del attributes

        serializer = find_serializer(value)
        # Path values: the source file's suffix wins over the
        # serializer's default ``.bin`` so e.g. ``capture.tdms`` stays
        # ``.tdms`` on disk.
        if isinstance(value, Path):
            ext = value.suffix or serializer.extension
        else:
            ext = serializer.extension
        prefix = f"{vector_id[:_VECTOR_ID_LENGTH]}_" if vector_id else ""

        # Compute session directory (date-partitioned for retention/ops)
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        # Resolve a unique filename within the session dir.
        filename = self._unique_filename(session_dir, f"{prefix}{name}", ext)
        dest = session_dir / filename

        # Write via the registered handler.
        serializer.write(value, dest)

        return f"file://{session_id}/{filename}"

    # ----- internals -------------------------------------------------

    def _session_dir(self, session_id: str) -> Path:
        """Resolve the on-disk directory for a session's files.

        Layout: ``{data_dir}/files/{date}/{session_id}/`` where
        ``date`` is the UTC date of *now* (the moment of put). This
        matches the date-partitioning convention of ``events/`` and
        ``channels/`` — keeps retention / ops boundaries consistent.
        """
        today = datetime.now(UTC).date().isoformat()
        return self._files_dir / today / session_id

    @staticmethod
    def _unique_filename(directory: Path, stem: str, ext: str) -> str:
        """Return a filename that does not collide in ``directory``.

        First attempt is ``{stem}{ext}``. On collision, appends
        ``_2``, ``_3``, … until an unused name is found. Preserves
        claim-check immutability — a repeated put never overwrites
        an existing artifact's bytes.
        """
        candidate = f"{stem}{ext}"
        if not (directory / candidate).exists():
            return candidate
        n = 2
        while True:
            candidate = f"{stem}_{n}{ext}"
            if not (directory / candidate).exists():
                return candidate
            n += 1
