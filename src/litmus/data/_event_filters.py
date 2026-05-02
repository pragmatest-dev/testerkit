"""Shared event filtering helpers."""

from __future__ import annotations


def event_matches_role(evt: dict, role: str) -> bool:
    """Check if an event relates to a given instrument role."""
    if evt.get("role") == role:
        return True
    if evt.get("instrument_role") == role:
        return True
    channel_id = evt.get("channel_id", "")
    if channel_id.startswith(f"{role}."):
        return True
    return False
