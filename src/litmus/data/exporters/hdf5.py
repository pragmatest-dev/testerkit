"""HDF5 subscriber — h5py library.

EventSubscriber that accumulates events and writes HDF5 on close.

Hierarchical structure:
  / (root attrs: run metadata)
  ├── instruments/{role}  (group attrs from InstrumentConnected)
  └── steps/{step_path}/  (group attrs: step metadata)
      └── vectors/{idx}/  (group)
          └── measurements/{meas_name}  (scalar dataset, attrs)
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import h5py  # pyright: ignore[reportMissingImports]

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

# ── Event subscriber ────────────────────────────────────────────────


class Hdf5Subscriber(EventSubscriber):
    """EventSubscriber that writes HDF5 on close."""

    format_name = "hdf5"
    event_types: set[type] = {
        RunStarted,
        InstrumentConnected,
        StepStarted,
        MeasurementRecorded,
        StepEnded,
        RunEnded,
    }

    def __init__(
        self,
        output_dir: Path,
        *,
        on_output: Callable[[OutputFile], None] | None = None,
    ) -> None:
        self._output_dir = output_dir / "exports" / "hdf5"
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

        run_id = self._short_run_id(s.run_id)
        out_file = self._output_dir / f"{run_id}.hdf5"

        with h5py.File(out_file, "w") as f:
            # Root attrs from RunStarted
            f.attrs["run_id"] = str(s.run_id) if s.run_id else ""
            f.attrs["started_at"] = s.occurred_at.isoformat()
            f.attrs["outcome"] = outcome or "errored"
            f.attrs["station_id"] = s.station_id
            f.attrs["project_name"] = s.project_name or ""
            f.attrs["test_phase"] = s.test_phase or ""
            f.attrs["dut_serial"] = s.dut_serial
            if s.dut_part_number:
                f.attrs["dut_part_number"] = s.dut_part_number
            if s.dut_revision:
                f.attrs["dut_revision"] = s.dut_revision
            if s.dut_lot_number:
                f.attrs["dut_lot_number"] = s.dut_lot_number
            if s.station_name:
                f.attrs["station_name"] = s.station_name
            if s.operator_id:
                f.attrs["operator_id"] = s.operator_id
            if s.part_id:
                f.attrs["part_id"] = s.part_id
            for key, val in s.custom_metadata.items():
                f.attrs[f"custom_{key}"] = val

            # Instruments
            if self._instruments:
                inst_grp = f.create_group("instruments")
                for inst in self._instruments:
                    ig = inst_grp.create_group(inst.role)
                    ig.attrs["instrument_id"] = inst.instrument_id
                    ig.attrs["resource"] = inst.resource
                    if inst.driver:
                        ig.attrs["driver"] = inst.driver
                    if inst.manufacturer:
                        ig.attrs["manufacturer"] = inst.manufacturer
                    if inst.model:
                        ig.attrs["model"] = inst.model
                    if inst.serial:
                        ig.attrs["serial"] = inst.serial
                    if inst.firmware:
                        ig.attrs["firmware"] = inst.firmware
                    if inst.cal_due:
                        ig.attrs["cal_due"] = inst.cal_due

            # Steps
            steps_grp = f.create_group("steps")

            # Create step groups
            step_h5: dict[int, h5py.Group] = {}
            for idx in sorted(self._step_starts):
                ss = self._step_starts[idx]
                path = ss.step_path if ss.step_path else ss.step_name
                grp = steps_grp.require_group(path)
                grp.attrs["name"] = ss.step_name
                grp.attrs["step_index"] = idx
                grp.attrs["started_at"] = ss.occurred_at.isoformat()
                if ss.description:
                    grp.attrs["description"] = ss.description
                end = self._step_ends.get(idx)
                if end:
                    grp.attrs["ended_at"] = end.occurred_at.isoformat()
                    grp.attrs["outcome"] = end.outcome
                grp.create_group("vectors")
                step_h5[idx] = grp

            # Measurements → datasets
            for m in self._measurements:
                grp = step_h5.get(m.step_index)
                if grp is None:
                    continue
                vec_idx = m.vector_index or 0
                vec_path = f"vectors/{vec_idx}"
                vec_grp = grp.require_group(vec_path)

                # Store inputs/outputs as vec attrs. By the Litmus data
                # model, all measurements in a vector share the same
                # ``params`` (parametrize args) and ``observations``
                # (set once per vector via ``context.observe()``), so
                # first-wins is equivalent to last-wins — the
                # ``not in vec_grp.attrs`` guard just avoids redundant
                # h5py writes.
                for k, v in m.inputs.items():
                    attr_key = f"in_{k}"
                    if attr_key not in vec_grp.attrs:
                        vec_grp.attrs[attr_key] = v
                for k, v in m.outputs.items():
                    attr_key = f"out_{k}"
                    if attr_key not in vec_grp.attrs:
                        vec_grp.attrs[attr_key] = v

                meas_grp = vec_grp.require_group("measurements")
                if m.value is not None:
                    ds = meas_grp.create_dataset(
                        m.measurement_name,
                        data=m.value,
                    )
                else:
                    ds = meas_grp.create_dataset(
                        m.measurement_name,
                        data=float("nan"),
                    )
                    ds.attrs["value_missing"] = True

                if m.units:
                    ds.attrs["units"] = m.units
                if m.limit_comparator:
                    ds.attrs["limit_comparator"] = m.limit_comparator
                if m.limit_low is not None:
                    ds.attrs["limit_low"] = m.limit_low
                if m.limit_high is not None:
                    ds.attrs["limit_high"] = m.limit_high
                if m.limit_nominal is not None:
                    ds.attrs["limit_nominal"] = m.limit_nominal
                if m.outcome:
                    ds.attrs["outcome"] = m.outcome
                if m.characteristic_id:
                    ds.attrs["characteristic_id"] = m.characteristic_id
                if m.dut_pin:
                    ds.attrs["dut_pin"] = m.dut_pin
                if m.instrument_name:
                    ds.attrs["instrument_name"] = m.instrument_name

        if self._on_output:
            self._on_output(OutputFile(path=out_file, format="hdf5", run_id=run_id))
