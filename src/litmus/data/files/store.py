"""FileStore — durable file artifact storage, session-scoped.

Holds non-numeric blobs: images, video, vendor files (.tdms), NPZ
waveforms, JSON exports, anything that fits the file-shaped pattern
(one artifact, one URI). Companion to ChannelStore for typed numeric
streaming; not for ChannelStore-shaped data (rejected by ChannelStore
via ``classify_value``).

URI format:    ``file://{session_id}/{filename}``
On disk:       ``{data_dir}/files/{date}/{session_id}/{filename}``
Sidecar:       ``{filename}.meta.json`` (item 1c — MIME + size +
               user attributes)

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

Streaming sink lands in 1b. Migration of the two legacy ``_ref``
dirs lands in 1d.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from litmus.data.data_dir import resolve_data_dir
from litmus.data.files.models import FileArtifactMetadata
from litmus.data.files.serializers import find_serializer

_SIDECAR_SUFFIX = ".meta.json"

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

    Per build item 1c, every put writes a sidecar
    ``{filename}.meta.json`` next to the artifact carrying
    :class:`FileArtifactMetadata` (mime / extension / size_bytes /
    user attributes). Read it back with :meth:`read_attributes`.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = resolve_data_dir(data_dir)
        self._files_dir = self._data_dir / "files"

    def write(
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
            attributes: User-supplied metadata bag persisted into the
                sidecar (item 1c). ``None`` writes an empty
                attributes dict. The bag is round-trippable via
                :meth:`read_attributes`.

        Returns:
            URI of the form ``file://{session_id}/{filename}``.
            The filename reflects the actual on-disk name (including
            any collision-avoidance ``_2`` / ``_3`` suffix).
        """
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

        # Item 1c: write the sidecar metadata file. size_bytes is read
        # after the write so it reflects the actual on-disk size,
        # whatever the serializer produced.
        metadata = FileArtifactMetadata(
            mime=serializer.mime,
            extension=ext,
            size_bytes=dest.stat().st_size,
            attributes=dict(attributes or {}),
        )
        sidecar_path = dest.with_name(dest.name + _SIDECAR_SUFFIX)
        sidecar_path.write_text(metadata.model_dump_json())

        return f"file://{session_id}/{filename}"

    def read_attributes(self, uri: str) -> FileArtifactMetadata | None:
        """Return the :class:`FileArtifactMetadata` for ``uri``, or None.

        ``None`` when the URI doesn't resolve to a FileStore artifact
        on disk, or when its sidecar is missing (e.g. an artifact put
        before item 1c landed).

        Args:
            uri: A ``file://{session_id}/{filename}`` URI returned
                by :meth:`put`.
        """
        artifact_path = self._resolve_uri(uri)
        if artifact_path is None:
            return None
        sidecar_path = artifact_path.with_name(artifact_path.name + _SIDECAR_SUFFIX)
        if not sidecar_path.exists():
            return None
        return FileArtifactMetadata.model_validate_json(sidecar_path.read_text())

    def _resolve_uri(self, uri: str) -> Path | None:
        """Walk date directories to find the on-disk path for a URI.

        URIs are logical references (``file://{session_id}/{filename}``)
        — date is intentionally absent so a backend swap or a manual
        date-dir reorganization stays transparent. Resolution walks
        ``{files_dir}/*/{session_id}/{filename}`` and returns the
        first match. ``None`` when nothing matches.
        """
        if not uri.startswith("file://"):
            return None
        rest = uri[len("file://") :]
        if "/" not in rest:
            return None
        session_id, _, filename = rest.partition("/")
        if not session_id or not filename:
            return None
        # Sidecars themselves end with .meta.json — refuse to resolve
        # them so callers can't accidentally read a sidecar as its own
        # artifact.
        if filename.endswith(_SIDECAR_SUFFIX):
            return None
        for date_dir in self._files_dir.glob("*"):
            candidate = date_dir / session_id / filename
            if candidate.exists():
                return candidate
        return None

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
