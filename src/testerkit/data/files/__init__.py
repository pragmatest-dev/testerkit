"""FileStore — durable artifact storage for non-numeric blobs.

Session-scoped, MIME-typed (later), format-portable. Reached by test authors
indirectly via ``observe()``/``verify()`` routing (when the value is
non-channel-shaped); reachable directly via ``files.write()`` /
``filestore.stream()`` for power-user cases.

URI scheme: ``file://{date}/{session_id}/{filename}`` — a logical reference. The
FileStore resolves to the active backend (today: local FS) so the URI is
backend-opaque and stays valid across future backend swaps.

On-disk layout (local backend): ``{data_dir}/files/{date}/{session_id}/{filename}``.

See ``docs/_internal/explorations/data-stores.md`` for the design.

Module-level factory ``get_filestore()`` returns a process-wide singleton
bound to the canonical ``resolve_data_dir()``. Callers from the verb layer
(``Context.observe``, future ``observer.read``) reach FileStore through
this factory rather than constructing per-call.
"""

from __future__ import annotations

from testerkit.data.files.models import FILE_METADATA_SCHEMA_VERSION, FileArtifactMetadata
from testerkit.data.files.serializers import (
    Serializer,
    find_serializer,
    register_serializer,
)
from testerkit.data.files.store import FileStore

__all__ = [
    "FILE_METADATA_SCHEMA_VERSION",
    "FileArtifactMetadata",
    "FileStore",
    "Serializer",
    "find_serializer",
    "get_filestore",
    "register_serializer",
]


_filestore: FileStore | None = None


def get_filestore() -> FileStore:
    """Return the process-wide ``FileStore`` singleton.

    Lazily constructs on first call, bound to the canonical
    ``resolve_data_dir()``. All verb-layer callers (observe / verify
    blob paths; future observer.read blob paths) reach FileStore via
    this factory.

    For test isolation, use ``_reset_for_tests()`` between tests that
    want a fresh data-dir resolution.
    """
    global _filestore
    if _filestore is None:
        _filestore = FileStore()
    return _filestore


def _reset_for_tests() -> None:
    """Discard the cached FileStore singleton.

    Tests that mutate ``TESTERKIT_HOME`` or other data-dir resolution
    inputs between cases call this to force re-resolution on the next
    ``get_filestore()``. Production code never calls this.
    """
    global _filestore
    _filestore = None
