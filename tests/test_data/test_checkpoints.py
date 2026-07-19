"""Producer liveness checkpoints — the producer-side half of P3.

A long active producer's samples/frames ride off-spine, so the channel + file
producers emit a low-rate checkpoint on a cadence so the session lease renews
instead of going silent. The channel producer emits ``ChannelCheckpoint``
(carrying ``sample_offset``); the file sink emits ``FileCheckpoint`` (carrying
``byte_offset``). Cadence resolves from ``StreamTuning`` (default ``lease/3``,
invariant ``< lease``).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from testerkit.data.channels.store import ChannelStore
from testerkit.data.event_log import EventLog
from testerkit.data.events import ChannelCheckpoint, FileCheckpoint
from testerkit.data.files.store import FileStore
from testerkit.data.files.streaming import _BaseSink
from testerkit.models.data_options import RUN_ORPHAN_TIMEOUT_SECONDS, SessionOptions, StreamTuning


class CollectingLog(EventLog):
    def __init__(self) -> None:
        self.emitted: list[Any] = []

    def emit(self, event: Any) -> None:
        self.emitted.append(event)


def _channel_checkpoints(log: CollectingLog) -> list[ChannelCheckpoint]:
    return [e for e in log.emitted if isinstance(e, ChannelCheckpoint)]


def _file_checkpoints(log: CollectingLog) -> list[FileCheckpoint]:
    return [e for e in log.emitted if isinstance(e, FileCheckpoint)]


# --------------------------------------------------------------------------- #
# StreamTuning.resolve_cadence                                                #
# --------------------------------------------------------------------------- #


def test_cadence_defaults_to_lease_over_three():
    assert StreamTuning().resolve_cadence(900.0) == 300.0


def test_cadence_explicit_override():
    assert StreamTuning(checkpoint_cadence=60.0).resolve_cadence(900.0) == 60.0


def test_cadence_must_be_under_lease():
    # A cadence >= the lease would let a live stream age out between checkpoints.
    with pytest.raises(ValueError, match="must be < idle_lease_seconds"):
        StreamTuning(checkpoint_cadence=900.0).resolve_cadence(900.0)


# --------------------------------------------------------------------------- #
# SessionOptions lease invariant — a session outlives its runs                #
# --------------------------------------------------------------------------- #


def test_default_lease_meets_run_timeout():
    assert SessionOptions().idle_lease_seconds >= RUN_ORPHAN_TIMEOUT_SECONDS


def test_lease_below_run_timeout_rejected():
    with pytest.raises(ValueError, match="must be >= the run orphan-timeout"):
        SessionOptions(idle_lease_seconds=60.0)


# --------------------------------------------------------------------------- #
# Channel producer — ChannelCheckpoint                                        #
# --------------------------------------------------------------------------- #


def test_channel_emits_checkpoint_once_per_cadence(tmp_path: Path):
    log = CollectingLog()
    sid = uuid4()
    store = ChannelStore(
        tmp_path, sid, flush_threshold=1000, event_log=log, checkpoint_cadence=10.0
    )
    store.open()
    # First write announces ChannelStarted (a spine event) and arms the clock —
    # no checkpoint yet.
    store.write("ch", 1.0)
    assert _channel_checkpoints(log) == []
    # Age the last spine emit past the cadence; the next write checkpoints.
    aged = datetime.now(UTC) - timedelta(seconds=100)
    store._last_spine_emit = aged
    store.write("ch", 2.0)
    cps = _channel_checkpoints(log)
    assert len(cps) == 1
    assert cps[0].session_id == sid
    assert cps[0].uri.startswith("channel://")
    assert cps[0].sample_offset >= 0
    # The checkpoint is a spine event — it renews the session lease by resetting
    # the cadence clock to "now" (past the aged value it fired from).
    assert store._last_spine_emit is not None
    assert store._last_spine_emit > aged
    store.close()


def test_channel_no_checkpoint_within_cadence(tmp_path: Path):
    log = CollectingLog()
    store = ChannelStore(
        tmp_path, uuid4(), flush_threshold=1000, event_log=log, checkpoint_cadence=3600.0
    )
    store.open()
    for i in range(50):
        store.write("ch", float(i))
    # 50 rapid writes, cadence 1h → not one checkpoint (bounded by time, not count).
    assert _channel_checkpoints(log) == []
    store.close()


def test_channel_no_checkpoint_without_cadence(tmp_path: Path):
    log = CollectingLog()
    store = ChannelStore(tmp_path, uuid4(), flush_threshold=1000, event_log=log)
    store.open()
    store._last_spine_emit = datetime.now(UTC) - timedelta(seconds=10_000)
    for i in range(10):
        store.write("ch", float(i))
    assert _channel_checkpoints(log) == []
    store.close()


# --------------------------------------------------------------------------- #
# File producer — FileCheckpoint                                              #
# --------------------------------------------------------------------------- #


def test_file_stream_emits_checkpoint_once_per_cadence(tmp_path: Path):
    log = CollectingLog()
    store = FileStore(_data_dir=tmp_path)
    sink = store.open_stream(
        name="capture",
        format="raw",
        session_id=str(uuid4()),
        event_log=log,
        checkpoint_cadence=10.0,
    )
    assert isinstance(sink, _BaseSink)
    sink.write(b"first")  # FileStarted armed the clock at construction
    assert _file_checkpoints(log) == []
    aged = time.monotonic() - 100.0  # age past cadence
    sink._last_spine_emit = aged
    sink.write(b"second")
    cps = _file_checkpoints(log)
    assert len(cps) == 1
    assert cps[0].uri.startswith("file://")
    assert cps[0].byte_offset == len(b"first") + len(b"second")
    # The checkpoint renews the session lease — the cadence clock advances past
    # the aged value it fired from.
    assert sink._last_spine_emit is not None
    assert sink._last_spine_emit > aged
    sink.close()


def test_file_stream_no_checkpoint_without_cadence(tmp_path: Path):
    log = CollectingLog()
    store = FileStore(_data_dir=tmp_path)
    sink = store.open_stream(name="capture", format="raw", session_id=str(uuid4()), event_log=log)
    assert isinstance(sink, _BaseSink)
    sink._last_spine_emit = time.monotonic() - 10_000.0
    sink.write(b"data")
    assert _file_checkpoints(log) == []
    sink.close()
