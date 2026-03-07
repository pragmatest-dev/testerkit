"""Session persistence via event log subscriber.

Writes a JSON metadata file per session under ``results/sessions/{date}/``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from litmus.data.events import EventBase, SessionEnded, SessionStarted


class SessionMetadata(BaseModel):
    """Persisted session metadata."""

    session_id: UUID
    session_type: str = "test_run"
    started_at: datetime
    ended_at: datetime | None = None
    outcome: str | None = None

    # Station
    station_id: str
    dut_serial: str
    product_id: str | None = None
    operator_id: str | None = None

    # References
    channel_refs: list[str] = Field(default_factory=list)
    run_id: UUID | None = None
    custom_metadata: dict[str, Any] = Field(default_factory=dict)


class SessionSubscriber:
    """Writes session metadata JSON on session close."""

    format_name = "sessions"
    event_types = {SessionStarted, SessionEnded}

    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_dir = sessions_dir
        self._metadata: SessionMetadata | None = None

    def open(self) -> None:
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    def on_event(self, event: EventBase) -> None:
        if isinstance(event, SessionStarted):
            self._metadata = SessionMetadata(
                session_id=event.session_id,
                session_type=event.session_type,
                started_at=event.occurred_at,
                station_id=event.station_id,
                dut_serial=event.dut_serial,
                product_id=event.product_id,
                operator_id=event.operator_id,
                channel_refs=list(event.channel_refs),
                run_id=event.run_id,
                custom_metadata=dict(event.custom_metadata),
            )
        elif isinstance(event, SessionEnded) and self._metadata is not None:
            self._metadata.ended_at = event.occurred_at
            self._metadata.outcome = event.outcome

    def close(self) -> None:
        if self._metadata is None:
            return
        date_dir = self._sessions_dir / date.today().isoformat()
        date_dir.mkdir(parents=True, exist_ok=True)
        path = date_dir / f"{self._metadata.session_id}.json"
        path.write_text(
            json.dumps(
                self._metadata.model_dump(mode="json"),
                indent=2,
            ),
            encoding="utf-8",
        )
