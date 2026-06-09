"""Blob backend — the pluggable byte-storage seam for FileStore.

FileStore holds the *shape* (claim-check: blobs by opaque ``file://`` URI,
a metadata catalog, immutable atomic writes); this module owns *where the
bytes live*. All blob byte I/O goes through a :class:`BlobBackend` over a
:class:`pyarrow.fs.FileSystem`, selected by a single root URI. Local disk
today (``file://``); ``s3://`` / ``gcs://`` later is a config change, not a
rewrite — that is the req-6 northstar.

A blob is addressed by a ``key`` relative to the backend root:
``{date}/{session_id}/{filename}``. The root is everything before it.

Writers that need a real local path (np.savez, TDMS, h5py) serialize to a
local **staging** path, then :meth:`BlobBackend.publish_atomic` commits it
to the backend (a rename on the local backend; an upload on a remote one).
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.fs as pafs

_ENV_BACKEND = "LITMUS_FILES_BACKEND"


def resolve_files_backend(
    files_dir: Path,
    *,
    allow_override: bool,
    backend_uri: str | None = None,
) -> str:
    """Resolve the FileStore blob-backend root URI.

    ``files_dir`` is the already-resolved local default
    (``{data_dir}/files``) — the local root URI when nothing overrides it.
    Resolution (first hit wins):

    1. Explicit ``backend_uri`` (tests, the backend-swap proof).
    2. When ``allow_override`` (no explicit ``data_dir`` was given — tests
       and the benchmark stay local regardless of project config):
       ``LITMUS_FILES_BACKEND`` env, then ``litmus.yaml`` ``files_backend``.
    3. Default ``files_dir.as_uri()``.
    """
    if backend_uri is not None:
        return backend_uri
    if allow_override:
        env = os.environ.get(_ENV_BACKEND)
        if env:
            return env
        try:
            from litmus.connect import _find_project_config

            found = _find_project_config()
            if found and found[1].files_backend:
                return found[1].files_backend
        except (ImportError, AttributeError, FileNotFoundError):
            pass
    return files_dir.as_uri()


class BlobBackend:
    """Byte storage over a :class:`pyarrow.fs.FileSystem`, addressed by key.

    Construct via :meth:`from_uri`. ``key`` is always relative to the
    backend root (``{date}/{session_id}/{filename}``); the backend joins
    it onto the root and talks to the underlying filesystem.
    """

    def __init__(self, fs: pafs.FileSystem, root: str) -> None:
        self._fs = fs
        self._root = root.rstrip("/")
        self._is_local = isinstance(fs, pafs.LocalFileSystem)

    @classmethod
    def from_uri(cls, root_uri: str) -> BlobBackend:
        fs, root = pafs.FileSystem.from_uri(root_uri)
        return cls(fs, root)

    @property
    def is_local(self) -> bool:
        return self._is_local

    def _full(self, key: str) -> str:
        return f"{self._root}/{key.lstrip('/')}"

    def local_path(self, key: str) -> Path | None:
        """Absolute local path for ``key``, or ``None`` for a remote backend."""
        return Path(self._full(key)) if self._is_local else None

    def _mkparent(self, full: str) -> None:
        parent = full.rsplit("/", 1)[0]
        # Recursive create; a no-op on object stores (no real directories).
        self._fs.create_dir(parent, recursive=True)

    def stage_path(self, key: str) -> Path:
        """A local path to serialize into before :meth:`publish_atomic`.

        Local backend: a sibling temp next to the destination so the publish
        is a same-filesystem rename. Remote backend: an OS temp file. The
        suffix is preserved so suffix-sensitive writers (np.savez) behave.
        """
        suffix = Path(key).suffix
        stem = Path(key).stem
        if self._is_local:
            dest = Path(self._full(key))
            dest.parent.mkdir(parents=True, exist_ok=True)
            return dest.parent / f"{stem}.part-{os.urandom(4).hex()}{suffix}"
        fd, tmp = tempfile.mkstemp(prefix=f"{stem}.part-", suffix=suffix)
        os.close(fd)
        return Path(tmp)

    def publish_atomic(self, staged: Path, key: str) -> None:
        """Commit a staged local file to ``key`` (rename local, upload remote)."""
        full = self._full(key)
        self._mkparent(full)
        if self._is_local:
            self._fs.move(str(staged), full)
        else:
            pafs.copy_files(
                str(staged),
                full,
                source_filesystem=pafs.LocalFileSystem(),
                destination_filesystem=self._fs,
            )
            Path(staged).unlink(missing_ok=True)

    def write_bytes(self, key: str, data: bytes) -> None:
        """Atomically write ``data`` to ``key`` (small payloads — sidecars).

        Local: temp + rename. Remote: a single object PUT (atomic by nature).
        """
        full = self._full(key)
        self._mkparent(full)
        if self._is_local:
            staged = self.stage_path(key + ".w")
            staged.write_bytes(data)
            self._fs.move(str(staged), full)
        else:
            with self._fs.open_output_stream(full) as o:
                o.write(data)

    def read_bytes(self, key: str) -> bytes | None:
        """Full contents of ``key``, or ``None`` if it doesn't exist."""
        try:
            with self._fs.open_input_file(self._full(key)) as f:
                return f.read()
        except (FileNotFoundError, OSError):
            return None

    def read_range(self, key: str, *, offset: int = 0, length: int | None = None) -> bytes | None:
        """Range-read ``[offset, offset+length)`` of ``key`` (HTTP Range)."""
        try:
            with self._fs.open_input_file(self._full(key)) as f:
                if offset:
                    f.seek(offset)
                return f.read(length) if length is not None else f.read()
        except (FileNotFoundError, OSError):
            return None

    def open_input(self, key: str) -> pa.NativeFile | None:
        """Open a streaming read handle for ``key`` (caller closes it), or None.

        A random-access ``NativeFile`` — sequential reads on a remote backend
        are ranged GETs under the hood. Lets a consumer stream a large blob in
        chunks instead of buffering the whole thing in memory.
        """
        try:
            return self._fs.open_input_file(self._full(key))
        except (FileNotFoundError, OSError):
            return None

    def open_output_stream(self, key: str) -> pa.NativeFile:
        """Open a streaming WRITE handle for ``key`` (caller writes + closes).

        Local backend: a truncating file write. Remote backend: a multipart
        upload that completes on ``close`` — so the object appears atomically
        when the caller closes the stream, never as a partial blob. This is
        the streaming-write path for append-only sinks (raw / jsonl); format
        writers that need a seekable local path (TDMS / HDF5) stage locally
        and :meth:`publish_atomic` instead.
        """
        full = self._full(key)
        self._mkparent(full)
        return self._fs.open_output_stream(full)

    def size(self, key: str) -> int | None:
        info = self._fs.get_file_info(self._full(key))
        return info.size if info.type == pafs.FileType.File else None

    def modified_at(self, key: str) -> datetime | None:
        """Last-modified time of ``key`` — local mtime / object LastModified."""
        info = self._fs.get_file_info(self._full(key))
        return info.mtime if info.type == pafs.FileType.File else None

    def exists(self, key: str) -> bool:
        return self._fs.get_file_info(self._full(key)).type != pafs.FileType.NotFound

    def delete(self, key: str) -> None:
        try:
            self._fs.delete_file(self._full(key))
        except (FileNotFoundError, OSError):
            pass
