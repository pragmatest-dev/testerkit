"""ATML subscriber — IEEE 1636.1 TestResultsCollection XML.

EventSubscriber that accumulates events and writes ATML XML on close.
Maps events → ResultSet, StepStarted → TestGroup (nested via step_path),
MeasurementRecorded → Test/NumericLimitTestResult.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

from litmus.data.event_log import EventSubscriber
from litmus.data.events import (
    InstrumentConnected,
    MeasurementRecorded,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
)
from litmus.data.subscribers._output_file import OutputFile

# IEEE 1636.1 / IEEE 1671 namespaces
_NS_TR = "urn:IEEE-1636.1:2011:01:TestResultsCollection"
_NS_C = "urn:IEEE-1671:2010:Common"

# Outcome mapping: Litmus → ATML
_OUTCOME_MAP: dict[str, str] = {
    "pass": "Passed",
    "fail": "Failed",
    "error": "Error",
    "skip": "Skipped",
    "aborted": "Aborted",
    "not_tested": "NotTested",
}

# Comparator → ATML comparator attribute (1:1, our enum is ATML-sourced)
_COMPARATOR_MAP = {
    "EQ": "EQ",
    "NE": "NE",
    "LT": "LT",
    "LE": "LE",
    "GT": "GT",
    "GE": "GE",
    "GELE": "GELE",
    "GELT": "GELT",
    "GTLE": "GTLE",
    "GTLT": "GTLT",
}


def _c(tag: str) -> str:
    return f"{{{_NS_C}}}{tag}"


def _tr(tag: str) -> str:
    return f"{{{_NS_TR}}}{tag}"


def _add_limit(
    parent: ET.Element,
    low: float | None,
    high: float | None,
    nominal: float | None,
    comparator: str | None,
    units: str | None,
) -> None:
    """Add limit elements to a test result element."""
    comp = comparator or "GELE"

    if comp in ("EQ", "NE"):
        if nominal is not None:
            limit_el = ET.SubElement(parent, _c("Limit"))
            limit_el.set("comparator", _COMPARATOR_MAP.get(comp, comp))
            datum = ET.SubElement(limit_el, _c("Datum"))
            datum.set("value", str(nominal))
            datum.set("xsi:type", "c:double")
            if units:
                datum.set("nonStandardUnit", units)
    elif comp in ("LT", "LE"):
        if high is not None:
            limit_el = ET.SubElement(parent, _c("Limit"))
            limit_el.set("comparator", _COMPARATOR_MAP.get(comp, comp))
            datum = ET.SubElement(limit_el, _c("Datum"))
            datum.set("value", str(high))
            datum.set("xsi:type", "c:double")
            if units:
                datum.set("nonStandardUnit", units)
    elif comp in ("GT", "GE"):
        if low is not None:
            limit_el = ET.SubElement(parent, _c("Limit"))
            limit_el.set("comparator", _COMPARATOR_MAP.get(comp, comp))
            datum = ET.SubElement(limit_el, _c("Datum"))
            datum.set("value", str(low))
            datum.set("xsi:type", "c:double")
            if units:
                datum.set("nonStandardUnit", units)
    else:
        # Range comparators: GELE, GELT, GTLE, GTLT
        if low is not None or high is not None:
            limits_el = ET.SubElement(parent, _c("Limits"))
            limits_el.set(
                "comparator",
                _COMPARATOR_MAP.get(comp, comp),
            )
            if low is not None:
                lo = ET.SubElement(limits_el, _c("LimitLow"))
                lo.set("value", str(low))
                lo.set("xsi:type", "c:double")
                if units:
                    lo.set("nonStandardUnit", units)
            if high is not None:
                hi = ET.SubElement(limits_el, _c("LimitHigh"))
                hi.set("value", str(high))
                hi.set("xsi:type", "c:double")
                if units:
                    hi.set("nonStandardUnit", units)


def _get_or_create_group(
    parent: ET.Element,
    path_parts: list[str],
    cache: dict[str, ET.Element],
) -> ET.Element:
    """Navigate/create nested TestGroup elements for a step_path."""
    current = parent
    built = ""
    for part in path_parts:
        built = f"{built}/{part}" if built else part
        if built not in cache:
            grp = ET.SubElement(current, _tr("TestGroup"))
            grp.set("name", part)
            cache[built] = grp
        current = cache[built]
    return current


# ── Event subscriber ────────────────────────────────────────────────


class AtmlSubscriber(EventSubscriber):
    """EventSubscriber that writes IEEE 1636.1 ATML XML on close."""

    format_name = "atml"

    def __init__(
        self,
        output_dir: Path,
        *,
        on_output: Callable[[OutputFile], None] | None = None,
    ) -> None:
        self.event_types: set[type] = {
            RunStarted,
            InstrumentConnected,
            StepStarted,
            MeasurementRecorded,
            StepEnded,
            RunEnded,
        }
        self._output_dir = output_dir / "exports" / "atml"
        self._on_output = on_output
        self._run_started: RunStarted | None = None
        self._instruments: list[InstrumentConnected] = []
        self._step_starts: dict[int, StepStarted] = {}
        self._step_ends: dict[int, StepEnded] = {}
        self._measurements: list[MeasurementRecorded] = []
        self._written = False

    def open(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def on_event(self, event: Any) -> None:
        if isinstance(event, RunStarted):
            self._run_started = event
        elif isinstance(event, InstrumentConnected):
            self._instruments.append(event)
        elif isinstance(event, StepStarted):
            self._step_starts[event.step_index] = event
        elif isinstance(event, MeasurementRecorded):
            self._measurements.append(event)
        elif isinstance(event, StepEnded):
            self._step_ends[event.step_index] = event
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

        ET.register_namespace("tr", _NS_TR)
        ET.register_namespace("c", _NS_C)
        ET.register_namespace(
            "xsi",
            "http://www.w3.org/2001/XMLSchema-instance",
        )

        root = ET.Element(_tr("TestResultsCollection"))
        root.set(
            "xmlns:xsi",
            "http://www.w3.org/2001/XMLSchema-instance",
        )

        # ResultSet
        result_set = ET.SubElement(root, _tr("ResultSet"))
        result_set.set("name", s.sequence_id or "")
        result_set.set("startDateTime", s.occurred_at.isoformat())
        final_outcome = outcome or "error"
        result_set.set(
            "status",
            _OUTCOME_MAP.get(final_outcome, "Unknown"),
        )

        # UUT
        uut = ET.SubElement(result_set, _tr("UUT"))
        uut.set("serialNumber", s.dut_serial)
        if s.dut_part_number:
            uut.set("partNumber", s.dut_part_number)
        if s.dut_revision:
            uut.set("partRevisionNumber", s.dut_revision)
        if s.dut_lot_number:
            uut.set("batchNumber", s.dut_lot_number)

        # Station
        station_el = ET.SubElement(result_set, _tr("TestStation"))
        station_el.set("id", s.station_id)
        if s.station_name:
            station_el.set("name", s.station_name)

        # Test equipment from InstrumentConnected events
        if self._instruments:
            equip_el = ET.SubElement(
                result_set,
                _tr("TestEquipment"),
            )
            for inst in self._instruments:
                item = ET.SubElement(equip_el, _tr("Equipment"))
                item.set("id", inst.instrument_id)
                item.set("role", inst.role)
                if inst.manufacturer:
                    item.set("manufacturer", inst.manufacturer)
                if inst.model:
                    item.set("model", inst.model)
                if inst.serial:
                    item.set("serialNumber", inst.serial)
                if inst.firmware:
                    item.set("firmwareVersion", inst.firmware)
                if inst.cal_due:
                    item.set("calibrationDue", inst.cal_due)

        # Operator
        if s.operator_id:
            op = ET.SubElement(result_set, _tr("Operator"))
            op.set("id", s.operator_id)
            if s.operator_name:
                op.set("name", s.operator_name)

        # Steps → TestGroups, Measurements → Tests
        group_cache: dict[str, ET.Element] = {}
        step_groups: dict[int, ET.Element] = {}

        # Create step groups from StepStarted events
        for idx in sorted(self._step_starts):
            ss = self._step_starts[idx]
            if ss.step_path:
                path_parts = ss.step_path.split("/")
                parent_el = _get_or_create_group(
                    result_set,
                    path_parts,
                    group_cache,
                )
            else:
                parent_el = result_set

            step_grp = ET.SubElement(parent_el, _tr("TestGroup"))
            step_grp.set("name", ss.step_name)
            step_grp.set("startDateTime", ss.occurred_at.isoformat())

            end = self._step_ends.get(idx)
            if end:
                step_grp.set("endDateTime", end.occurred_at.isoformat())
                step_grp.set(
                    "status",
                    _OUTCOME_MAP.get(end.outcome, "Unknown"),
                )

            step_groups[idx] = step_grp

        # Measurements → Test elements
        for m in self._measurements:
            parent = step_groups.get(m.step_index, result_set)

            test_el = ET.SubElement(parent, _tr("Test"))
            test_el.set("name", m.measurement_name)
            if m.spec_id:
                test_el.set("callerName", m.spec_id)

            m_outcome = _OUTCOME_MAP.get(
                m.outcome or "",
                "Unknown",
            )
            test_el.set("status", m_outcome)

            result_el = ET.SubElement(test_el, _tr("TestResult"))
            result_el.set(
                "xsi:type",
                "tr:NumericLimitTestResult",
            )

            if m.value is not None:
                datum = ET.SubElement(result_el, _c("Datum"))
                datum.set("value", str(m.value))
                datum.set("xsi:type", "c:double")
                if m.units:
                    datum.set("nonStandardUnit", m.units)

            _add_limit(
                result_el,
                m.low_limit,
                m.high_limit,
                m.nominal,
                m.comparator,
                m.units,
            )

            if m.meas_dut_pin:
                test_el.set("dutPin", m.meas_dut_pin)
            if m.meas_instrument:
                test_el.set("instrumentName", m.meas_instrument)

        run_id = str(s.run_id)[:8] if s.run_id else "unknown"
        out_file = self._output_dir / f"{run_id}.xml"
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        tree.write(out_file, xml_declaration=True, encoding="unicode")

        if self._on_output:
            self._on_output(OutputFile(path=out_file, format="atml", run_id=run_id))
