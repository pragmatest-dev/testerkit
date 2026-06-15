"""Producer-local data options — the project-controllable knobs.

One home for the tuning levers that used to be scattered across the channel
sink, channel store, IPC writer, push relay, and files frame relay. They group
by scope (store or session) and by *who can change them*:

- These models hold the **producer-local** knobs — they run in the project's
  own process, so a project owns them and may set them in ``litmus.yaml`` under
  ``channels:`` / ``files:`` / ``session:``. Defaults reproduce the historical
  values exactly.
- Daemon-global caps (a shared singleton's internals) are NOT here: a project
  influences daemon behavior only by what the producer/consumer sends on the
  wire (the subscribe ``?policy=`` pattern, or the session *will* stamped on
  ``SessionStarted``), never via config. Those live as constants next to the
  daemon code that uses them.
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


class SessionOptions(BaseModel):
    """Session liveness policy — the producer's *will* defaults (settable in
    ``litmus.yaml`` under ``session:``).

    The **owner** resolves these at session open and stamps them onto
    ``SessionStarted``; the reaper reads them off that event (never config) to
    decide when a silent session is abandoned. A second producer sharing the
    ``session_id`` attaches without a ``SessionStarted``, so there is exactly one
    will per session. Per-producer variation (interactive vs test) is a caller
    override at ``open_session``, not a separate yaml block.
    """

    model_config = {"extra": "forbid", "frozen": True}

    # No durable spine event tagged this session_id for this long → the reaper
    # treats the session as suspect. A session outlives its runs, so this must
    # never be shorter than the run orphan-timeout (the platform default anchors
    # to it).
    idle_lease_seconds: float = Field(default=900.0, gt=0)
    # After the lease, a late event / reconnect still rescues the session for
    # this long before the derived ``SessionEnded`` is emitted.
    abandon_grace_seconds: float = Field(default=300.0, ge=0)
    # Stamped onto the derived ``SessionEnded.reason`` when the reaper closes it.
    abandon_reason: str = "abandoned"


class StreamTuning(BaseModel):
    """Streaming liveness cadence — how often an active stream sink emits a
    durable checkpoint (settable in ``litmus.yaml`` under ``stream:``).

    A stream's samples/frames ride the off-spine fan-out, so a long active
    stream would otherwise emit nothing durable between ``StreamStarted`` and
    ``StreamEnded`` and the reaper couldn't tell a live stream from a dead one.
    The sink emits one ``StreamCheckpoint`` (carrying offset-so-far) when this
    long has elapsed since its last spine event — bounded to one per cadence
    regardless of sample rate. Shared by the channel + file producers.
    """

    model_config = {"extra": "forbid", "frozen": True}

    # ``None`` → derive ``idle_lease_seconds / 3`` producer-side (DDS
    # assertions_per_lease_duration = 3). Must resolve to ``< idle_lease_seconds``
    # so a live stream always asserts within the lease window.
    checkpoint_cadence: float | None = Field(default=None, gt=0)

    def resolve_cadence(self, idle_lease_seconds: float) -> float:
        """Resolve the checkpoint cadence against a session's lease.

        ``None`` derives ``lease / 3``; an explicit value must stay under the
        lease so a live stream always asserts before it expires.
        """
        cadence = (
            self.checkpoint_cadence
            if self.checkpoint_cadence is not None
            else idle_lease_seconds / 3
        )
        if cadence >= idle_lease_seconds:
            raise ValueError(
                f"checkpoint_cadence ({cadence}s) must be < idle_lease_seconds "
                f"({idle_lease_seconds}s) so a live stream asserts within the lease"
            )
        return cadence


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
