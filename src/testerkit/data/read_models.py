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
vectors, measurements, IO); it only adds the SELECTs those two modules
don't publish: the run-header's own column tuple (``run_projection`` exposes
no such tuple, unlike ``measurement_projection``'s ``STEPS_COLUMNS``/etc.),
the slim measurements column subset (docs/48 D3), the catalog DISTINCT
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
  lists for cloud ingest: a run header, slim measurements rows, and the
  per-run catalog deltas (steps / measurements / inputs / outputs / runs /
  inputs_measurements co-occurrence) a derive task unions into its org-wide
  catalogs. Naming follows the docs/44 §1 rule: logical name = local
  TesterKit's public view name, catalogs suffixed `_catalog` (e.g. `runs`
  and `runs_catalog`, never `catalog_runs`).

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

# A read-model column's declared type: either a scalar BigQuery type name
# (e.g. "STRING", "INTEGER" — every column so far), or — for a nested
# REPEATED RECORD column like `measurements_slim`'s `inputs` (docs/48 §4b
# track A1) — a tuple of the struct's own (name, type) sub-columns, meaning
# "ARRAY<STRUCT<...>>". `read_model_fingerprint` (below) and testerkit-
# server's `bq_schema.measurements_slim_bigquery_schema` (read-only here)
# both branch on `isinstance(kind, str)` to tell the two apart.
ColumnType = str | tuple[tuple[str, str], ...]

# --------------------------------------------------------------------------- #
# runs                                                                        #
# --------------------------------------------------------------------------- #

# `runs_projection_select`'s own SELECT list order + `DURATION_S_EXPR`
# appended (the same composition `testerkit_server.query_service`'s
# `query_runs_for_run_ids` uses: `f"SELECT *, {DURATION_S_EXPR} FROM
# ({projection})"`). `run_projection.py` does not publish this as a column
# tuple the way `measurement_projection.py` publishes `STEPS_COLUMNS`/etc.,
# so it is defined here, once — drift-guarded against a live query's actual
# column names in `tests/test_read_models.py` (never hand-duplicated
# silently).
RUNS_COLUMNS: tuple[tuple[str, str], ...] = (
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


def runs_select(source_sql: str) -> str:
    """The `runs` projection: `runs_projection_select` + `DURATION_S_EXPR`
    (one row per run — `source_sql` is expected to expose exactly one run)."""
    return f"SELECT *, {rp.DURATION_S_EXPR} FROM ({rp.runs_projection_select(source_sql)})"


# --------------------------------------------------------------------------- #
# runs (slim) — docs/48 D19                                                   #
# --------------------------------------------------------------------------- #

# Exactly the columns yield (all runs, first/final pass, DPPM), retest and
# trend group or filter by — derived from BOTH `testerkit_server.metrics_
# service`'s SQL (`build_yield_sql`'s `runs` CTE: `run_id, uut_serial_number,
# run_outcome, run_started_at, run_ended_at`, plus its `_common_filters`
# columns `test_phase`/date-window; `build_retest_sql`'s `serial_counts`
# groups by `uut_serial_number` under the same part/station/phase/period
# filters; `build_trend_sql` groups by `DATE(run_started_at)` and
# `run_outcome`) AND `testerkit-server-bench/bench/target.py`'s
# `build_runs_ctas_sql` (the physical shape already benched under docs/48
# D19: `run_id, run_started_at, run_ended_at, outcome, uut_serial_number,
# part_id, station_id, fixture_id, test_phase` — CLUSTER BY `org_id, part_id,
# station_id, test_phase`). The bench's filter columns are `part_id`/
# `station_id` (the catalog identifiers docs/44 §1's Firestore menus already
# use), NOT `metrics_service`'s current `uut_part_number`/`station_hostname`
# — flagged in this task's report for track C, which must switch its filter
# columns when it moves off `run_rows`/`measurements` onto this table; not
# resolved here (no `metrics_service.py` change is in this track's scope).
# Named `outcome`/`started_at`/`ended_at` (not the bench's `run_outcome`/
# `run_started_at`/`run_ended_at`) per docs/44 §1's naming rule: logical
# name = local TesterKit's `runs` view's own column names (already the
# case for every other column here, and for `RUNS_COLUMNS` itself).
_RUNS_SLIM_NAMES: tuple[str, ...] = (
    "run_id",
    "uut_serial_number",
    "outcome",
    "started_at",
    "ended_at",
    "test_phase",
    "uut_part_number",
    "station_hostname",
    "part_id",
    "station_id",
    "fixture_id",
)
_RUNS_TYPES: dict[str, str] = dict(RUNS_COLUMNS)
assert set(_RUNS_SLIM_NAMES) <= set(_RUNS_TYPES), (
    "runs_slim must stay a column subset of RUNS_COLUMNS"
)

RUNS_SLIM_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (name, _RUNS_TYPES[name]) for name in _RUNS_SLIM_NAMES
)


