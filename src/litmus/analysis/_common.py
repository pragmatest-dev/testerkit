"""Shared utilities for the analysis module."""

from __future__ import annotations

from datetime import datetime


def parse_datetime(value: str | datetime | None) -> datetime | None:
    """Parse a datetime string or pass through a datetime object.

    Handles ISO format strings and the common "Z" UTC suffix.
    Returns None on failure (malformed strings, None input).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
