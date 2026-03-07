"""Shared row-building helpers for the parquet backend.

Produces denormalized rows with run-level and measurement-level fields.
This module extracts the common logic so new columns only need to be
added in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from litmus.data.models import Measurement, TestRun, TestVector


# Prefix for path references in output columns
REF_PATH_PREFIX = "_ref/"


class MeasurementRow(BaseModel):
    """A single denormalized measurement row for streaming and storage."""

    model_config = ConfigDict(extra="forbid")

    # Run identity
    run_id: str
    run_started_at: datetime | None = None
    run_ended_at: datetime | None = None

    # Operator
    operator_id: str | None = None
    operator_name: str | None = None

    # DUT
    dut_serial: str
    dut_part_number: str | None = None
    dut_revision: str | None = None
    dut_lot_number: str | None = None

    # Product
    product_id: str | None = None
    product_name: str | None = None
    product_revision: str | None = None

    # Station
    station_id: str
    station_name: str | None = None
    station_type: str | None = None
    station_location: str | None = None

    # Fixture
    fixture_id: str | None = None

    # Test context
    sequence_id: str | None = None
    test_phase: str | None = None
    git_commit: str | None = None

    # Environment traceability
    python_version: str | None = None
    litmus_version: str | None = None
    env_fingerprint: str | None = None

    # Step/vector context
    step_name: str
    step_index: int
    step_started_at: datetime | None = None
    step_ended_at: datetime | None = None
    vector_index: int | None = None
    attempt: int | None = None
    vector_started_at: datetime | None = None
    vector_ended_at: datetime | None = None

    # Measurement
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
    meas_dut_pin: str | None = None
    meas_fixture_point: str | None = None
    meas_instrument: str | None = None
    meas_instrument_resource: str | None = None
    meas_instrument_channel: str | None = None

    # Outcomes
    vector_outcome: str | None = None
    run_outcome: str | None = None

    # Dynamic namespaced columns
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    instruments: dict[str, list[str | bool | None]] = Field(default_factory=dict)
    custom: dict[str, Any] = Field(default_factory=dict)

    def to_flat_dict(self) -> dict[str, Any]:
        """Flatten to denormalized dict for JSONL/Parquet write boundary.

        Merges dynamic columns back into the flat namespace:
        - ``inputs`` keys are prefixed with ``in_`` (provide unprefixed keys)
        - ``outputs`` keys are prefixed with ``out_`` (provide unprefixed keys)
        - ``instruments`` keys pass through (already ``instr_``-prefixed)
        - ``custom`` keys are prefixed with ``custom_`` (provide unprefixed keys)

        Datetime values are left as ``datetime`` objects — callers must
        serialise them at the actual write boundary (e.g. ``.isoformat()``).
        """
        row = self.model_dump(
            exclude={"inputs", "outputs", "instruments", "custom"},
        )
        for k, v in self.inputs.items():
            row[f"in_{k}"] = v
        for k, v in self.outputs.items():
            row[f"out_{k}"] = v
        row.update(self.instruments)
        for k, v in self.custom.items():
            row[f"custom_{k}"] = v
        return row


def build_run_metadata(test_run: TestRun) -> dict[str, Any]:
    """Extract run-level metadata fields from a TestRun.

    These fields are identical on every row in a run.  Returns raw
    Python objects (datetime, str, None) — callers that need JSON
    serialisation should post-process timestamps.
    """
    return {
        "run_id": str(test_run.id),
        "run_started_at": test_run.started_at,
        "run_ended_at": test_run.ended_at,
        # WHO
        "operator_id": test_run.operator_id,
        "operator_name": test_run.operator_name,
        # DUT
        "dut_serial": test_run.dut.serial,
        "dut_part_number": test_run.dut.part_number,
        "dut_revision": test_run.dut.revision,
        "dut_lot_number": test_run.dut.lot_number,
        # Product
        "product_id": test_run.product_id,
        "product_name": test_run.product_name,
        "product_revision": test_run.product_revision,
        # Station
        "station_id": test_run.station_id,
        "station_name": test_run.station_name,
        "station_type": test_run.station_type,
        "station_location": test_run.station_location,
        # Fixture
        "fixture_id": test_run.fixture_id,
        # Test context
        "sequence_id": test_run.test_sequence_id,
        "test_phase": test_run.test_phase,
        "git_commit": test_run.git_commit,
        # Environment traceability (scalars from environment snapshot)
        **_env_columns(test_run.environment_json),
    }


def _env_columns(environment_json: str | None) -> dict[str, str | None]:
    """Extract queryable environment columns from the JSON snapshot."""
    if not environment_json:
        return {"python_version": None, "litmus_version": None, "env_fingerprint": None}

    from litmus.environment import EnvironmentSnapshot

    snapshot = EnvironmentSnapshot.model_validate_json(environment_json)
    return {
        "python_version": snapshot.python_version,
        "litmus_version": snapshot.litmus_version,
        "env_fingerprint": snapshot.fingerprint,
    }


def build_measurement_fields(measurement: Measurement) -> dict[str, Any]:
    """Extract measurement-level fields from a Measurement."""
    return {
        "measurement_name": measurement.name,
        "measurement_timestamp": measurement.timestamp,
        "value": measurement.value,
        "units": measurement.units,
        "outcome": measurement.outcome.value if measurement.outcome else None,
        # Limits
        "low_limit": measurement.low_limit,
        "high_limit": measurement.high_limit,
        "nominal": measurement.nominal,
        "comparator": measurement.comparator,
        # Spec traceability
        "spec_id": measurement.spec_id,
        "spec_ref": measurement.spec_ref,
        # Signal path
        "meas_dut_pin": measurement.dut_pin,
        "meas_fixture_point": measurement.fixture_point,
        "meas_instrument": measurement.instrument_name,
        "meas_instrument_resource": measurement.instrument_resource,
        "meas_instrument_channel": measurement.instrument_channel,
    }


def build_input_columns(vector: TestVector) -> dict[str, Any]:
    """Build inputs dict from vector params and stimulus records.

    Keys are unprefixed (e.g. ``"vin"``); ``to_flat_dict()`` adds the ``in_`` prefix.
    """
    cols: dict[str, Any] = {}

    for param, value in vector.params.items():
        if param.startswith("_"):
            continue
        cols[param] = value

    for stim in vector.stimulus:
        param = stim.param
        if stim.value is not None:
            cols[param] = stim.value
        if stim.instrument:
            cols[f"{param}_instrument"] = stim.instrument
        if stim.resource:
            cols[f"{param}_resource"] = stim.resource
        if stim.channel:
            cols[f"{param}_channel"] = stim.channel
        if stim.dut_pin:
            cols[f"{param}_dut_pin"] = stim.dut_pin
        if stim.fixture_point:
            cols[f"{param}_fixture_point"] = stim.fixture_point

    return cols


def build_output_columns(
    vector: TestVector,
    ref_saver: Callable[[str, str, Any], str] | None = None,
) -> dict[str, Any]:
    """Build outputs dict from vector observations.

    Keys are unprefixed (e.g. ``"temperature"``); ``to_flat_dict()`` adds
    the ``out_`` prefix.  Scalars are inlined.  Large data uses *ref_saver*
    if provided, otherwise non-serializable types get ``repr()``.
    """
    from litmus.data.models import Waveform

    cols: dict[str, Any] = {}

    for key, value in vector.observations.items():
        if key.startswith("_"):
            continue

        if isinstance(value, (int, float, str, bool, type(None))):
            cols[key] = value
        elif ref_saver is not None and isinstance(value, (Path, Waveform, bytes)):
            cols[key] = ref_saver(str(vector.id)[:8], key, value)
        elif ref_saver is not None and hasattr(value, "tolist"):
            cols[key] = ref_saver(str(vector.id)[:8], key, value)
        elif ref_saver is not None and hasattr(value, "model_dump"):
            cols[key] = ref_saver(str(vector.id)[:8], key, value)
        elif isinstance(value, (list, dict)):
            cols[key] = value
        elif ref_saver is None:
            cols[key] = repr(value)
        else:
            cols[key] = ref_saver(str(vector.id)[:8], key, value)

    return cols


def save_ref_to_dir(ref_dir: Path, vector_id: str, key: str, value: Any) -> str:
    """Save large observation data to a _ref/ directory and return the reference path.

    This is the shared implementation used by both JournalWriter and
    ParquetBackend — the only difference is which directory they target.

    Args:
        ref_dir: Target directory for reference files.
        vector_id: Vector ID prefix (first 8 chars).
        key: Key name for the data.
        value: Data to save (Path, Waveform, bytes, ndarray, Pydantic model).

    Returns:
        Reference string like ``"_ref/abc123_waveform.npz"``.
    """
    import json as _json
    import pickle
    import shutil

    from litmus.data.models import Waveform

    prefix = f"{vector_id}_{key}"

    if isinstance(value, Path):
        ext = value.suffix or ".bin"
        filename = f"{prefix}{ext}"
        shutil.copy(value, ref_dir / filename)

    elif isinstance(value, Waveform):
        filename = f"{prefix}.npz"
        try:
            import numpy as np  # type: ignore[import-not-found]

            np.savez(
                ref_dir / filename,
                Y=value.Y,
                t0=value.t0,
                dt=value.dt,
                **value.attrs,
            )
        except ImportError:
            filename = f"{prefix}.json"
            (ref_dir / filename).write_text(value.model_dump_json())

    elif isinstance(value, bytes):
        filename = f"{prefix}.bin"
        (ref_dir / filename).write_bytes(value)

    elif hasattr(value, "model_dump"):
        filename = f"{prefix}.json"
        (ref_dir / filename).write_text(value.model_dump_json())

    elif hasattr(value, "tolist"):
        filename = f"{prefix}.npy"
        try:
            import numpy as np  # type: ignore[import-not-found]

            np.save(ref_dir / filename, value)
        except ImportError:
            filename = f"{prefix}.json"
            (ref_dir / filename).write_text(_json.dumps(value.tolist()))

    else:
        filename = f"{prefix}.pkl"
        with open(ref_dir / filename, "wb") as f:
            pickle.dump(value, f)

    return f"{REF_PATH_PREFIX}{filename}"


def build_row(
    test_run: TestRun,
    measurement: Measurement,
    step_name: str,
    step_index: int,
    vector: TestVector,
    instrument_arrays: dict[str, list[str | bool | None]],
    ref_saver: Callable[[str, str, Any], str] | None = None,
    *,
    step_started_at: datetime | None = None,
    step_ended_at: datetime | None = None,
) -> MeasurementRow:
    """Build a complete MeasurementRow from test execution context."""
    meta = build_run_metadata(test_run)
    meas = build_measurement_fields(measurement)

    return MeasurementRow(
        **meta,
        **meas,
        # Step/vector context
        step_name=step_name,
        step_index=step_index,
        step_started_at=step_started_at,
        step_ended_at=step_ended_at,
        vector_index=vector.index,
        attempt=vector.attempt,
        vector_started_at=vector.started_at,
        vector_ended_at=vector.ended_at,
        # Outcomes
        vector_outcome=vector.outcome.value if vector.outcome else None,
        run_outcome=test_run.outcome.value,
        # Dynamic columns
        inputs=build_input_columns(vector),
        outputs=build_output_columns(vector, ref_saver=ref_saver),
        instruments=instrument_arrays,
        custom=dict(test_run.custom_metadata),
    )
