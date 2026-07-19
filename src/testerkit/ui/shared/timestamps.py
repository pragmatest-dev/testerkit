"""Shared timestamp utilities for UI components."""

from __future__ import annotations

from datetime import datetime

from testerkit.data.models import ensure_utc


def parse_iso_timestamp(s: str) -> datetime:
    """Parse ISO 8601 string to a UTC-aware datetime, handling 'Z' suffix.

    Naive strings are assumed UTC (the server's storage convention) and
    stamped without shifting; aware strings are converted to UTC.
    """
    return ensure_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))


def format_time_short(ts: str) -> str:
    """Extract HH:MM:SS from an ISO timestamp string."""
    return ts[11:19] if len(ts) >= 19 else ts
