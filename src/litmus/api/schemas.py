"""Wire DTOs for parquet read endpoints.

Field names mirror the write-side event hierarchy in litmus/data/events.py:
  RunView       ← RunStarted + RunEnded folded together
  StepView      ← StepStarted + StepEnded folded together
  MeasurementView ← MeasurementRecorded
  InstrumentView  ← InstrumentConnected

build_run_view() composes the entity tree from typed sources:
- Run-level fields from a RunRow (the daemon's ``runs`` table).
- Step list from a list[StepRow] (the daemon's ``steps`` table).
- Measurements per step from flat parquet measurement rows,
  filtered by step_index.

Each layer is sourced independently so measurement-less runs
(setup-only / all-skipped) still render their step list.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
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


def _measurements_for_step(
    step_rows: list[dict[str, Any]],
    *,
    in_keys: list[str],
    out_keys: list[str],
    custom_keys: list[str],
) -> list[MeasurementView]:
    """Build MeasurementViews for one step from its measurement rows.

    Rehydrates the dynamic ``in_*`` / ``out_*`` / ``custom_*`` parquet
    columns into typed dicts on each MeasurementView. The prefix-key
    lists are precomputed by the caller once per run (the parquet
    schema is uniform across all rows of a run).
    """
    return [
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


def build_run_view(
    run: Any,
    steps: list[Any],
    measurement_rows: list[dict[str, Any]],
) -> RunView:
    """Compose a RunView from typed inputs.

    Args:
        run: ``RunRow`` from ``RunsQuery.get(run_id)`` — the run-level
            metadata source. Typed as ``Any`` to avoid a hard import
            cycle (``api.schemas`` is imported by paths that don't
            need ``litmus.analysis.runs_query``).
        steps: ``list[StepRow]`` from
            ``StepsQuery.list_for_run(run_id)`` — the step list. One
            ``StepView`` is rendered per ``StepRow`` regardless of
            whether the step has measurements.
        measurement_rows: Flat measurement parquet rows (from
            ``ParquetBackend.get_measurements``). May be empty for
            setup-only / all-skipped runs.

    Each step's ``MeasurementView``s come from ``measurement_rows``
    filtered by ``step_index``; instruments are reconstructed from
    the per-step ``step_instruments_*`` arrays carried on those
    rows. Measurement-less steps render with empty instruments and
    measurements but still appear in the view.
    """
    # Group measurement rows by step_index. Steps without measurements
    # get an empty list — the step still renders.
    #
    # Filter out the placeholder "empty row" written by
    # ``ParquetBackend._build_empty_row`` for measurement-less runs.
    # That row exists only to keep the measurement parquet schema
    # well-formed; it has no real measurement (``measurement_name``
    # is None) and shouldn't show up as a measurement in the view.
    rows_by_step: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in measurement_rows:
        if not row.get("measurement_name"):
            continue
        rows_by_step[int(row.get("step_index") or 0)].append(row)

    # Prefix-key lists are uniform across rows of a run; precompute
    # once and reuse per step (avoids scanning row keys 5k× for large runs).
    in_keys: list[str] = []
    out_keys: list[str] = []
    custom_keys: list[str] = []
    if measurement_rows:
        first = measurement_rows[0]
        in_keys = [k for k in first if k.startswith("in_")]
        out_keys = [k for k in first if k.startswith("out_")]
        custom_keys = [k for k in first if k.startswith("custom_")]

    step_views: list[StepView] = []
    for step in sorted(steps, key=lambda s: s.step_index or 0):
        step_idx = int(step.step_index or 0)
        step_rows = rows_by_step.get(step_idx, [])
        step_views.append(
            StepView(
                step_name=step.step_name or "",
                step_index=step_idx,
                step_path=step.step_path or "",
                started_at=step.started_at,
                ended_at=step.ended_at,
                outcome=step.outcome,
                instruments=_instruments_from_step_rows(step_rows),
                measurements=_measurements_for_step(
                    step_rows,
                    in_keys=in_keys,
                    out_keys=out_keys,
                    custom_keys=custom_keys,
                ),
            )
        )

    return RunView(
        run_id=run.run_id or "",
        session_id=run.session_id,
        station_id=run.station_id,
        dut_serial=run.dut_serial,
        dut_part_number=run.dut_part_number,
        product_id=run.product_id,
        test_phase=run.test_phase,
        started_at=run.started_at,
        ended_at=run.ended_at,
        outcome=run.outcome,
        steps=step_views,
    )


def load_run_view(
    run_id: str,
    *,
    data_dir: Path | str | None = None,
) -> RunView | None:
    """Compose a RunView for ``run_id`` from typed queries + measurements.

    Single shared composition path used by:
    - ``GET /api/runs/{run_id}`` (HTTP)
    - ``litmus`` MCP tool's ``get`` action
    - report generation in ``litmus.reports``

    Returns ``None`` if the run isn't in the runs table. Callers
    decide whether that means 404 or a fallback view.
    """
    # Lazy imports — avoid circular: api.schemas is imported by paths
    # that don't always need the analysis or backends modules.
    from litmus.analysis.runs_query import RunsQuery
    from litmus.analysis.steps_query import StepsQuery
    from litmus.data.backends.parquet import ParquetBackend

    runs_q = RunsQuery(_data_dir=data_dir)
    steps_q = StepsQuery(_data_dir=data_dir)
    try:
        run = runs_q.get(run_id)
        if run is None:
            return None
        steps = steps_q.list_for_run(run_id)
    finally:
        runs_q.close()
        steps_q.close()

    backend = ParquetBackend(data_dir=data_dir)
    measurement_rows = backend.get_measurements(run_id)
    return build_run_view(run, steps, measurement_rows)
