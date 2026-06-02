"""Models for FileStore artifact metadata (build item 1c)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FileArtifactMetadata(BaseModel):
    """Metadata persisted next to a FileStore artifact as a sidecar.

    Written at put time alongside the artifact at
    ``{filename}.meta.json``. Captures the kind of file that landed,
    its size on disk, and any user-supplied attributes routed through
    :meth:`FileStore.write`'s ``attributes`` kwarg.

    Per build item 13, the ``mime`` value follows the Litmus
    convention table — see :mod:`litmus.data.files.serializers`.

    Per build item 17, the metadata-bag field is called
    ``attributes`` (matching :class:`ChannelDescriptor.attributes`
    and :class:`Waveform.attributes`).

    Format-specific extraction (image dimensions, audio duration,
    video duration) is not in the initial cut — that lands when the
    relevant format library exposes the data inexpensively. Today
    callers carry whatever they want via the ``attributes`` kwarg.
    """

    mime: str
    extension: str
    size_bytes: int
    attributes: dict[str, Any] = Field(default_factory=dict)
