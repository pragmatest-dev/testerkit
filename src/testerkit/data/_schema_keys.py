"""Canonical served-table grain keys — PRIVATE, internal storage-dedup detail.

**Not public library surface.** A bench client's own code has no reason to
import a served table's uniqueness grain (it's a storage/MERGE-dedup
implementation detail, not part of the measurement-projection data model a
client works with — that's ``measurement_projection.py``/``run_projection.py``'s
job). This module exists so the LOGICAL grain — the raw column names, per
served table, that together uniquely identify one row — is defined exactly
once and consumed by every internal renderer of it: the local DuckDB daemon's
``PRIMARY KEY`` DDL (``_runs_duckdb_daemon.py``) and the cloud's BigQuery
``MERGE`` backends (``testerkit_server``'s ``runs_backend.py`` / ``steps_backend.py``
/ ``vectors_backend.py`` / ``measurements_backend.py`` / ``lanes_backend.py``).
The cloud is already a privileged internal consumer of private framework
modules (``from testerkit.data._sql_helpers import ...``); importing this one
is the same precedent, not a new one.

**Share the grain, not the rendering.** Each ``*_KEY`` tuple below is the
LOGICAL key — plain column names, engine-agnostic. Neither consumer renders
the OTHER's SQL, and each renderer's NULL-handling reflects that engine's OWN
actual nullability, not a shared assumption:

- The DuckDB daemon's ``steps_materialized``/``vectors_materialized`` declare
  ``vector_index``/``vector_retry`` ``NOT NULL`` (a vector row always has a
  concrete condition-point identity) — only ``vector_outer_index`` is ever
  genuinely NULL-able there (no enclosing outer sweep), so only IT gets a
  computed ``vector_outer_index_key = COALESCE(vector_outer_index, -1)``
  sentinel column, and only IT needs rewriting for a ``PRIMARY KEY (...)``
  clause (a real DuckDB PK cannot contain a NULL-able column). See
  :func:`primary_key_ddl_columns`, which the daemon's ``CREATE TABLE`` DDL
  calls to build its PK clause FROM the canonical tuple below (so the DDL and
  the constant cannot drift apart).
- The cloud's flat BigQuery tables mix step-scope AND vector-scope rows in
  ONE physical table (unlike the daemon's star-schema split), so every
  ``vector_*`` coordinate genuinely CAN be NULL on any given row there —
  every one needs ``COALESCE(col, -1)`` in the ``MERGE`` ``ON``/dedup clause.
  See :func:`null_safe_key_columns`.

**Where each grain came from (not re-guessed).** ``STEPS_KEY``/``VECTORS_KEY``
are read directly off ``steps_materialized``/``vectors_materialized``'s real
DuckDB ``PRIMARY KEY`` constraints. ``RUNS_KEY`` is ``runs_materialized.run_id
PRIMARY KEY``. ``MEASUREMENT_FACTS_KEY``/``LANES_KEY`` have no real PK
anywhere today — ``measurements_materialized`` is a plain ``CREATE TABLE``
(the daemon re-derives it by delete-then-reinsert per file, never a keyed
upsert, so it was never forced to declare one); their grain was only ever
DESCRIBED in a comment ("the coordinate columns... ARE the grain key...
`ordinal`... the true PK discriminator" — `_runs_duckdb_daemon.py`'s
``measurements_materialized`` block) and never enforced or exported. That
absence was itself a gap (a hand-copied cloud MERGE key silently missing
`vector_outer_index`/`vector_retry` was the incident that prompted this
module): ``MEASUREMENT_FACTS_KEY``/``LANES_KEY`` are the union of their
carrier's own grain (a measurement/lane entry rides EITHER a step row's grain
— ``run_id, step_path, step_retry, vector_outer_index`` — OR a vector row's,
which also carries ``vector_index, vector_retry``) plus the carrier-scoped
discriminator (``ordinal`` for a measurement — a name can repeat on one
carrier; ``role, name`` for a lane entry).
"""

from __future__ import annotations

# Each entry: the served table's grain, in a stable (not necessarily DDL-
# clause) order. Every column here is a plain, at-rest column name — never a
# rendered expression.
RUNS_KEY: tuple[str, ...] = ("run_id",)

STEPS_KEY: tuple[str, ...] = ("run_id", "step_path", "step_retry", "vector_outer_index")

VECTORS_KEY: tuple[str, ...] = (
    "run_id",
    "step_path",
    "step_retry",
    "vector_outer_index",
    "vector_index",
    "vector_retry",
)

MEASUREMENT_FACTS_KEY: tuple[str, ...] = (
    "run_id",
    "step_path",
    "step_retry",
    "vector_outer_index",
    "vector_index",
    "vector_retry",
    "ordinal",
)

LANES_KEY: tuple[str, ...] = (
    "run_id",
    "step_path",
    "step_retry",
    "vector_outer_index",
    "vector_index",
    "vector_retry",
    "role",
    "name",
)

# The only columns the LOCAL DuckDB daemon ever materializes a `{col}_key =
# COALESCE({col}, -1)` sentinel for (see `steps_materialized`/
# `vectors_materialized`'s DDL) — `vector_index`/`vector_retry` are declared
# `NOT NULL` on `vectors_materialized` itself (a vector's own identity is
# always concrete), so they need no sentinel there at all. Used only by
# :func:`primary_key_ddl_columns`; the cloud MERGE path does NOT use this —
# see :func:`null_safe_key_columns`.
_DDL_SENTINEL_COLUMNS: frozenset[str] = frozenset({"vector_outer_index"})


def null_safe_key_columns(key: tuple[str, ...]) -> tuple[str, ...]:
    """Which columns of `key` need NULL-safe `COALESCE(..., -1)` treatment in
    a BigQuery `MERGE` `ON`/dedup clause (`NULL = NULL` is never a match
    there) — every `vector_*` coordinate, since the cloud's flat tables mix
    step-scope and vector-scope rows and so never enforce any of them
    `NOT NULL` the way the local DuckDB tables do (see module docstring).
    `run_id`/`step_path` are always required, `step_retry` is already
    `COALESCE(...,0)`-normalized by the projection layer before it reaches
    any consumer of this module, and `ordinal`/`role`/`name` are always
    concrete for the row they're on. A single derivable rule instead of a
    second hand-maintained tuple per table (the exact drift risk this module
    exists to close)."""
    return tuple(c for c in key if c.startswith("vector_"))


def primary_key_ddl_columns(key: tuple[str, ...]) -> tuple[str, ...]:
    """`key` rewritten for a local DuckDB `PRIMARY KEY (...)` clause: each
    column in `_DDL_SENTINEL_COLUMNS` (today, only `vector_outer_index`) named
    as its `{col}_key` sentinel — the `COALESCE({col}, -1)` column the table
    itself stores and keeps in sync (`vector_outer_index_key`) — since a real
    DuckDB PK cannot contain a NULL-able column at all. Every other column
    (including `vector_index`/`vector_retry`, `NOT NULL` at rest on
    `vectors_materialized`) passes through unchanged. Lets the daemon's
    `CREATE TABLE`s build their `PRIMARY KEY` clause FROM the canonical grain
    instead of a hand-typed column list that could drift from it."""
    return tuple(f"{c}_key" if c in _DDL_SENTINEL_COLUMNS else c for c in key)
