"""Tests for SessionSubscriber and SessionMetadata."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from litmus.data.events import SessionEnded, SessionStarted
from litmus.data.sessions import SessionMetadata, SessionSubscriber


class TestSessionMetadata:
    def test_roundtrip(self):
        sid = uuid4()
        meta = SessionMetadata(
            session_id=sid,
            session_type="test_run",
            started_at=datetime.now(UTC),
            station_id="st1",
            dut_serial="SN001",
            product_id="PROD-1",
            run_id=uuid4(),
        )
        json_str = meta.model_dump_json()
        restored = SessionMetadata.model_validate_json(json_str)
        assert restored.session_id == sid
        assert restored.session_type == "test_run"
        assert restored.station_id == "st1"


class TestSessionSubscriber:
    def test_writes_json(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sub = SessionSubscriber(sessions_dir)
        sub.open()

        sid = uuid4()
        run_id = uuid4()
        now = datetime.now(UTC)

        start = SessionStarted(
            session_id=sid,
            run_id=run_id,
            session_type="test_run",
            station_id="st1",
            dut_serial="SN001",
        )
        start.occurred_at = now
        sub.on_event(start)

        end = SessionEnded(
            session_id=sid,
            run_id=run_id,
            outcome="pass",
        )
        sub.on_event(end)
        sub.close()

        # Find the written JSON
        json_files = list(sessions_dir.rglob("*.json"))
        assert len(json_files) == 1

        restored = SessionMetadata.model_validate_json(json_files[0].read_text())
        assert restored.session_id == sid
        assert restored.outcome == "pass"
        assert restored.station_id == "st1"

    def test_no_write_without_start(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sub = SessionSubscriber(sessions_dir)
        sub.open()
        sub.close()
        json_files = list(sessions_dir.rglob("*.json"))
        assert len(json_files) == 0