def runs_slim_select(source_sql: str) -> str:
    """Column subset of `runs_select` — docs/48 D19 (drops every column not
    grouped or filtered by yield/retest/trend; env, station/part naming
    detail, git/environment traceability, and `duration_s` all stay on the
    full `runs` header / `run_rows`, recomputed at read time from
    `started_at`/`ended_at` the same way `metrics_service.py` and the bench
    already do)."""
    cols = ", ".join(_RUNS_SLIM_NAMES)
    return f"SELECT {cols} FROM ({runs_select(source_sql)})"


# --------------------------------------------------------------------------- #
# measurements (slim) — docs/48 D3                                           #
# --------------------------------------------------------------------------- #

# plan-ingest-derivation.md §1.2's exact slim tuple. `org_id` (the plan's
# 26th column) is NOT included here: it is a cloud/server concern (which
# tenant this object belongs to), never derivable from one run's Parquet —
# the caller (the derive task) adds it when staging. This keeps the slim
# tuple a strict COLUMN SUBSET of `MEASUREMENTS_COLUMNS` (asserted
# below), never a re-model, per the plan's own drift guard.
_MEASUREMENTS_SLIM_NAMES: tuple[str, ...] = (
    "run_id",
    "session_id",
    "run_started_at",
    "run_outcome",
    "uut_serial_number",
    "uut_part_number",
    "station_hostname",
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
    "index",
    "measurement_name",
    "measurement_value",
    "measurement_outcome",
    "measurement_unit",
    "measurement_timestamp",
    "limit_low",
    "limit_high",
    "limit_nominal",
    "limit_comparator",
    # Ppk's grouping keys (local measurements_query.ppk groups by these), so the
    # cloud's dashboard Ppk matches local instead of merging characteristics/pins.
    "characteristic_id",
    "uut_pin",
)
_MEASUREMENTS_TYPES: dict[str, str] = dict(mp.MEASUREMENTS_COLUMNS)
assert set(_MEASUREMENTS_SLIM_NAMES) <= set(_MEASUREMENTS_TYPES), (
    "measurements_slim must stay a column subset of MEASUREMENTS_COLUMNS"
)

# The carrier's nested `inputs` entry shape (docs/48 §4b track A1;
# plan-serving-cutover.md §3.1 CORRECTION: the slim BigQuery table nests each
# measurement's carrier inputs, "no join, no separate IO table" — Google:
# "use nested and repeated fields... instead of repeatedly joining"). Exactly
# `IO_TABLE_COLUMNS`' own fields — the local `inputs` table's own row shape —
# MINUS the carrier keys already on the measurement row itself
# (`run_id`/`file_path`/`step_index`/`step_path`/`step_retry`/`vector_index`/
# `vector_outer_index`/`vector_retry`; see `measurements_slim_select`'s LEFT
# JOIN, which re-derives that key rather than repeating it inside every
# entry). BigQuery-dialect type names (this tuple feeds
# `measurements_slim_bigquery_schema` exactly like every other
# `MEASUREMENTS_SLIM_COLUMNS` entry) — not `IO_TABLE_COLUMNS`'s own DuckDB
# dialect strings, so this is a hand-typed parallel tuple, guarded by the
# name-subset assert below rather than a type-preserving derivation.
MEASUREMENT_INPUT_ENTRY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ordinal", "INTEGER"),
    ("index", "INTEGER"),
    ("name", "STRING"),
    ("value_type", "STRING"),
    ("value_int", "INTEGER"),
    ("value_double", "FLOAT64"),
    ("value_bool", "BOOL"),
    ("value_text", "STRING"),
    ("value_timestamp", "TIMESTAMP"),
    ("value_json", "STRING"),
    ("unit", "STRING"),
    ("uut_pin", "STRING"),
)
_MEASUREMENT_INPUT_ENTRY_NAMES: tuple[str, ...] = tuple(
    name for name, _ in MEASUREMENT_INPUT_ENTRY_COLUMNS
)
_IO_TABLE_NAMES: set[str] = {name for name, _ in mp.IO_TABLE_COLUMNS}
assert set(_MEASUREMENT_INPUT_ENTRY_NAMES) <= _IO_TABLE_NAMES, (
    "measurement input entry fields must stay a subset of IO_TABLE_COLUMNS "
    "(the local `inputs` table's own shape)"
)

