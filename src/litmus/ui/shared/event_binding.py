"""Thread-safe EventStore / ChannelStore → NiceGUI binding.

Two data paths:

1. **Events** (session lifecycle, instrument activity) →
   ``ui_subscribe(event_store, callback, ...)``

2. **Channel data** (measurements, waveforms) →
   ``bind_channel_store(store)`` once at startup, then
   ``ui_channel_data(channel_id).subscribe(handler)`` per component.

   Both stores deliver on a background Flight reader thread. The
   binding marshals every callback onto the NiceGUI event loop before
   it runs, so handlers may mutate elements directly. NiceGUI ``Event``
   handles multi-client delivery and auto-unsubscribe on client
   disconnect.

Usage::

    from litmus.ui.shared.event_binding import (
        bind_channel_store,
        ui_channel_data,
        ui_subscribe,
    )

    # Session / instrument events via EventStore:
    ui_subscribe(store, handle_event, event_type="instrument.read")

    # Channel data — bind once, subscribe per-component:
    bind_channel_store(station.channel_store)
    ui_channel_data("scope.waveform").subscribe(update_chart)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from nicegui import Event, core

from litmus.data.channels.client import ChannelClient
from litmus.data.channels.models import ChannelSample
from litmus.data.channels.store import ChannelStore
from litmus.data.event_store import EventStore


def _on_ui_loop(fn: Callable[..., None], *args: object) -> None:
    """Run ``fn(*args)`` on the NiceGUI event loop, safe from any thread.

    EventStore and ChannelStore both deliver on background Flight reader
    threads, but NiceGUI element mutation must happen on the UI event
    loop. ``core.loop`` is the loop NiceGUI runs on (set at server
    startup). With no running loop — tests, in-process use — run inline.
    """
    loop = core.loop
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(fn, *args)
    else:
        fn(*args)


def ui_subscribe(
    store: EventStore,
    callback: Callable[[dict], None],
    *,
    event_type: str | None = None,
    role: str | None = None,
    session_id: UUID | None = None,
    run_id: UUID | None = None,
    since: datetime | None = None,
) -> Callable[[], None]:
    """Subscribe to EventStore events, delivering on the NiceGUI UI thread.

    Wraps ``store.on_event()`` so the callback always runs on the UI
    event loop — safe to mutate any NiceGUI element directly.

    Returns an unsubscribe callable.
    """
    return store.on_event(
        lambda evt: _on_ui_loop(callback, evt),
        event_type=event_type,
        role=role,
        session_id=session_id,
        run_id=run_id,
        since=since,
    )


# ---------------------------------------------------------------------------
# Channel data via NiceGUI Event[ChannelSample]
# ---------------------------------------------------------------------------

_channel_signals: dict[str, Event] = {}
_bound_locations: set[str] = set()
_global_signal: Event | None = None


def ui_channel_data(channel_id: str) -> Event:
    """Get or create a NiceGUI Event for a channel.

    UI components subscribe from within their UI context::

        ui_channel_data("scope.waveform").subscribe(lambda sample: ...)

    Handlers run on the UI event loop — the binding marshals samples
    off the Flight reader thread — so they may mutate elements
    directly. NiceGUI handles multi-client delivery and auto-unsubscribe
    on client disconnect.
    """
    if channel_id not in _channel_signals:
        _channel_signals[channel_id] = Event()
    return _channel_signals[channel_id]


def ui_global_channel_data() -> Event:
    """Get or create a NiceGUI Event that fires for ALL channels."""
    global _global_signal
    if _global_signal is None:
        _global_signal = Event()
    return _global_signal


def reset_channel_signals() -> None:
    """Clear all channel event state. For testing only."""
    _channel_signals.clear()
    _bound_locations.clear()
    global _global_signal
    _global_signal = None


def _emit_to_signals(sample: ChannelSample) -> None:
    """Fan out one sample to its per-channel and global NiceGUI signals."""
    evt = _channel_signals.get(sample.channel_id)
    if evt is not None:
        evt.emit(sample)
    if _global_signal is not None:
        _global_signal.emit(sample)


def _dispatch_sample_to_signals(sample: ChannelSample) -> None:
    """Deliver a sample to its signals on the UI event loop.

    ``ChannelClient.on_channel`` calls this from a background reader
    thread; the loop hop keeps NiceGUI element mutation on the loop.
    """
    _on_ui_loop(_emit_to_signals, sample)


def bind_flight_location(location: str) -> Callable[[], None]:
    """Bridge a channels-daemon Flight server into NiceGUI Events.

    The ``litmus serve`` startup path acquires the channels daemon but
    isn't a writer — it has no ChannelStore of its own to bridge.
    This entry point takes the daemon's gRPC location directly,
    subscribes ``"*"`` via :class:`ChannelClient`, and fans samples
    out to the per-channel + global NiceGUI signals that pages
    subscribe to via :func:`ui_channel_data` /
    :func:`ui_global_channel_data`.

    Idempotent: a second call for the same location is a no-op.

    Returns a cleanup callable.
    """
    if not location or location in _bound_locations:
        return lambda: None

    _bound_locations.add(location)
    client = ChannelClient(location)
    unsub = client.on_channel("*", _dispatch_sample_to_signals)

    def _close() -> None:
        unsub()
        client.close()
        _bound_locations.discard(location)

    return _close


def bind_channel_store(store: ChannelStore) -> Callable[[], None]:
    """Bridge a ChannelStore into NiceGUI Events for all browser clients.

    If the store has a Flight server, subscribes via ``ChannelClient``
    for cross-process data — same path as :func:`bind_flight_location`.
    Otherwise, subscribes in-process via ``store.on_channel()``.

    Call once at startup. Returns a cleanup callable.
    """
    location = store.flight_location
    if location:
        return bind_flight_location(location)

    # Fallback: in-process only
    return store.on_channel(None, _dispatch_sample_to_signals)
