"""Shared utilities for the analysis module."""

from __future__ import annotations

from datetime import datetime

from testerkit.data.models import ensure_utc


def parse_datetime(value: str | datetime | None) -> datetime | None:
    """Parse a datetime string or pass through a datetime object.

    Handles ISO format strings and the common "Z" UTC suffix. Returns a
    UTC-aware datetime (naive inputs are assumed UTC, the server's storage
    convention), or None on failure (malformed strings, None input).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    try:
        return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None
