"""Wire DTOs for parquet read endpoints.

Field names mirror the write-side event hierarchy in litmus/data/events.py:
  RunView       ← RunStarted + RunEnded folded together
  StepView      ← StepStarted + StepEnded folded together
  MeasurementView ← MeasurementRecorded
  InstrumentView  ← InstrumentConnected

build_run_view() reconstructs the entity tree from flat parquet rows returned
by RunStore.get_measurements(). Flat in_*/out_*/custom_*/step_instruments_* columns are
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
    limit_low: float | None = None
    limit_high: float | None = None
    limit_nominal: float | None = None
    limit_comparator: str | None = None
    characteristic_id: str | None = None
    spec_ref: str | None = None
    # Per-measurement signal path (distinct from per-step step_instruments_* arrays)
    dut_pin: str | None = None
    fixture_connection: str | None = None
    instrument_name: str | None = None
    instrument_resource: str | None = None
    instrument_channel: str | None = None
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


class RequirementSummary(BaseModel):
    """Flat HTTP shape for a product capability requirement."""

    function: str
    direction: str
    characteristic_name: str


class CapabilitySummary(BaseModel):
    """Flat HTTP shape for a station capability."""

    function: str
    direction: str
    instrument_type: str
    instrument_name: str
    channel: str | None = None


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
    """Reconstruct InstrumentView list from per-step step_instruments_* parallel arrays.

    All rows within a step share the same step_instruments_* arrays (written once per step).
    Uses the first row that has a non-empty step_instruments_name list.
    """
    names: list[Any] = []
    source_row: dict[str, Any] = {}
    for row in rows:
        candidate = row.get("step_instruments_name")
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
                instrument_id=str(_get("step_instruments_id") or ""),
                driver=_opt_str(_get("step_instruments_driver")),
                resource=_opt_str(_get("step_instruments_resource")),
                protocol=_opt_str(_get("step_instruments_protocol")),
                manufacturer=_opt_str(_get("step_instruments_manufacturer")),
                model=_opt_str(_get("step_instruments_model")),
                serial=_opt_str(_get("step_instruments_serial")),
                firmware=_opt_str(_get("step_instruments_firmware")),
                cal_due=_opt_str(_get("step_instruments_cal_due")),
                cal_last=_opt_str(_get("step_instruments_cal_last")),
                cal_certificate=_opt_str(_get("step_instruments_cal_certificate")),
                cal_lab=_opt_str(_get("step_instruments_cal_lab")),
                mocked=bool(_get("step_instruments_mocked")),
            )
        )
    return instruments


def _step_outcome(rows: list[dict[str, Any]]) -> str | None:
    """Derive step outcome by escalating measurement outcomes."""
    outcomes = {row.get("measurement_outcome") for row in rows if row.get("measurement_outcome")}
    for severity in ("aborted", "errored", "failed", "passed", "done", "skipped", "planned"):
        if severity in outcomes:
            return severity
    return next(iter(outcomes), None)


def build_run_view(rows: list[dict[str, Any]]) -> RunView:
    """Build a RunView from flat parquet measurement rows.

    Groups rows by step_index, reconstructs step_instruments_* arrays into
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
                value=row.get("measurement_value"),
                units=row.get("measurement_units"),
                outcome=row.get("measurement_outcome"),
                limit_low=row.get("limit_low"),
                limit_high=row.get("limit_high"),
                limit_nominal=row.get("limit_nominal"),
                limit_comparator=row.get("limit_comparator"),
                characteristic_id=row.get("characteristic_id"),
                spec_ref=row.get("spec_ref"),
                dut_pin=row.get("dut_pin"),
                fixture_connection=row.get("fixture_connection"),
                instrument_name=row.get("instrument_name"),
                instrument_resource=row.get("instrument_resource"),
                instrument_channel=row.get("instrument_channel"),
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
