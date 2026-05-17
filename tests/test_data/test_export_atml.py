"""Tests for ATML (IEEE 1636.1) subscriber."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

from litmus.data.exporters.atml import AtmlSubscriber
from litmus.data.models import TestRun

_NS_TR = "urn:IEEE-1636.1:2011:01:TestResultsCollection"
_NS_C = "urn:IEEE-1671:2010:Common"


class TestAtmlSubscriber:
    """Test the event-driven subscriber path."""

    def _write_via_subscriber(
        self,
        test_run: TestRun,
        tmp_path: Path,
        replay: Callable[[TestRun, Any], None],
    ) -> Path:
        sub = AtmlSubscriber(tmp_path)
        sub.open()
        replay(test_run, sub)
        # RunEnded triggers _write, but close is idempotent
        sub.close()
        run_id = str(test_run.id)[:8]
        return tmp_path / f"{run_id}.xml"

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

    def test_valid_xml(
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
        tree = ET.parse(result)
        root = tree.getroot()
        assert root.tag == f"{{{_NS_TR}}}TestResultsCollection"

    def test_result_set_metadata(
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
        tree = ET.parse(result)
        root = tree.getroot()
        rs = root.find(f"{{{_NS_TR}}}ResultSet")
        assert rs is not None
        assert rs.get("name") == "seq_power_validation"
        assert rs.get("status") == "Failed"

    def test_uut_from_events(
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
        tree = ET.parse(result)
        root = tree.getroot()
        uut = root.find(f".//{{{_NS_TR}}}UUT")
        assert uut is not None
        assert uut.get("serialNumber") == "DUT-001"
        assert uut.get("partNumber") == "PN-200"
        assert uut.get("partRevisionNumber") == "B"
        assert uut.get("batchNumber") == "LOT-42"

    def test_measurements_present(
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
        tree = ET.parse(result)
        root = tree.getroot()
        tests = root.findall(f".//{{{_NS_TR}}}Test")
        names = [t.get("name") for t in tests]
        assert "vout" in names
        assert "iout" in names
        assert "ilimit" in names
        assert "broken_sensor" in names

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
        tree = ET.parse(result)
        root = tree.getroot()
        groups = root.findall(f".//{{{_NS_TR}}}TestGroup")
        names = [g.get("name") for g in groups]
        assert "voltage_check" in names
        assert "current_limit" in names

    def test_limits_from_events(
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
        tree = ET.parse(result)
        root = tree.getroot()
        tests = root.findall(f".//{{{_NS_TR}}}Test")
        vout = next(t for t in tests if t.get("name") == "vout")
        limits = vout.find(f".//{{{_NS_C}}}Limits")
        assert limits is not None
        assert limits.get("comparator") == "GELE"

    def test_single_bound_limit(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        """LE comparator produces single Limit element."""
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        tree = ET.parse(result)
        root = tree.getroot()
        tests = root.findall(f".//{{{_NS_TR}}}Test")
        ilimit_test = next(t for t in tests if t.get("name") == "ilimit")
        limit = ilimit_test.find(f".//{{{_NS_C}}}Limit")
        assert limit is not None
        assert limit.get("comparator") == "LE"

    def test_nominal_comparator(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        """EQ comparator produces Limit with nominal datum."""
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        tree = ET.parse(result)
        root = tree.getroot()
        tests = root.findall(f".//{{{_NS_TR}}}Test")
        eq_test = next(t for t in tests if t.get("name") == "vref_eq")
        limit = eq_test.find(f".//{{{_NS_C}}}Limit")
        assert limit is not None
        assert limit.get("comparator") == "EQ"
        datum = limit.find(f"{{{_NS_C}}}Datum")
        assert datum is not None
        assert datum.get("value") == "1.25"

    def test_error_measurement_included(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        """value=None measurement is still exported with Error status."""
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        tree = ET.parse(result)
        root = tree.getroot()
        tests = root.findall(f".//{{{_NS_TR}}}Test")
        broken = next(t for t in tests if t.get("name") == "broken_sensor")
        assert broken.get("status") == "Error"

    def test_operator_element(
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
        tree = ET.parse(result)
        root = tree.getroot()
        op = root.find(f".//{{{_NS_TR}}}Operator")
        assert op is not None
        assert op.get("id") == "OP-42"
        assert op.get("name") == "Jane Doe"
