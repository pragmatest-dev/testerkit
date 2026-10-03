"""``testerkit forward`` batching — unit tests (no network, no daemon).

Segment files are written by hand into a tmp dir (a segment is just an Arrow IPC
stream), the clock and file mtimes are injected, and the HTTP POST is replaced by
a fake server that mimics the server chain's rule: per stream keep only rows with
``offset > hwm`` and remember the new hwm. The real-server contract lives in
testerkit-server's ``tests/test_forward_contract.py``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from testerkit import replication
from testerkit.cli import forward_cmd
from testerkit.cli.forward_cmd import ChannelsCursor, ForwardBatchPolicy

DAY = "2026-10-02"
T0 = 1_800_000_000.0  # injected "now" (epoch seconds)


def _seg_name(channel: str, seq: int) -> str:
    stem = f"{channel}_0123abcd"
    return f"{stem}.arrow" if seq == 0 else f"{stem}_{seq:03d}.arrow"


def _stream(channel: str) -> str:
    return f"{DAY}/{channel}_0123abcd"


def _write_seg(
    channels_dir: Path,
    channel: str,
    seq: int,
    offsets: list[int] | None,
    *,
    mtime: float = T0,
) -> Path:
    """One closed segment file; ``offsets=None`` writes a pre-offset segment."""
    day = channels_dir / DAY
    day.mkdir(parents=True, exist_ok=True)
    n = len(offsets) if offsets is not None else 2
    cols: dict[str, pa.Array] = {"value": pa.array([float(i) for i in range(n)])}
    if offsets is not None:
        cols["sample_offset"] = pa.array(offsets, type=pa.int64())
    table = pa.table(cols)
    path = day / _seg_name(channel, seq)
    with ipc.new_stream(str(path), table.schema) as w:
        w.write_table(table)
    os.utime(path, (mtime, mtime))
    return path


class FakeServer:
    """Mimics ``ingest_chain.accept``: per channel keep rows with offset > hwm."""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.hwm: dict[str, int] = {}
        self.objects: list[tuple[str, str, list[int]]] = []  # (channel, rel_path, offsets kept)
        self.received: dict[str, list[int]] = {}  # every offset ever offered, in arrival order
        self.in_flight: dict[str, int] = {}
        self.max_per_stream = 0
        self.max_total = 0
        self._total = 0
        self.fail_after_commit: set[str] = set()  # rel_path substrings: commit, drop response
        self._delay = delay
        self._lock = threading.Lock()

    def post(self, url, token, channel_id, table, *, rel_path, timeout):
        with self._lock:
            self.in_flight[channel_id] = self.in_flight.get(channel_id, 0) + 1
            self._total += 1
            self.max_per_stream = max(self.max_per_stream, self.in_flight[channel_id])
            self.max_total = max(self.max_total, self._total)
        try:
            if self._delay:
                time.sleep(self._delay)
            offsets = table.column("sample_offset").to_pylist()
            with self._lock:
                self.received.setdefault(channel_id, []).extend(offsets)
                hwm = self.hwm.get(channel_id, -1)
                kept = sorted({o for o in offsets if o > hwm})
                if kept:
                    self.hwm[channel_id] = kept[-1]
                    self.objects.append((channel_id, rel_path, kept))
            if any(s in rel_path for s in self.fail_after_commit):
                raise OSError("response lost")
            return {"row_count": len(kept), "segment_key": "k"}
        finally:
            with self._lock:
                self.in_flight[channel_id] -= 1
                self._total -= 1

    def stored(self, channel: str) -> list[int]:
        out: list[int] = []
        for ch, _, kept in self.objects:
            if ch == channel:
                out.extend(kept)
        return out


@pytest.fixture
def server(monkeypatch) -> FakeServer:
    fake = FakeServer()
    monkeypatch.setattr(forward_cmd, "_post_channel_segment", fake.post)
    return fake


def _run(channels_dir: Path, cursor_path: Path, **kw):
    kw.setdefault("clock", lambda: T0)
    return forward_cmd._forward_channels_once(
        channels_dir, cursor_path, "http://x", "tk", timeout=5.0, **kw
    )


# --------------------------------------------------------------------------- #
# Triggers                                                                     #
# --------------------------------------------------------------------------- #


def test_young_small_stream_is_held(tmp_path: Path, server: FakeServer) -> None:
    ch = tmp_path / "channels"
    for seq in range(3):
        _write_seg(ch, "v", seq, [seq * 2, seq * 2 + 1], mtime=T0 - 10)

    assert _run(ch, tmp_path / "c.json") is None
    assert server.objects == []
    assert not (tmp_path / "c.json").exists()


def test_age_trigger_sends_one_range_named_batch(tmp_path: Path, server: FakeServer) -> None:
    ch = tmp_path / "channels"
    for seq in range(3):
        _write_seg(ch, "v", seq, [seq * 2, seq * 2 + 1], mtime=T0 - 61 + seq)

    result = _run(ch, tmp_path / "c.json")

    assert result == {"segments": 3, "posts": 1, "rows": 6}
    assert server.objects == [("v", f"{_stream('v')}_000000-000002.arrow", [0, 1, 2, 3, 4, 5])]
    assert forward_cmd._load_channels_cursor(tmp_path / "c.json").streams == {_stream("v"): 2}


def test_age_trigger_is_exactly_the_threshold(tmp_path: Path, server: FakeServer) -> None:
    ch = tmp_path / "channels"
    _write_seg(ch, "v", 0, [0], mtime=T0 - 59.9)
    assert _run(ch, tmp_path / "c.json") is None
    assert _run(ch, tmp_path / "c.json", clock=lambda: T0 + 0.1) is not None


def test_size_trigger_splits_a_backlog_into_batches(tmp_path: Path, server: FakeServer) -> None:
    ch = tmp_path / "channels"
    paths = [_write_seg(ch, "v", seq, [seq], mtime=T0) for seq in range(6)]
    two_files = sum(p.stat().st_size for p in paths[:2])

    result = _run(ch, tmp_path / "c.json", policy=ForwardBatchPolicy(channel_flush_bytes=two_files))

    assert result == {"segments": 6, "posts": 3, "rows": 6}
    assert [o[1].rsplit("_", 1)[1] for o in server.objects] == [
        "000000-000001.arrow",
        "000002-000003.arrow",
        "000004-000005.arrow",
    ]


def test_flush_all_sends_a_young_stream(tmp_path: Path, server: FakeServer) -> None:
    ch = tmp_path / "channels"
    _write_seg(ch, "v", 0, [0, 1], mtime=T0)
    assert _run(ch, tmp_path / "c.json", flush_all=True) == {"segments": 1, "posts": 1, "rows": 2}


def test_numeric_order_across_999_to_1000(tmp_path: Path, server: FakeServer) -> None:
    """A string sort sent ``_1000`` first and the server hwm dropped ``_101``-``_999``."""
    ch = tmp_path / "channels"
    day = ch / DAY
    day.mkdir(parents=True)
    seqs = [98, 99, 100, 101, 999, 1000, 1001]
    for seq in seqs:
        table = pa.table({"value": [0.0], "sample_offset": pa.array([seq], type=pa.int64())})
        name = f"v_0123abcd_{seq:03d}.arrow"
        with ipc.new_stream(str(day / name), table.schema) as w:
            w.write_table(table)

    _run(ch, tmp_path / "c.json", flush_all=True)

    assert server.stored("v") == seqs
    assert server.received["v"] == seqs  # arrived in ascending order


# --------------------------------------------------------------------------- #
# Unreadable files                                                             #
# --------------------------------------------------------------------------- #


def test_stream_stops_at_first_unreadable_file(tmp_path: Path, server: FakeServer) -> None:
    ch = tmp_path / "channels"
    _write_seg(ch, "v", 0, [0], mtime=T0)
    _write_seg(ch, "v", 1, [1], mtime=T0)
    (ch / DAY / _seg_name("v", 2)).write_bytes(b"torn")
    os.utime(ch / DAY / _seg_name("v", 2), (T0, T0))
    _write_seg(ch, "v", 3, [3], mtime=T0)

    _run(ch, tmp_path / "c.json", flush_all=True)

    assert server.stored("v") == [0, 1]  # 3 must not overtake 2
    assert forward_cmd._load_channels_cursor(tmp_path / "c.json").streams == {_stream("v"): 1}


def test_unreadable_over_ten_minutes_is_skipped_with_warning(
    tmp_path: Path, server: FakeServer, caplog
) -> None:
    ch = tmp_path / "channels"
    _write_seg(ch, "v", 0, [0], mtime=T0 - 700)
    bad = ch / DAY / _seg_name("v", 1)
    bad.write_bytes(b"torn")
    os.utime(bad, (T0 - 700, T0 - 700))
    _write_seg(ch, "v", 2, [2], mtime=T0 - 700)

    with caplog.at_level("WARNING", logger="testerkit.forward"):
        _run(ch, tmp_path / "c.json")

    assert server.stored("v") == [0, 2]
    assert any("skipping channel segment" in r.message for r in caplog.records)
    assert forward_cmd._load_channels_cursor(tmp_path / "c.json").streams == {_stream("v"): 2}


def test_unreadable_last_file_is_never_skipped(tmp_path: Path, server: FakeServer) -> None:
    """Nothing behind it: it may just be mid-flush, however old its mtime says."""
    ch = tmp_path / "channels"
    _write_seg(ch, "v", 0, [0], mtime=T0 - 700)
    bad = ch / DAY / _seg_name("v", 1)
    bad.write_bytes(b"torn")
    os.utime(bad, (T0 - 700, T0 - 700))

    _run(ch, tmp_path / "c.json")

    assert forward_cmd._load_channels_cursor(tmp_path / "c.json").streams == {_stream("v"): 0}


# --------------------------------------------------------------------------- #
# Pre-offset files                                                             #
# --------------------------------------------------------------------------- #


def test_files_without_sample_offset_go_alone_under_their_real_rel_path(
    tmp_path: Path, monkeypatch
) -> None:
    ch = tmp_path / "channels"
    _write_seg(ch, "v", 0, [0], mtime=T0)
    _write_seg(ch, "v", 1, None, mtime=T0)
    _write_seg(ch, "v", 2, None, mtime=T0)
    _write_seg(ch, "v", 3, [3], mtime=T0)
    posts: list[tuple[str, int]] = []

    def _post(url, token, channel_id, table, *, rel_path, timeout):
        posts.append((rel_path, table.num_rows))
        return {"row_count": table.num_rows}

    monkeypatch.setattr(forward_cmd, "_post_channel_segment", _post)
    _run(ch, tmp_path / "c.json", flush_all=True)

    assert posts == [
        (f"{_stream('v')}_000000-000000.arrow", 1),
        (f"{DAY}/{_seg_name('v', 1)}", 2),
        (f"{DAY}/{_seg_name('v', 2)}", 2),
        (f"{_stream('v')}_000003-000003.arrow", 1),
    ]


# --------------------------------------------------------------------------- #
# Cursor                                                                       #
# --------------------------------------------------------------------------- #


def test_v1_cursor_migrates_to_contiguous_high_water_marks(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    sent = [
        f"{DAY}/{_seg_name('a', 0)}",
        f"{DAY}/{_seg_name('a', 1)}",
        f"{DAY}/{_seg_name('a', 2)}",
        f"{DAY}/{_seg_name('a', 4)}",  # above a gap: conservatively re-sent
        f"{DAY}/{_seg_name('b', 1)}",  # no seq 0: nothing provably sent
        "stray/not-a-segment.arrow",
    ]
    path.write_text(json.dumps({"sent": sent}))

    assert forward_cmd._load_channels_cursor(path).streams == {_stream("a"): 2}


def test_v1_cursor_then_pass_resumes_after_the_hwm(tmp_path: Path, server: FakeServer) -> None:
    ch = tmp_path / "channels"
    for seq in range(4):
        _write_seg(ch, "v", seq, [seq], mtime=T0)
    cursor = tmp_path / "c.json"
    cursor.write_text(json.dumps({"sent": [f"{DAY}/{_seg_name('v', s)}" for s in (0, 1)]}))

    _run(ch, cursor, flush_all=True)

    assert server.stored("v") == [2, 3]
    saved = json.loads(cursor.read_text())
    assert saved == {"version": 2, "streams": {_stream("v"): 3}}


def test_cursor_is_saved_once_per_pass_and_pruned(
    tmp_path: Path, server: FakeServer, monkeypatch
) -> None:
    ch = tmp_path / "channels"
    for seq in range(5):
        _write_seg(ch, "v", seq, [seq], mtime=T0)
    cursor = tmp_path / "c.json"
    forward_cmd._save_channels_cursor(cursor, ChannelsCursor(streams={f"{DAY}/gone_0123abcd": 9}))
    saves: list[ChannelsCursor] = []
    real_save = forward_cmd._save_channels_cursor
    monkeypatch.setattr(
        forward_cmd, "_save_channels_cursor", lambda p, c: (saves.append(c), real_save(p, c))
    )
    one_file = (ch / DAY / _seg_name("v", 0)).stat().st_size

    _run(ch, cursor, flush_all=True, policy=ForwardBatchPolicy(channel_flush_bytes=one_file))

    assert len(server.objects) == 5  # five batches ...
    assert len(saves) == 1  # ... one save
    assert saves[0].streams == {_stream("v"): 4}  # stream with no files left is pruned


def test_no_cursor_never_touches_the_cursor_file(tmp_path: Path, server: FakeServer) -> None:
    ch = tmp_path / "channels"
    _write_seg(ch, "v", 0, [0], mtime=T0)
    _run(ch, tmp_path / "c.json", flush_all=True, use_cursor=False)
    assert not (tmp_path / "c.json").exists()
    assert server.stored("v") == [0]


# --------------------------------------------------------------------------- #
# Concurrency                                                                  #
# --------------------------------------------------------------------------- #


def test_one_in_flight_per_stream_up_to_four_streams(tmp_path: Path, monkeypatch) -> None:
    server = FakeServer(delay=0.02)
    monkeypatch.setattr(forward_cmd, "_post_channel_segment", server.post)
    ch = tmp_path / "channels"
    channels = [f"ch{i}" for i in range(8)]
    for c in channels:
        for seq in range(4):
            _write_seg(ch, c, seq, [seq], mtime=T0)
    one_file = (ch / DAY / _seg_name("ch0", 0)).stat().st_size

    _run(
        ch,
        tmp_path / "c.json",
        flush_all=True,
        policy=ForwardBatchPolicy(channel_flush_bytes=one_file),
    )

    assert server.max_per_stream == 1
    assert 1 < server.max_total <= 4
    for c in channels:
        assert server.received[c] == [0, 1, 2, 3]  # serial, ascending


# --------------------------------------------------------------------------- #
# Crash / resume                                                               #
# --------------------------------------------------------------------------- #


def test_lost_response_then_growth_resumes_without_gap_or_duplicate(
    tmp_path: Path, server: FakeServer
) -> None:
    ch = tmp_path / "channels"
    cursor = tmp_path / "c.json"
    for seq in range(3):
        _write_seg(ch, "v", seq, [seq * 2, seq * 2 + 1], mtime=T0 - 100)
    server.fail_after_commit = {"_000000-000002"}  # server commits, bench never hears

    with pytest.raises(OSError):
        _run(ch, cursor)
    assert not cursor.exists()  # nothing acknowledged
    assert server.stored("v") == [0, 1, 2, 3, 4, 5]

    server.fail_after_commit = set()
    for seq in range(3, 5):  # the stream grows while the bench was down
        _write_seg(ch, "v", seq, [seq * 2, seq * 2 + 1], mtime=T0 - 100)
    _run(ch, cursor)  # re-sends 0-4 as one grown batch; the server drops the overlap

    assert server.stored("v") == list(range(10))
    assert forward_cmd._load_channels_cursor(cursor).streams == {_stream("v"): 4}


def test_crash_between_batches_keeps_the_acknowledged_prefix(
    tmp_path: Path, server: FakeServer
) -> None:
    ch = tmp_path / "channels"
    cursor = tmp_path / "c.json"
    paths = [_write_seg(ch, "v", seq, [seq], mtime=T0 - 100) for seq in range(4)]
    one_file = paths[0].stat().st_size
    policy = ForwardBatchPolicy(channel_flush_bytes=2 * one_file)
    server.fail_after_commit = {"_000002-000003"}

    with pytest.raises(OSError):
        _run(ch, cursor, policy=policy)

    # first batch (0-1) acknowledged and saved; second committed server-side but unacked
    assert forward_cmd._load_channels_cursor(cursor).streams == {_stream("v"): 1}
    server.fail_after_commit = set()
    _run(ch, cursor, policy=policy)
    assert server.stored("v") == [0, 1, 2, 3]
    assert forward_cmd._load_channels_cursor(cursor).streams == {_stream("v"): 3}


# --------------------------------------------------------------------------- #
# Scanner                                                                      #
# --------------------------------------------------------------------------- #


def test_channel_scanner_lists_by_directory_mtime(tmp_path: Path, monkeypatch) -> None:
    ch = tmp_path / "channels"
    for seq in range(3):
        _write_seg(ch, "v", seq, [seq])
    day = ch / DAY
    old = T0 - 3600
    os.utime(day, (old, old))
    lists: list[Path] = []
    real = replication.ChannelScanner._list_day

    def _spy(channels_dir, d):
        lists.append(d)
        return real(channels_dir, d)

    monkeypatch.setattr(replication.ChannelScanner, "_list_day", staticmethod(_spy))
    scanner = replication.ChannelScanner(clock=lambda: T0)

    first = scanner.streams(ch)
    scanner.streams(ch)
    assert len(lists) == 1  # unchanged directory is not listed again
    assert [f.seq for f in first[_stream("v")]] == [0, 1, 2]

    _write_seg(ch, "v", 3, [3])
    os.utime(day, (old + 5, old + 5))  # a new file changes the directory mtime
    again = scanner.streams(ch)
    assert len(lists) == 2
    assert [f.seq for f in again[_stream("v")]] == [0, 1, 2, 3]


def test_channel_scanner_never_caches_a_fresh_directory(tmp_path: Path) -> None:
    ch = tmp_path / "channels"
    _write_seg(ch, "v", 0, [0])
    scanner = replication.ChannelScanner()  # real clock: the dir was just modified
    scanner.streams(ch)
    _write_seg(ch, "v", 1, [1])  # same mtime tick is possible; must still be seen
    assert [f.seq for f in scanner.streams(ch)[_stream("v")]] == [0, 1]


# --------------------------------------------------------------------------- #
# Events                                                                       #
# --------------------------------------------------------------------------- #


def _events_table(writer: str, offsets: range, *, age_s: float, now: float) -> pa.Table:
    n = len(offsets)
    occurred = datetime.fromtimestamp(now - age_s, UTC)
    return pa.table(
        {
            "id": [f"{writer}-{o}" for o in offsets],
            "event_type": ["measurement.recorded"] * n,
            "writer_key": [writer] * n,
            "event_offset": pa.array(list(offsets), type=pa.int64()),
            "occurred_at": pa.array(
                [occurred + timedelta(milliseconds=o) for o in offsets],
                pa.timestamp("us", tz="UTC"),
            ),
        }
    )


def _write_wal(events_dir: Path, name: str, table: pa.Table) -> Path:
    day = events_dir / DAY
    day.mkdir(parents=True, exist_ok=True)
    path = day / name
    with ipc.new_stream(str(path), table.schema) as w:
        w.write_table(table)
    return path


class _IngestSpy:
    def __init__(self) -> None:
        self.bodies: list[pa.Table] = []

    def __call__(self, url, token, body, *, timeout):
        self.bodies.append(ipc.open_stream(pa.BufferReader(body)).read_all())
        return {"inserted": self.bodies[-1].num_rows, "deduped": 0, "rejected_ids": []}


def _fwd_events(events_dir: Path, cursor: Path, **kw):
    kw.setdefault("clock", lambda: T0)
    return forward_cmd._forward_once(events_dir, cursor, "http://x", "tk", timeout=5.0, **kw)


def test_events_young_small_writer_is_held(tmp_path: Path, monkeypatch) -> None:
    spy = _IngestSpy()
    monkeypatch.setattr(forward_cmd, "_post_ingest", spy)
    ev = tmp_path / "events"
    _write_wal(ev, "w.arrow", _events_table("w1", range(5), age_s=10, now=T0))

    assert _fwd_events(ev, tmp_path / "e.json") is None
    assert spy.bodies == []
    assert not (tmp_path / "e.json").exists()  # cursor stays put


def test_events_age_trigger_sends_all_pending_ascending(tmp_path: Path, monkeypatch) -> None:
    spy = _IngestSpy()
    monkeypatch.setattr(forward_cmd, "_post_ingest", spy)
    ev = tmp_path / "events"
    _write_wal(ev, "w.arrow", _events_table("w1", range(5), age_s=61, now=T0))

    result = _fwd_events(ev, tmp_path / "e.json")
    assert result is not None and result["inserted"] == 5
    assert spy.bodies[0].column("event_offset").to_pylist() == [0, 1, 2, 3, 4]
    assert forward_cmd._load_cursor(tmp_path / "e.json") == {"w1": 4}


def test_events_size_trigger(tmp_path: Path, monkeypatch) -> None:
    spy = _IngestSpy()
    monkeypatch.setattr(forward_cmd, "_post_ingest", spy)
    ev = tmp_path / "events"
    _write_wal(ev, "w.arrow", _events_table("w1", range(50), age_s=1, now=T0))

    assert (
        _fwd_events(ev, tmp_path / "e.json", policy=ForwardBatchPolicy(event_flush_bytes=1))
        is not None
    )


def test_events_flush_all_overrides_the_hold(tmp_path: Path, monkeypatch) -> None:
    spy = _IngestSpy()
    monkeypatch.setattr(forward_cmd, "_post_ingest", spy)
    ev = tmp_path / "events"
    _write_wal(ev, "w.arrow", _events_table("w1", range(3), age_s=1, now=T0))

    assert _fwd_events(ev, tmp_path / "e.json", flush_all=True) is not None
    assert forward_cmd._load_cursor(tmp_path / "e.json") == {"w1": 2}


def test_events_only_due_writers_send_and_the_held_cursor_stays(
    tmp_path: Path, monkeypatch
) -> None:
    spy = _IngestSpy()
    monkeypatch.setattr(forward_cmd, "_post_ingest", spy)
    ev = tmp_path / "events"
    both = pa.concat_tables(
        [
            _events_table("old", range(3), age_s=120, now=T0),
            _events_table("new", range(3), age_s=1, now=T0),
        ]
    )
    _write_wal(ev, "w.arrow", both)

    _fwd_events(ev, tmp_path / "e.json")

    assert set(spy.bodies[0].column("writer_key").to_pylist()) == {"old"}
    assert forward_cmd._load_cursor(tmp_path / "e.json") == {"old": 2}  # "new" not skipped ahead


def test_select_due_writers_unit() -> None:
    t = pa.concat_tables(
        [
            _events_table("a", range(2), age_s=90, now=T0),
            _events_table("b", range(2), age_s=5, now=T0),
        ]
    )
    kw = {"now": T0, "min_bytes": 10**9, "max_age_s": 60.0}
    out = replication.select_due_writers(t, **kw)
    assert out is not None and set(out.column("writer_key").to_pylist()) == {"a"}
    assert replication.select_due_writers(t.slice(2), **kw) is None
    assert replication.select_due_writers(t.slice(2), flush_all=True, **kw) is not None


def test_wal_scanner_skips_files_already_past_the_cursor(tmp_path: Path, monkeypatch) -> None:
    ev = tmp_path / "events"
    _write_wal(ev, "a.arrow", _events_table("w1", range(0, 5), age_s=1, now=T0))
    _write_wal(ev, "b.arrow", _events_table("w1", range(5, 8), age_s=1, now=T0))
    reads: list[str] = []
    real = replication.read_ipc_batches
    monkeypatch.setattr(
        replication, "read_ipc_batches", lambda p: (reads.append(p.name), real(p))[1]
    )
    scanner = replication.WalScanner()

    first = replication.read_segments(ev, cursor={}, scanner=scanner)
    assert first is not None and first.num_rows == 8
    assert sorted(reads) == ["a.arrow", "b.arrow"]

    reads.clear()
    second = replication.read_segments(ev, cursor={"w1": 4}, scanner=scanner)
    assert second is not None and second.num_rows == 3
    assert reads == ["b.arrow"]  # a.arrow is fully sent and unchanged: not re-read

    reads.clear()
    assert replication.read_segments(ev, cursor={"w1": 7}, scanner=scanner) is None
    assert reads == []  # every file is now fully past the cursor: none re-read

    table = _events_table("w1", range(8, 10), age_s=1, now=T0)
    _write_wal(
        ev, "b.arrow", pa.concat_tables([_events_table("w1", range(5, 8), age_s=1, now=T0), table])
    )
    third = replication.read_segments(ev, cursor={"w1": 7}, scanner=scanner)
    assert third is not None and third.column("event_offset").to_pylist() == [8, 9]


# --------------------------------------------------------------------------- #
# Knobs                                                                        #
# --------------------------------------------------------------------------- #


def test_policy_defaults() -> None:
    p = ForwardBatchPolicy()
    assert (p.channel_flush_bytes, p.channel_flush_age_s) == (4 * 1024 * 1024, 60.0)
    assert (p.event_flush_bytes, p.event_flush_age_s) == (1024 * 1024, 60.0)


def test_policy_resolves_flag_then_env_then_default(monkeypatch) -> None:
    monkeypatch.setenv("TESTERKIT_FORWARD_CHANNEL_FLUSH_BYTES", "123")
    monkeypatch.setenv("TESTERKIT_FORWARD_CHANNEL_FLUSH_AGE", "7.5")
    monkeypatch.setenv("TESTERKIT_FORWARD_EVENT_FLUSH_BYTES", "junk")
    p = ForwardBatchPolicy.resolve(channel_flush_age=3.0, event_flush_age=-1)
    assert p.channel_flush_bytes == 123  # env
    assert p.channel_flush_age_s == 3.0  # flag beats env
    assert p.event_flush_bytes == 1024 * 1024  # unparseable env -> default
    assert p.event_flush_age_s == 60.0  # non-positive flag -> default
