"""JSON subscriber — stdlib, no extra dependencies.

EventSubscriber that accumulates all events and writes a structured
JSON file on close.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from testerkit.data.event_log import EventSubscriber
from testerkit.data.events import (
    MeasurementRecorded,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
)
from testerkit.data.subscribers._output_file import OutputFile


def _jsonable(obj: Any) -> Any:
    """Coerce a value tree to valid-JSON-safe form before ``json.dumps``.

    Non-finite floats (``NaN``/``Inf`` are not valid JSON) become ``null``;
    ``datetime`` / ``UUID`` / ``bytes`` / ``set`` — which can appear in the
    free-form ``custom_metadata`` / ``inputs`` / ``outputs`` dicts — become
    strings/lists. Without this a NaN measurement emits invalid JSON and a
    datetime in custom metadata raises ``TypeError`` mid-export.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    return obj


class JsonSubscriber(EventSubscriber):
    """EventSubscriber that writes a JSON file on close.

    Accumulates all events and builds a structured JSON document
    mirroring the TestRun hierarchy.
    """

    format_name = "json"
    event_types: set[type] = {
        RunStarted,
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
        self._output_dir = output_dir
        self._on_output = on_output
        self._run_started: RunStarted | None = None
        self._step_starts: dict[int, StepStarted] = {}
        self._step_ends: dict[int, StepEnded] = {}
        self._measurements: list[MeasurementRecorded] = []
        self._run_ended: RunEnded | None = None
        self._written = False

    def open(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def on_event(self, event: Any) -> None:
        if isinstance(event, RunStarted):
            self._run_started = event
        elif isinstance(event, StepStarted):
            self._step_starts[event.step_index] = event
        elif isinstance(event, MeasurementRecorded):
            self._measurements.append(event)
        elif isinstance(event, StepEnded):
            self._step_ends[event.step_index] = event
        elif isinstance(event, RunEnded):
            self._run_ended = event
            self._write()

    def close(self) -> None:
        if not self._written:
            self._write()

    def _write(self) -> None:
        if self._written:
            return
        self._written = True

        s = self._run_started
        if not s:
            return

        run_id = self._short_run_id(s.run_id)
        out_file = self._output_dir / f"{run_id}.json"

        # Build step hierarchy from events
        steps: list[dict[str, Any]] = []
        # Group measurements by step_index
        meas_by_step: dict[int, list[MeasurementRecorded]] = {}
        for m in self._measurements:
            meas_by_step.setdefault(m.step_index, []).append(m)

        all_indices = sorted(
            set(self._step_starts) | set(self._step_ends) | set(meas_by_step),
        )
        for idx in all_indices:
            ss = self._step_starts.get(idx)
            se = self._step_ends.get(idx)
            step_name = ss.step_name if ss else (se.step_name if se else f"step_{idx}")

            # Group measurements by vector_index
            vec_meas: dict[int, list[MeasurementRecorded]] = {}
            for m in meas_by_step.get(idx, []):
                vi = m.vector_index or 0
                vec_meas.setdefault(vi, []).append(m)

            vectors: list[dict[str, Any]] = []
            for vi in sorted(vec_meas):
                meas_list = vec_meas[vi]
                measurements: list[dict[str, Any]] = []
                for m in meas_list:
                    md: dict[str, Any] = {
                        "name": m.measurement_name,
                        "value": m.value,
                    }
                    if m.unit:
                        md["unit"] = m.unit
                    if m.outcome:
                        md["outcome"] = m.outcome
                    if m.limit_low is not None:
                        md["limit_low"] = m.limit_low
                    if m.limit_high is not None:
                        md["limit_high"] = m.limit_high
                    if m.limit_nominal is not None:
                        md["limit_nominal"] = m.limit_nominal
                    if m.limit_comparator:
                        md["limit_comparator"] = m.limit_comparator
                    if m.characteristic_id:
                        md["characteristic_id"] = m.characteristic_id
                    if m.uut_pin:
                        md["uut_pin"] = m.uut_pin
                    if m.instrument_name:
                        md["instrument_name"] = m.instrument_name
                    measurements.append(md)

                vec_dict: dict[str, Any] = {
                    "index": vi,
                    "measurements": measurements,
                }
                if meas_list:
                    # By the TesterKit data model, every measurement in
                    # a vector shares the same params / observations /
                    # retry — they're per-vector fields stamped onto
                    # each MeasurementRecorded event. Reading from the
                    # first measurement is canonical, not a heuristic.
                    first = meas_list[0]
                    if first.inputs:
                        vec_dict["params"] = dict(first.inputs)
                    if first.outputs:
                        vec_dict["observations"] = dict(first.outputs)
                    if first.retry is not None:
                        vec_dict["retry"] = first.retry
                vectors.append(vec_dict)

            step_dict: dict[str, Any] = {
                "name": step_name,
                "vectors": vectors,
            }
            if se:
                step_dict["outcome"] = se.outcome
            if ss and ss.step_path:
                step_dict["step_path"] = ss.step_path
            if ss and ss.description:
                step_dict["description"] = ss.description
            if ss:
                step_dict["started_at"] = ss.occurred_at.isoformat()
            if se:
                step_dict["ended_at"] = se.occurred_at.isoformat()
            steps.append(step_dict)

        data: dict[str, Any] = {
            "run_id": str(s.run_id) if s.run_id else None,
            "station_id": s.station_id,
            "uut": {
                "serial": s.uut_serial_number,
                "part_number": s.uut_part_number,
                "revision": s.uut_revision,
                "lot_number": s.uut_lot_number,
            },
            "project_name": s.project_name,
            "test_phase": s.test_phase,
            "started_at": s.occurred_at.isoformat(),
            "outcome": self._run_ended.outcome if self._run_ended else "errored",
            "steps": steps,
        }
        if s.operator_id:
            data["operator_id"] = s.operator_id
        if s.operator_name:
            data["operator_name"] = s.operator_name
        if s.station_name:
            data["station_name"] = s.station_name
        if s.part_id:
            data["part_id"] = s.part_id
        if s.custom_metadata:
            data["custom_metadata"] = dict(s.custom_metadata)

        out_file.write_text(json.dumps(_jsonable(data), indent=2) + "\n")

        if self._on_output:
            self._on_output(OutputFile(path=out_file, format="json", run_id=run_id))
