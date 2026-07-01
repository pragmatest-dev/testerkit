"""Tests for STDF V4 subscriber."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("Semi_ATE")
from Semi_ATE.STDF import FAR, MIR, MRR, PIR, PRR, PTR  # pyright: ignore[reportMissingImports]
from Semi_ATE.STDF.STDR import STDR  # pyright: ignore[reportMissingImports]

from litmus.data.exporters.stdf import StdfSubscriber
from litmus.data.models import TestRun


def _read_records(path: Path) -> list[STDR]:
    """Parse STDF binary into record objects using Semi-ATE-STDF."""
    import struct

    data = path.read_bytes()
    records = []
    offset = 0

    record_map = {
        (0, 10): FAR,
        (1, 10): MIR,
        (5, 10): PIR,
        (15, 10): PTR,
        (5, 20): PRR,
        (1, 20): MRR,
    }

    while offset < len(data):
        if offset + 4 > len(data):
            break
        rec_len, rec_typ, rec_sub = struct.unpack_from("<HBB", data, offset)
        record_bytes = data[offset : offset + 4 + rec_len]
        offset += 4 + rec_len

        cls = record_map.get((rec_typ, rec_sub))
        if cls is not None:
            rec = cls()
            rec(endian="<", record=record_bytes)
            records.append(rec)

    return records


class TestStdfSubscriber:
    """Test the event-driven subscriber path."""

    def _write_via_subscriber(
        self,
        test_run: TestRun,
        tmp_path: Path,
        replay: Callable[[TestRun, Any], None],
    ) -> Path:
        sub = StdfSubscriber(tmp_path)
        sub.open()
        replay(test_run, sub)
        sub.close()
        run_id = str(test_run.id)[:8]
        return tmp_path / "exports" / "stdf" / f"{run_id}.stdf"

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
        assert result.stat().st_size > 0

    def test_record_sequence(
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
        records = _read_records(result)
        types = [r.id for r in records]
        assert types[0] == "FAR"
        assert types[1] == "MIR"
        assert types[-2] == "PRR"
        assert types[-1] == "MRR"

    def test_ptr_count(
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
        records = _read_records(result)
        ptrs = [r for r in records if r.id == "PTR"]
        expected = sum(len(v.measurements) for s in realistic_test_run.steps for v in s.vectors)
        assert len(ptrs) == expected

    def test_mir_metadata(
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
        records = _read_records(result)
        mir = records[1]
        assert mir.get_value("NODE_NAM") == "station_alpha"
        assert mir.get_value("PART_TYP") == "PN-200"

    def test_far_version(
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
        records = _read_records(result)
        far = records[0]
        assert far.get_value("CPU_TYPE") == 2
        assert far.get_value("STDF_VER") == 4

    def test_ptr_values(
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
        records = _read_records(result)
        ptrs = [r for r in records if r.id == "PTR"]
        first = ptrs[0]
        result_value = first.get_value("RESULT")
        test_txt = first.get_value("TEST_TXT")
        assert isinstance(result_value, float)
        assert isinstance(test_txt, str)
        assert abs(result_value - 3.30) < 0.01
        assert "vout" in test_txt
        assert first.get_value("UNITS") == "V"

    def test_null_value_flagged(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        """value=None measurements have TEST_FLG bit 1 set (invalid)."""
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        records = _read_records(result)
        ptrs = [r for r in records if r.id == "PTR"]

        def _txt(p: STDR) -> str:
            t = p.get_value("TEST_TXT")
            assert isinstance(t, str)
            return t

        broken = next(p for p in ptrs if "broken_sensor" in _txt(p))
        test_flg = broken.get_value("TEST_FLG")
        assert isinstance(test_flg, list)
        assert test_flg[1] == "1"

    def test_prr_part_id(
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
        records = _read_records(result)
        prr = next(r for r in records if r.id == "PRR")
        assert prr.get_value("PART_ID") == "UUT-001"

    def test_site_num_single_uut_is_zero(
        self,
        realistic_test_run: TestRun,
        tmp_path: Path,
        replay_events: Callable[[TestRun, Any], None],
    ):
        """A single-UUT run (site_index is None) emits SITE_NUM 0 on every
        part record (PIR/PTR/PRR) — 0-based, per the site_index contract."""
        result = self._write_via_subscriber(
            realistic_test_run,
            tmp_path,
            replay_events,
        )
        records = _read_records(result)
        part_records = [r for r in records if r.id in ("PIR", "PTR", "PRR")]
        assert part_records  # sanity: the run produced part records
        for r in part_records:
            assert r.get_value("SITE_NUM") == 0


def test_build_ptr_threads_site_num_for_multi_site():
    """A multi-site worker's site_index flows straight into PTR SITE_NUM."""
    from litmus.data.exporters.stdf import _build_ptr

    raw = _build_ptr(1, 3, "step", "meas", 1.0, "passed", None, 0.0, 2.0, "V")
    ptr = PTR()
    ptr(endian="<", record=raw)
    assert ptr.get_value("SITE_NUM") == 3
