"""Scaling benchmark for the long/EAV measurement index.

``bench_index_shape.py`` proved the long table beats the ``dynamic_attrs MAP``
at 200k rows / 8+8 dense names. It does NOT prove the long table *scales* — and
it ran in ``:memory:`` with no bound, which OOMs at a few million rows on an
artifact production never hits. The runs daemon index is **on-disk**
(``duckdb.connect(str(index_path))``) with the default ``memory_limit`` and
spills to a temp dir; it never holds the whole build in RAM.

This bench mirrors that: an **on-disk** database with an explicit
``memory_limit``, and the big tables generated *inside* DuckDB (via ``range``
cross-joins) so the data streams to disk and never lands in the Python process.
Two scaling axes:

- **rows** — measurements × (in+out) = long-table row count. Dense 8+8 names so
  the ``typed-wide`` gold standard is buildable as the ratio baseline. Sweeps to
  tens of millions of long rows. Watches the Cpk self-join, the one operation
  whose cost is superlinear in row count.
- **pool** — distinct measurement NAMES. This is the old column-explosion threat,
  relocated: in the wide shape every new name is a new column (infeasible past a
  few thousand); in the long shape a name is a *value* under one ``name`` index.
  Each measurement draws a sparse subset of a large name pool, so ``WHERE
  name='x'`` is selective. The wide shape is not even built here — at this
  cardinality it can't be.

Usage::

    uv run --with duckdb==1.5.3 python scripts/bench_index_scale.py
    uv run --with duckdb==1.5.3 python scripts/bench_index_scale.py --rows 1000000,5000000,10000000
    uv run --with duckdb==1.5.3 python scripts/bench_index_scale.py --pool 2000,20000 --rows 1000000
    uv run --with duckdb==1.5.3 python scripts/bench_index_scale.py --memory-limit 2GB
"""

from __future__ import annotations

import argparse
import statistics
import tempfile
import time
from pathlib import Path

import duckdb

IN_CARDINALITY = 8  # distinct sweep levels of in_c0, for the Cpk GROUP BY
IN_COLS = 8
OUT_COLS = 8
_WITH_MID_INDEX = False  # ART index on measurement_id: memory-resident, no-spill,
#                          and unused by hash joins. Toggle with --mid-index.


def _connect(tmp: Path, name: str, memory_limit: str) -> duckdb.DuckDBPyConnection:
    """On-disk connection bounded like the production daemon index."""
    db = tmp / f"{name}.duckdb"
    con = duckdb.connect(str(db))
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute(f"SET temp_directory='{tmp / (name + '.tmp')}'")
    # DuckDB's first recommendation for large spilling builds; ART indexes are
    # memory-resident and don't spill, so we also keep them minimal.
    con.execute("SET preserve_insertion_order=false")
    return con


def _median_ms(con: duckdb.DuckDBPyConnection, sql: str, rounds: int) -> float:
    con.execute(sql).fetchall()  # warm
    times = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        con.execute(sql).fetchall()
        times.append(time.perf_counter() - t0)
    return statistics.median(times) * 1000.0


def _db_mb(tmp: Path, name: str) -> float:
    f = tmp / f"{name}.duckdb"
    return f.stat().st_size / 1e6 if f.exists() else 0.0


# ── row-count scaling: dense 8+8 names, typed-wide baseline ───────────────────


def _build_dense(con: duckdb.DuckDBPyConnection, rows: int) -> None:
    """Build typed-wide + long from one generated source, entirely in-DB."""
    in_sel = ", ".join(
        # in_c0 is the discrete sweep used by Cpk's GROUP BY; rest are noise.
        (
            f"3.0 + 2.5 * ((m % {IN_CARDINALITY})::DOUBLE / {IN_CARDINALITY}) AS in_c0"
            if i == 0
            else f"3.0 + ((m * {7 * i + 1}) % 1000)::DOUBLE / 1000 AS in_c{i}"
        )
        for i in range(IN_COLS)
    )
    out_sel = ", ".join(
        f"3.3 + ((m * {13 * i + 3}) % 1000)::DOUBLE / 10000 AS out_c{i}" for i in range(OUT_COLS)
    )
    con.execute(
        f"CREATE TABLE wide AS SELECT m AS measurement_id, {in_sel}, {out_sel} "
        f"FROM range(0, {rows}) t(m)"
    )

    in_un = " UNION ALL ".join(
        f"SELECT measurement_id, 'in' AS side, 'in_c{i}' AS name, "
        f"in_c{i} AS value_double, NULL::VARCHAR AS value_text FROM wide"
        for i in range(IN_COLS)
    )
    out_un = " UNION ALL ".join(
        f"SELECT measurement_id, 'out' AS side, 'out_c{i}' AS name, "
        f"out_c{i} AS value_double, NULL::VARCHAR AS value_text FROM wide"
        for i in range(OUT_COLS)
    )
    con.execute(f"CREATE TABLE long_idx AS {in_un} UNION ALL {out_un}")
    con.execute("CREATE INDEX idx_long_name ON long_idx(name)")
    if _WITH_MID_INDEX:
        con.execute("CREATE INDEX idx_long_mid ON long_idx(measurement_id)")