# `("inputs", MEASUREMENT_INPUT_ENTRY_COLUMNS)` is appended below rather than
# folded into `_MEASUREMENTS_SLIM_NAMES`/`_MEASUREMENTS_TYPES`: `inputs` is
# not a column of `MEASUREMENTS_COLUMNS` (it comes from the separate
# `inputs_projection_select` builder, aggregated per carrier — see
# `_measurement_input_entries_select`), so it is not part of the strict
# column-subset relationship the assert above guards. Its type slot is not a
# scalar BigQuery type string but the nested tuple declared above — see
# :data:`ColumnType`.
MEASUREMENTS_SLIM_COLUMNS: tuple[tuple[str, ColumnType], ...] = tuple(
    (name, _MEASUREMENTS_TYPES[name]) for name in _MEASUREMENTS_SLIM_NAMES
) + (("inputs", MEASUREMENT_INPUT_ENTRY_COLUMNS),)


def _measurement_input_entries_select(source_sql: str) -> str:
    """One row per measurement carrier, with its `inputs` entries aggregated
    into a single ordered list — built entirely from the existing shared
    `inputs_projection_select` builder (no hand-copied SQL shape).

    Grouped by the carrier key `measurements_projection_select`'s rows join
    to (`run_id, step_path, step_retry, vector_index, vector_outer_index` —
    the same key `steps_query._step_io_join` uses, byte-identical
    normalization per `io_table_select`'s docstring), so a step-scope
    carrier (`vector_index IS NULL`) groups its entries just as cleanly as a
    vector-scope one. Each entry keeps exactly `MEASUREMENT_INPUT_ENTRY_
    COLUMNS`'s fields, ordered by `ordinal` (the same UNNEST-WITH-ORDINALITY
    position the local `inputs` table stores)."""
    entry_struct = ", ".join(f'"{name}" := "{name}"' for name in _MEASUREMENT_INPUT_ENTRY_NAMES)
    return f"""
        SELECT run_id, step_path, step_retry, vector_index, vector_outer_index,
            LIST(STRUCT_PACK({entry_struct}) ORDER BY ordinal) AS inputs
        FROM ({mp.inputs_projection_select(source_sql)})
        GROUP BY run_id, step_path, step_retry, vector_index, vector_outer_index"""


def measurements_slim_select(source_sql: str) -> str:
    """Column subset of `measurements_projection_select` — docs/48 D3
    (drops env / instrument / pin / spec / most run-context columns, which
    stay on the run header / `run_rows`) — PLUS the carrier's `inputs`
    nested as a list (docs/48 §4b track A1), so parametric/multivari need no
    join. Built from the two existing shared builders
    (`measurements_projection_select` + `inputs_projection_select`, via
    :func:`_measurement_input_entries_select`) — no hand-copied SQL shape.
    LEFT JOIN so a measurement with no inputs (a plain, unswept step) gets an
    empty list rather than dropping the row."""
    slim_cols = ", ".join(f"ms.{name}" for name in _MEASUREMENTS_SLIM_NAMES)
    return f"""
        SELECT {slim_cols}, COALESCE(io.inputs, []) AS inputs
        FROM ({mp.measurements_projection_select(source_sql)}) AS ms
        LEFT JOIN ({_measurement_input_entries_select(source_sql)}) AS io
            ON io.run_id = ms.run_id
           AND io.step_path = ms.step_path
           AND io.step_retry = ms.step_retry
           AND io.vector_index IS NOT DISTINCT FROM ms.vector_index
           AND io.vector_outer_index IS NOT DISTINCT FROM ms.vector_outer_index"""


# --------------------------------------------------------------------------- #
# steps (slim) — docs/48 D19                                                  #
# --------------------------------------------------------------------------- #

