"""Tests for MDF4 subscriber."""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

from asammdf import MDF

from litmus.data.exporters.mdf4 import Mdf4Subscriber
from litmus.data.models import TestRun


class TestMdf4Subscriber:
    """Test the event-driven subscriber path."""

    def _write_via_subscriber(
        self,
        test_run: TestRun,
        tmp_path: Path,
        replay: Callable[[TestRun, Any], None],
    ) -> Path:
        sub = Mdf4Subscriber(tmp_path)
        sub.open()
        replay(test_run, sub)
        sub.close()
        run_id = str(test_run.id)[:8]
        return tmp_path / "exports" / "mdf4" / f"{run_id}.mf4"

    def test_creates_file(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        assert result.exists()

    def test_channel_groups(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        mdf = MDF(result)
        # 2 steps with measurements, skip step has none
        assert len(mdf.groups) == 2

    def test_signal_values(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        mdf = MDF(result)
        sig = mdf.get("vout", group=0)
        assert len(sig.samples) == 2
        assert abs(sig.samples[0] - 3.30) < 0.01

    def test_signal_units(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        mdf = MDF(result)
        sig = mdf.get("vout", group=0)
        assert sig.unit == "V"

    def test_signal_comment_xml(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        """Signal comment contains XML with limit metadata."""
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        mdf = MDF(result)
        sig = mdf.get("vout", group=0)
        assert "low_limit" in sig.comment
        assert "3.0" in sig.comment
        assert "GELE" in sig.comment

    def test_null_value_as_nan(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        mdf = MDF(result)
        sig = mdf.get("broken_sensor", group=1)
        assert math.isnan(sig.samples[0])

    def test_multiple_measurements_per_step(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        """Step with multiple measurements creates multiple signals."""
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        mdf = MDF(result)
        channel_names = [ch.name for ch in mdf.groups[0].channels if ch.name != "time"]
        assert "vout" in channel_names
        assert "iout" in channel_names
        assert "vref_eq" in channel_names

    def test_timestamps_are_sequential(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        mdf = MDF(result)
        sig = mdf.get("vout", group=0)
        assert sig.timestamps[0] == 0.0
        assert sig.timestamps[1] == 1.0
