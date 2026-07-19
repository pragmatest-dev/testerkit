"""Tests for TDMS subscriber."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("nptdms")
from nptdms import TdmsFile  # pyright: ignore[reportMissingImports]

from testerkit.data.exporters.tdms import TdmsSubscriber
from testerkit.data.models import TestRun


class TestTdmsSubscriber:
    """Test the event-driven subscriber path."""

    def _write_via_subscriber(
        self,
        test_run: TestRun,
        tmp_path: Path,
        replay: Callable[[TestRun, Any], None],
    ) -> Path:
        sub = TdmsSubscriber(tmp_path)
        sub.open()
        replay(test_run, sub)
        sub.close()
        run_id = str(test_run.id)[:8]
        return tmp_path / "exports" / "tdms" / f"{run_id}.tdms"

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

    def test_root_properties(
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
        tf = TdmsFile.read(result)
        props = dict(tf.properties)
        assert props["station_id"] == "station_alpha"
        assert props["uut_serial_number"] == "UUT-001"

    def test_step_groups(
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
        tf = TdmsFile.read(result)
        names = [g.name for g in tf.groups()]
        assert "power.output.voltage" in names
        assert "power.protection" in names

    def test_channels_rectangular(
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
        tf = TdmsFile.read(result)
        grp = tf["power.output.voltage"]
        lengths = [len(c) for c in grp.channels()]
        assert len(set(lengths)) == 1

    def test_input_channels(
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
        tf = TdmsFile.read(result)
        grp = tf["power.output.voltage"]
        ch_names = [c.name for c in grp.channels()]
        assert "in_vin" in ch_names
        assert "in_load" in ch_names

    def test_measurement_properties(
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
        tf = TdmsFile.read(result)
        vout_ch = tf["power.output.voltage"]["vout"]
        props = dict(vout_ch.properties)
        assert props["unit"] == "V"
        assert props["limit_comparator"] == "GELE"

    def test_groups_from_step_path(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        """Steps use flattened step_path (/ → .) as group names."""
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        tf = TdmsFile.read(result)
        group_names = [g.name for g in tf.groups()]
        assert "power.output.voltage" in group_names
        assert "power.protection" in group_names
