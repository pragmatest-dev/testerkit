"""``testerkit.data._schema_keys`` — the canonical served-table grain keys
(docs/36 P3 root-cause fix).

Proves three things:

1. The exported ``*_KEY`` tuples match the REAL DuckDB ``PRIMARY KEY``
   constraints ``_ensure_schema`` creates — introspected from a live
   connection via ``duckdb_constraints()``, not re-read from the DDL source
   text, so this actually exercises what gets created, not just what's typed.
2. ``primary_key_ddl_columns`` is what the daemon's own DDL uses (drift-guard
   on the daemon side — a divergence here would mean the daemon's PK clause
   and the constant it claims to derive from have come apart).
3. ``null_safe_key_columns``/``primary_key_ddl_columns`` behave correctly on
   their own, including the asymmetry between the two (steps/vectors DDL only
   sentinels `vector_outer_index`; the cloud's null-safety rule covers every
   `vector_*` column) that the module's docstring explains.
"""

from __future__ import annotations

import duckdb

from testerkit.data._runs_duckdb_daemon import _ensure_schema
from testerkit.data._schema_keys import (
    IO_KEY,
    MEASUREMENT_FACTS_KEY,
    RUNS_KEY,
    STEPS_KEY,
    VECTORS_KEY,
    null_safe_key_columns,
    primary_key_ddl_columns,
)


def _live_pk_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    rows = con.execute(
        "SELECT constraint_column_names FROM duckdb_constraints() "
        "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'",
        [table],
    ).fetchall()
    assert len(rows) == 1, f"expected exactly one PK constraint on {table}, got {rows}"
    return list(rows[0][0])


def test_runs_materialized_pk_matches_runs_key() -> None:
    con = duckdb.connect()
    try:
        _ensure_schema(con)
        assert _live_pk_columns(con, "runs_materialized") == list(RUNS_KEY)
    finally:
        con.close()


def test_steps_materialized_pk_matches_derived_steps_key() -> None:
    con = duckdb.connect()
    try:
        _ensure_schema(con)
        assert _live_pk_columns(con, "steps_materialized") == list(
            primary_key_ddl_columns(STEPS_KEY)
        )
    finally:
        con.close()


def test_vectors_materialized_pk_matches_derived_vectors_key() -> None:
    con = duckdb.connect()
    try:
        _ensure_schema(con)
        assert _live_pk_columns(con, "vectors_materialized") == list(
            primary_key_ddl_columns(VECTORS_KEY)
        )
    finally:
        con.close()


def test_null_safe_key_columns_is_every_vector_coordinate() -> None:
    assert null_safe_key_columns(STEPS_KEY) == ("vector_outer_index",)
    assert null_safe_key_columns(VECTORS_KEY) == (
        "vector_outer_index",
        "vector_index",
        "vector_retry",
    )
    assert null_safe_key_columns(MEASUREMENT_FACTS_KEY) == (
        "vector_outer_index",
        "vector_index",
        "vector_retry",
    )
    assert null_safe_key_columns(IO_KEY) == (
        "vector_outer_index",
        "vector_index",
        "vector_retry",
    )
    assert null_safe_key_columns(RUNS_KEY) == ()  # run_id is never NULL-able


def test_primary_key_ddl_columns_only_sentinels_vector_outer_index() -> None:
    """The DDL-rendering asymmetry the module docstring describes: unlike
    `null_safe_key_columns` (every `vector_*` column, for the cloud's flat
    MERGE tables), `primary_key_ddl_columns` only rewrites `vector_outer_index`
    — `vector_index`/`vector_retry` are `NOT NULL` on `vectors_materialized`
    itself, so they pass through unchanged, never getting a nonexistent
    `vector_index_key`/`vector_retry_key` column reference."""
    assert primary_key_ddl_columns(VECTORS_KEY) == (
        "run_id",
        "step_path",
        "step_retry",
        "vector_outer_index_key",
        "vector_index",
        "vector_retry",
    )


def test_measurement_facts_key_is_the_union_of_step_and_vector_grain_plus_ordinal() -> None:
    """docs/36 P3: measurement_facts has no real local PK — this tuple is the
    union of the step carrier's own grain and the vector carrier's own grain,
    plus `ordinal` (the true within-carrier discriminator)."""
    assert set(STEPS_KEY) <= set(MEASUREMENT_FACTS_KEY)
    assert set(VECTORS_KEY) <= set(MEASUREMENT_FACTS_KEY)
    assert MEASUREMENT_FACTS_KEY[-1] == "ordinal"


def test_io_key_is_the_union_of_step_and_vector_grain_plus_role_name() -> None:
    assert set(STEPS_KEY) <= set(IO_KEY)
    assert set(VECTORS_KEY) <= set(IO_KEY)
    assert IO_KEY[-2:] == ("role", "name")


def test_schema_keys_module_is_private() -> None:
    """Regression guard for the "don't promote this into public library
    surface" requirement: the module name itself must stay underscore-
    prefixed (private-by-convention), and it must not be re-exported from
    `measurement_projection.py`/`run_projection.py` (the actual public
    surface a bench client imports)."""
    import testerkit.data._schema_keys as mod
    import testerkit.data.measurement_projection as mp
    import testerkit.data.run_projection as rp

    assert mod.__name__.rsplit(".", 1)[-1].startswith("_")
    for name in ("STEPS_KEY", "VECTORS_KEY", "MEASUREMENT_FACTS_KEY", "IO_KEY", "RUNS_KEY"):
        assert not hasattr(mp, name), f"{name} leaked into measurement_projection's public surface"
        assert not hasattr(rp, name), f"{name} leaked into run_projection's public surface"
