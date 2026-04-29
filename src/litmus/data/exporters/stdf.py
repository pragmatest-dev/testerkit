"""STDF subscriber — Semi-ATE-STDF library.

EventSubscriber that accumulates events and writes STDF V4 binary on close.
Record sequence: FAR → MIR → PIR → PTR* → PRR → MRR.

STDF V4 is inherently flat: one PIR/PRR pair per part insertion, PTR
records for each parametric result. There is no step hierarchy concept
in the spec — step name is encoded in TEST_TXT as ``step/measurement``.
InstrumentConnected events are not captured because STDF has no
equipment identity records beyond the MIR NODE_NAM field.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from pathlib import Path
from typing import Any

from Semi_ATE.STDF import FAR, MIR, MRR, PIR, PRR, PTR  # pyright: ignore[reportMissingImports]

from litmus.data.event_log import EventSubscriber
from litmus.data.events import (
    MeasurementRecorded,
    RunEnded,
    RunStarted,
)
from litmus.data.subscribers._output_file import OutputFile


def _dt_to_epoch(dt: object) -> int:
    """Convert datetime to Unix epoch uint32. Returns 0 if None."""
    if dt is None:
        return 0
    return int(calendar.timegm(dt.timetuple()))  # type: ignore[union-attr]


def _make_test_flg(outcome: str | None, value: float | None) -> list[str]:
    """Build TEST_FLG 8-bit list from outcome string."""
    bits = ["0"] * 8
    if value is None:
        bits[1] = "1"  # result invalid
    if outcome is None or outcome == "skip":
        bits[4] = "1"  # not executed
        bits[6] = "1"  # pass/fail not valid
    elif outcome == "error":
        bits[1] = "1"  # result invalid
        bits[6] = "1"  # pass/fail not valid
    elif outcome == "fail":
        bits[7] = "1"  # fail
    return bits


def _make_opt_flag(
    comparator: str | None,
    low: float | None,
    high: float | None,
) -> list[str]:
    """Build OPT_FLAG 8-bit list for limit validity and exclusivity."""
    bits = ["0"] * 8
    comp = comparator or "GELE"

    has_low = low is not None and comp not in ("EQ", "NE", "LT", "LE")
    has_high = high is not None and comp not in ("EQ", "NE", "GT", "GE")

    if not has_low:
        bits[4] = "1"  # no low limit
        bits[2] = "1"  # no low spec
    if not has_high:
        bits[5] = "1"  # no high limit
        bits[3] = "1"  # no high spec

    if comp in ("GTLT", "GTLE"):
        bits[6] = "1"  # low limit exclusive
    if comp in ("GTLT", "GELT"):
        bits[7] = "1"  # high limit exclusive

    return bits


def _pack_record(rec: object) -> bytes:
    """Pack a Semi-ATE STDF record to bytes.

    Semi-ATE's __repr__ returns bytes (the binary STDF record), not str.
    """
    return rec.__repr__()  # type: ignore[return-value]


def _build_ptr(
    test_num: int,
    step_name: str,
    meas_name: str,
    value: float | None,
    outcome: str | None,
    comparator: str | None,
    limit_low: float | None,
    limit_high: float | None,
    units: str | None,
) -> bytes:
    """Build and pack a single PTR record."""
    ptr = PTR()
    ptr.set_value("TEST_NUM", test_num)
    ptr.set_value("HEAD_NUM", 1)
    ptr.set_value("SITE_NUM", 1)
    ptr.set_value("TEST_FLG", _make_test_flg(outcome, value))
    ptr.set_value("PARM_FLG", ["0"] * 8)
    ptr.set_value("RESULT", value if value is not None else 0.0)
    ptr.set_value("TEST_TXT", f"{step_name}/{meas_name}")
    ptr.set_value(
        "OPT_FLAG",
        _make_opt_flag(comparator, limit_low, limit_high),
    )
    if limit_low is not None:
        ptr.set_value("LO_LIMIT", limit_low)
    if limit_high is not None:
        ptr.set_value("HI_LIMIT", limit_high)
    if units:
        ptr.set_value("UNITS", units)
    return _pack_record(ptr)


# ── Event subscriber ────────────────────────────────────────────────


class StdfSubscriber(EventSubscriber):
    """EventSubscriber that writes STDF V4 binary on close."""

    format_name = "stdf"

    def __init__(
        self,
        output_dir: Path,
        *,
        on_output: Callable[[OutputFile], None] | None = None,
    ) -> None:
        self.event_types: set[type] = {
            RunStarted,
            MeasurementRecorded,
            RunEnded,
        }
        self._output_dir = output_dir / "exports" / "stdf"
        self._on_output = on_output
        self._run_started: RunStarted | None = None
        self._measurements: list[MeasurementRecorded] = []
        self._written = False

    def open(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def on_event(self, event: Any) -> None:
        if isinstance(event, RunStarted):
            self._run_started = event
        elif isinstance(event, MeasurementRecorded):
            self._measurements.append(event)
        elif isinstance(event, RunEnded):
            self._write(outcome=event.outcome)

    def close(self) -> None:
        if not self._written:
            self._write()

    def _write(self, outcome: str | None = None) -> None:
        if self._written:
            return
        self._written = True

        s = self._run_started
        if not s:
            return

        records: list[bytes] = []

        # FAR
        far = FAR()
        far.set_value("CPU_TYPE", 2)
        far.set_value("STDF_VER", 4)
        records.append(_pack_record(far))

        # MIR
        mir = MIR()
        mir.set_value("SETUP_T", _dt_to_epoch(s.occurred_at))
        mir.set_value("START_T", _dt_to_epoch(s.occurred_at))
        mir.set_value("STAT_NUM", 1)
        mir.set_value("MODE_COD", "P")
        if s.dut_lot_number:
            mir.set_value("LOT_ID", s.dut_lot_number)
        if s.dut_part_number:
            mir.set_value("PART_TYP", s.dut_part_number)
        mir.set_value("NODE_NAM", s.station_id)
        mir.set_value("JOB_NAM", s.project_name or "")
        if s.operator_id:
            mir.set_value("OPER_NAM", s.operator_id)
        if s.test_phase:
            mir.set_value("TEST_COD", s.test_phase)
        records.append(_pack_record(mir))

        # PIR
        pir = PIR()
        pir.set_value("HEAD_NUM", 1)
        pir.set_value("SITE_NUM", 1)
        records.append(_pack_record(pir))

        # PTR records
        any_fail = False
        for m in self._measurements:
            test_num = m.step_index * 1000 + (m.vector_index or 0)
            records.append(
                _build_ptr(
                    test_num,
                    m.step_name,
                    m.measurement_name,
                    m.value,
                    m.outcome,
                    m.limit_comparator,
                    m.limit_low,
                    m.limit_high,
                    m.units,
                )
            )
            if m.outcome == "fail":
                any_fail = True

        # PRR
        prr = PRR()
        prr.set_value("HEAD_NUM", 1)
        prr.set_value("SITE_NUM", 1)
        part_flg = ["0"] * 8
        if any_fail or outcome in ("fail", "error"):
            part_flg[3] = "1"
        prr.set_value("PART_FLG", part_flg)
        prr.set_value("NUM_TEST", len(self._measurements))
        prr.set_value("HARD_BIN", 1 if not any_fail else 0)
        prr.set_value("SOFT_BIN", 1 if not any_fail else 0)
        prr.set_value("PART_ID", s.dut_serial)
        records.append(_pack_record(prr))

        # MRR
        mrr = MRR()
        mrr.set_value("FINISH_T", _dt_to_epoch(None))
        records.append(_pack_record(mrr))

        run_id = self._short_run_id(s.run_id)
        out_file = self._output_dir / f"{run_id}.stdf"
        out_file.write_bytes(b"".join(records))

        if self._on_output:
            self._on_output(OutputFile(path=out_file, format="stdf", run_id=run_id))
