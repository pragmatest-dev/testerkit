"""Consumer verbs on litmus.channels: query (pull), live (push batches),
latest (push newest, conflated). Per test convention: serve=True uses the
canonical data dir (never tmp_path), isolated by a unique uuid channel name.
"""

from __future__ import annotations

import time
from uuid import uuid4

import litmus.channels as channels
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