def _run_rows(rows_list: list[int], memory_limit: str, rounds: int) -> None:
    val = float(3.0 + 2.5 * (3 / IN_CARDINALITY))  # an in_c0 sweep level
    print(f"\n=== ROW-COUNT SCALING (dense {IN_COLS}+{OUT_COLS}, mem={memory_limit}) ===")
    header = (
        f"{'measurements':>13} {'long rows':>12} {'db MB':>8}  "
        f"{'enum tw/long':>16} {'filter tw/long':>17} {'cpk tw/long':>17}"
    )
    print(header)
    print("-" * len(header))
    for rows in rows_list:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            con = _connect(tmp, "dense", memory_limit)
            _build_dense(con, rows)
            long_rows = rows * (IN_COLS + OUT_COLS)

            enum_tw = _median_ms(con, "SELECT DISTINCT in_c0 FROM wide", rounds)
            enum_lg = _median_ms(
                con, "SELECT DISTINCT value_double FROM long_idx WHERE name='in_c0'", rounds
            )
            filt_tw = _median_ms(con, f"SELECT count(*) FROM wide WHERE in_c0={val}", rounds)
            filt_lg = _median_ms(
                con,
                f"SELECT count(*) FROM long_idx WHERE name='in_c0' AND value_double={val}",
                rounds,
            )
            cpk_tw = _median_ms(
                con, "SELECT in_c0, avg(out_c0), stddev(out_c0) FROM wide GROUP BY in_c0", rounds
            )
            cpk_lg = _median_ms(
                con,
                "SELECT i.value_double g, avg(o.value_double), stddev(o.value_double) "
                "FROM long_idx i JOIN long_idx o USING (measurement_id) "
                "WHERE i.name='in_c0' AND o.name='out_c0' GROUP BY g",
                rounds,
            )
            mb = _db_mb(tmp, "dense")
            con.close()
            print(
                f"{rows:>13,} {long_rows:>12,} {mb:>8.0f}  "
                f"{enum_tw:>6.1f}/{enum_lg:<8.1f} {filt_tw:>7.1f}/{filt_lg:<8.1f} "
                f"{cpk_tw:>7.1f}/{cpk_lg:<8.1f}"
            )


# ── name-cardinality scaling: sparse names from a large pool, long only ───────


def _build_sparse(con: duckdb.DuckDBPyConnection, rows: int, pool: int, slots: int) -> None:
    """Long table where each measurement draws ``slots`` names from a pool of
    ``pool`` distinct names. No wide table — at this cardinality it can't exist.
    """
    # name = 'p' || ((m*prime + slot) % pool); deterministic, spread across pool.
    con.execute(
        f"CREATE TABLE long_idx AS "
        f"SELECT m AS measurement_id, "
        f"'p' || (((m * 2654435761 + s) % {pool})) AS name, "
        f"3.0 + ((m * 31 + s) % 1000)::DOUBLE / 1000 AS value_double, "
        f"NULL::VARCHAR AS value_text "
        f"FROM range(0, {rows}) t(m) CROSS JOIN range(0, {slots}) u(s)"
    )
    con.execute("CREATE INDEX idx_long_name ON long_idx(name)")
    if _WITH_MID_INDEX:
        con.execute("CREATE INDEX idx_long_mid ON long_idx(measurement_id)")


def _run_pool(pools: list[int], rows: int, slots: int, memory_limit: str, rounds: int) -> None:
    title = f"rows={rows:,}, {slots} names/meas, mem={memory_limit}"
    print(f"\n=== NAME-CARDINALITY SCALING ({title}) ===")
    print("(wide shape would need 'pool' columns — not buildable; long only)")
    header = (
        f"{'name pool':>10} {'long rows':>12} {'db MB':>8} {'distinct':>9}  "
        f"{'enum 1name':>11} {'filter 1name':>13} {'global distinct-names':>21}"
    )
    print(header)
    print("-" * len(header))
    for pool in pools:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            con = _connect(tmp, "sparse", memory_limit)
            _build_sparse(con, rows, pool, slots)
            long_rows = rows * slots
            probe = "p7"  # one name in the pool
            enum1 = _median_ms(
                con, f"SELECT DISTINCT value_double FROM long_idx WHERE name='{probe}'", rounds
            )
            filt1 = _median_ms(
                con,
                f"SELECT count(*) FROM long_idx WHERE name='{probe}' AND value_double > 3.5",
                rounds,
            )
            distinct_names = _median_ms(con, "SELECT DISTINCT name FROM long_idx", rounds)
            n_distinct = con.execute("SELECT count(DISTINCT name) FROM long_idx").fetchone()
            mb = _db_mb(tmp, "sparse")
            con.close()
            nd = n_distinct[0] if n_distinct else 0
            print(
                f"{pool:>10,} {long_rows:>12,} {mb:>8.0f} {nd:>9,}  "
                f"{enum1:>9.1f}ms {filt1:>11.1f}ms {distinct_names:>19.1f}ms"
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default="200000,1000000,5000000,10000000")
    ap.add_argument("--pool", default="2000,20000,200000")
    ap.add_argument("--pool-rows", type=int, default=1_000_000)
    ap.add_argument("--slots", type=int, default=16)
    ap.add_argument("--memory-limit", default="2GB")
    ap.add_argument("--rounds", type=int, default=7)
    ap.add_argument("--skip-rows", action="store_true")
    ap.add_argument("--skip-pool", action="store_true")
    ap.add_argument(
        "--mid-index", action="store_true", help="also build ART index on measurement_id"
    )
    args = ap.parse_args()

    global _WITH_MID_INDEX
    _WITH_MID_INDEX = args.mid_index

    if not args.skip_rows:
        _run_rows([int(x) for x in args.rows.split(",")], args.memory_limit, args.rounds)
    if not args.skip_pool:
        _run_pool(
            [int(x) for x in args.pool.split(",")],
            args.pool_rows,
            args.slots,
            args.memory_limit,
            args.rounds,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
