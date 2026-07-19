"""Unified reference protocol for external data storage.

Every ``out_*`` column value is either a raw scalar (inline) or a URI
pointing to external storage:

- ``channel://scope.ch1.waveform?session=abc`` — numeric time-series in ChannelStore
- ``file://2026-03-08/abc123_ref/waveform.npz`` — blob in local file

``classify_value()`` decides the storage route. ``is_ref()`` / ``ref_scheme()``
dispatch on retrieval.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import parse_qs, quote, unquote, urlencode

from pydantic import BaseModel


class ChannelTicket(BaseModel):
    """A reference to channel data: a channel + session, optionally pinned to
    one sample by its ``sample_offset``.

    ``sample_offset`` is set when the reference points at exactly one channel row (a
    single ``write`` / ``observe``); ``None`` means the whole channel+session.
    The single-sample form is what lets each vector of a sweep reference its own
    sample instead of the whole channel.
    """

    channel_id: str
    session_id: str
    sample_offset: int | None = None


@runtime_checkable
class Latchable(Protocol):
    """Structural type for handle objects observe() can latch on.

    Per design doc §4: ``observe(name, ref)`` should stamp the
    existing URI without re-writing when ``ref`` is a handle to data
    already in a store. Handles (channel sinks, file streaming sinks,
    anything else with a stable ``.uri`` for the data it represents)
    expose this property; observe checks for it via ``isinstance``
    against this Protocol.

    ``uri`` must be a ``channel://`` or ``file://`` reference string
    pointing at the data the handle wraps — the same string the store
    would have returned from a write() call.
    """

    @property
    def uri(self) -> str: ...


def classify_value(value: Any) -> Literal["scalar", "numeric_array", "channel", "blob"]:
    """Classify a value for storage routing.

    - **scalar**: int, float, str, bool, None — stored inline
    - **numeric_array**: list/tuple/array of bool/int/float/str leaves,
      waveform tuple, numpy array — ChannelStore. (The name says
      "numeric" but post-item-6 + C2 typed leaf-types it covers any
      primitive leaf — including str arrays for status streams. The
      literal stays for API stability.)
    - **channel**: dict with numeric/structured data — ChannelStore
      (flexible / struct schema)
    - **blob**: everything else (bytes, Path, pickle-only objects) —
      file ref
    """
    if isinstance(value, (int, float, str, bool, type(None))):
        return "scalar"
    if isinstance(value, dict):
        return "channel"
    if isinstance(value, (list, tuple)) and len(value) >= 1:
        first = value[0]
        # Item 6 + C2 typed leaf-types: bool / int / float / str arrays
        # all route to ChannelStore. Order: bool BEFORE int because
        # ``True`` is also an ``int`` in Python; if int came first,
        # bool arrays would still match (via subclass) but the
        # explicit bool check documents the intent. ``str`` reaches
        # the typed-str-leaf codepath C2 added.
        if isinstance(first, (bool, int, float, str)):
            return "numeric_array"
        if isinstance(first, (list, tuple)):  # waveform: ([samples], dt)
            return "numeric_array"
    if hasattr(value, "tolist"):  # numpy array
        return "numeric_array"
    return "blob"


def make_channel_uri(channel_id: str, session_id: str, sample_offset: int | None = None) -> str:
    """Build a ``channel://`` URI for a channel data reference.

    ``sample_offset`` pins the reference to one channel row (omitted → whole
    channel+session). ``session`` MUST stay first in the query string: the
    runs-index ref scanner regex-extracts session from the URI, so the
    sample_offset is appended after it.

    >>> make_channel_uri("scope.ch1.waveform", "abc123")
    'channel://scope.ch1.waveform?session=abc123'
    >>> make_channel_uri("scope.ch1.waveform", "abc123", sample_offset=7)
    'channel://scope.ch1.waveform?session=abc123&sample_offset=7'
    """
    query = urlencode({"session": session_id})
    if sample_offset is not None:
        query += f"&{urlencode({'sample_offset': sample_offset})}"
    return f"channel://{quote(channel_id, safe='.')}?{query}"


def parse_channel_uri(uri: str) -> ChannelTicket:
    """Parse a ``channel://`` URI into a :class:`ChannelTicket`.

    A URI without ``sample_offset`` parses to ``sample_offset=None`` (whole channel+session).

    >>> parse_channel_uri("channel://scope.ch1.waveform?session=abc123")
    ChannelTicket(channel_id='scope.ch1.waveform', session_id='abc123', sample_offset=None)
    >>> parse_channel_uri("channel://scope.ch1.waveform?session=abc123&sample_offset=7").sample_offset
    7
    """
    if not uri.startswith("channel://"):
        raise ValueError(f"Not a channel URI: {uri!r}")
    rest = uri[len("channel://") :]
    sample_offset: int | None = None
    if "?" in rest:
        channel_id, query = rest.split("?", 1)
        params = parse_qs(query)
        session_id = params.get("session", [""])[0]
        raw_sample_offset = params.get("sample_offset", [None])[0]
        if raw_sample_offset is not None:
            sample_offset = int(raw_sample_offset)
    else:
        channel_id = rest
        session_id = ""
    return ChannelTicket(
        channel_id=unquote(channel_id), session_id=session_id, sample_offset=sample_offset
    )


def is_ref(value: object) -> bool:
    """Check if a value is a URI reference (has ``://`` scheme)."""
    return isinstance(value, str) and value.startswith(("channel://", "file://"))


def ref_scheme(value: str) -> str:
    """Extract the scheme from a URI reference.

    >>> ref_scheme("channel://scope.ch1?session=abc")
    'channel'
    >>> ref_scheme("file://2026-03-08/ref/wave.npz")
    'file'
    """
    return value.split("://", 1)[0]
