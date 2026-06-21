"""CSV subscriber — stdlib, no extra dependencies.

EventSubscriber that accumulates MeasurementRecorded events and writes
one row per measurement as CSV on close, with all metadata denormalized.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path
from typing import Any

from litmus.data.event_log import EventSubscriber
from litmus.data.events import (
    MeasurementRecorded,
    RunStarted,
)
from litmus.data.subscribers._output_file import OutputFile

# Fixed columns written first (in this order), followed by any
# dynamic columns discovered across all rows.
_FIXED_COLUMNS = [
    "run_id",
    "step_name",
    "step_index",
    "vector_index",
    "retry",
    "measurement_name",
    "value",
    "unit",
    "limit_low",
    "limit_high",
    "limit_nominal",
    "limit_comparator",
    "outcome",
    "characteristic_id",
    "spec_ref",
    "uut_pin",
    "instrument_name",
    "uut_serial",
    "station_id",
    "operator_id",
    "test_phase",
]


class CsvSubscriber(EventSubscriber):
    """EventSubscriber that writes CSV (one row per measurement) on close.

    Accumulates MeasurementRecorded events with RunStarted metadata,
    then writes a denormalized CSV with fixed + dynamic columns.
    """

    format_name = "csv"
    event_types: set[type] = {RunStarted, MeasurementRecorded}

    def __init__(
        self,
        output_dir: Path,
        *,
        on_output: Callable[[OutputFile], None] | None = None,
    ) -> None:
        self._output_dir = output_dir
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

    def close(self) -> None:
        if not self._written:
            self._write()

    def _write(self) -> None:
        if self._written:
            return
        self._written = True

        s = self._run_started
        if not s or not self._measurements:
            return

        run_id = self._short_run_id(s.run_id)
        out_file = self._output_dir / f"{run_id}.csv"

        # Build flat rows from measurement events + run metadata
        flat_rows: list[dict[str, Any]] = []
        extra_keys: list[str] = []
        seen: set[str] = set(_FIXED_COLUMNS)

        for m in self._measurements:
            row: dict[str, Any] = {
                "run_id": str(s.run_id) if s.run_id else "",
                "step_name": m.step_name,
                "step_index": m.step_index,
                "vector_index": m.vector_index or 0,
                "retry": m.retry,
                "measurement_name": m.measurement_name,
                "value": m.value,
                "unit": m.unit or "",
                "limit_low": m.limit_low,
                "limit_high": m.limit_high,
                "limit_nominal": m.limit_nominal,
                "limit_comparator": m.limit_comparator or "",
                "outcome": m.outcome or "",
                "characteristic_id": m.characteristic_id or "",
                "spec_ref": m.spec_ref or "",
                "uut_pin": m.uut_pin or "",
                "instrument_name": m.instrument_name or "",
                "uut_serial": s.uut_serial,
                "station_id": s.station_id,
                "operator_id": s.operator_id or "",
                "test_phase": s.test_phase,
            }
            # Dynamic columns from inputs/outputs
            for k, v in m.inputs.items():
                key = f"input_{k}"
                row[key] = v
                if key not in seen:
                    seen.add(key)
                    extra_keys.append(key)
            for k, v in m.outputs.items():
                key = f"output_{k}"
                row[key] = v
                if key not in seen:
                    seen.add(key)
                    extra_keys.append(key)
            for k, v in s.custom_metadata.items():
                key = f"custom_{k}"
                row[key] = v
                if key not in seen:
                    seen.add(key)
                    extra_keys.append(key)

            flat_rows.append(row)

        # ``seen`` is seeded with _FIXED_COLUMNS; it only ever grows.
        fieldnames = _FIXED_COLUMNS + extra_keys

        with out_file.open("w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()
            for row in flat_rows:
                clean: dict[str, Any] = {}
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        clean[k] = v.isoformat()
                    elif v is None:
                        clean[k] = ""
                    else:
                        clean[k] = v
                writer.writerow(clean)

        if self._on_output:
            self._on_output(OutputFile(path=out_file, format="csv", run_id=run_id))
