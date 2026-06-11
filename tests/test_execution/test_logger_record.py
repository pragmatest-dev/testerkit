"""Tests for TestRunLogger.record()."""

from __future__ import annotations

from litmus.data.events import RecordEvent
from litmus.execution.logger import TestRunLogger


class _FakeEventLog:
    """Minimal event log that captures emitted events."""

    def __init__(self):
        self.events: list = []
        self.path = None

    def emit(self, event):
        self.events.append(event)

    def save_ref(self, key, data):
        return f"ref://{key}"

    def close(self):
        pass


class TestLoggerRecord:
    def _make_logger(self) -> tuple[TestRunLogger, _FakeEventLog]:
        logger = TestRunLogger(
            uut_serial="SN001",
            station_id="st1",
        )
        fake_log = _FakeEventLog()
        logger._event_log = fake_log  # type: ignore[assignment]
        logger._session_id = logger.test_run.id
        return logger, fake_log

    def test_record_with_active_step(self):
        logger, fake_log = self._make_logger()
        logger.start_step("step1")
        logger.record("fw_version", "1.2.3")

        records = [e for e in fake_log.events if isinstance(e, RecordEvent)]
        assert len(records) == 1
        assert records[0].key == "fw_version"
        assert records[0].value == "1.2.3"
        assert records[0].step_name == "step1"
        assert records[0].step_index == 0

    def test_record_without_active_step(self):
        logger, fake_log = self._make_logger()
        logger.record("build_id", 42)

        records = [e for e in fake_log.events if isinstance(e, RecordEvent)]
        assert len(records) == 1
        assert records[0].step_name == ""
        assert records[0].step_index == -1

    def test_record_no_event_log(self):
        """record() is a no-op when no event log is wired."""
        logger = TestRunLogger(
            uut_serial="SN001",
            station_id="st1",
        )
        # Should not raise
        logger.record("key", "value")
