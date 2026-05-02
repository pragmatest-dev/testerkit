"""Output format subscribers and utilities.

All output formats are implemented as ``EventSubscriber`` subclasses.
The subscriber registry in ``litmus.data.event_log.EventSubscriber._registry``
is the single source of truth for format name -> class mappings.
"""

from __future__ import annotations

from litmus.data.event_log import EventSubscriber
from litmus.data.subscribers._base import get_subscriber_class, list_subscribers
from litmus.data.subscribers.replay import replay_to_subscriber

# Report formats handled by litmus.reports (not subscribers).
_REPORT_FORMATS = {"html", "pdf"}


def is_report_format(format_name: str) -> bool:
    """Check if a format is handled by litmus.reports (not a subscriber)."""
    return format_name in _REPORT_FORMATS


__all__ = [
    "EventSubscriber",
    "get_subscriber_class",
    "is_report_format",
    "list_subscribers",
    "replay_to_subscriber",
]