# Exactly what time loss and RTY need, WITHOUT a steps⋈runs join (coordinator
# correction, docs/44 §1's Google citation: "use nested and repeated fields…
# instead of repeatedly joining" — the same precedence `measurements_
# projection_select` already applies by denormalizing run context onto every
# measurement row rather than joining at read time). Derived from BOTH
# `metrics_service.build_yield_sql`'s RTY `step_agg` CTE (`s.step_path,
# s.outcome`, `JOIN runs USING (run_id)`, `WHERE step_path IS NOT NULL AND
# outcome IS NOT NULL`, `GROUP BY step_path` — the join this table replaces
# by carrying the run's own filter/group columns denormalized on every step
# row) AND the bench's `step_time_src`/`time_loss` CTE in
# `build_metrics_bundle_sql`'s "slim" grain (`run_id, outcome, started_at,
# ended_at`, summing `TIMESTAMP_DIFF(ended_at, started_at)` where `outcome IN
# ('failed', 'errored')`).
#
# `step_retry` is included: RTY's pooled first-pass-yield formula needs each
# step's FIRST attempt only (`step_retry = 0`) — a retried step's later
# attempts must be filterable out at query time, not silently pooled in
# alongside first attempts. `uut_serial_number`, `test_phase`, `part_id`,
# `station_id`, `fixture_id`, `run_outcome` are `metrics_service._common_
# filters`'s (part/station/phase) and yield/retest's (serial, run outcome)
# own filter/group columns, ALL already present on `STEPS_COLUMNS` (denorm-
# alized run context every step row already carries) — none missing here.
# `step_name`, `vector_outer_index`, `step_index`, `duration_s`,
# `measurement_count`, `markers` are still excluded: no metric groups,
# filters, or joins on them.
_STEPS_SLIM_NAMES: tuple[str, ...] = (
    "run_id",
    "uut_serial_number",
    "test_phase",
    "uut_part_number",
    "station_hostname",
    "part_id",
    "station_id",
    "fixture_id",
    "run_outcome",
    "step_path",
    "step_retry",
    "outcome",
    "started_at",
    "ended_at",
)
_STEPS_TYPES: dict[str, str] = dict(mp.STEPS_COLUMNS)
assert set(_STEPS_SLIM_NAMES) <= set(_STEPS_TYPES), (
    "steps_slim must stay a column subset of STEPS_COLUMNS"
)

STEPS_SLIM_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (name, _STEPS_TYPES[name]) for name in _STEPS_SLIM_NAMES
)


def steps_slim_select(source_sql: str) -> str:
    """Column subset of `measurement_projection.steps_projection_select` —
    docs/48 D19 (coordinator correction). Drops `step_name`,
    `vector_outer_index`, `step_index`, `duration_s`, `measurement_count`,
    `markers` — none of which time loss or RTY group, filter, or join by.
    KEEPS `step_retry` (RTY needs each step's first attempt, `step_retry ==
    0`) and the run-context columns the step metrics filter/group by
    (`uut_serial_number`, `test_phase`, `part_id`, `station_id`,
    `fixture_id`, `run_outcome`), already denormalized onto every step row by
    `steps_projection_select` — so RTY and time_loss need no steps⋈runs join
    (see the column comment above). No run-level DATE/timestamp column
    beyond what `STEPS_COLUMNS` itself carries is included (only the step's
    own `started_at`/`ended_at`): the BigQuery physical table's day-
    partition/window-filter column is a server-side stamp at staging
    (denormalized from the run header), the same pattern `measurements_
    slim`'s docstring already establishes for `org_id` — see this task's
    report for the exact staging need."""
    cols = ", ".join(_STEPS_SLIM_NAMES)
    return f"SELECT {cols} FROM ({mp.steps_projection_select(source_sql)})"


# --------------------------------------------------------------------------- #
# Catalogs — plan-ingest-derivation.md §1.2. Each is a per-run DISTINCT       #
# projection; the cloud derive task set-unions these into org-wide Firestore #
# catalogs (no counts — monotone set-union, docs/48 §3). Naming: docs/44 §1  #
# — logical name = local view name, `_catalog` suffix (never `catalog_`      #
# prefix).                                                                    #
# --------------------------------------------------------------------------- #

STEPS_CATALOG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("step_path", "STRING"),
    ("step_name", "STRING"),
)


def steps_catalog_select(source_sql: str) -> str:
    """Distinct (step_path, step_name) — serves the step catalog / `/tests`."""
    return f"SELECT DISTINCT step_path, step_name FROM ({mp.steps_projection_select(source_sql)})"


