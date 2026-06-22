"""Replay stored events through a subscriber — the post-hoc bridge.

This replaces the old ``reconstruct_test_run → exporter.export()`` chain.
Any subscriber can be fed stored events to produce its output format
after the fact, without needing a live test session.

Usage::

    from litmus.data.subscribers.replay import replay_to_subscriber
    from litmus.data.exporters.json_exporter import JsonSubscriber

    sub = JsonSubscriber(output_dir=Path("out"))
    replay_to_subscriber(sub, event_dicts)
"""

from __future__ import annotations

import warnings
from typing import Any

from pydantic import TypeAdapter, ValidationError

from litmus.data.event_log import EventSubscriber
from litmus.data.events import Event

_event_adapter: TypeAdapter[Any] = TypeAdapter(Event)


def replay_to_subscriber(
    subscriber: EventSubscriber,
    events: list[dict[str, Any]],
) -> None:
    """Deserialize event dicts and feed them through a subscriber.

    Args:
        subscriber: An ``EventSubscriber`` instance (has ``open``,
            ``on_event``, ``close``, and ``event_types``).
        events: Raw event dicts, e.g. from ``EventLog.events()``
            or ``EventStore.events(session_id=...)``.
    """
    subscriber.open()
    try:
        for raw in events:
            try:
                event = _event_adapter.validate_python(raw)
            except ValidationError:
                warnings.warn(
                    f"Skipping invalid event: {raw.get('event_type', '?')}",
                    stacklevel=2,
                )
                continue
            if type(event) in subscriber.event_types:
                subscriber.on_event(event)
    finally:
        subscriber.close()
