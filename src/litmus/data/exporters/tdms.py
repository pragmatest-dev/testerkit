"""TDMS subscriber — npTDMS library (NI ecosystem).

EventSubscriber that accumulates events and writes TDMS on close.

3-level TDMS structure:
  Root (properties: run metadata from RunStarted)
  └── Group per step (flattened step_path with ".")
      Properties: step-level constants (name, outcome, description)
      └── Channels: rectangular dataframe per step
          - vector_index: int channel
          - in_{param}: input parameter channels
          - out_{obs}: observation channels
          - {meas_name}: measurement value channels (props: limits/units)
          - {meas_name}_outcome: outcome string channels

Constants (station, UUT, operator) go in root/group properties.
Only per-vector-varying data becomes channels.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from nptdms import (  # pyright: ignore[reportMissingImports]
    ChannelObject,
    GroupObject,
    RootObject,
    TdmsWriter,
)

from litmus.data.event_log import EventSubscriber
from litmus.data.events import (
    MeasurementRecorded,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
)
from litmus.data.exporters._helpers import discover_dynamic_columns
from litmus.data.subscribers._output_file import OutputFile


def _group_name(step_path: str, step_name: str) -> str:
    """Build TDMS group name from step_path (flattened with '.')."""
    if step_path:
        return step_path.replace("/", ".")
    return step_name


def _build_step_channels(
    grp_name: str,
    measurements: list[MeasurementRecorded],
) -> list[ChannelObject]:
    """Build rectangular channel arrays from measurements for one step.

    All channels in the group have equal length (one row per measurement
    event), forming a square dataframe.
    """
    if not measurements:
        return []

    # Discover all column names — inputs / outputs via shared helper,
    # measurement names locally (TDMS-specific channel layout).
    all_in_keys, all_out_keys = discover_dynamic_columns(measurements)
    all_meas_names: list[str] = []
    meas_set: set[str] = set()
    for m in measurements:
        if m.measurement_name not in meas_set:
            meas_set.add(m.measurement_name)
            all_meas_names.append(m.measurement_name)

    n_rows = len(measurements)

    # Build column arrays
    vec_indices = np.empty(n_rows, dtype=np.int32)
    in_cols: dict[str, list[float]] = {k: [] for k in all_in_keys}
    out_cols: dict[str, list[float]] = {k: [] for k in all_out_keys}
    meas_vals: dict[str, list[float]] = {n: [] for n in all_meas_names}
    meas_outcomes: dict[str, list[str]] = {n: [] for n in all_meas_names}

    for i, m in enumerate(measurements):
        vec_indices[i] = m.vector_index or 0

        for k in all_in_keys:
            v = m.inputs.get(k)
            in_cols[k].append(
                float(v) if v is not None else float("nan"),
            )
        for k in all_out_keys:
            v = m.outputs.get(k)
            out_cols[k].append(
                float(v) if v is not None else float("nan"),
            )
        for mname in all_meas_names:
            if mname == m.measurement_name:
                val = m.value if m.value is not None else float("nan")
                meas_vals[mname].append(val)
                meas_outcomes[mname].append(m.outcome or "")
            else:
                meas_vals[mname].append(float("nan"))
                meas_outcomes[mname].append("")

    channels: list[ChannelObject] = []

    channels.append(
        ChannelObject(
            grp_name,
            "vector_index",
            vec_indices,
        )
    )

    for k in all_in_keys:
        channels.append(
            ChannelObject(
                grp_name,
                f"in_{k}",
                np.array(in_cols[k], dtype=np.float64),
            )
        )

    for k in all_out_keys:
        channels.append(
            ChannelObject(
                grp_name,
                f"out_{k}",
                np.array(out_cols[k], dtype=np.float64),
            )
        )

    # Find first measurement event per name for metadata
    first_meas: dict[str, MeasurementRecorded] = {}
    for m in measurements:
        if m.measurement_name not in first_meas:
            first_meas[m.measurement_name] = m

    for mname in all_meas_names:
        fm = first_meas[mname]
        props: dict[str, object] = {}
        if fm.units:
            props["units"] = fm.units
        if fm.limit_comparator:
            props["limit_comparator"] = fm.limit_comparator
        if fm.limit_low is not None:
            props["limit_low"] = fm.limit_low
        if fm.limit_high is not None:
            props["limit_high"] = fm.limit_high
        if fm.limit_nominal is not None:
            props["limit_nominal"] = fm.limit_nominal

        channels.append(
            ChannelObject(
                grp_name,
                mname,
                np.array(meas_vals[mname], dtype=np.float64),
                properties=props if props else None,
            )
        )
        channels.append(
            ChannelObject(
                grp_name,
                f"{mname}_outcome",
                np.array(meas_outcomes[mname]),
            )
        )

    return channels


# ── Event subscriber ────────────────────────────────────────────────


class TdmsSubscriber(EventSubscriber):
    """EventSubscriber that writes NI TDMS on close."""

    format_name = "tdms"
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
        self._output_dir = output_dir / "exports" / "tdms"
        self._on_output = on_output
        self._run_started: RunStarted | None = None
        self._step_starts: dict[int, StepStarted] = {}
        self._step_ends: dict[int, StepEnded] = {}
        # Measurements grouped by step_index
        self._meas_by_step: dict[int, list[MeasurementRecorded]] = {}
        self._written = False

    def open(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def on_event(self, event: Any) -> None:
        if isinstance(event, RunStarted):
            self._run_started = event
        elif isinstance(event, StepStarted):
            self._step_starts[event.step_index] = event
        elif isinstance(event, MeasurementRecorded):
            self._meas_by_step.setdefault(
                event.step_index,
                [],
            ).append(event)
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
        out_file = self._output_dir / f"{run_id}.tdms"

        root_props: dict[str, object] = {
            "run_id": str(s.run_id) if s.run_id else "",
            "started_at": s.occurred_at.isoformat(),
            "outcome": outcome or "errored",
            "station_id": s.station_id,
            "project_name": s.project_name or "",
            "test_phase": s.test_phase or "",
            "uut_serial": s.uut_serial,
        }
        if s.uut_part_number:
            root_props["uut_part_number"] = s.uut_part_number
        if s.operator_id:
            root_props["operator_id"] = s.operator_id
        if s.station_name:
            root_props["station_name"] = s.station_name
        if s.station_hostname:
            root_props["station_hostname"] = s.station_hostname

        with TdmsWriter(out_file) as writer:
            segments: list[RootObject | GroupObject | ChannelObject] = []
            segments.append(RootObject(properties=root_props))

            all_indices = sorted(
                set(self._step_starts) | set(self._step_ends),
            )
            for idx in all_indices:
                ss = self._step_starts.get(idx)
                se = self._step_ends.get(idx)
                step_name = ss.step_name if ss else (se.step_name if se else f"step_{idx}")
                step_path = ss.step_path if ss else (se.step_path if se else "")
                grp_name = _group_name(step_path, step_name)

                grp_props: dict[str, object] = {
                    "step_name": step_name,
                }
                if se:
                    grp_props["outcome"] = se.outcome
                if ss and ss.description:
                    grp_props["description"] = ss.description
                if ss:
                    grp_props["started_at"] = ss.occurred_at.isoformat()
                if se:
                    grp_props["ended_at"] = se.occurred_at.isoformat()

                segments.append(
                    GroupObject(grp_name, properties=grp_props),
                )
                step_meas = self._meas_by_step.get(idx, [])
                segments.extend(
                    _build_step_channels(grp_name, step_meas),
                )

            writer.write_segment(segments)

        if self._on_output:
            self._on_output(OutputFile(path=out_file, format="tdms", run_id=run_id))