MEASUREMENTS_CATALOG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("step_path", "STRING"),
    ("step_name", "STRING"),
    ("measurement_name", "STRING"),
    ("measurement_unit", "STRING"),
)


def measurements_catalog_select(source_sql: str) -> str:
    """Distinct (step_path, step_name, measurement_name, measurement_unit) —
    serves `/measurements/series`; `/measurements/names` is a further
    DISTINCT over `measurement_name` on top of this (plan-ingest-derivation.md
    §1.2: "names = distinct over series"), not a separate catalog here."""
    return (
        "SELECT DISTINCT step_path, step_name, measurement_name, measurement_unit "
        f"FROM ({mp.measurements_projection_select(source_sql)})"
    )


INPUTS_CATALOG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("name", "STRING"),
    ("unit", "STRING"),
)


def inputs_catalog_select(source_sql: str) -> str:
    """Distinct (name, unit) over the ``inputs`` projection — serves
    `/inputs/names`. Mirrors what a local ``DISTINCT name, unit FROM inputs``
    gives (docs/44 §1: no ``role`` column to filter on — the table IS the
    role)."""
    return f"SELECT DISTINCT name, unit FROM ({mp.inputs_projection_select(source_sql)})"


OUTPUTS_CATALOG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("name", "STRING"),
    ("unit", "STRING"),
)


def outputs_catalog_select(source_sql: str) -> str:
    """Distinct (name, unit) over the ``outputs`` projection — serves
    `/outputs/names`. See :func:`inputs_catalog_select`."""
    return f"SELECT DISTINCT name, unit FROM ({mp.outputs_projection_select(source_sql)})"


RUNS_CATALOG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("part_id", "STRING"),
    ("part_name", "STRING"),
    ("part_revision", "STRING"),
    ("station_id", "STRING"),
    ("station_name", "STRING"),
    ("station_type", "STRING"),
    ("station_location", "STRING"),
    ("fixture_id", "STRING"),
    ("git_branch", "STRING"),
    ("uut_lot_number", "STRING"),
)


def runs_catalog_select(source_sql: str) -> str:
    """Distinct run identity (part + station + fixture) off the raw
    measurement-grain source (a run's identity columns are constant within
    its own Parquet, same denormalization `_RUN_CONTEXT_COLUMNS` already
    assumes). Merges the former separate part/station/fixture catalogs into
    one `runs_catalog` (docs/44 §1: one catalog per logical view — `runs` —
    not one per identity column). Excludes only rows where part_id,
    station_id AND fixture_id are ALL NULL (the union of the three former
    per-column NULL exclusions, not their intersection: a row missing only
    part_id still contributes its station/fixture identity)."""
    return (
        "SELECT DISTINCT part_id, part_name, part_revision, "
        "station_id, station_name, station_type, station_location, fixture_id, "
        "git_branch, uut_lot_number "
        f"FROM {source_sql} WHERE run_id IS NOT NULL "
        "AND NOT (part_id IS NULL AND station_id IS NULL AND fixture_id IS NULL)"
    )


INPUTS_MEASUREMENTS_CATALOG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("input_name", "STRING"),
    ("measurement_name", "STRING"),
)


def inputs_measurements_catalog_select(inputs_src: str, measurements_src: str) -> str:
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
        JOIN ({measurements_src}) AS M
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
    """One row of :data:`RUNS_COLUMNS` (`runs_select`)."""

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


class RunSlimRow(BaseModel):
    """One row of :data:`RUNS_SLIM_COLUMNS` (docs/48 D19) — the BigQuery
    `runs` fact table's shape, one row per run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    uut_serial_number: str | None = None
    outcome: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    test_phase: str | None = None
    uut_part_number: str | None = None
    station_hostname: str | None = None
    part_id: str | None = None
    station_id: str | None = None
    fixture_id: str | None = None


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


class StepSlimRow(BaseModel):
    """One row of :data:`STEPS_SLIM_COLUMNS` (docs/48 D19) — the BigQuery
    `steps` fact table's shape, one row per step execution."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    uut_serial_number: str | None = None
    test_phase: str | None = None
    uut_part_number: str | None = None
    station_hostname: str | None = None
    part_id: str | None = None
    station_id: str | None = None
    fixture_id: str | None = None
    run_outcome: str | None = None
    step_path: str | None = None
    step_retry: int | None = None
    outcome: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


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


