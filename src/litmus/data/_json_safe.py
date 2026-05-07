"""JSON-safe coercion for event payloads.

Vector params, observations, and other dynamic dicts end up in event
``model_dump_json()`` calls.  Most parametrize values are scalars, lists,
or dicts — fine as-is.  Bytes / class types / arbitrary objects (e.g. a
parametrize value that happens to be a class) need stringification or
they break Pydantic JSON serialization.

This helper centralizes the coercion policy so every event-builder uses
the same rules.  Lives in the data layer so producer (logger), pytest
plugin, and downstream subscribers can all import from one place
without reaching across module boundaries.
"""

from __future__ import annotations

from typing import Any


def json_safe(value: Any, _visited: set[int] | None = None) -> Any:
    """Coerce a value to a JSON-serializable representation.

    * Scalars (``None`` / ``str`` / ``int`` / ``float`` / ``bool``) pass through.
    * Lists and tuples recurse element-wise; tuples become lists.
    * Dicts recurse value-wise; keys stringified.
    * Bytes / bytearray render as ``"<bytes:N>"``.
    * Anything else falls back to ``repr(value)``.

    Cycle detection: tracks ``id()`` of mutable containers in ``_visited``
    so a self-referential dict (``d['self'] = d``) returns ``"<circular>"``
    instead of recursing forever.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes:{len(value)}>"

    visited = _visited if _visited is not None else set()
    if isinstance(value, (list, tuple, dict)):
        vid = id(value)
        if vid in visited:
            return "<circular>"
        visited.add(vid)
        try:
            if isinstance(value, dict):
                return {str(k): json_safe(v, visited) for k, v in value.items()}
            return [json_safe(v, visited) for v in value]
        finally:
            visited.discard(vid)

    return repr(value)


def coerce_dict(d: dict[str, Any] | None) -> dict[str, Any]:
    """Apply :func:`json_safe` to every value in ``d``; ``None`` → ``{}``.

    Used at every site that builds an event payload from a dict that may
    contain non-JSON-safe values (vector params from pytest parametrize,
    harness observations, etc.).
    """
    if d is None:
        return {}
    return {k: json_safe(v) for k, v in d.items()}
