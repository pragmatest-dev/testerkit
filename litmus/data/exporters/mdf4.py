"""MDF4 subscriber — asammdf library (ASAM automotive).

EventSubscriber that accumulates events and writes MDF4 on close.

Time-series oriented format:
  - Each step → channel group
  - Each measurement name → Signal with one sample per vector
  - Limits stored in Signal comments as XML fragments
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from asammdf import MDF, Signal

from litmus.data.event_log import EventSubscriber
from litmus.data.events import (
    MeasurementRecorded,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
)
from litmus.data.subscribers._output_file import OutputFile


def _build_comment(
    meas_name: str,
    units: str | None,
    comparator: str | None,
    low: float | None,
    high: float | None,
    nominal: float | None,
) -> str:
    """Build XML comment fragment with limit metadata."""
    parts = [f'<measurement name="{meas_name}">']
    if units:
        parts.append(f"  <units>{units}</units>")
    if comparator:
        parts.append(f"  <comparator>{comparator}</comparator>")
    if low is not None:
        parts.append(f"  <low_limit>{low}</low_limit>")
    if high is not None:
        parts.append(f"  <high_limit>{high}</high_limit>")
    if nominal is not None:
        parts.append(f"  <nominal>{nominal}</nominal>")
    parts.append("</measurement>")
    return "\n".join(parts)


def _signals_from_measurements(
    measurements: list[MeasurementRecorded],
) -> list[Signal]:
    """Build Signals from a list of MeasurementRecorded events.

    Groups measurements by name. Each name becomes one Signal with
    one sample per vector (using vector_index for ordering).
    """
    # Collect data per measurement name
    meas_data: dict[str, dict[int, float]] = {}
    meas_meta: dict[str, dict[str, Any]] = {}
    max_vec = 0

    for m in measurements:
        mname = m.measurement_name
        vec_idx = m.vector_index or 0
        if mname not in meas_data:
            meas_data[mname] = {}
            meas_meta[mname] = {
                "units": m.units if isinstance(m.units, str) else None,
                "comparator": m.comparator,
                "low_limit": m.low_limit,
                "high_limit": m.high_limit,
                "nominal": m.nominal,
            }
        val = m.value
        meas_data[mname][vec_idx] = (
            val if val is not None else float("nan")
        )
        max_vec = max(max_vec, vec_idx)

    if not meas_data:
        return []

    n_vectors = max_vec + 1
    timestamps = np.arange(n_vectors, dtype=np.float64)

    signals: list[Signal] = []
    for mname, vec_vals in meas_data.items():
        samples = np.full(n_vectors, float("nan"), dtype=np.float64)
        for vi, val in vec_vals.items():
            samples[vi] = val

        meta = meas_meta[mname]
        comment = _build_comment(
            mname,
            meta["units"],
            meta["comparator"],
            meta["low_limit"],
            meta["high_limit"],
            meta["nominal"],
        )
        signals.append(Signal(
            samples=samples,
            timestamps=timestamps,
            name=mname,
            unit=meta["units"] or "",
            comment=comment,
        ))

    return signals


# ── Event subscriber ────────────────────────────────────────────────

class Mdf4Subscriber(EventSubscriber):
    """EventSubscriber that writes ASAM MDF4 on close."""

    format_name = "mdf4"

    def __init__(
        self,
        output_dir: Path,
        *,
        on_output: Callable[[OutputFile], None] | None = None,
    ) -> None:
        self.event_types: set[type] = {
            RunStarted, StepStarted, MeasurementRecorded,
            StepEnded, RunEnded,
        }
        self._output_dir = output_dir / "exports" / "mdf4"
        self._on_output = on_output
        self._run_started: RunStarted | None = None
        self._step_starts: dict[int, StepStarted] = {}
        self._step_ends: dict[int, StepEnded] = {}
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
                event.step_index, [],
            ).append(event)
        elif isinstance(event, StepEnded):
            self._step_ends[event.step_index] = event
        elif isinstance(event, RunEnded):
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

        mdf = MDF()

        all_indices = sorted(
            set(self._step_starts) | set(self._meas_by_step),
        )
        for idx in all_indices:
            step_meas = self._meas_by_step.get(idx, [])
            if not step_meas:
                continue

            signals = _signals_from_measurements(step_meas)
            if not signals:
                continue

            ss = self._step_starts.get(idx)
            comment = ss.step_name if ss else f"step_{idx}"
            mdf.append(signals, comment=comment)

        run_id = str(s.run_id)[:8] if s.run_id else "unknown"
        out_file = self._output_dir / f"{run_id}.mf4"
        mdf.save(out_file, overwrite=True)

        if self._on_output:
            self._on_output(OutputFile(path=out_file, format="mdf4", run_id=run_id))

