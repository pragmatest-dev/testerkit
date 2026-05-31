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

This initial cut (build item 1a) implements ``put()`` with type
dispatch reused from the existing ``save_ref_to_dir`` helper logic.
Forward-compatible parameters (``attributes``) are accepted but not
yet persisted — that lands in item 1c. Streaming sink lands in 1b.
Migration of the two legacy ``_ref`` dirs lands in 1d.
"""

from __future__ import annotations

import json
import pickle
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from litmus.data.data_dir import resolve_data_dir
from litmus.data.models import Waveform

try:
    import importlib.util as _ilu

    HAS_NUMPY = _ilu.find_spec("numpy") is not None
except Exception:  # pragma: no cover - defensive
    HAS_NUMPY = False

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
                chosen by the type dispatch.
            value: The value to write. Type-dispatched:
                ``Path`` → copy with suffix preserved;
                ``Waveform`` → ``.npz`` (with ``Y`` / ``t0`` / ``dt``
                  / attrs as keys);
                ``bytes`` → ``.bin``;
                Pydantic ``BaseModel`` → ``.json``;
                numpy ``ndarray`` → ``.npy``;
                anything else → ``.pkl`` (last-resort fallback).
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

        # Pick the extension via type dispatch (without writing yet)
        # so we can compute a collision-free filename first.
        ext = self._extension_for(value)
        prefix = f"{vector_id[:_VECTOR_ID_LENGTH]}_" if vector_id else ""

        # Compute session directory (date-partitioned for retention/ops)
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        # Resolve a unique filename within the session dir.
        filename = self._unique_filename(session_dir, f"{prefix}{name}", ext)
        dest = session_dir / filename

        # Write via type-dispatched serializer.
        self._write(value, dest)

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

    @staticmethod
    def _extension_for(value: Any) -> str:
        """Pick the file extension this value will serialize to."""
        if isinstance(value, Path):
            return value.suffix or ".bin"
        if isinstance(value, Waveform):
            return ".npz" if HAS_NUMPY else ".json"
        if isinstance(value, bytes):
            return ".bin"
        if hasattr(value, "model_dump"):
            return ".json"
        if hasattr(value, "tolist"):
            return ".npy" if HAS_NUMPY else ".json"
        return ".pkl"

    @staticmethod
    def _write(value: Any, dest: Path) -> None:
        """Type-dispatched write. Mirrors ``save_ref_to_dir`` in
        ``data/backends/_row_helpers.py``. Item 12 will promote both
        to a registry; for 1a we duplicate the dispatch locally so
        the legacy helper's behavior is unaffected.
        """
        if isinstance(value, Path):
            shutil.copy(value, dest)
            return

        if isinstance(value, Waveform):
            if HAS_NUMPY:
                import numpy as np  # noqa: PLC0415

                np.savez(dest, Y=value.Y, t0=value.t0, dt=value.dt, **value.attrs)
            else:
                dest.write_text(value.model_dump_json())
            return

        if isinstance(value, bytes):
            dest.write_bytes(value)
            return

        if hasattr(value, "model_dump"):
            dest.write_text(value.model_dump_json())
            return

        if hasattr(value, "tolist"):
            if HAS_NUMPY:
                import numpy as np  # noqa: PLC0415

                np.save(dest, value)
            else:
                dest.write_text(json.dumps(value.tolist()))
            return

        with open(dest, "wb") as f:
            pickle.dump(value, f)
