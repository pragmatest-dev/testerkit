"""FileStore — durable artifact storage for non-numeric blobs.

Session-scoped, MIME-typed (later), format-portable. Reached by test authors
indirectly via ``observe()``/``verify()`` routing (when the value is
non-channel-shaped); reachable directly via ``filestore.put()`` /
``filestore.stream()`` for power-user cases.

URI scheme: ``file://{session_id}/{filename}`` — a logical reference. The
FileStore resolves to the active backend (today: local FS) so the URI is
backend-opaque and stays valid across future backend swaps.

On-disk layout (local backend): ``{data_dir}/files/{date}/{session_id}/{filename}``.

See ``docs/_internal/explorations/data-stores.md`` for the design.
"""

from litmus.data.files.store import FileStore

__all__ = ["FileStore"]
