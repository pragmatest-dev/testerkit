"""Shared row-building helpers for parquet and journal backends.

Both backends produce denormalized rows with the same run-level and
measurement-level fields.  This module extracts the common logic so
new columns only need to be added in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litmus.data.models import Measurement, TestRun


def build_run_metadata(test_run: TestRun) -> dict[str, Any]:
    """Extract run-level metadata fields from a TestRun.

    These fields are identical on every row in a run.  Returns raw
    Python objects (datetime, str, None) — callers that need JSON
    serialisation (journal) should post-process timestamps.
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
