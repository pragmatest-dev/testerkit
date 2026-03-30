"""Subscriber lookup functions.

Registration is automatic via ``EventSubscriber.__init_subclass__``.
Built-in subscribers are imported in ``__init__.py``.
Third-party plugins are loaded via entry points.
"""

from __future__ import annotations

from litmus.data.event_log import EventSubscriber


def get_subscriber_class(format_name: str) -> type[EventSubscriber] | None:
    """Look up a subscriber class by format name."""
    return EventSubscriber._registry.get(format_name)


def list_subscribers() -> list[str]:
    """Return sorted list of all registered subscriber format names."""
    return sorted(EventSubscriber._registry)
