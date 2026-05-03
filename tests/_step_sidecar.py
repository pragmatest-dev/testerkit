"""Shared test helper: write a ``_steps.parquet`` companion.

The ``runs`` DuckDB view in the daemon sources from ``_steps.parquet``
sidecars (not from the main measurements parquet) — every step row
carries the run's outcome plus the denormalized run context, which
is enough for ``RunStore.list_runs`` and the analytics surface. Tests
that hand-roll measurement parquets need a matching steps file or
the run is invisible to the view.

Production path goes through ``ParquetSubscriber._write_steps_parquet``,
which emits both files together. This helper is its test-only shim.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from litmus.data.schemas import STEP_SCHEMA


def write_steps_sidecar(
    pq_path: Path,
    *,
    run_id: str,
    session_id: str,
    started_at: datetime,
    ended_at: datetime,
    outcome: str = "passed",
    dut_serial: str | None = None,
    station_id: str | None = None,
    step_index: int = 0,
    step_name: str = "test_step",
    measurement_count: int = 1,
    extra: dict | None = None,
) -> Path:
    """Write the ``{stem}_steps.parquet`` sibling for a measurements parquet.

    Returns the steps parquet path. Pads every column STEP_SCHEMA
    declares with ``None`` so the daemon's ``runs`` view binds cleanly.
    Pass ``extra={"product_id": "PN-100", ...}`` to populate optional
    run-context fields.
    """
    populated: dict = {
        "index": step_index,
        "name": step_name,
        "step_path": step_name,
        "outcome": outcome,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_s": (ended_at - started_at).total_seconds(),
        "has_measurements": measurement_count > 0,
        "measurement_count": measurement_count,
        "vector_count": 1,
        "run_id": run_id,
        "session_id": session_id,
        "run_started_at": started_at,
        "run_ended_at": ended_at,
        "run_outcome": outcome,
        "dut_serial": dut_serial,
        "station_id": station_id,
    }
    if extra:
        populated.update(extra)
    cols = {f.name: [populated.get(f.name)] for f in STEP_SCHEMA}
    steps_path = pq_path.with_name(pq_path.stem + "_steps.parquet")
    pq.write_table(pa.table(cols, schema=STEP_SCHEMA), steps_path)
    return steps_path
