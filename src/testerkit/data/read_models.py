"""Read models — pure functions over ONE run's Parquet (cloud alignment P1).

The testerkit-server cloud plan (docs/48 "one system of record, purpose-built
read models") treats every serving store as a **read model**: a pure function
of the system-of-record object (one run's Parquet), computed by a SINGLE
shared definition that both the cloud ingest task and local parity tests
import — never a hand-written, dialect-specific copy. This module is that
definition (plan-ingest-derivation.md §1.2: "the new testerkit module
``testerkit/data/read_models.py`` is the 'single definition' the server
imports, so nothing is hand-written in the server").

Every builder here composes the EXISTING shared projection SQL in
``run_projection.py`` / ``measurement_projection.py`` — this module adds no
hand-written SQL of its own for the shapes those already cover (runs, steps,
vectors, measurement_facts, IO); it only adds the SELECTs those two modules
don't publish: the run-header's own column tuple (``run_projection`` exposes
no such tuple, unlike ``measurement_projection``'s ``STEPS_COLUMNS``/etc.),
the slim measurement-facts column subset (docs/48 D3), the catalog DISTINCT
projections, and the input/measurement co-occurrence join (moved here from
testerkit-server's ``parametric_service.build_cooccurring_pairs_sql`` per
plan-ingest-derivation.md §1.2 — same join predicate, decomposed per-run
since the join key includes ``run_id``).

Two entry points, both pure (one run's Parquet in, Pydantic models out; the
in-memory DuckDB connection is local to the call, never httpfs, never a
network read):

- :func:`run_detail` — the row shapes plan-serving-cutover.md §2 designs for
  the cloud's ``GET /runs/{run_id}/detail`` reader: one run row plus every
  step / vector / measurement-fact / input / output row, in TesterKit's own
  column order so the web's existing mappers (``mapRow``/``mapStepRow``/
  ``mapVectorRow``/``mapMeasurement`` in ``web/lib/server-data.ts``) keep
  working unchanged. ``inputs``/``outputs`` are docs/44 §1's honestly-named,
  two-table shape (not the former single ``mapLaneRow``-shaped ``io`` field)
  — the web mapper follow-up is written against `InputRow`/`OutputRow`.
- :func:`derive_run` — the read-model rows plan-ingest-derivation.md §1.2
  lists for cloud ingest: a run header, slim measurement-fact rows, and the
  per-run catalog deltas (steps / series / IO names / part / station /
  fixture / co-occurrence) a derive task unions into its org-wide catalogs.

:func:`read_model_fingerprint` extends testerkit-server's own
``fingerprints.py`` pattern (SQL text + a manual version constant) with the
model's column tuple and the source schema version (plan-ingest-derivation.md
§1.3) — read, never imported, since testerkit does not depend on
testerkit-server.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict

from testerkit.data import measurement_projection as mp
from testerkit.data import run_projection as rp
from testerkit.data.schema_versions import CURRENT_SCHEMA_VERSION, SchemaStore

# --------------------------------------------------------------------------- #
# Run header                                                                  #
# --------------------------------------------------------------------------- #

# `runs_projection_select`'s own SELECT list order + `DURATION_S_EXPR`
# appended (the same composition `testerkit_server.query_service`'s
# `query_runs_for_run_ids` uses: `f"SELECT *, {DURATION_S_EXPR} FROM
# ({projection})"`). `run_projection.py` does not publish this as a column
# tuple the way `measurement_projection.py` publishes `STEPS_COLUMNS`/etc.,
# so it is defined here, once — drift-guarded against a live query's actual
# column names in `tests/test_read_models.py` (never hand-duplicated
# silently).
RUN_ROW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run_id", "STRING"),
    ("file_path", "STRING"),
    ("session_id", "STRING"),
    ("site_index", "INTEGER"),
    ("site_name", "STRING"),
    ("uut_serial_number", "STRING"),
    ("uut_part_number", "STRING"),
    ("uut_revision", "STRING"),
    ("uut_lot_number", "STRING"),
    ("station_id", "STRING"),
    ("station_name", "STRING"),
    ("station_hostname", "STRING"),
    ("machine_id", "STRING"),
    ("fixture_id", "STRING"),
    ("outcome", "STRING"),
    ("started_at", "TIMESTAMP"),
    ("ended_at", "TIMESTAMP"),
    ("num_measurements", "INTEGER"),
    ("num_steps", "INTEGER"),
    ("test_phase", "STRING"),
    ("part_id", "STRING"),
    ("part_name", "STRING"),
    ("part_revision", "STRING"),
    ("station_type", "STRING"),
    ("station_location", "STRING"),
    ("operator_id", "STRING"),
    ("operator_name", "STRING"),
    ("project_name", "STRING"),
    ("git_commit", "STRING"),
    ("git_branch", "STRING"),
    ("git_remote", "STRING"),
    ("python_version", "STRING"),
    ("testerkit_version", "STRING"),
    ("env_fingerprint", "STRING"),
    ("duration_s", "FLOAT64"),
)


def run_header_select(source_sql: str) -> str:
    """The run-header projection: `runs_projection_select` + `DURATION_S_EXPR`
    (one row per run — `source_sql` is expected to expose exactly one run)."""
    return f"SELECT *, {rp.DURATION_S_EXPR} FROM ({rp.runs_projection_select(source_sql)})"


# --------------------------------------------------------------------------- #
# measurement_facts (slim) — docs/48 D3                                      #
# --------------------------------------------------------------------------- #

# plan-ingest-derivation.md §1.2's exact slim tuple. `org_id` (the plan's
# 26th column) is NOT included here: it is a cloud/server concern (which
# tenant this object belongs to), never derivable from one run's Parquet —
# the caller (the derive task) adds it when staging. This keeps the slim
# tuple a strict COLUMN SUBSET of `MEASUREMENT_FACTS_COLUMNS` (asserted
# below), never a re-model, per the plan's own drift guard.
_MEASUREMENT_FACTS_SLIM_NAMES: tuple[str, ...] = (
    "run_id",
    "session_id",
    "run_started_at",
    "run_outcome",
    "uut_serial_number",
    "part_id",
    "station_id",
    "fixture_id",
    "test_phase",
    "step_path",
    "step_name",
    "step_retry",
    "step_outcome",
    "vector_index",
    "vector_outer_index",
    "vector_retry",
    "occurrence_index",
    "measurement_name",
    "measurement_value",
    "measurement_outcome",
    "measurement_unit",
    "limit_low",
    "limit_high",
    "limit_nominal",
    "limit_comparator",
)
_FACTS_TYPES: dict[str, str] = dict(mp.MEASUREMENT_FACTS_COLUMNS)
assert set(_MEASUREMENT_FACTS_SLIM_NAMES) <= set(_FACTS_TYPES), (
    "measurement_facts_slim must stay a column subset of MEASUREMENT_FACTS_COLUMNS"
)
MEASUREMENT_FACTS_SLIM_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (name, _FACTS_TYPES[name]) for name in _MEASUREMENT_FACTS_SLIM_NAMES
)


def measurement_facts_slim_select(source_sql: str) -> str:
    """Column subset of `measurement_facts_projection_select` — docs/48 D3
    (drops env / instrument / pin / spec / most run-context columns, which
    stay on the run header / `run_rows`)."""
    cols = ", ".join(_MEASUREMENT_FACTS_SLIM_NAMES)
    return f"SELECT {cols} FROM ({mp.measurement_facts_projection_select(source_sql)})"


# --------------------------------------------------------------------------- #
# Catalog deltas — plan-ingest-derivation.md §1.2. Each is a per-run DISTINCT #
# projection; the cloud derive task set-unions these into org-wide Firestore #
# catalogs (no counts — monotone set-union, docs/48 §3).                     #
# --------------------------------------------------------------------------- #

CATALOG_STEP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("step_path", "STRING"),
    ("step_name", "STRING"),
)


def catalog_steps_select(source_sql: str) -> str:
    """Distinct (step_path, step_name) — serves the step catalog / `/tests`."""
    return f"SELECT DISTINCT step_path, step_name FROM ({mp.steps_projection_select(source_sql)})"


CATALOG_SERIES_COLUMNS: tuple[tuple[str, str], ...] = (
    ("step_path", "STRING"),
    ("step_name", "STRING"),
    ("measurement_name", "STRING"),
    ("measurement_unit", "STRING"),
)


def catalog_series_select(source_sql: str) -> str:
    """Distinct (step_path, step_name, measurement_name, measurement_unit) —
    serves `/measurements/series`; `/measurements/names` is a further
    DISTINCT over `measurement_name` on top of this (plan-ingest-derivation.md
    §1.2: "names = distinct over series"), not a separate catalog here."""
    return (
        "SELECT DISTINCT step_path, step_name, measurement_name, measurement_unit "
        f"FROM ({mp.measurement_facts_projection_select(source_sql)})"
    )


CATALOG_INPUT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("name", "STRING"),
    ("unit", "STRING"),
)


def catalog_inputs_select(source_sql: str) -> str:
    """Distinct (name, unit) over the ``inputs`` projection — serves
    `/inputs/names`. Mirrors what a local ``DISTINCT name, unit FROM inputs``
    gives (docs/44 §1: no ``role`` column to filter on — the table IS the
    role)."""
    return f"SELECT DISTINCT name, unit FROM ({mp.inputs_projection_select(source_sql)})"


CATALOG_OUTPUT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("name", "STRING"),
    ("unit", "STRING"),
)


def catalog_outputs_select(source_sql: str) -> str:
    """Distinct (name, unit) over the ``outputs`` projection — serves
    `/outputs/names`. See :func:`catalog_inputs_select`."""
    return f"SELECT DISTINCT name, unit FROM ({mp.outputs_projection_select(source_sql)})"


CATALOG_PART_COLUMNS: tuple[tuple[str, str], ...] = (
    ("part_id", "STRING"),
    ("part_name", "STRING"),
    ("part_revision", "STRING"),
)


def catalog_parts_select(source_sql: str) -> str:
    """Distinct part identity off the raw measurement-grain source (a run's
    part is constant within its own Parquet, same denormalization
    `_RUN_CONTEXT_COLUMNS` already assumes)."""
    return (
        "SELECT DISTINCT part_id, part_name, part_revision "
        f"FROM {source_sql} WHERE run_id IS NOT NULL AND part_id IS NOT NULL"
    )


CATALOG_STATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("station_id", "STRING"),
    ("station_name", "STRING"),
    ("station_type", "STRING"),
    ("station_location", "STRING"),
)


def catalog_stations_select(source_sql: str) -> str:
    """Distinct station identity off the raw measurement-grain source."""
    return (
        "SELECT DISTINCT station_id, station_name, station_type, station_location "
        f"FROM {source_sql} WHERE run_id IS NOT NULL AND station_id IS NOT NULL"
    )


CATALOG_FIXTURE_COLUMNS: tuple[tuple[str, str], ...] = (("fixture_id", "STRING"),)


def catalog_fixtures_select(source_sql: str) -> str:
    """Distinct fixture identity off the raw measurement-grain source."""
    return (
        "SELECT DISTINCT fixture_id "
        f"FROM {source_sql} WHERE run_id IS NOT NULL AND fixture_id IS NOT NULL"
    )


COOCCURRENCE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("input_name", "STRING"),
    ("measurement_name", "STRING"),
)


def cooccurrence_select(inputs_src: str, facts_src: str) -> str:
    """Distinct (input_name, measurement_name) co-occurrence — the join body
    of testerkit-server's ``parametric_service.build_cooccurring_pairs_sql``,
    moved here per plan-ingest-derivation.md §1.2 ("per-run decomposable
    because the join key includes run_id, so the org-wide DISTINCT equals
    the union of the per-run DISTINCTs"). Same join predicate as that
    function: `(run_id, step_path, step_retry, COALESCE(vector_index, -1),
    COALESCE(vector_outer_index, -1))`, no value filters ("missing is valid"
    — co-occurrence answers "did they ever share a carrier row", not "did
    they both have a value"). ``inputs_src`` is expected to already be the
    INPUTS projection (`measurement_projection.inputs_projection_select`'s
    output) — no ``role`` column/filter needed, docs/44 §1: the source IS
    inputs."""
    return f"""
        SELECT DISTINCT L.name AS input_name, M.measurement_name AS measurement_name
        FROM ({inputs_src}) AS L
        JOIN ({facts_src}) AS M
            ON L.run_id = M.run_id
           AND L.step_path = M.step_path
           AND L.step_retry = M.step_retry
           AND COALESCE(L.vector_index, -1) = COALESCE(M.vector_index, -1)
           AND COALESCE(L.vector_outer_index, -1) = COALESCE(M.vector_outer_index, -1)"""


# --------------------------------------------------------------------------- #
# Row models (Pydantic) — one class per column tuple above / in                #
# `measurement_projection.py`. Field sets are drift-guarded against their     #
# source column tuples in `tests/test_read_models.py`, not re-derived         #
# dynamically here (explicit fields keep this module's public surface        #
# statically typed for pyright).                                             #
# --------------------------------------------------------------------------- #


class RunRow(BaseModel):
    """One row of :data:`RUN_ROW_COLUMNS` (`run_header_select`)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    file_path: str | None = None
    session_id: str | None = None
    site_index: int | None = None
    site_name: str | None = None
    uut_serial_number: str | None = None
    uut_part_number: str | None = None
    uut_revision: str | None = None
    uut_lot_number: str | None = None
    station_id: str | None = None
    station_name: str | None = None
    station_hostname: str | None = None
    machine_id: str | None = None
    fixture_id: str | None = None
    outcome: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    num_measurements: int | None = None
    num_steps: int | None = None
    test_phase: str | None = None
    part_id: str | None = None
    part_name: str | None = None
    part_revision: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    operator_id: str | None = None
    operator_name: str | None = None
    project_name: str | None = None
    git_commit: str | None = None
    git_branch: str | None = None
    git_remote: str | None = None
    python_version: str | None = None
    testerkit_version: str | None = None
    env_fingerprint: str | None = None
    duration_s: float | None = None


class StepRow(BaseModel):
    """One row of `measurement_projection.STEPS_COLUMNS`."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    file_path: str | None = None
    session_id: str | None = None
    site_index: int | None = None
    site_name: str | None = None
    uut_serial_number: str | None = None
    uut_part_number: str | None = None
    uut_revision: str | None = None
    uut_lot_number: str | None = None
    station_id: str | None = None
    station_name: str | None = None
    station_hostname: str | None = None
    fixture_id: str | None = None
    test_phase: str | None = None
    part_id: str | None = None
    part_name: str | None = None
    part_revision: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    operator_id: str | None = None
    operator_name: str | None = None
    project_name: str | None = None
    run_outcome: str | None = None
    step_path: str | None = None
    step_retry: int | None = None
    vector_outer_index: int | None = None
    step_index: int | None = None
    step_name: str | None = None
    outcome: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_s: float | None = None
    measurement_count: int | None = None
    markers: str | None = None


class VectorRow(BaseModel):
    """One row of `measurement_projection.VECTORS_COLUMNS`."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    file_path: str | None = None
    session_id: str | None = None
    site_index: int | None = None
    site_name: str | None = None
    uut_serial_number: str | None = None
    uut_part_number: str | None = None
    uut_revision: str | None = None
    uut_lot_number: str | None = None
    station_id: str | None = None
    station_name: str | None = None
    station_hostname: str | None = None
    fixture_id: str | None = None
    test_phase: str | None = None
    part_id: str | None = None
    part_name: str | None = None
    part_revision: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    operator_id: str | None = None
    operator_name: str | None = None
    project_name: str | None = None
    run_outcome: str | None = None
    step_path: str | None = None
    step_retry: int | None = None
    vector_outer_index: int | None = None
    vector_index: int | None = None
    vector_retry: int | None = None
    step_index: int | None = None
    step_name: str | None = None
    outcome: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_s: float | None = None
    measurement_count: int | None = None


class MeasurementFactRow(BaseModel):
    """One row of `measurement_projection.MEASUREMENT_FACTS_COLUMNS` (full,
    60-column shape — used by :func:`run_detail`, not the slim ingest tuple)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    file_path: str | None = None
    session_id: str | None = None
    site_index: int | None = None
    site_name: str | None = None
    uut_serial_number: str | None = None
    uut_part_number: str | None = None
    uut_revision: str | None = None
    uut_lot_number: str | None = None
    station_id: str | None = None
    station_name: str | None = None
    station_hostname: str | None = None
    fixture_id: str | None = None
    test_phase: str | None = None
    part_id: str | None = None
    part_name: str | None = None
    part_revision: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    operator_id: str | None = None
    operator_name: str | None = None
    project_name: str | None = None
    run_started_at: datetime | None = None
    run_ended_at: datetime | None = None
    run_outcome: str | None = None
    step_index: int | None = None
    step_path: str | None = None
    step_retry: int | None = None
    step_name: str | None = None
    step_outcome: str | None = None
    step_started_at: datetime | None = None
    step_ended_at: datetime | None = None
    vector_index: int | None = None
    vector_outer_index: int | None = None
    vector_retry: int | None = None
    vector_outcome: str | None = None
    ordinal: int | None = None
    occurrence_index: int | None = None
    measurement_name: str | None = None
    measurement_value: float | None = None
    measurement_outcome: str | None = None
    measurement_unit: str | None = None
    measurement_timestamp: datetime | None = None
    limit_low: float | None = None
    limit_high: float | None = None
    limit_nominal: float | None = None
    limit_comparator: str | None = None
    characteristic_id: str | None = None
    spec_ref: str | None = None
    uut_pin: str | None = None
    fixture_connection: str | None = None
    instrument_name: str | None = None
    instrument_resource: str | None = None
    instrument_channel: str | None = None
    git_commit: str | None = None
    git_branch: str | None = None
    git_remote: str | None = None
    python_version: str | None = None
    testerkit_version: str | None = None
    env_fingerprint: str | None = None


class InputRow(BaseModel):
    """One row of `measurement_projection.IO_TABLE_COLUMNS` (the local
    ``inputs`` table's own shape — docs/44 §1: no ``role`` column, carrier
    keys + ``ordinal``/``index``/typed ``value_*``/``unit``/``uut_pin``)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    file_path: str | None = None
    step_index: int | None = None
    step_path: str | None = None
    step_retry: int | None = None
    vector_index: int | None = None
    vector_outer_index: int | None = None
    vector_retry: int | None = None
    ordinal: int | None = None
    index: int | None = None
    name: str | None = None
    value_type: str | None = None
    value_int: int | None = None
    value_double: float | None = None
    value_bool: bool | None = None
    value_text: str | None = None
    value_timestamp: datetime | None = None
    value_json: str | None = None
    unit: str | None = None
    uut_pin: str | None = None


class OutputRow(BaseModel):
    """One row of `measurement_projection.IO_TABLE_COLUMNS` (the local
    ``outputs`` table's own shape). See :class:`InputRow`."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    file_path: str | None = None
    step_index: int | None = None
    step_path: str | None = None
    step_retry: int | None = None
    vector_index: int | None = None
    vector_outer_index: int | None = None
    vector_retry: int | None = None
    ordinal: int | None = None
    index: int | None = None
    name: str | None = None
    value_type: str | None = None
    value_int: int | None = None
    value_double: float | None = None
    value_bool: bool | None = None
    value_text: str | None = None
    value_timestamp: datetime | None = None
    value_json: str | None = None
    unit: str | None = None
    uut_pin: str | None = None


class MeasurementFactSlimRow(BaseModel):
    """One row of :data:`MEASUREMENT_FACTS_SLIM_COLUMNS` (docs/48 D3)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    session_id: str | None = None
    run_started_at: datetime | None = None
    run_outcome: str | None = None
    uut_serial_number: str | None = None
    part_id: str | None = None
    station_id: str | None = None
    fixture_id: str | None = None
    test_phase: str | None = None
    step_path: str | None = None
    step_name: str | None = None
    step_retry: int | None = None
    step_outcome: str | None = None
    vector_index: int | None = None
    vector_outer_index: int | None = None
    vector_retry: int | None = None
    occurrence_index: int | None = None
    measurement_name: str | None = None
    measurement_value: float | None = None
    measurement_outcome: str | None = None
    measurement_unit: str | None = None
    limit_low: float | None = None
    limit_high: float | None = None
    limit_nominal: float | None = None
    limit_comparator: str | None = None


class CatalogStepRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_path: str | None = None
    step_name: str | None = None


class CatalogSeriesRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_path: str | None = None
    step_name: str | None = None
    measurement_name: str | None = None
    measurement_unit: str | None = None


class CatalogInputRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    unit: str | None = None


class CatalogOutputRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    unit: str | None = None


class CatalogPartRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: str | None = None
    part_name: str | None = None
    part_revision: str | None = None


class CatalogStationRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station_id: str | None = None
    station_name: str | None = None
    station_type: str | None = None
    station_location: str | None = None


class CatalogFixtureRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_id: str | None = None


class CooccurrencePairRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_name: str | None = None
    measurement_name: str | None = None


class CatalogDelta(BaseModel):
    """One run's catalog deltas (docs/48 §3 — monotone set-union, no counts).
    The cloud derive task set-unions each list into an org-wide Firestore
    catalog collection; a rebuild replays every run's `CatalogDelta` and
    unions them all."""

    model_config = ConfigDict(extra="forbid")

    steps: list[CatalogStepRow]
    series: list[CatalogSeriesRow]
    inputs: list[CatalogInputRow]
    outputs: list[CatalogOutputRow]
    parts: list[CatalogPartRow]
    stations: list[CatalogStationRow]
    fixtures: list[CatalogFixtureRow]
    cooccurrence: list[CooccurrencePairRow]


class RunDetail(BaseModel):
    """The row shapes plan-serving-cutover.md §2.1 designs for the cloud's
    ``GET /runs/{run_id}/detail`` reader. Deliberately does NOT include that
    plan's ``source`` field (GCS key/generation/bytes/rows) — that is a
    request-time fact about the object read, added by the server after
    calling :func:`run_detail`, not something derivable from the Parquet
    bytes alone.

    ``inputs``/``outputs`` mirror LOCAL TesterKit's own ``inputs``/``outputs``
    tables exactly (docs/44 §1: no EAV rows, no ``role`` column, no collapsed
    single ``value``, no ``lanes`` — ``ordinal``/``index``/``uut_pin`` all
    present), via `measurement_projection.inputs_projection_select`/
    `outputs_projection_select` — not the legacy, role-filtered
    `io_projection_select` shape this field used to be built from."""

    model_config = ConfigDict(extra="forbid")

    run: RunRow
    steps: list[StepRow]
    vectors: list[VectorRow]
    measurements: list[MeasurementFactRow]
    inputs: list[InputRow]
    outputs: list[OutputRow]


class DerivedRun(BaseModel):
    """The read-model rows plan-ingest-derivation.md §1.2 lists for cloud
    ingest: a run header (1 row), slim measurement-fact rows (docs/48 D3),
    and this run's catalog deltas."""

    model_config = ConfigDict(extra="forbid")

    header: RunRow
    measurement_facts: list[MeasurementFactSlimRow]
    catalog: CatalogDelta


# --------------------------------------------------------------------------- #
# Execution — in-memory DuckDB over ONE run's Arrow table. Never httpfs,      #
# never a network read; a fresh connection per call (serving-cutover.md      #
# §2.2's reader design).                                                     #
# --------------------------------------------------------------------------- #


def _as_table(source: pa.Table | str | Path) -> tuple[pa.Table, str]:
    """Normalize `source` into `(table, default_file_path)` — a path reads
    the Parquet off disk and its own path becomes the default `file_path`; an
    already-in-memory `pa.Table` has no path of its own, so the default is
    empty (the caller passes `file_path=` explicitly when one is known, e.g.
    the object's GCS key)."""
    if isinstance(source, pa.Table):
        return source, ""
    path = Path(source)
    return pq.read_table(path), str(path)


def _source_sql(con: duckdb.DuckDBPyConnection, table: pa.Table, file_path: str) -> str:
    """Register `table` and wrap it as a relation exposing a `filename`
    column — the shape every builder in `run_projection`/
    `measurement_projection` expects as `source_sql` (their own docstrings:
    "any relation exposing RUN_ROW_SCHEMA columns plus a filename column")."""
    con.register("run_src", table)
    escaped = file_path.replace("'", "''")
    return f"(SELECT *, '{escaped}' AS filename FROM run_src)"


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _rows(con: duckdb.DuckDBPyConnection, select_sql: str, model: type[_ModelT]) -> list[_ModelT]:
    """Execute `select_sql` and validate every row into `model`."""
    arrow_table = con.sql(select_sql).to_arrow_table()
    return [model.model_validate(row) for row in arrow_table.to_pylist()]


def run_detail(source: pa.Table | str | Path, *, file_path: str | None = None) -> RunDetail:
    """The run-page read model (plan-serving-cutover.md §2) — one run's row,
    steps, vectors, measurement facts (full columns) and `role='input'` IO
    rows, computed by TesterKit's own shared projections over `source`.

    `source` is either an already-read `pyarrow.Table` (one run's rows) or a
    path to that run's Parquet file. `file_path` overrides the `file_path`
    column stamped on every row (e.g. the object's real GCS key); when
    omitted it defaults to `source`'s own path, or `""` for an in-memory
    table with no path of its own.
    """
    table, default_file_path = _as_table(source)
    resolved_file_path = default_file_path if file_path is None else file_path
    con = duckdb.connect(":memory:")
    try:
        src = _source_sql(con, table, resolved_file_path)
        run_rows = _rows(con, run_header_select(src), RunRow)
        if len(run_rows) != 1:
            raise ValueError(f"expected exactly one run in the source, found {len(run_rows)}")
        steps = _rows(con, mp.steps_projection_select(src), StepRow)
        vectors = _rows(con, mp.vectors_projection_select(src), VectorRow)
        measurements = _rows(con, mp.measurement_facts_projection_select(src), MeasurementFactRow)
        inputs = _rows(con, mp.inputs_projection_select(src), InputRow)
        outputs = _rows(con, mp.outputs_projection_select(src), OutputRow)
        return RunDetail(
            run=run_rows[0],
            steps=steps,
            vectors=vectors,
            measurements=measurements,
            inputs=inputs,
            outputs=outputs,
        )
    finally:
        con.close()


def derive_run(source: pa.Table | str | Path, *, file_path: str | None = None) -> DerivedRun:
    """The cloud ingest read model (plan-ingest-derivation.md §1.2, §2.2) —
    the run header, slim measurement-fact rows, and this run's catalog
    deltas, computed by TesterKit's own shared projections over `source`.

    Same `source`/`file_path` contract as :func:`run_detail`.
    """
    table, default_file_path = _as_table(source)
    resolved_file_path = default_file_path if file_path is None else file_path
    con = duckdb.connect(":memory:")
    try:
        src = _source_sql(con, table, resolved_file_path)
        header_rows = _rows(con, run_header_select(src), RunRow)
        if len(header_rows) != 1:
            raise ValueError(f"expected exactly one run in the source, found {len(header_rows)}")
        facts = _rows(con, measurement_facts_slim_select(src), MeasurementFactSlimRow)
        inputs_src = mp.inputs_projection_select(src)
        facts_src = mp.measurement_facts_projection_select(src)
        catalog = CatalogDelta(
            steps=_rows(con, catalog_steps_select(src), CatalogStepRow),
            series=_rows(con, catalog_series_select(src), CatalogSeriesRow),
            inputs=_rows(con, catalog_inputs_select(src), CatalogInputRow),
            outputs=_rows(con, catalog_outputs_select(src), CatalogOutputRow),
            parts=_rows(con, catalog_parts_select(src), CatalogPartRow),
            stations=_rows(con, catalog_stations_select(src), CatalogStationRow),
            fixtures=_rows(con, catalog_fixtures_select(src), CatalogFixtureRow),
            cooccurrence=_rows(
                con, cooccurrence_select(inputs_src, facts_src), CooccurrencePairRow
            ),
        )
        return DerivedRun(header=header_rows[0], measurement_facts=facts, catalog=catalog)
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Registry + fingerprints (plan-ingest-derivation.md §1.3)                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReadModelSpec:
    """One read model's identity for fingerprinting: its SQL builder, output
    column tuple, and a manual mapping-version escape hatch (the same
    residual edge `testerkit_server.fingerprints` documents for Python-side
    mapping changes the hashed SQL text wouldn't otherwise catch). A plain
    dataclass, not a Pydantic model — it holds a function reference, not
    validated data."""

    name: str
    builder: Callable[..., str]
    columns: tuple[tuple[str, str], ...]
    mapping_version: str
    builder_arity: int = 1


# Never actually run as SQL — every builder only needs a syntactically
# irrelevant `source_sql` placeholder to build its SELECT text; fingerprinting
# is a pure string operation, matching `testerkit_server.fingerprints`'s own
# `_SOURCE_PLACEHOLDER` convention.
_SOURCE_PLACEHOLDER = "__SOURCE__"

READ_MODELS: dict[str, ReadModelSpec] = {
    "run_header": ReadModelSpec(
        name="run_header",
        builder=run_header_select,
        columns=RUN_ROW_COLUMNS,
        mapping_version="1",
    ),
    "steps": ReadModelSpec(
        name="steps",
        builder=mp.steps_projection_select,
        columns=mp.STEPS_COLUMNS,
        mapping_version="1",
    ),
    "vectors": ReadModelSpec(
        name="vectors",
        builder=mp.vectors_projection_select,
        columns=mp.VECTORS_COLUMNS,
        mapping_version="1",
    ),
    "measurement_facts": ReadModelSpec(
        name="measurement_facts",
        builder=mp.measurement_facts_projection_select,
        columns=mp.MEASUREMENT_FACTS_COLUMNS,
        mapping_version="1",
    ),
    "inputs": ReadModelSpec(
        name="inputs",
        builder=mp.inputs_projection_select,
        columns=mp.IO_TABLE_COLUMNS,
        mapping_version="1",
    ),
    "outputs": ReadModelSpec(
        name="outputs",
        builder=mp.outputs_projection_select,
        columns=mp.IO_TABLE_COLUMNS,
        mapping_version="1",
    ),
    "measurement_facts_slim": ReadModelSpec(
        name="measurement_facts_slim",
        builder=measurement_facts_slim_select,
        columns=MEASUREMENT_FACTS_SLIM_COLUMNS,
        mapping_version="1",
    ),
    "catalog_steps": ReadModelSpec(
        name="catalog_steps",
        builder=catalog_steps_select,
        columns=CATALOG_STEP_COLUMNS,
        mapping_version="1",
    ),
    "catalog_series": ReadModelSpec(
        name="catalog_series",
        builder=catalog_series_select,
        columns=CATALOG_SERIES_COLUMNS,
        mapping_version="1",
    ),
    "catalog_inputs": ReadModelSpec(
        name="catalog_inputs",
        builder=catalog_inputs_select,
        columns=CATALOG_INPUT_COLUMNS,
        mapping_version="1",
    ),
    "catalog_outputs": ReadModelSpec(
        name="catalog_outputs",
        builder=catalog_outputs_select,
        columns=CATALOG_OUTPUT_COLUMNS,
        mapping_version="1",
    ),
    "catalog_parts": ReadModelSpec(
        name="catalog_parts",
        builder=catalog_parts_select,
        columns=CATALOG_PART_COLUMNS,
        mapping_version="1",
    ),
    "catalog_stations": ReadModelSpec(
        name="catalog_stations",
        builder=catalog_stations_select,
        columns=CATALOG_STATION_COLUMNS,
        mapping_version="1",
    ),
    "catalog_fixtures": ReadModelSpec(
        name="catalog_fixtures",
        builder=catalog_fixtures_select,
        columns=CATALOG_FIXTURE_COLUMNS,
        mapping_version="1",
    ),
    "catalog_cooccurrence": ReadModelSpec(
        name="catalog_cooccurrence",
        builder=cooccurrence_select,
        columns=COOCCURRENCE_COLUMNS,
        mapping_version="1",
        builder_arity=2,
    ),
}


def _builder_sql_for_fingerprint(spec: ReadModelSpec) -> str:
    if spec.builder_arity == 2:
        return spec.builder(_SOURCE_PLACEHOLDER, _SOURCE_PLACEHOLDER)
    return spec.builder(_SOURCE_PLACEHOLDER)


def read_model_fingerprint(name: str) -> str:
    """Content-address of the `name` read model (plan-ingest-derivation.md
    §1.3): `sha256(builder SQL text + column name:type tuple + mapping
    version + source schema version)`. Extends
    `testerkit_server.fingerprints._hash`'s SQL-text-plus-version pattern
    (read for reference, never imported — testerkit does not depend on
    testerkit-server) with the model's column tuple and
    `schema_versions.CURRENT_SCHEMA_VERSION[SchemaStore.RUNS]` (the schema of
    the run Parquet every builder here reads), so a change to either the SQL
    OR the declared output shape OR the source schema forks the fingerprint.
    """
    spec = READ_MODELS[name]
    sql = _builder_sql_for_fingerprint(spec)
    normalized_sql = " ".join(sql.split())
    cols = "|".join(f"{col_name}:{col_type}" for col_name, col_type in spec.columns)
    schema_version = CURRENT_SCHEMA_VERSION[SchemaStore.RUNS]
    payload = "\n".join(
        [
            "--sql--",
            normalized_sql,
            "--cols--",
            cols,
            "--map--",
            spec.mapping_version,
            "--schema--",
            schema_version,
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()