class MeasurementRow(BaseModel):
    """One row of `measurement_projection.MEASUREMENTS_COLUMNS` (full,
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
    index: int | None = None
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


class MeasurementInputEntry(BaseModel):
    """One entry of a `measurements_slim` row's nested `inputs`
    (docs/48 §4b track A1) — the local `inputs` table's own per-entry
    fields (:data:`measurement_projection.IO_TABLE_COLUMNS`), minus the
    carrier keys already on the enclosing :class:`MeasurementSlimRow`
    (`run_id`/`step_path`/`step_retry`/`vector_index`/`vector_outer_index`).
    See :data:`MEASUREMENT_INPUT_ENTRY_COLUMNS`."""

    model_config = ConfigDict(extra="forbid")

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


class MeasurementSlimRow(BaseModel):
    """One row of :data:`MEASUREMENTS_SLIM_COLUMNS` (docs/48 D3), with the
    carrier's `inputs` nested (docs/48 §4b track A1) — no join needed for
    parametric/multivari reads."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    session_id: str | None = None
    run_started_at: datetime | None = None
    run_outcome: str | None = None
    uut_serial_number: str | None = None
    uut_part_number: str | None = None
    station_hostname: str | None = None
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
    index: int | None = None
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
    uut_pin: str | None = None
    inputs: list[MeasurementInputEntry]


class StepsCatalogRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_path: str | None = None
    step_name: str | None = None


class MeasurementsCatalogRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_path: str | None = None
    step_name: str | None = None
    measurement_name: str | None = None
    measurement_unit: str | None = None


class InputsCatalogRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    unit: str | None = None


class OutputsCatalogRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    unit: str | None = None


class RunsCatalogRow(BaseModel):
    """One row of :data:`RUNS_CATALOG_COLUMNS` (`runs_catalog_select`) — the
    merged part/station/fixture identity catalog (docs/44 §1: one catalog
    per logical view)."""

    model_config = ConfigDict(extra="forbid")

    part_id: str | None = None
    part_name: str | None = None
    part_revision: str | None = None
    station_id: str | None = None
    station_name: str | None = None
    station_type: str | None = None
    station_location: str | None = None
    fixture_id: str | None = None
    git_branch: str | None = None
    uut_lot_number: str | None = None


class InputsMeasurementsCatalogRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_name: str | None = None
    measurement_name: str | None = None


class CatalogDelta(BaseModel):
    """One run's catalog deltas (docs/48 §3 — monotone set-union, no counts).
    The cloud derive task set-unions each list into an org-wide Firestore
    catalog collection; a rebuild replays every run's `CatalogDelta` and
    unions them all."""

    model_config = ConfigDict(extra="forbid")

    steps: list[StepsCatalogRow]
    measurements: list[MeasurementsCatalogRow]
    inputs: list[InputsCatalogRow]
    outputs: list[OutputsCatalogRow]
    runs: list[RunsCatalogRow]
    inputs_measurements: list[InputsMeasurementsCatalogRow]


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
    measurements: list[MeasurementRow]
    inputs: list[InputRow]
    outputs: list[OutputRow]


