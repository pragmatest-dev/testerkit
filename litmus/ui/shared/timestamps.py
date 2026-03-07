"""Shared timestamp utilities for UI components."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_iso_timestamp(s: str) -> datetime:
    """Parse ISO 8601 string to UTC datetime, handling 'Z' suffix."""
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(UTC)


def format_time_short(ts: str) -> str:
    """Extract HH:MM:SS from an ISO timestamp string."""
    return ts[11:19] if len(ts) >= 19 else ts
