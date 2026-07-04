"""Schema-consistency guard across the whole run plumbing.

Two kinds of drift this file guards against, post projection-normalization
(0.3.1):

1. **Run identity** — a run field is COLLECTED once (at rest, in
   ``RUN_ROW_SCHEMA``) and lives in exactly ONE place downstream: ``runs``
   (``runs_materialized`` / ``RunRow`` / ``INFLIGHT_RUNS_SCHEMA``). That is
   exactly how ``uut_revision`` — and ``station_type``/``station_location``,
   ``operator_name``, ``part_name``/``part_revision``, and the git/env/version
   block — were lost (2026-07-03): present at rest, dropped by every
   projection. A new run column must be added to ALL THREE of the below (or,
   for the live overlay only, declared finalization-only) — never left to
   vanish. There is deliberately no equivalent check against
   ``steps_materialized``/``measurements_materialized``/``instruments_materialized``
   any more — those tables carry NO run identity by design (star schema:
   identity lives once, in ``runs``; the ``steps``/``measurements``/
   ``instruments`` VIEWS join it back in, see ``_create_views``), so there is
   nothing to propagate-and-drift there.

2. **Nested-struct fields** — the uniform rule for every OTHER derived table:
   it surfaces every field of the at-rest nested struct it was UNNESTed from
   (``measurements_materialized`` ← ``_MEASUREMENT_STRUCT``,
   ``instruments_materialized`` ← ``_INSTRUMENT_STRUCT``, ``inputs``/
   ``outputs`` ← ``_LANE_STRUCT``). No identity-propagation involved — just
   "did the UNNEST forget a field."
"""

from __future__ import annotations

from litmus.analysis.runs_query import RunRow
from litmus.data._accumulator_pool import INFLIGHT_RUNS_SCHEMA
from litmus.data._runs_duckdb_daemon import (
    _INSTRUMENTS_PERSISTED_COLUMNS,
    _LANE_PERSISTED_COLUMNS,
    _MEASUREMENTS_PERSISTED_COLUMNS,
    _RUNS_PERSISTED_COLUMNS,
    _STEPS_PERSISTED_COLUMNS,
    _VECTORS_PERSISTED_COLUMNS,
)
from litmus.data.backends._row_helpers import LANE_FIELDS
from litmus.data.schemas import _INSTRUMENT_STRUCT, _MEASUREMENT_STRUCT, RUN_ROW_SCHEMA

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
    {"record_type", "inputs", "outputs", "measurements", "instruments", "uut_pin"}
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


# --- steps grain (LOGICAL steps only, post vectors split) -------------------

_STEP_ALIASES = {
    "step_outcome": "outcome",
    "step_started_at": "started_at",
    "step_ended_at": "ended_at",
}
# Step-grain columns the steps projection does NOT surface — made EXPLICIT so
# the omission is a decision, not silent drift. pytest-collection metadata a
# step summary reasonably omits (``step_markers`` IS carried as ``markers``, so
# it's not here).
_STEP_PROJECTION_OMITTED = frozenset(
    {"step_node_id", "step_module", "step_file", "step_class", "step_function", "step_markers"}
)


def _collected_step_columns() -> set[str]:
    # Full snowflake: ``step_*`` fields belong to ``steps_materialized``;
    # ``vector_*`` fields moved to ``vectors_materialized`` (checked separately).
    cols = {str(f.name) for f in RUN_ROW_SCHEMA if f.name.startswith("step_")}
    return {_STEP_ALIASES.get(c, c) for c in cols}


def test_steps_materialized_surfaces_every_collected_step_column() -> None:
    steps = {c for c, _ in _STEPS_PERSISTED_COLUMNS}
    missing = _collected_step_columns() - steps - _STEP_PROJECTION_OMITTED
    assert not missing, (
        f"steps projection silently drops collected step columns: {sorted(missing)} — add each to "
        "_STEPS_PERSISTED_COLUMNS (+ the step ingest / inflight schema / view), or to "
        "_STEP_PROJECTION_OMITTED as an explicit decision."
    )


# --- vectors grain (swept condition points, split out 0.3.1 phase 6) --------

_VECTOR_ALIASES = {
    "vector_outcome": "outcome",
    "vector_started_at": "started_at",
    "vector_ended_at": "ended_at",
}


def _collected_vector_columns() -> set[str]:
    cols = {str(f.name) for f in RUN_ROW_SCHEMA if f.name.startswith("vector_")}
    return {_VECTOR_ALIASES.get(c, c) for c in cols}


def test_vectors_materialized_surfaces_every_collected_vector_column() -> None:
    vectors = {c for c, _ in _VECTORS_PERSISTED_COLUMNS}
    missing = _collected_vector_columns() - vectors
    assert not missing, (
        f"vectors projection drops collected vector columns: {sorted(missing)} — add each to "
        "_VECTORS_PERSISTED_COLUMNS (+ the vector ingest / view)."
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


# --- instruments grain -------------------------------------------------------

# The instruments projection UNNESTs the nested ``_INSTRUMENT_STRUCT``;
# ``name``/``id`` are renamed to avoid shadowing run-level names.
_INSTRUMENT_FIELD_ALIASES = {"name": "role", "id": "instrument_id"}


def _collected_instrument_columns() -> set[str]:
    return {_INSTRUMENT_FIELD_ALIASES.get(str(f.name), str(f.name)) for f in _INSTRUMENT_STRUCT}


def test_instruments_materialized_surfaces_every_collected_instrument_field() -> None:
    instr = {c for c, _ in _INSTRUMENTS_PERSISTED_COLUMNS}
    missing = _collected_instrument_columns() - instr
    assert not missing, (
        f"instruments projection drops collected instrument-struct fields: {sorted(missing)}"
    )


# --- inputs / outputs (EAV lane) grain --------------------------------------

# ``inputs``/``outputs`` UNNEST the nested ``_LANE_STRUCT`` verbatim — no
# aliasing (splitting the EAV by role, projection-normalization 0.3.1,
# renames nothing).


def test_lane_tables_surface_every_collected_lane_field() -> None:
    lane_cols = {c for c, _ in _LANE_PERSISTED_COLUMNS}
    missing = set(LANE_FIELDS) - lane_cols
    assert not missing, (
        f"inputs/outputs tables drop collected lane-struct fields: {sorted(missing)}"
    )
