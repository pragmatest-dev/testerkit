"""Models for FileStore artifact metadata (build item 1c)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# FIRST stamp of the FileStore sidecar format. The sidecar
# ({filename}.meta.json) is a published, directly-readable consumer
# surface — version it so readers can detect format changes.
FILE_METADATA_SCHEMA_VERSION = "1.0"


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
    # Optional provenance — populated when a Waveform / channel-shaped
    # value falls through to FileStore (no ChannelStore wired). Without
    # these, the FileStore fallback path silently loses the instrument
    # context that the ChannelStore descriptor would have captured
    # (first-write provenance). Consumers joining back to the run
    # record can use these to reconstruct the same context that the
    # ChannelStore path would have provided.
    instrument_role: str = ""
    resource: str = ""
    # The run that produced this artifact (when written inside a run), so the
    # catalog can be filtered by run and the UI can link back. Persisted in the
    # sidecar so a daemon-restart rescan recovers it. ``None`` for run-less writes.
    run_id: str | None = None
    # Schema version stamp — included in every sidecar so readers can detect
    # format changes without scanning field presence. Defaults to the current
    # version so old sidecars (missing this field) still validate: Pydantic
    # fills in the default, giving backward-tolerant reads.
    schema_version: str = Field(default=FILE_METADATA_SCHEMA_VERSION)
