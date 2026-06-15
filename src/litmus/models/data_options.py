"""Per-store data options — the project-controllable knobs for each store.

One home for the tuning levers that used to be scattered across the channel
sink, channel store, IPC writer, push relay, and files frame relay. They group
by store and by *who can change them*:

- These models hold the **producer-local** knobs — they run in the project's
  own process, so a project owns them and may set them in ``litmus.yaml`` under
  ``channels:`` / ``files:``. Defaults reproduce the historical values exactly.
- Daemon-global caps (a shared singleton's internals) are NOT here: a project
  influences daemon behavior only by what the producer/consumer sends on the
  wire (the subscribe ``?policy=`` pattern), never via config. Those live as
  constants next to the daemon code that uses them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChannelOptions(BaseModel):
    """Channel-store data options (settable in ``litmus.yaml`` under ``channels:``).

    These tune how channel *writes* are buffered and published — they apply to
    ``write`` / ``write_many`` / ``stream`` alike, not only streaming.
    """

    model_config = {"extra": "forbid", "frozen": True}

    # Sink-side batching (``channels.stream``): coalesce buffered samples into
    # one columnar block at this row count OR after this interval.
    sink_flush_rows: int = Field(default=1000, gt=0)
    sink_flush_interval: float = Field(default=0.005, gt=0)

    # Durable segment writer: flush/rotate the Arrow-IPC segment at this row
    # count OR after this idle interval (idle flush bounds visibility for
    # low-rate streams).
    writer_flush_threshold: int = Field(default=100, gt=0)
    writer_flush_interval: float = Field(default=1.0, gt=0)

    # Producer push relay: coalesce up to this many rows OR this long before one
    # do_put; bounded queue, drop-oldest on overflow (live = from-now).
    push_max_rows: int = Field(default=1000, gt=0)
    push_max_wait: float = Field(default=0.005, gt=0)
    push_queue_max: int = Field(default=10_000, gt=0)


class FileOptions(BaseModel):
    """File-store data options (settable in ``litmus.yaml`` under ``files:``)."""

    model_config = {"extra": "forbid", "frozen": True}

    # Blob-backend root URI for FileStore artifacts. Unset keeps blobs under
    # ``{data_dir}/files``; an ``s3://`` / ``gcs://`` URI stores them in an
    # object store — only this changes, no code does. (``LITMUS_FILES_BACKEND``
    # env overrides this.)
    backend: str | None = None

    # Streaming frame push relay: coalesce up to this many frames OR this long
    # before one do_put; bounded queue, drop-oldest on overflow (live = from-now).
    frame_push_max_rows: int = Field(default=256, gt=0)
    frame_push_max_wait: float = Field(default=0.05, gt=0)
    frame_push_queue_max: int = Field(default=1024, gt=0)
