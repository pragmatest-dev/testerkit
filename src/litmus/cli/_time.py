"""CLI-local time utilities: duration parsing, local↔UTC conversion, display formatting.

The CLI is a client-edge converter — all timestamps flow to and from the query layer
as UTC ISO strings.  This module handles the two conversion directions:

  Input:  user value (duration / absolute / bare)  →  UTC ISO string (for query layer)
  Output: UTC datetime from query result            →  local+offset string (for display)

The ``--utc`` flag and ``TZ`` environment variable are honored for both directions.
Python's ``datetime.astimezone()`` automatically respects the ``TZ`` environment
variable on Linux/macOS, so no explicit TZ wrangling is needed here.

Duration grammar
----------------
``<N>d``  days    (e.g. ``7d``, ``30d``)
``<N>h``  hours   (e.g. ``4h``, ``12h``)
``<N>m``  minutes (e.g. ``30m``, ``90m``)
``<N>s``  seconds (e.g. ``120s``)

Where N is a positive integer.  Compound forms (``1h30m``) are not supported;
use a single unit or an absolute timestamp instead.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_DURATION_RE = re.compile(r"^(\d+)([dhms])$")


def parse_cli_duration(s: str) -> timedelta:
    """Parse a duration string into a :class:`datetime.timedelta`.

    Supports ``Nd`` (days), ``Nh`` (hours), ``Nm`` (minutes), ``Ns`` (seconds).

    Args:
        s: Duration string, e.g. ``"7d"``, ``"4h"``, ``"30m"``, ``"120s"``.

    Returns:
        Corresponding :class:`datetime.timedelta`.

    Raises:
        ValueError: If *s* does not match the expected format.

    Examples::

        parse_cli_duration("7d")  # timedelta(days=7)
        parse_cli_duration("4h")  # timedelta(hours=4)
        parse_cli_duration("30m") # timedelta(minutes=30)
        parse_cli_duration("90s") # timedelta(seconds=90)
    """
    m = _DURATION_RE.match(s.strip())
    if not m:
        raise ValueError(
            f"Invalid duration '{s}'. "
            "Expected '<N>d' (days), '<N>h' (hours), '<N>m' (minutes), "
            "or '<N>s' (seconds) — e.g. '7d', '4h', '30m'."
        )
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        return timedelta(days=n)
    if unit == "h":
        return timedelta(hours=n)
    if unit == "m":
        return timedelta(minutes=n)
    return timedelta(seconds=n)  # 's'


def _is_duration(s: str) -> bool:
    """Return ``True`` if *s* looks like a duration string (e.g. ``'7d'``, ``'4h'``)."""
    return bool(_DURATION_RE.match(s.strip()))


def resolve_since_until(s: str, *, utc: bool) -> str:
    """Convert a ``--since`` / ``--until`` CLI value to a UTC ISO datetime string.

    Three forms are accepted:

    1. **Relative duration** (``7d``, ``4h``, ``30m``, ``90s``): resolved to
       ``now(UTC) − duration``.  Timezone-free — no ``--utc`` / ``TZ`` effect.
    2. **Absolute value with explicit offset** (``2024-01-01T10:00:00+05:00`` or
       ``2024-01-01T10:00:00Z``): parsed and converted to UTC.  The user-supplied
       offset is always honored.
    3. **Bare date or datetime** (no offset, e.g. ``2024-01-01`` or
       ``2024-01-01T08:00:00``): interpreted as **local** time by default
       (``utc=False``), or as **UTC** when ``utc=True``.  The ``TZ``
       environment variable is respected automatically for local interpretation.

    Args:
        s: Raw CLI value from ``--since`` or ``--until``.
        utc: When ``True``, bare values are treated as UTC; when ``False``
            (the default), they are treated as local time.

    Returns:
        ISO 8601 UTC datetime string suitable for the query layer
        (e.g. ``'2024-01-01T03:00:00+00:00'``).

    Raises:
        ValueError: If *s* cannot be parsed by any of the three strategies.
    """
    s = s.strip()

    # 1. Relative duration — timezone-free, always resolves against now(UTC).
    if _is_duration(s):
        delta = parse_cli_duration(s)
        return (datetime.now(UTC) - delta).isoformat()

    # 2 + 3. ISO datetime (with or without offset).
    # Python 3.11+ fromisoformat handles trailing 'Z'; 3.9/3.10 need a shim.
    norm = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(norm)
    except ValueError:
        raise ValueError(
            f"Cannot parse time value '{s}'. "
            "Use a relative duration (e.g. '7d', '4h', '30m') or an ISO "
            "date/datetime (e.g. '2024-01-01', '2024-01-01T08:00:00', "
            "'2024-01-01T08:00:00+05:00')."
        ) from None

    if dt.tzinfo is not None:
        # Explicit offset supplied — honor it exactly, convert to UTC.
        return dt.astimezone(UTC).isoformat()

    # Bare (no offset): interpret as local or UTC per --utc flag.
    if utc:
        dt = dt.replace(tzinfo=UTC)
    else:
        # astimezone() on a naive datetime uses the local timezone,
        # which on Linux/macOS respects the TZ environment variable.
        dt = dt.astimezone()  # naive → local-aware
        dt = dt.astimezone(UTC)  # local-aware → UTC
    return dt.isoformat()


def format_ts(dt: datetime | str | None, *, utc: bool) -> str:
    """Format a datetime value for CLI output.

    - ``utc=False`` (default): returns the timestamp in **local time** with an
      **explicit UTC offset** (e.g. ``'2024-01-01T10:00:00+02:00'``).  The
      ``TZ`` environment variable is respected automatically.
    - ``utc=True``: returns a UTC timestamp ending in ``Z``
      (e.g. ``'2024-01-01T08:00:00Z'``).

    A naive timestamp is **never** returned.

    Args:
        dt: A :class:`datetime.datetime`, an ISO string, or ``None``.
            String values that carry no timezone are assumed to be UTC
            (matching the storage convention — all stored timestamps are UTC).
        utc: Display mode; see above.

    Returns:
        Formatted timestamp string, or ``''`` if *dt* is ``None`` / empty.
    """
    if dt is None:
        return ""

    if isinstance(dt, str):
        if not dt:
            return ""
        # Python 3.9/3.10 compat: replace trailing 'Z' before fromisoformat.
        norm = dt.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(norm)
        except ValueError:
            return dt  # unrecognised format — return raw string unchanged
        if parsed.tzinfo is None:
            # All stored timestamps are UTC; tag naive strings accordingly.
            parsed = parsed.replace(tzinfo=UTC)
        dt = parsed

    if utc:
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    local_dt = dt.astimezone()  # respects TZ env var on Linux/macOS
    return local_dt.strftime("%Y-%m-%dT%H:%M:%S%z")
