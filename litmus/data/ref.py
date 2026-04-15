"""Unified reference protocol for external data storage.

Every ``out_*`` column value is either a raw scalar (inline) or a URI
pointing to external storage:

- ``channel://scope.ch1.waveform?session=abc`` — numeric time-series in ChannelStore
- ``file://2026-03-08/abc123_ref/waveform.npz`` — blob in local file

``classify_value()`` decides the storage route. ``is_ref()`` / ``ref_scheme()``
dispatch on retrieval.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import parse_qs, quote, unquote, urlencode


def classify_value(value: Any) -> Literal["scalar", "numeric_array", "channel", "blob"]:
    """Classify a value for storage routing.

    - **scalar**: int, float, str, bool, None — stored inline
    - **numeric_array**: list/tuple of numbers, waveform tuple, numpy array — ChannelStore
    - **channel**: dict with numeric/structured data — ChannelStore (flexible schema)
    - **blob**: everything else (bytes, Path, pickle-only objects) — file ref
    """
    if isinstance(value, (int, float, str, bool, type(None))):
        return "scalar"
    if isinstance(value, dict):
        return "channel"
    if isinstance(value, (list, tuple)) and len(value) >= 1:
        first = value[0]
        if isinstance(first, (int, float)):
            return "numeric_array"
        if isinstance(first, (list, tuple)):  # waveform: ([samples], dt)
            return "numeric_array"
    if hasattr(value, "tolist"):  # numpy array
        return "numeric_array"
    return "blob"


def make_channel_uri(channel_id: str, session_id: str) -> str:
    """Build a ``channel://`` URI for a channel data reference.

    >>> make_channel_uri("scope.ch1.waveform", "abc123")
    'channel://scope.ch1.waveform?session=abc123'
    """
    return f"channel://{quote(channel_id, safe='.')}?{urlencode({'session': session_id})}"


def parse_channel_uri(uri: str) -> tuple[str, str]:
    """Parse a ``channel://`` URI into (channel_id, session_id).

    >>> parse_channel_uri("channel://scope.ch1.waveform?session=abc123")
    ('scope.ch1.waveform', 'abc123')
    """
    if not uri.startswith("channel://"):
        raise ValueError(f"Not a channel URI: {uri!r}")
    rest = uri[len("channel://") :]
    if "?" in rest:
        channel_id, query = rest.split("?", 1)
        params = parse_qs(query)
        session_id = params.get("session", [""])[0]
    else:
        channel_id = rest
        session_id = ""
    return unquote(channel_id), session_id


def is_ref(value: object) -> bool:
    """Check if a value is a URI reference (has ``://`` scheme)."""
    return isinstance(value, str) and "://" in value


def ref_scheme(value: str) -> str:
    """Extract the scheme from a URI reference.

    >>> ref_scheme("channel://scope.ch1?session=abc")
    'channel'
    >>> ref_scheme("file://2026-03-08/ref/wave.npz")
    'file'
    """
    return value.split("://", 1)[0]
