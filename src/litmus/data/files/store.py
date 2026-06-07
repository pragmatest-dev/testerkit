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
from typing import TYPE_CHECKING, Any
from uuid import UUID

from litmus.data.data_dir import resolve_data_dir
from litmus.data.files.catalog import catalog_row
from litmus.data.files.catalog_manager import (
    is_running as _catalog_running,
)
from litmus.data.files.catalog_manager import (
    push_artifact as _catalog_push,
)
from litmus.data.files.catalog_manager import (
    resolve_uri as _catalog_resolve,
)
from litmus.data.files.models import FileArtifactMetadata
from litmus.data.files.serializers import find_serializer
from litmus.data.files.streaming import StreamingSink, get_format

if TYPE_CHECKING:
    from litmus.data.files.streaming import EventEmitter

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
        instrument_role: str = "",
        resource: str = "",
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
            instrument_role: Optional provenance — the station-config
                instrument role (e.g. ``"scope"``, ``"psu"``) that
                produced this artifact. Populated when a Waveform /
                channel-shaped value falls through to FileStore from
                ``Context.observe`` because no ChannelStore was wired
                (bare-Context bringup tests). Without this the FileStore
                fallback path would silently lose the provenance the
                ChannelStore descriptor would have captured.
            resource: Optional provenance — VISA / network resource
                string for the instrument (paired with
                ``instrument_role``). Same population path as above.

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
        #
        # Atomicity: the sidecar pairs the artifact one-to-one (mime,
        # size, attributes). A crash between artifact-write and
        # sidecar-write would leave an artifact with no metadata,
        # silently breaking the audit trail (read_attributes() returns
        # None, every downstream consumer sees a metadata-less file).
        # Write to a tmp path then atomic rename so the sidecar
        # either lands fully or not at all.
        metadata = FileArtifactMetadata(
            mime=serializer.mime,
            extension=ext,
            size_bytes=dest.stat().st_size,
            attributes=dict(attributes or {}),
            instrument_role=instrument_role,
            resource=resource,
        )
        sidecar_path = dest.with_name(dest.name + _SIDECAR_SUFFIX)
        sidecar_tmp = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
        sidecar_tmp.write_text(metadata.model_dump_json())
        sidecar_tmp.replace(sidecar_path)

        uri = f"file://{session_id}/{filename}"
        # Keep the daemon's warm catalog current (req 2). Best-effort and
        # non-spawning: skips silently if no daemon is running, and the
        # sidecar on disk is the durable truth a restart rebuilds from.
        _catalog_push(
            self._files_dir,
            catalog_row(
                uri=uri,
                session_id=session_id,
                name=filename,
                path=dest,
                meta=metadata,
                created_at=datetime.now(UTC),
            ),
        )
        return uri

    def open_stream(
        self,
        name: str,
        *,
        format: str,
        session_id: str,
        vector_id: str | None = None,
        attributes: dict[str, Any] | None = None,
        event_log: EventEmitter | None = None,
        run_id: UUID | None = None,
    ) -> StreamingSink:
        """Open a streaming sink — one file, written incrementally.

        Build item 2 (C5). Companion to :meth:`write` for cases where
        bytes arrive over time rather than all-at-once (continuous DAQ,
        video capture, line-delimited logs). The sink:

        - allocates the on-disk path (collision-safe via the same
          ``_unique_filename`` scheme as :meth:`write`)
        - emits :class:`StreamStarted` on open (carries absolute path)
        - emits :class:`StreamFrameIndex` after every :meth:`write` call
          (carries ``byte_offset`` for HTTP range-read)
        - emits :class:`StreamEnded` on :meth:`close` (carries final
          ``file://`` URI + total size)
        - writes the item-1c sidecar metadata on close

        Live consumers learn the path from :class:`StreamStarted`,
        range-read the new byte window on each :class:`StreamFrameIndex`,
        and reach the final URI in :class:`StreamEnded`. See the
        :mod:`litmus.data.files.streaming` module docstring for format
        coverage + caveats per format on partial-decode-during-write.

        Args:
            name: Artifact name (becomes part of the filename).
            format: One of the registered streaming formats —
                ``"raw"``, ``"jsonl"``, ``"tdms"``, ``"h5"`` in
                v0.2.0. See :func:`litmus.data.files.streaming.registered_formats`.
            session_id: Session this artifact belongs to. Required.
            vector_id: Optional vector context; first 8 chars prefix
                the filename for audit.
            attributes: User-supplied metadata bag — persisted into
                the item-1c sidecar at close.
            event_log: Event log to emit Stream* events into. ``None``
                is allowed (silent writes — useful for tests of the
                file path in isolation); production paths always plumb
                this from the active session.
            run_id: Optional run UUID stamped on Stream* events.

        Returns:
            A :class:`StreamingSink` — context-manageable; call
            :meth:`StreamingSink.close` (or use a ``with`` block) to
            finalize and receive the ``file://`` URI.
        """
        fmt = get_format(format)
        prefix = f"{vector_id[:_VECTOR_ID_LENGTH]}_" if vector_id else ""
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        filename = self._unique_filename(session_dir, f"{prefix}{name}", fmt.extension)
        dest = session_dir / filename

        # Capture for sidecar write at close
        attrs_for_sidecar = dict(attributes or {})

        def _finalize() -> None:
            metadata = FileArtifactMetadata(
                mime=fmt.mime,
                extension=fmt.extension,
                size_bytes=dest.stat().st_size if dest.exists() else 0,
                attributes=attrs_for_sidecar,
            )
            sidecar_path = dest.with_name(dest.name + _SIDECAR_SUFFIX)
            sidecar_path.write_text(metadata.model_dump_json())

        return fmt.open(
            path=dest,
            name=name,
            format_name=format,
            session_id=session_id,
            event_log=event_log,
            run_id=run_id,
            finalizer=_finalize,
        )

    def read_attributes(self, uri: str) -> FileArtifactMetadata | None:
        """Return the :class:`FileArtifactMetadata` for ``uri``, or None.

        ``None`` when the URI doesn't resolve to a FileStore artifact
        on disk, or when its sidecar is missing (e.g. an artifact put
        before item 1c landed).

        Args:
            uri: A ``file://{session_id}/{filename}`` URI returned
                by :meth:`put`.
        """
        artifact_path = self.resolve_uri(uri)
        if artifact_path is None:
            return None
        sidecar_path = artifact_path.with_name(artifact_path.name + _SIDECAR_SUFFIX)
        if not sidecar_path.exists():
            return None
        return FileArtifactMetadata.model_validate_json(sidecar_path.read_text())

    def resolve_uri(self, uri: str) -> Path | None:
        """Walk date directories to find the on-disk path for a URI.

        Public — callers in the operator UI, materializer, and HTTP
        ``/files-static`` route reach for this to map a logical
        ``file://{session_id}/{filename}`` URI to its current on-disk
        location. Date is intentionally absent from the URI so a backend
        swap or a manual date-dir reorganization stays transparent.
        Resolution walks ``{files_dir}/*/{session_id}/{filename}`` and
        returns the first match. ``None`` when nothing matches.
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
        # Prefer the daemon's warm catalog (req 2). Fall through to the
        # date-dir walk when no daemon is running (tests, offline) or the
        # catalog hasn't caught up to a just-written file. Phase E removes
        # the walk fallback once the backend swap requires it.
        if _catalog_running(self._files_dir):
            hit = _catalog_resolve(self._files_dir, uri)
            if hit is not None:
                return Path(hit)
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
