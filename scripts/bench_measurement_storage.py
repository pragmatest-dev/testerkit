"""Measurement-storage query benchmark — typed columns vs JSON vs VARIANT.

The measurement-storage redesign (#37/#38) replaces the wide dynamically-typed
``in_<k>`` / ``out_<k>`` parquet columns with one semi-structured column per
side — lossless, mixed-type-safe, swap-ready. Two encodings are in play:

- **json**    — ``to_json`` text. Self-describing but stored as a string;
  every query parses text and every number round-trips through VARCHAR.
- **variant** — DuckDB 1.5.x native VARIANT (typed binary). On parquet write
  DuckDB *shreds* consistently-typed sub-fields into native typed columns
  (``inputs.c0`` → its own ``typed_value`` column). The open Parquet/Iceberg-v3
  standard; portable to Snowflake/Spark/Dremio (whose readers DO push into the
  shredded columns). DuckDB's own reader does NOT — verified through 1.5.3, it
  reconstructs the variant + ``variant_extract`` per row, so parquet-scan
  queries land slower than JSON. VARIANT is the at-rest win (compact, lossless,
  portable); the query path on DuckDB must be a typed projection.

The viewer leans on three query patterns:

- **enum**   — distinct values of an input (populate a condition dropdown).
- **filter** — rows where an input equals a value (select a condition).
- **cpk**    — avg + stddev of an output grouped by an input (capability).

This measures each pattern on json and variant relative to a native typed
column, both in-memory and scanning parquet (where variant shredding kicks in).
All tables hold identical data, derived from the typed one via DuckDB
``to_json`` / ``::VARIANT`` (apples-to-apples, no Python encode skew). Read it as
same-run ratios, not absolute ms (machine variance).

Usage::

    uv run python scripts/bench_measurement_storage.py
    uv run python scripts/bench_measurement_storage.py --rows 500000 --in-cols 12 --out-cols 12
"""

from __future__ import annotations

import argparse
import os
import statistics
import tempfile
import time

import duckdb
import numpy as np
import pyarrow as pa

# Input columns are sweep conditions → low cardinality (a handful of distinct
# values, the thing a dropdown enumerates and a Cpk groups by). Output columns
# are measured values → continuous.
IN_CARDINALITY = 8


def _build(con: duckdb.DuckDBPyConnection, rows: int, in_cols: int, out_cols: int) -> None:
    """Create ``typed`` (wide cols), ``js`` (JSON text), ``var`` (VARIANT) tables."""
    rng = np.random.default_rng(1234)
    sweep = np.linspace(3.0, 5.5, IN_CARDINALITY)
    cols: dict[str, np.ndarray] = {}
    for i in range(in_cols):
        cols[f"in_c{i}"] = rng.choice(sweep, size=rows)
    for i in range(out_cols):
        cols[f"out_c{i}"] = rng.normal(3.3, 0.05, size=rows)
    con.register("_gen", pa.table(cols))
    con.execute("CREATE TABLE typed AS SELECT * FROM _gen")
    con.unregister("_gen")

    # One struct expression per side, reused for both encodings so json and
    # variant hold byte-identical logical data derived from the typed columns.
    in_struct = "{" + ", ".join(f"'c{i}': in_c{i}" for i in range(in_cols)) + "}"
    out_struct = "{" + ", ".join(f"'c{i}': out_c{i}" for i in range(out_cols)) + "}"
    con.execute(
        f"CREATE TABLE js AS SELECT to_json({in_struct}) AS inputs, "
        f"to_json({out_struct}) AS outputs FROM typed"
    )
    con.execute(
        f"CREATE TABLE var AS SELECT ({in_struct})::VARIANT AS inputs, "
        f"({out_struct})::VARIANT AS outputs FROM typed"
    )

    # Maintained enum projection: distinct values + counts per input name. The
    # backend-neutral enumeration optimization — a plain table (O(distinct), any
    # SQL backend has it), NOT a DB-specific index. Derived from the source like
    # any projection; refreshed as runs materialize. Orthogonal to the encoding.
    parts = " UNION ALL ".join(
        f"SELECT 'c{i}' AS name, CAST(in_c{i} AS VARCHAR) AS value, count(*) AS n "
        f"FROM typed GROUP BY in_c{i}"
        for i in range(in_cols)
    )
    con.execute(f"CREATE TABLE enum_index AS {parts}")


