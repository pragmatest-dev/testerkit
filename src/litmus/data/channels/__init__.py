"""Channel data storage — time-series materialized from instrument events."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from litmus.data.channels.client import ChannelClient
from litmus.data.channels.models import ChannelDescriptor, ChannelSample
from litmus.data.channels.store import ChannelStore
from litmus.data.event_store import EventStore

__all__ = [
    "ChannelClient",
    "ChannelDescriptor",
    "ChannelSample",
    "ChannelStore",
    "channel_subscribe",
]


def channel_subscribe(
    event_store: EventStore,
    channel_id: str,
    callback: Callable[[dict], None],
    *,
    resource: str | None = None,
    session_id: UUID | None = None,
    since: datetime | None = None,
) -> Callable[[], None]:
    """Cross-process channel subscription via EventStore.

    Filters instrument.read/instrument.set events by channel_id.
    Works across processes since EventStore queries via Arrow Flight.

    NOTE: not currently consumed anywhere in src/. Kept as the
    EventStore-bridging entry point — see ROADMAP "Channel
    EventStore-bridging subscription" for the wiring plan.
    """

    def _filter(evt: dict) -> None:
        if evt.get("channel_id") != channel_id:
            return
        if resource and evt.get("resource") != resource:
            return
        callback(evt)

    unsub_read = event_store.on_event(
        _filter,
        event_type="instrument.read",
        session_id=session_id,
        since=since,
    )
    unsub_set = event_store.on_event(
        _filter,
        event_type="instrument.set",
        session_id=session_id,
        since=since,
    )

    def unsub() -> None:
        unsub_read()
        unsub_set()

    return unsub
