"""FileStore — durable file artifact storage, session-scoped.

Holds non-numeric blobs: images, video, vendor files (.tdms), NPZ
waveforms, JSON exports, anything that fits the file-shaped pattern
(one artifact, one URI). Companion to ChannelStore for typed numeric
streaming; not for ChannelStore-shaped data (rejected by ChannelStore
via ``classify_value``).

URI format:    ``file://{date}/{session_id}/{filename}``
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
from litmus.data.files._backend import BlobBackend, resolve_files_backend
from litmus.data.files.catalog import catalog_row
from litmus.data.files.catalog_manager import (
    push_artifact as _catalog_push,
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

    def __init__(self, data_dir: Path | None = None, *, backend_uri: str | None = None) -> None:
        self._data_dir = resolve_data_dir(data_dir)
        self._files_dir = self._data_dir / "files"
        # Blob bytes flow through this backend (local disk by default; an
        # object store by config — the only thing that changes for a swap).
        # Default root is self._files_dir so it tracks whatever resolved the
        # data dir (incl. test monkeypatches); env/config override only when
        # no explicit data_dir was given.
        self._backend = BlobBackend.from_uri(
            resolve_files_backend(
                self._files_dir, allow_override=data_dir is None, backend_uri=backend_uri
            )
        )

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
        run_id: UUID | None = None,
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
            URI of the form ``file://{date}/{session_id}/{filename}``.
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

        # Resolve a unique filename within the session dir; the backend key
        # is that filename under {date}/{session_id}.
        filename = self._unique_filename(session_dir, f"{prefix}{name}", ext)
        date = session_dir.parent.name
        key = f"{date}/{session_id}/{filename}"

        # Serialize to a local staging path (suffix preserved — writers like
        # np.savez rename themselves otherwise), then publish atomically to
        # the blob backend: a same-fs rename locally, an upload remotely. A
        # crash mid-serialize leaves the staging temp, never a published
        # artifact the catalog could point at.
        staged = self._backend.stage_path(key)
        serializer.write(value, staged)
        self._backend.publish_atomic(staged, key)

        # Item 1c: write the sidecar metadata. size_bytes is read back from
        # the backend after publish so it reflects the actual stored size.
        # The sidecar pairs the artifact one-to-one (mime, size, attributes);
        # backend.write_bytes is atomic (temp+rename local, single PUT remote)
        # so it either lands fully or not at all.
        metadata = FileArtifactMetadata(
            mime=serializer.mime,
            extension=ext,
            size_bytes=self._backend.size(key) or 0,
            attributes=dict(attributes or {}),
            instrument_role=instrument_role,
            resource=resource,
            run_id=str(run_id) if run_id else None,
        )
        self._backend.write_bytes(f"{key}{_SIDECAR_SUFFIX}", metadata.model_dump_json().encode())

        uri = f"file://{key}"
        # Keep the daemon's warm catalog current (req 2). Best-effort and
        # non-spawning: skips silently if no daemon is running, and the
        # sidecar is the durable truth a restart rebuilds from. The catalog
        # stores the backend ``key`` (not an absolute path) so resolution
        # stays backend-agnostic.
        _catalog_push(
            self._files_dir,
            catalog_row(
                uri=uri,
                session_id=session_id,
                name=filename,
                key=key,
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

        - allocates the artifact's backend key (collision-safe via the
          same ``_unique_filename`` scheme as :meth:`write`)
        - emits :class:`StreamStarted` on open
        - pushes the new bytes as an ephemeral frame after every
          :meth:`write` (via the files daemon, not the event log) so
          live consumers receive them push-style, no poll
        - emits :class:`StreamEnded` on :meth:`close` (carries final
          ``file://`` URI + total size)
        - writes the item-1c sidecar metadata + catalog row on close

        Live consumers subscribe to the stream's frames and receive each
        new chunk push-style; the final URI arrives in
        :class:`StreamEnded`. See the
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
        date = session_dir.parent.name
        key = f"{date}/{session_id}/{filename}"
        uri = f"file://{key}"

        # Capture for sidecar write at close
        attrs_for_sidecar = dict(attributes or {})

        def _finalize(staged: Path | None) -> None:
            # Format sinks (tdms/h5) staged to a local path → publish it now;
            # raw/jsonl already published by closing the backend output stream.
            # Then the sidecar + catalog row — the same durable tail write() runs.
            if staged is not None:
                self._backend.publish_atomic(staged, key)
            metadata = FileArtifactMetadata(
                mime=fmt.mime,
                extension=fmt.extension,
                size_bytes=self._backend.size(key) or 0,
                attributes=attrs_for_sidecar,
                run_id=str(run_id) if run_id else None,
            )
            self._backend.write_bytes(
                f"{key}{_SIDECAR_SUFFIX}", metadata.model_dump_json().encode()
            )
            _catalog_push(
                self._files_dir,
                catalog_row(
                    uri=uri,
                    session_id=session_id,
                    name=filename,
                    key=key,
                    meta=metadata,
                    created_at=datetime.now(UTC),
                ),
            )

        common: dict[str, Any] = {
            "uri": uri,
            "files_dir": self._files_dir,
            "name": name,
            "format_name": format,
            "session_id": session_id,
            "event_log": event_log,
            "run_id": run_id,
        }
        if fmt.needs_local_path:
            # nptdms / h5py need a seekable local file; stage there, publish on close.
            staged = self._backend.stage_path(key)
            return fmt.open(path=staged, finalizer=lambda: _finalize(staged), **common)
        # raw/jsonl write straight to the backend output stream (local file /
        # S3 multipart) — completes on close, so no separate publish step.
        stream = self._backend.open_output_stream(key)
        return fmt.open(stream=stream, finalizer=lambda: _finalize(None), **common)

    def read(self, uri: str) -> bytes | None:
        """Return the full bytes of the artifact at ``uri``, or ``None``.

        The store serves the file — callers never touch the filesystem
        or know where the bytes live. Goes through the blob backend, so
        a remote (S3/GCS) backend serves the same way as local disk.
        """
        key = self._resolve_key(uri)
        return None if key is None else self._backend.read_bytes(key)

    def read_range(self, uri: str, *, offset: int = 0, length: int | None = None) -> bytes | None:
        """Range-read ``[offset, offset+length)`` of ``uri`` (HTTP Range)."""
        key = self._resolve_key(uri)
        return None if key is None else self._backend.read_range(key, offset=offset, length=length)

    def size(self, uri: str) -> int | None:
        """Stored byte size of the artifact at ``uri``, or ``None``."""
        key = self._resolve_key(uri)
        return None if key is None else self._backend.size(key)

    def modified_at(self, uri: str) -> datetime | None:
        """Last-modified time of the artifact at ``uri``, or ``None``.

        Backend-portable (local mtime / object-store LastModified). For an
        immutable write-once artifact this is effectively its creation time.
        """
        key = self._resolve_key(uri)
        return None if key is None else self._backend.modified_at(key)

    def open_input(self, uri: str) -> Any:
        """Open a streaming read handle for ``uri`` (caller closes it), or ``None``.

        Returns a random-access ``pyarrow.NativeFile`` so a consumer can stream
        a large artifact in chunks rather than buffering it whole — sequential
        reads become ranged GETs on a remote backend.
        """
        key = self._resolve_key(uri)
        return None if key is None else self._backend.open_input(key)

    def read_attributes(self, uri: str) -> FileArtifactMetadata | None:
        """Return the :class:`FileArtifactMetadata` for ``uri``, or None.

        ``None`` when the URI doesn't resolve to a FileStore artifact, or
        when its sidecar is missing (e.g. an artifact put before item 1c
        landed). The sidecar is read through the backend like any blob.

        Args:
            uri: A ``file://{date}/{session_id}/{filename}`` URI returned
                by :meth:`put`.
        """
        key = self._resolve_key(uri)
        if key is None:
            return None
        raw = self._backend.read_bytes(f"{key}{_SIDECAR_SUFFIX}")
        if raw is None:
            return None
        return FileArtifactMetadata.model_validate_json(raw)

    def delete(self, uri: str) -> None:
        """Delete the artifact at ``uri`` and its sidecar (best-effort).

        A no-op when the URI doesn't resolve. Removes the blob bytes through
        the backend; the catalog row, if any, clears when the daemon next
        rescans (the sidecar — its durable source — is gone).
        """
        key = self._resolve_key(uri)
        if key is None:
            return
        self._backend.delete(key)
        self._backend.delete(f"{key}{_SIDECAR_SUFFIX}")

    def _resolve_key(self, uri: str) -> str | None:
        """Map a ``file://`` URI to its backend-relative key — pure parsing.

        The URI carries the full key (``{date}/{session_id}/{filename}``), so
        resolution is a string strip: no catalog lookup, no disk scan, no
        daemon. The key is identical across backends; the backend root is
        config, so a local→remote swap is transparent and a point read needs
        no index (the catalog daemon serves queries/discovery, not point
        resolution). ``None`` for a non-``file://`` URI, an empty key, or one
        that names a sidecar.
        """
        if not uri.startswith("file://"):
            return None
        key = uri[len("file://") :]
        # Refuse empty keys + sidecars (so a caller can't read one as an artifact).
        if not key or key.endswith(_SIDECAR_SUFFIX):
            return None
        return key

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