def _queries(typed: str, js: str, var: str, val: float) -> dict[str, tuple[str, str, str]]:
    """Build the (typed, json, variant) SQL triple for each pattern over given sources.

    ``typed`` / ``js`` / ``var`` are relation expressions — a table name in-memory,
    or a ``read_parquet('...')`` call for the on-disk scan.
    """
    return {
        "enum   (DISTINCT in c0)": (
            f"SELECT DISTINCT in_c0 FROM {typed}",
            f"SELECT DISTINCT inputs->>'$.c0' FROM {js}",
            f"SELECT DISTINCT (inputs.c0)::DOUBLE FROM {var}",
        ),
        "filter (in c0 = v)": (
            f"SELECT count(*) FROM {typed} WHERE in_c0 = {val}",
            f"SELECT count(*) FROM {js} WHERE (inputs->>'$.c0')::DOUBLE = {val}",
            f"SELECT count(*) FROM {var} WHERE (inputs.c0)::DOUBLE = {val}",
        ),
        "cpk    (avg/stddev out c0 BY in c0)": (
            f"SELECT in_c0, avg(out_c0), stddev(out_c0) FROM {typed} GROUP BY in_c0",
            f"SELECT inputs->>'$.c0', avg((outputs->>'$.c0')::DOUBLE), "
            f"stddev((outputs->>'$.c0')::DOUBLE) FROM {js} GROUP BY 1",
            f"SELECT (inputs.c0)::DOUBLE, avg((outputs.c0)::DOUBLE), "
            f"stddev((outputs.c0)::DOUBLE) FROM {var} GROUP BY 1",
        ),
    }


def _median_ms(con: duckdb.DuckDBPyConnection, sql: str, rounds: int = 9) -> float:
    con.execute(sql).fetchall()  # warm (plan + cache)
    times = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        con.execute(sql).fetchall()
        times.append(time.perf_counter() - t0)
    return statistics.median(times) * 1000.0


def _table(
    con: duckdb.DuckDBPyConnection,
    title: str,
    cases: dict[str, tuple[str, str, str]],
    rounds: int,
) -> None:
    print(f"\n{title}")
    print(f"{'query':<38} {'typed':>9} {'json':>9} {'variant':>9} {'j/t':>5} {'v/t':>5}")
    print("-" * 80)
    for label, (t_sql, j_sql, v_sql) in cases.items():
        t = _median_ms(con, t_sql, rounds)
        j = _median_ms(con, j_sql, rounds)
        v = _median_ms(con, v_sql, rounds)
        print(f"{label:<38} {t:>7.2f}ms {j:>7.2f}ms {v:>7.2f}ms {j / t:>4.1f}x {v / t:>4.1f}x")


def _run(rows: int, in_cols: int, out_cols: int, rounds: int) -> None:
    con = duckdb.connect(":memory:")
    _build(con, rows, in_cols, out_cols)
    val = float(np.linspace(3.0, 5.5, IN_CARDINALITY)[3])

    print(f"\nrows={rows:,}  in_cols={in_cols}  out_cols={out_cols}  rounds={rounds}")

    # 1. In-memory: typed columns vs JSON-text vs VARIANT-binary.
    _table(con, "in-memory", _queries("typed", "js", "var", val), rounds)

    # 2. Parquet scan (zstd, as the runs store writes): VARIANT shreds typed
    # sub-columns on write, but DuckDB's reader does not push into them (it
    # reconstructs the variant per row) — so variant scans land slower than
    # JSON here. The shredded format pays off only in readers that push down
    # (Snowflake/Spark) or if DuckDB adds it later.
    with tempfile.TemporaryDirectory() as d:
        tp = os.path.join(d, "typed.parquet")
        jp = os.path.join(d, "json.parquet")
        vp = os.path.join(d, "var.parquet")
        for tbl, path in (("typed", tp), ("js", jp), ("var", vp)):
            con.execute(f"COPY {tbl} TO '{path}' (FORMAT parquet, COMPRESSION zstd)")

        rp = lambda p: f"read_parquet('{p}')"  # noqa: E731
        _table(con, "parquet scan", _queries(rp(tp), rp(jp), rp(vp), val), rounds)

        st, sj, sv = os.path.getsize(tp), os.path.getsize(jp), os.path.getsize(vp)
        print(
            f"\nparquet size (zstd): typed={st:,}B  json={sj:,}B ({sj / max(st, 1):.1f}x)  "
            f"variant={sv:,}B ({sv / max(st, 1):.1f}x)"
        )

    # 3. Enumeration via the maintained index (the distinct_values() API seam):
    # a lookup keyed on name — O(distinct), independent of corpus size and of
    # the encoding. Orthogonal optimization that any backend can hold.
    idx = _median_ms(con, "SELECT value, n FROM enum_index WHERE name = 'c0'", rounds)
    j_enum = _median_ms(con, "SELECT DISTINCT inputs->>'$.c0' FROM js", rounds)
    print(
        f"\nenum   (maintained index lookup)       {idx:>7.2f}ms   "
        f"{j_enum / idx:>5.0f}x vs json-scan"
    )
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=200_000)
    ap.add_argument("--in-cols", type=int, default=8)
    ap.add_argument("--out-cols", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=9)
    args = ap.parse_args()
    for rows in (50_000, args.rows) if args.rows != 50_000 else (50_000,):
        _run(rows, args.in_cols, args.out_cols, args.rounds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
