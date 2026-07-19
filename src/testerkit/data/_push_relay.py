"""Shared lossy-live producer push relay.

The dumb-tickerplant tail every store's streaming producer needs: a producer
enqueues a wire item non-blocking; a background thread coalesces a bounded
burst and ships it down a held ``do_put``; a slow daemon/subscriber never
backpressures capture — the relay drops the oldest item on overflow and counts
it. Live = from-now; the durable record (the store's IPC segment / on-disk
object) is whole regardless, so a dropped push is never data loss.

This is the *engine* only — the queue, the overflow policy, the drain timing
(accumulate up to ``max_weight`` OR ``max_wait``), and grouping by key. The
*transport* (a held per-channel Flight writer vs. a pooled single-descriptor
``do_put``) and the *codec* (concat ``RecordBatch``es vs. ``from_pylist`` of
frame dicts) legitimately differ per store, so they live in the ``flush``
callback the caller supplies.

One engine for every store's streaming producer — channels and the files store
each grew their own copy before this consolidated them.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Hashable
from typing import Any


class PushRelay:
    """Bounded queue + background coalescing drain, drop-oldest on overflow.

    ``flush(key, items)`` ships one coalesced group; the relay groups queued
    items by ``key(item)`` and weights the burst by ``weight(item)`` (rows for
    a batch, 1 for a frame). It accumulates up to ``max_weight`` total weight
    OR ``max_wait`` seconds — whichever first — so a per-sample firehose
    coalesces into larger pushes while live latency stays bounded.
    """

    def __init__(
        self,
        *,
        flush: Callable[[Hashable, list[Any]], None],
        max_weight: int,
        max_wait: float,
        queue_max: int,
        thread_name: str,
        key: Callable[[Any], Hashable] = lambda _: "",
        weight: Callable[[Any], int] = lambda _: 1,
        poll_interval: float = 0.1,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._flush = flush
        self._key = key
        self._weight = weight
        self._max_weight = max_weight
        self._max_wait = max_wait
        self._poll = poll_interval
        self._on_close = on_close
        self._q: queue.Queue[Any] = queue.Queue(maxsize=queue_max)
        self._dropped = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._drain, name=thread_name, daemon=True)
        self._thread.start()

    @property
    def dropped(self) -> int:
        """Items dropped under overflow — the gap signal a subscriber can see.

        Live = from-now; an overflow drop never affects the durable record.
        """
        return self._dropped

    def publish(self, item: Any) -> None:
        """Enqueue an item non-blocking; drop-oldest + count on overflow.

        Never blocks the capture path: a full queue means the daemon/subscriber
        fell behind, so the oldest queued item is discarded to make room for the
        newest (live = from-now) rather than stalling the producer.
        """
        try:
            self._q.put_nowait(item)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            self._dropped += 1
            try:
                self._q.put_nowait(item)
            except queue.Full:
                pass

    def _drain(self) -> None:
        """Background consumer: block for an item, accumulate a bounded burst,
        group by key, flush each group. Drains the queue on stop so buffered
        items aren't lost at close."""
        while not self._stop.is_set() or not self._q.empty():
            try:
                first = self._q.get(timeout=self._poll)
            except queue.Empty:
                continue
            grouped: dict[Hashable, list[Any]] = {self._key(first): [first]}
            total = self._weight(first)
            deadline = time.monotonic() + self._max_wait
            while total < self._max_weight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = self._q.get(timeout=remaining)
                except queue.Empty:
                    break
                grouped.setdefault(self._key(item), []).append(item)
                total += self._weight(item)
            for group_key, items in grouped.items():
                self._flush(group_key, items)

    def close(self) -> None:
        """Signal stop, let the drain flush remaining items, then tear down."""
        self._stop.set()
        self._thread.join(timeout=5.0)
        if self._on_close is not None:
            self._on_close()
