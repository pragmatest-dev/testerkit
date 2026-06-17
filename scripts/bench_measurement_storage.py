"""Measurement-storage query benchmark — typed columns vs JSON.

The measurement-storage redesign (#37/#38) replaces the wide dynamically-typed
``in_<k>`` / ``out_<k>`` parquet columns with a single ``inputs`` / ``outputs``
JSON (semi-structured) column — lossless, mixed-type-safe, swap-ready. The one
cost is query speed: JSON-path access is slower than a native typed column, and
the parametric viewer leans on three patterns:

- **enum**   — distinct values of an input (populate a condition dropdown).
- **filter** — rows where an input equals a value (select a condition).
- **cpk**    — avg + stddev of an output grouped by an input (capability).

This measures how much slower each is on a JSON column vs a typed column, to
decide whether the JSON source needs a typed derived projection (the runs-daemon
DuckDB index) for the viewer, or whether raw JSON is fast enough on its own.

Both tables hold identical data; the JSON table is derived from the typed one via
DuckDB ``to_json`` (so generation is apples-to-apples, no Python encode skew).
Read it as same-run ratios, not absolute ms (machine variance).

Usage::

    uv run python scripts/bench_measurement_storage.py
    uv run python scripts/bench_measurement_storage.py --rows 500000 --in-cols 12 --out-cols 12
"""

from __future__ import annotations

import argparse
import statistics
import time

import duckdb
import numpy as np
import pyarrow as pa

# Input columns are sweep conditions → low cardinality (a handful of distinct
# values, the thing a dropdown enumerates and a Cpk groups by). Output columns
# are measured values → continuous.
IN_CARDINALITY = 8


def _build(con: duckdb.DuckDBPyConnection, rows: int, in_cols: int, out_cols: int) -> None:
    """Create ``typed`` (wide columns) and ``json`` (one JSON col each) tables."""
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

    in_pack = ", ".join(f"'c{i}': in_c{i}" for i in range(in_cols))
    out_pack = ", ".join(f"'c{i}': out_c{i}" for i in range(out_cols))
    con.execute(
        f"CREATE TABLE js AS SELECT to_json({{{in_pack}}}) AS inputs, "
        f"to_json({{{out_pack}}}) AS outputs FROM typed"
    )

    # Maintained enum projection: distinct values + counts per input name. The
    # backend-neutral enumeration optimization — a plain table (O(distinct), any
    # SQL backend has it), NOT a DB-specific JSON index. Derived from the source
    # like any projection; refreshed as runs materialize.
    parts = " UNION ALL ".join(
        f"SELECT 'c{i}' AS name, CAST(in_c{i} AS VARCHAR) AS value, count(*) AS n "
        f"FROM typed GROUP BY in_c{i}"
        for i in range(in_cols)
    )
    con.execute(f"CREATE TABLE enum_index AS {parts}")


def _median_ms(con: duckdb.DuckDBPyConnection, sql: str, rounds: int = 9) -> float:
    con.execute(sql).fetchall()  # warm (plan + cache)
    times = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        con.execute(sql).fetchall()
        times.append(time.perf_counter() - t0)
    return statistics.median(times) * 1000.0


def _run(rows: int, in_cols: int, out_cols: int, rounds: int) -> None:
    con = duckdb.connect(":memory:")
    _build(con, rows, in_cols, out_cols)
    val = float(np.linspace(3.0, 5.5, IN_CARDINALITY)[3])

    cases = {
        "enum   (DISTINCT in_c0)": (
            "SELECT DISTINCT in_c0 FROM typed",
            "SELECT DISTINCT inputs->>'$.c0' FROM js",
        ),
        "filter (in_c0 = v)": (
            f"SELECT count(*) FROM typed WHERE in_c0 = {val}",
            f"SELECT count(*) FROM js WHERE (inputs->>'$.c0')::DOUBLE = {val}",
        ),
        "cpk    (avg/stddev out_c0 BY in_c0)": (
            "SELECT in_c0, avg(out_c0), stddev(out_c0) FROM typed GROUP BY in_c0",
            "SELECT inputs->>'$.c0', avg((outputs->>'$.c0')::DOUBLE), "
            "stddev((outputs->>'$.c0')::DOUBLE) FROM js GROUP BY 1",
        ),
    }

    print(f"\nrows={rows:,}  in_cols={in_cols}  out_cols={out_cols}  rounds={rounds}")
    print(f"{'query':<38} {'typed':>9} {'json':>9} {'ratio':>7}")
    print("-" * 66)
    for label, (typed_sql, json_sql) in cases.items():
        t = _median_ms(con, typed_sql, rounds)
        j = _median_ms(con, json_sql, rounds)
        print(f"{label:<38} {t:>7.2f}ms {j:>7.2f}ms {j / t:>6.1f}x")

    # Enumeration via the maintained index (the distinct_values() API seam):
    # a lookup keyed on name — O(distinct), independent of corpus size and of
    # whether the source is typed or JSON.
    idx = _median_ms(con, "SELECT value, n FROM enum_index WHERE name = 'c0'", rounds)
    j_enum = _median_ms(con, "SELECT DISTINCT inputs->>'$.c0' FROM js", rounds)
    print(
        f"{'enum   (maintained index lookup)':<38} {idx:>7.2f}ms "
        f"{'':>9} {j_enum / idx:>5.0f}x vs json-scan"
    )

    # Storage: write each to parquet (zstd, as the runs store does) and compare
    # on-disk bytes — JSON text vs native typed columns.
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tp, jp = os.path.join(d, "typed.parquet"), os.path.join(d, "json.parquet")
        con.execute(f"COPY typed TO '{tp}' (FORMAT parquet, COMPRESSION zstd)")
        con.execute(f"COPY js TO '{jp}' (FORMAT parquet, COMPRESSION zstd)")
        st, sj = os.path.getsize(tp), os.path.getsize(jp)
        print(f"\nparquet size (zstd): typed={st:,}B  json={sj:,}B  ({sj / max(st, 1):.1f}x)")
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
