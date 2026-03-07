"""Data models for the channel store.

ChannelSource and ChannelSegment are defined here for Phase 3+ (sessions,
retention, channel metadata). They are not yet consumed by ChannelStore.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ChannelSource(BaseModel):
    """Describes a channel data source (one per instrument method)."""

    id: str  # "dmm.dc_voltage"
    instrument_role: str
    data_type: str = "float64"
    units: str | None = None


class ChannelSegment(BaseModel):
    """Metadata for one Arrow IPC file segment."""

    source_id: str
    partition: str  # "2026-03-06"
    started_at: datetime
    ended_at: datetime
    file_path: str
    row_count: int