class DerivedRun(BaseModel):
    """The read-model rows plan-ingest-derivation.md §1.2 lists for cloud
    ingest: a run row (1 row), slim measurements rows (docs/48 D3), slim
    runs/steps rows for the BigQuery run/step fact tables (docs/48 D19), and
    this run's catalog deltas.

    ``runs``/``steps`` are ``list[...]`` — the SAME shape as ``.measurements``
    — even though ``runs_slim`` is always exactly one row per run (the same
    grain as ``.run``): every model a derive task stages into BigQuery
    (``measurements``, ``runs``, ``steps``) is uniformly "a list of rows to
    append", so the staging/loader code (track B) needs no special case for
    a single-row model. ``.run`` (singular ``RunRow``, the FULL header) stays
    as-is — it feeds the Firestore `runs` DOCUMENT (class B), a different
    consumer with a genuinely singular shape, not a table the batched loader
    appends rows to."""

    model_config = ConfigDict(extra="forbid")

    run: RunRow
    runs: list[RunSlimRow]
    measurements: list[MeasurementSlimRow]
    steps: list[StepSlimRow]
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
    steps, vectors, measurements (full columns) and `role='input'` IO
    rows, computed by TesterKit's own shared projections over `source`.

    `source` is either an already-read `pyarrow.Table` (one run's rows) or a
    path to that run's Parquet file. `file_path` overrides the `file_path`
    column stamped on every row (e.g. the object's real GCS key); when
    omitted it defaults to `source`'s own path, or `""` for an in-memory
    table with no path of its own.
    """
    parts = run_detail_parts(source, file_path=file_path)
    run_rows = parts.runs
    if len(run_rows) != 1:
        raise ValueError(f"expected exactly one run in the source, found {len(run_rows)}")
    return RunDetail(
        run=run_rows[0],
        steps=parts.steps,
        vectors=parts.vectors,
        measurements=parts.measurements,
        inputs=parts.inputs,
        outputs=parts.outputs,
    )


class RunDetailParts(BaseModel):
    """The lists :func:`run_detail` assembles, before its one-run check; a list not
    asked for (``only``) is empty. ``runs`` holds every run row in the source."""

    model_config = ConfigDict(extra="forbid")

    runs: list[RunRow] = []
    steps: list[StepRow] = []
    vectors: list[VectorRow] = []
    measurements: list[MeasurementRow] = []
    inputs: list[InputRow] = []
    outputs: list[OutputRow] = []


_ALL_PARTS = frozenset({"runs", "steps", "vectors", "measurements", "inputs", "outputs"})


def _has_io_entries(table: pa.Table, column: str) -> bool:
    """Whether any carrier row has an entry in the nested ``column`` list."""
    if column not in table.column_names:
        return False
    return any(entries for entries in table.column(column).to_pylist())


def run_detail_parts(
    source: pa.Table | str | Path,
    *,
    file_path: str | None = None,
    only: frozenset[str] = _ALL_PARTS,
) -> RunDetailParts:
    """The shared projections behind :func:`run_detail`, optionally limited to the
    lists named in ``only`` (a subset of ``runs, steps, vectors, measurements,
    inputs, outputs``): each is one DuckDB query with a fixed planning cost, so a
    caller that knows some can't have rows (the live pusher re-projecting one sweep
    bucket) skips them. Same SQL, same models — only fewer of them run. The IO lists
    also skip their query when no carrier row has an entry."""
    table, default_file_path = _as_table(source)
    resolved_file_path = default_file_path if file_path is None else file_path
    con = duckdb.connect(":memory:")
    try:
        src = _source_sql(con, table, resolved_file_path)
        out = RunDetailParts()
        if "runs" in only:
            out.runs = _rows(con, runs_select(src), RunRow)
        if "steps" in only:
            out.steps = _rows(con, mp.steps_projection_select(src), StepRow)
        if "vectors" in only:
            out.vectors = _rows(con, mp.vectors_projection_select(src), VectorRow)
        if "measurements" in only:
            out.measurements = _rows(con, mp.measurements_projection_select(src), MeasurementRow)
        if "inputs" in only and _has_io_entries(table, "inputs"):
            out.inputs = _rows(con, mp.inputs_projection_select(src), InputRow)
        if "outputs" in only and _has_io_entries(table, "outputs"):
            out.outputs = _rows(con, mp.outputs_projection_select(src), OutputRow)
        return out
    finally:
        con.close()


def derive_run(source: pa.Table | str | Path, *, file_path: str | None = None) -> DerivedRun:
    """The cloud ingest read model (plan-ingest-derivation.md §1.2, §2.2) —
    the run header, slim runs/measurements/steps rows (docs/48 D3, D19), and
    this run's catalog deltas, computed by TesterKit's own shared projections
    over `source`.

    Same `source`/`file_path` contract as :func:`run_detail`.
    """
    table, default_file_path = _as_table(source)
    resolved_file_path = default_file_path if file_path is None else file_path
    con = duckdb.connect(":memory:")
    try:
        src = _source_sql(con, table, resolved_file_path)
        run_rows = _rows(con, runs_select(src), RunRow)
        if len(run_rows) != 1:
            raise ValueError(f"expected exactly one run in the source, found {len(run_rows)}")
        runs_slim = _rows(con, runs_slim_select(src), RunSlimRow)
        measurements = _rows(con, measurements_slim_select(src), MeasurementSlimRow)
        steps_slim = _rows(con, steps_slim_select(src), StepSlimRow)
        inputs_src = mp.inputs_projection_select(src)
        measurements_src = mp.measurements_projection_select(src)
        catalog = CatalogDelta(
            steps=_rows(con, steps_catalog_select(src), StepsCatalogRow),
            measurements=_rows(con, measurements_catalog_select(src), MeasurementsCatalogRow),
            inputs=_rows(con, inputs_catalog_select(src), InputsCatalogRow),
            outputs=_rows(con, outputs_catalog_select(src), OutputsCatalogRow),
            runs=_rows(con, runs_catalog_select(src), RunsCatalogRow),
            inputs_measurements=_rows(
                con,
                inputs_measurements_catalog_select(inputs_src, measurements_src),
                InputsMeasurementsCatalogRow,
            ),
        )
        return DerivedRun(
            run=run_rows[0],
            runs=runs_slim,
            measurements=measurements,
            steps=steps_slim,
            catalog=catalog,
        )
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
    columns: tuple[tuple[str, ColumnType], ...]
    mapping_version: str
    builder_arity: int = 1


# Never actually run as SQL — every builder only needs a syntactically
# irrelevant `source_sql` placeholder to build its SELECT text; fingerprinting
# is a pure string operation, matching `testerkit_server.fingerprints`'s own
# `_SOURCE_PLACEHOLDER` convention.
_SOURCE_PLACEHOLDER = "__SOURCE__"

READ_MODELS: dict[str, ReadModelSpec] = {
    "runs": ReadModelSpec(
        name="runs",
        builder=runs_select,
        columns=RUNS_COLUMNS,
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
    "measurements": ReadModelSpec(
        name="measurements",
        builder=mp.measurements_projection_select,
        columns=mp.MEASUREMENTS_COLUMNS,
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
    "measurements_slim": ReadModelSpec(
        name="measurements_slim",
        builder=measurements_slim_select,
        columns=MEASUREMENTS_SLIM_COLUMNS,
        mapping_version="1",
    ),
    "runs_slim": ReadModelSpec(
        name="runs_slim",
        builder=runs_slim_select,
        columns=RUNS_SLIM_COLUMNS,
        mapping_version="1",
    ),
    "steps_slim": ReadModelSpec(
        name="steps_slim",
        builder=steps_slim_select,
        columns=STEPS_SLIM_COLUMNS,
        mapping_version="1",
    ),
    "steps_catalog": ReadModelSpec(
        name="steps_catalog",
        builder=steps_catalog_select,
        columns=STEPS_CATALOG_COLUMNS,
        mapping_version="1",
    ),
    "measurements_catalog": ReadModelSpec(
        name="measurements_catalog",
        builder=measurements_catalog_select,
        columns=MEASUREMENTS_CATALOG_COLUMNS,
        mapping_version="1",
    ),
    "inputs_catalog": ReadModelSpec(
        name="inputs_catalog",
        builder=inputs_catalog_select,
        columns=INPUTS_CATALOG_COLUMNS,
        mapping_version="1",
    ),
    "outputs_catalog": ReadModelSpec(
        name="outputs_catalog",
        builder=outputs_catalog_select,
        columns=OUTPUTS_CATALOG_COLUMNS,
        mapping_version="1",
    ),
    "runs_catalog": ReadModelSpec(
        name="runs_catalog",
        builder=runs_catalog_select,
        columns=RUNS_CATALOG_COLUMNS,
        mapping_version="1",
    ),
    "inputs_measurements_catalog": ReadModelSpec(
        name="inputs_measurements_catalog",
        builder=inputs_measurements_catalog_select,
        columns=INPUTS_MEASUREMENTS_CATALOG_COLUMNS,
        mapping_version="1",
        builder_arity=2,
    ),
}


def _builder_sql_for_fingerprint(spec: ReadModelSpec) -> str:
    if spec.builder_arity == 2:
        return spec.builder(_SOURCE_PLACEHOLDER, _SOURCE_PLACEHOLDER)
    return spec.builder(_SOURCE_PLACEHOLDER)


def _column_type_token(kind: ColumnType) -> str:
    """Render one column's :data:`ColumnType` for the fingerprint payload: a
    scalar BigQuery type name unchanged, or — for a nested-struct column
    like `inputs` (a tuple of its own (name, type) sub-columns) —
    `ARRAY<STRUCT<name:type, ...>>`, so the fingerprint text also documents
    the physical BigQuery shape a change would force the server to rebuild."""
    if isinstance(kind, str):
        return kind
    inner = ", ".join(f"{sub_name}:{sub_kind}" for sub_name, sub_kind in kind)
    return f"ARRAY<STRUCT<{inner}>>"


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
    cols = "|".join(
        f"{col_name}:{_column_type_token(col_type)}" for col_name, col_type in spec.columns
    )
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
