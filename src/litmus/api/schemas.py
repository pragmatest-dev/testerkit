"""Wire DTOs for parquet read endpoints.

Field names mirror the write-side event hierarchy in litmus/data/events.py:
  RunView       ← RunStarted + RunEnded folded together
  StepView      ← StepStarted + StepEnded folded together
  MeasurementView ← MeasurementRecorded
  InstrumentView  ← InstrumentConnected

build_run_view() reconstructs the entity tree from flat parquet rows returned
by RunStore.get_measurements(). Flat in_*/out_*/custom_*/instr_* columns are
re-categorized into structured dicts / lists matching the original event fields.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class InstrumentView(BaseModel):
    """An instrument as connected during a test step.

    Mirrors InstrumentConnected (litmus/data/events.py), minus EventBase fields.
    """

    role: str
    instrument_id: str
    driver: str | None = None
    resource: str | None = None
    protocol: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None
    cal_due: str | None = None
    cal_last: str | None = None
    cal_certificate: str | None = None
    cal_lab: str | None = None
    mocked: bool = False


class MeasurementView(BaseModel):
    """A single measurement.

    Mirrors MeasurementRecorded (litmus/data/events.py), minus step context
    (lifted to StepView) and EventBase fields.
    """

    measurement_name: str
    measurement_timestamp: datetime | None = None
    value: float | None = None
    units: str | None = None
    outcome: str | None = None
    low_limit: float | None = None
    high_limit: float | None = None
    nominal: float | None = None
    comparator: str | None = None
    spec_id: str | None = None
    spec_ref: str | None = None
    # Per-measurement signal path (distinct from per-step instr_* arrays)
    meas_dut_pin: str | None = None
    meas_fixture_connection: str | None = None
    meas_instrument: str | None = None
    meas_instrument_resource: str | None = None
    meas_instrument_channel: str | None = None
    # Typed dicts rehydrated from in_*/out_*/custom_* flat columns
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    custom: dict[str, Any] = {}


class StepView(BaseModel):
    """A test step with its instruments and measurements.

    Mirrors StepStarted + StepEnded folded together (litmus/data/events.py).
    """

    step_name: str
    step_index: int
    step_path: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    outcome: str | None = None
    instruments: list[InstrumentView] = []
    measurements: list[MeasurementView] = []


class RunView(BaseModel):
    """A complete test run with nested steps, instruments, and measurements.

    Mirrors RunStarted + RunEnded folded together (litmus/data/events.py).
    Returned by GET /api/runs/{run_id} and MCP _get_run.
    """

    run_id: str
    session_id: str | None = None
    station_id: str | None = None
    station_name: str | None = None
    dut_serial: str | None = None
    dut_part_number: str | None = None
    product_id: str | None = None
    test_phase: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    outcome: str | None = None
    steps: list[StepView] = []


def _instruments_from_step_rows(rows: list[dict[str, Any]]) -> list[InstrumentView]:
    """Reconstruct InstrumentView list from per-step instr_* parallel arrays.

    All rows within a step share the same instr_* arrays (written once per step).
    Uses the first row that has a non-empty instr_name list.
    """
    names: list[Any] = []
    source_row: dict[str, Any] = {}
    for row in rows:
        candidate = row.get("instr_name")
        if candidate:
            names = candidate
            source_row = row
            break
    if not names:
        return []

    instruments: list[InstrumentView] = []
    for i, name in enumerate(names):
        if name is None:
            continue

        def _opt_str(v: Any) -> str | None:
            return str(v) if v else None

        def _get(col: str, idx: int = i, r: dict = source_row) -> Any:
            lst = r.get(col) or []
            return lst[idx] if idx < len(lst) else None

        instruments.append(
            InstrumentView(
                role=str(name),
                instrument_id=str(_get("instr_id") or ""),
                driver=_opt_str(_get("instr_driver")),
                resource=_opt_str(_get("instr_resource")),
                protocol=_opt_str(_get("instr_protocol")),
                manufacturer=_opt_str(_get("instr_manufacturer")),
                model=_opt_str(_get("instr_model")),
                serial=_opt_str(_get("instr_serial")),
                firmware=_opt_str(_get("instr_firmware")),
                cal_due=_opt_str(_get("instr_cal_due")),
                cal_last=_opt_str(_get("instr_cal_last")),
                cal_certificate=_opt_str(_get("instr_cal_certificate")),
                cal_lab=_opt_str(_get("instr_cal_lab")),
                mocked=bool(_get("instr_mocked")),
            )
        )
    return instruments


def _step_outcome(rows: list[dict[str, Any]]) -> str | None:
    """Derive step outcome by escalating measurement outcomes."""
    outcomes = {row.get("outcome") for row in rows if row.get("outcome")}
    if "error" in outcomes:
        return "error"
    if "fail" in outcomes:
        return "fail"
    if "pass" in outcomes:
        return "pass"
    return next(iter(outcomes), None)


def build_run_view(rows: list[dict[str, Any]]) -> RunView:
    """Build a RunView from flat parquet measurement rows.

    Groups rows by step_index, reconstructs instr_* arrays into
    InstrumentView lists, and rehydrates in_*/out_*/custom_* prefixed
    columns into typed dicts on each MeasurementView.
    """
    if not rows:
        return RunView(run_id="")

    first = rows[0]

    # Pre-compute prefix key lists once — schema is uniform across all rows in a run,
    # so scanning for startswith() once beats doing it per-row (5k× for large runs).
    in_keys: list[str] = [k for k in first if k.startswith("in_")]
    out_keys: list[str] = [k for k in first if k.startswith("out_")]
    custom_keys: list[str] = [k for k in first if k.startswith("custom_")]

    # Group by step_index preserving insertion order
    step_map: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        step_map[int(row.get("step_index") or 0)].append(row)

    steps: list[StepView] = []
    for step_idx in sorted(step_map):
        step_rows = step_map[step_idx]
        first_step = step_rows[0]

        measurements = [
            MeasurementView(
                measurement_name=row.get("measurement_name") or "",
                measurement_timestamp=row.get("measurement_timestamp"),
                value=row.get("value"),
                units=row.get("units"),
                outcome=row.get("outcome"),
                low_limit=row.get("low_limit"),
                high_limit=row.get("high_limit"),
                nominal=row.get("nominal"),
                comparator=row.get("comparator"),
                spec_id=row.get("spec_id"),
                spec_ref=row.get("spec_ref"),
                meas_dut_pin=row.get("meas_dut_pin"),
                meas_fixture_connection=row.get("meas_fixture_connection"),
                meas_instrument=row.get("meas_instrument"),
                meas_instrument_resource=row.get("meas_instrument_resource"),
                meas_instrument_channel=row.get("meas_instrument_channel"),
                inputs={k[3:]: row[k] for k in in_keys if row[k] is not None},
                outputs={k[4:]: row[k] for k in out_keys if row[k] is not None},
                custom={k[7:]: row[k] for k in custom_keys if row[k] is not None},
            )
            for row in step_rows
        ]

        steps.append(
            StepView(
                step_name=first_step.get("step_name") or "",
                step_index=step_idx,
                step_path=first_step.get("step_path") or "",
                started_at=first_step.get("step_started_at"),
                ended_at=first_step.get("step_ended_at"),
                outcome=_step_outcome(step_rows),
                instruments=_instruments_from_step_rows(step_rows),
                measurements=measurements,
            )
        )

    return RunView(
        run_id=first.get("run_id") or "",
        session_id=first.get("session_id"),
        station_id=first.get("station_id"),
        station_name=first.get("station_name"),
        dut_serial=first.get("dut_serial"),
        dut_part_number=first.get("dut_part_number"),
        product_id=first.get("product_id"),
        test_phase=first.get("test_phase"),
        started_at=first.get("run_started_at"),
        ended_at=first.get("run_ended_at"),
        outcome=first.get("run_outcome"),
        steps=steps,
    )
