"""Shared held-open ``do_get`` subscription reader.

Channels (``on_channel`` / ``on_channel_batch``) and the files frame stream all
spawn the same thing: a daemon thread holding ONE ``do_get`` stream open,
handing each arriving batch to a decode callback until an unsubscribe signal.
This is that loop, written once — the consumer-side mirror of the shared
producer ``PushRelay``.

Per-caller differences ride flags, not copies of the loop:

- ``client_stop`` — an optional shared event (a client-wide ``close()``) that
  also ends the loop, on top of this subscription's own unsub.
- ``swallow_errors`` — drop transport errors silently (the files contract);
  otherwise a mid-stream error re-raises unless we're already stopping (the
  channels contract).
- ``on_close`` — per-subscription teardown run by ``unsub`` (files closes its
  dedicated client + releases the daemon ref).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pyarrow as pa
import pyarrow.flight as flight


def subscribe(
    client: flight.FlightClient,
    ticket: flight.Ticket,
    on_batch: Callable[[pa.RecordBatch], None],
    *,
    name: str = "flight-sub",
    client_stop: threading.Event | None = None,
    swallow_errors: bool = False,
    on_close: Callable[[], None] | None = None,
) -> tuple[Callable[[], None], threading.Thread]:
    """Hold a ``do_get`` stream open, hand each batch to ``on_batch`` until unsub.

    Returns ``(unsub, thread)``. ``unsub`` stops the reader and runs ``on_close``;
    ``thread`` is the daemon reader (callers that join on close keep a reference).
    """
    stop = threading.Event()

    def _stopped() -> bool:
        return stop.is_set() or bool(client_stop and client_stop.is_set())

    def _run() -> None:
        try:
            for chunk in client.do_get(ticket):
                if _stopped():
                    break
                on_batch(chunk.data)
        except (OSError, pa.ArrowException):
            if not swallow_errors and not _stopped():
                raise

    thread = threading.Thread(target=_run, daemon=True, name=name)
    thread.start()

    def unsub() -> None:
        stop.set()
        if on_close is not None:
            on_close()

    return unsub, thread
