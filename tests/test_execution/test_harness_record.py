"""Tests for TestHarness.record() delegation."""

from __future__ import annotations

from unittest.mock import MagicMock

from litmus.execution.harness import TestHarness


class TestHarnessRecord:
    def test_delegates_to_logger(self):
        logger = MagicMock()
        harness = TestHarness.__new__(TestHarness)
        harness._logger = logger
        harness.record("key", "value")
        logger.record.assert_called_once_with("key", "value")

    def test_no_logger(self):
        harness = TestHarness.__new__(TestHarness)
        harness._logger = None
        # Should not raise
        harness.record("key", "value")
