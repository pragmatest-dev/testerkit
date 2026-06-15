"""Unit tests for the shared lossy-live PushRelay engine.

Pure in-memory (no daemon): the relay's queue + drain + overflow + grouping,
exercised through a synchronous flush callback that records what it received.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Hashable

from litmus.data._push_relay import PushRelay


def test_flushes_published_items() -> None:
    flushed: list[int] = []
    lock = threading.Lock()

    def flush(_key: object, items: list[int]) -> None:
        with lock:
            flushed.extend(items)

    relay = PushRelay(flush=flush, max_weight=10, max_wait=0.01, queue_max=100, thread_name="t")
    for i in range(5):
        relay.publish(i)
    relay.close()

    with lock:
        assert sorted(flushed) == [0, 1, 2, 3, 4]


def test_groups_by_key() -> None:
    flushed: dict[Hashable, list[int]] = {}
    lock = threading.Lock()

    def flush(key: Hashable, items: list[int]) -> None:
        with lock:
            flushed.setdefault(key, []).extend(items)

    relay = PushRelay(
        flush=flush,
        max_weight=100,
        max_wait=0.05,
        queue_max=100,
        thread_name="t",
        key=lambda item: item % 2,
    )
    for i in range(6):
        relay.publish(i)
    relay.close()

    with lock:
        evens = sorted(flushed.get(0, []))
        odds = sorted(flushed.get(1, []))
    assert evens == [0, 2, 4]
    assert odds == [1, 3, 5]


def test_weight_bounds_the_burst() -> None:
    sizes: list[int] = []
    lock = threading.Lock()

    def flush(_key: object, items: list[int]) -> None:
        with lock:
            sizes.append(len(items))

    # weight=1 per item, max_weight=3 → no single flush group exceeds 3 items.
    relay = PushRelay(flush=flush, max_weight=3, max_wait=1.0, queue_max=100, thread_name="t")
    for i in range(9):
        relay.publish(i)
    relay.close()

    with lock:
        captured = list(sizes)
    assert sum(captured) == 9
    assert max(captured) <= 3


def test_drop_oldest_and_count_on_overflow() -> None:
    # Block the drain so the queue genuinely fills, forcing overflow.
    gate = threading.Event()

    def flush(_key: object, _items: list[int]) -> None:
        gate.wait(timeout=2.0)

    relay = PushRelay(flush=flush, max_weight=1, max_wait=0.01, queue_max=2, thread_name="t")
    # First publish is picked up by the drain (blocked in flush). The next
    # fill the size-2 queue; further publishes drop the oldest + count.
    for i in range(20):
        relay.publish(i)
    assert relay.dropped > 0
    gate.set()
    relay.close()


def test_close_drains_remaining_items() -> None:
    flushed: list[int] = []
    lock = threading.Lock()

    def flush(_key: object, items: list[int]) -> None:
        time.sleep(0.01)
        with lock:
            flushed.extend(items)

    relay = PushRelay(flush=flush, max_weight=1, max_wait=0.001, queue_max=100, thread_name="t")
    for i in range(10):
        relay.publish(i)
    relay.close()  # must drain the queue, not drop buffered items

    with lock:
        assert sorted(flushed) == list(range(10))


def test_on_close_callback_runs() -> None:
    closed: list[bool] = []
    relay = PushRelay(
        flush=lambda _key, _items: None,
        max_weight=1,
        max_wait=0.01,
        queue_max=10,
        thread_name="t",
        on_close=lambda: closed.append(True),
    )
    relay.close()
    assert closed == [True]
