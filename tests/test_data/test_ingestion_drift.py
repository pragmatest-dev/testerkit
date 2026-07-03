"""Schema-consistency guard across the whole run plumbing.

A run field is COLLECTED once (at rest, in ``RUN_ROW_SCHEMA``) but must be
surfaced by every layer that reads runs back — otherwise it dies silently and
is invisible to queries, with no error anywhere. The layers each independently
list run columns and therefore drift apart:

    RUN_ROW_SCHEMA (collected, at rest)
      → INFLIGHT_RUNS_SCHEMA + snapshot_run_row   (live overlay)
      → runs_materialized / _RUNS_PERSISTED_COLUMNS (ingested)
      → the ``runs`` view
      → RunRow                                     (the model consumers read)

That is exactly how ``uut_revision`` — and ``station_type``/``station_location``,
``operator_name``, ``part_name``/``part_revision``, and the git/env/version block
— were lost (2026-07-03): present at rest, dropped by every projection. These
tests assert each read layer surfaces every run-scoped column we collect. A new
run column must be added to ALL of them (or, for the live overlay only, declared
finalization-only) — never left to vanish.
"""

from __future__ import annotations

from litmus.analysis.runs_query import RunRow
from litmus.data._accumulator_pool import INFLIGHT_RUNS_SCHEMA
from litmus.data._runs_duckdb_daemon import (
    _MEASUREMENTS_PERSISTED_COLUMNS,
    _RUNS_PERSISTED_COLUMNS,
    _STEPS_PERSISTED_COLUMNS,
)
from litmus.data.schemas import _MEASUREMENT_STRUCT, RUN_ROW_SCHEMA

# Non-run-scoped columns of RUN_ROW_SCHEMA — step / vector / measurement grain
# and nested lanes. These belong to the step/measurement projections, not runs.
_NOT_RUN_SCOPED_PREFIXES = (
    "step_",
    "vector_",
    "measurement_",
    "limit_",
    "characteristic",
    "spec_",
    "fixture_connection",
    "instrument_",
)
_NOT_RUN_SCOPED_EXACT = frozenset(
    {"record_type", "inputs", "outputs", "measurements", "dynamic_attrs", "instruments", "uut_pin"}
)
# The projections surface these three at-rest run columns under an alias.
_ALIASES = {"run_started_at": "started_at", "run_ended_at": "ended_at", "run_outcome": "outcome"}
# Run columns known ONLY at finalization (absent from the RunStarted event), so
# the LIVE overlay legitimately cannot carry them — they populate once the run
# materializes to parquet.
_FINALIZATION_ONLY = frozenset({"env_fingerprint", "litmus_version", "python_version"})


def _collected_run_columns() -> set[str]:
    cols = {
        str(f.name)
        for f in RUN_ROW_SCHEMA
        if not f.name.startswith(_NOT_RUN_SCOPED_PREFIXES) and f.name not in _NOT_RUN_SCOPED_EXACT
    }
    return {_ALIASES.get(c, c) for c in cols}


def test_runs_materialized_surfaces_every_collected_run_column() -> None:
    missing = _collected_run_columns() - {c for c, _ in _RUNS_PERSISTED_COLUMNS}
    assert not missing, (
        f"runs_materialized drops collected run columns: {sorted(missing)} — add each to "
        "_RUNS_PERSISTED_COLUMNS AND the ingest SELECT / GROUP BY / ON CONFLICT in "
        "_runs_duckdb_daemon.py, or it is written to parquet and never queryable."
    )


def test_runrow_surfaces_every_collected_run_column() -> None:
    missing = _collected_run_columns() - set(RunRow.model_fields)
    assert not missing, f"RunRow drops collected run columns: {sorted(missing)}"


def test_inflight_runs_surfaces_every_live_knowable_run_column() -> None:
    live_knowable = _collected_run_columns() - _FINALIZATION_ONLY
    missing = live_knowable - set(INFLIGHT_RUNS_SCHEMA.names)
    assert not missing, (
        f"INFLIGHT_RUNS_SCHEMA drops live-knowable run columns: {sorted(missing)} — add each to "
        "INFLIGHT_RUNS_SCHEMA, snapshot_run_row, and the inflight branch of the runs view "
        "(or to _FINALIZATION_ONLY if it is genuinely only known at finalization)."
    )


# --- steps grain -----------------------------------------------------------

_STEP_ALIASES = {
    "step_outcome": "outcome",
    "step_started_at": "started_at",
    "step_ended_at": "ended_at",
}
# Step-grain columns the steps projection does NOT surface — made EXPLICIT so
# the omission is a decision, not silent drift. Split by reason:
#   pytest-collection metadata a step summary reasonably omits —
_STEP_OMITTED_METADATA = frozenset(
    {"step_node_id", "step_module", "step_file", "step_class", "step_function", "step_markers"}
)
#   vector-execution fields not yet surfaced on the step_vectors grain — a real
#   gap to close, tracked in #57 —
_STEP_OMITTED_GAP = frozenset(
    {"vector_outcome", "vector_retry", "vector_started_at", "vector_ended_at"}
)
_STEP_PROJECTION_OMITTED = _STEP_OMITTED_METADATA | _STEP_OMITTED_GAP


def _collected_step_columns() -> set[str]:
    cols = {str(f.name) for f in RUN_ROW_SCHEMA if f.name.startswith(("step_", "vector_"))}
    return {_STEP_ALIASES.get(c, c) for c in cols}


def test_steps_materialized_surfaces_every_collected_step_column() -> None:
    steps = {c for c, _ in _STEPS_PERSISTED_COLUMNS}
    missing = _collected_step_columns() - steps - _STEP_PROJECTION_OMITTED
    assert not missing, (
        f"steps projection silently drops collected step columns: {sorted(missing)} — add each to "
        "_STEPS_PERSISTED_COLUMNS (+ the step ingest / inflight schema / view), or to "
        "_STEP_PROJECTION_OMITTED as an explicit decision."
    )


# --- measurements grain ----------------------------------------------------

# The measurements projection UNNESTs the nested ``_MEASUREMENT_STRUCT`` and
# prefixes its scalar fields with ``measurement_``.
_MEASUREMENT_FIELD_ALIASES = {
    "name": "measurement_name",
    "value": "measurement_value",
    "unit": "measurement_unit",
    "outcome": "measurement_outcome",
    "timestamp": "measurement_timestamp",
}


def _collected_measurement_columns() -> set[str]:
    return {_MEASUREMENT_FIELD_ALIASES.get(str(f.name), str(f.name)) for f in _MEASUREMENT_STRUCT}


def test_measurements_materialized_surfaces_every_collected_measurement_column() -> None:
    meas = {c for c, _ in _MEASUREMENTS_PERSISTED_COLUMNS}
    missing = _collected_measurement_columns() - meas
    assert not missing, (
        f"measurements projection drops collected measurement-struct fields: {sorted(missing)}"
    )
