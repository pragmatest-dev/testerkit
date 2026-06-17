"""Consumer verbs on litmus.channels: query (pull), live (push batches),
latest (push newest, conflated). Per test convention: serve=True uses the
canonical data dir (never tmp_path), isolated by a unique uuid channel name.
"""

from __future__ import annotations

import threading
import time
from uuid import uuid4

import pyarrow as pa

import litmus.channels as channels
from litmus.channels import _dedup_against_history
from litmus.data.channels.store import ChannelStore
from litmus.data.data_dir import resolve_data_dir


def _wait(pred, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _producer() -> ChannelStore:
    store = ChannelStore(resolve_data_dir(), uuid4(), serve=True)
    store.open()
    return store


def test_query_reads_written_samples() -> None:
    ch = f"verbtest.q_{uuid4().hex[:8]}"
    store = _producer()
    try:
        for i in range(5):
            store.write(ch, float(i))
        assert _wait(lambda: channels.query(ch).num_rows >= 5)
    finally:
        store.close()


def test_live_receives_batches() -> None:
    ch = f"verbtest.live_{uuid4().hex[:8]}"
    store = _producer()
    received: list = []
    unsub = channels.live(ch, received.append)
    try:
        time.sleep(0.3)  # let the subscription attach
        for i in range(3):
            store.write(ch, float(i))
        assert _wait(lambda: sum(b.num_rows for b in received) >= 3)
    finally:
        unsub()
        store.close()


def test_latest_conflates_to_newest() -> None:
    ch = f"verbtest.latest_{uuid4().hex[:8]}"
    store = _producer()
    seen: list = []
    unsub = channels.latest(ch, seen.append)
    try:
        time.sleep(0.3)
        store.write(ch, 42.0)
        assert _wait(lambda: any(float(s.value) == 42.0 for s in seen))
    finally:
        unsub()
        store.close()


def test_window_prefills_history_then_continues_live() -> None:
    """The stitch delivers the full union — every sequence once, no gap, no dup."""
    ch = f"verbtest.win_{uuid4().hex[:8]}"
    store = _producer()
    n_hist, n_live = 8, 5
    delivered: list[int] = []
    lock = threading.Lock()

    def collect(batch: pa.RecordBatch) -> None:
        seqs = batch.column("sample_offset").to_pylist()
        with lock:
            delivered.extend(seqs)

    try:
        for i in range(n_hist):
            store.write(ch, float(i))
        # History must reach the warm index before the window reads it.
        assert _wait(lambda: channels.query(ch).num_rows >= n_hist)

        unsub = channels.window(ch, collect, dur=3600.0)
        try:
            assert _wait(lambda: len(delivered) >= n_hist)  # prefill arrived
            for i in range(n_hist, n_hist + n_live):
                store.write(ch, float(i))
            assert _wait(lambda: len(delivered) >= n_hist + n_live)
        finally:
            unsub()
    finally:
        store.close()

    with lock:
        seen = sorted(delivered)
    # Exact union: 0..N+M-1, each exactly once. A gap means a lost sample; a
    # repeat means the seam double-counted.
    assert seen == list(range(n_hist + n_live))


def test_dedup_against_history_drops_covered_rows() -> None:
    """A live row at or below its session's history high-water is a duplicate."""
    batch = pa.record_batch(
        {
            "session_id": ["s1", "s1", "s1", "s2"],
            "sample_offset": [3, 4, 5, 0],
        }
    )
    # s1 history reached sample_offset 4; s2 has no history.
    survivors = _dedup_against_history(batch, {"s1": 4})
    assert survivors is not None
    pairs = list(
        zip(
            survivors.column("session_id").to_pylist(),
            survivors.column("sample_offset").to_pylist(),
            strict=True,
        )
    )
    # seq 3,4 on s1 are covered (≤4) and dropped; 5 survives; s2/0 survives.
    assert pairs == [("s1", 5), ("s2", 0)]
    # No history → nothing dropped.
    assert _dedup_against_history(batch, {}) is batch
