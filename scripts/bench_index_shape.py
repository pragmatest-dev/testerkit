"""Index-shape benchmark — daemon MAP vs typed LONG/EAV table.

P2 has to change how the runs daemon fills its DuckDB index from the new nested
parquet. Two candidate shapes for the index:

- **map**  — keep today's ``dynamic_attrs MAP(VARCHAR, VARCHAR)`` column on the
  wide ``measurements`` table; queries do ``TRY_CAST(dynamic_attrs['k'][1] AS T)``.
  Inputs and outputs of a measurement stay on ONE row (no join), but values are
  stringified.
- **long** — a separate ``measurements_dynamic`` table, one row per
  ``(measurement, name)``, value in a typed lane (``value_double`` / ``value_text``).
  Native typed columns (fast filter/enum), BUT correlating an input with an
  output for Cpk needs a SELF-JOIN on ``measurement_id``.

The earlier typed-vs-JSON bench never measured the MAP path, and the long table's
join cost is unknown. This measures both on the three viewer patterns against a
``typed-wide`` gold standard (native columns, no map, no join), so the P2 shape
decision is made on numbers.

Usage::

    uv run --with duckdb==1.5.3 python scripts/bench_index_shape.py
    uv run --with duckdb==1.5.3 python scripts/bench_index_shape.py --rows 500000
"""

from __future__ import annotations

import argparse
import statistics
import time

import duckdb
import numpy as np
import pyarrow as pa

IN_CARDINALITY = 8


def _build(con: duckdb.DuckDBPyConnection, rows: int, in_cols: int, out_cols: int) -> None:
    """Build the three index shapes from one identical wide source."""
    rng = np.random.default_rng(1234)
    sweep = np.linspace(3.0, 5.5, IN_CARDINALITY)
    cols: dict[str, np.ndarray] = {"measurement_id": np.arange(rows, dtype=np.int64)}
    for i in range(in_cols):
        cols[f"in_c{i}"] = rng.choice(sweep, size=rows)
    for i in range(out_cols):
        cols[f"out_c{i}"] = rng.normal(3.3, 0.05, size=rows)
    con.register("_gen", pa.table(cols))
    con.execute("CREATE TABLE wide AS SELECT * FROM _gen")  # typed-wide gold standard
    con.unregister("_gen")

    # map shape: dynamic_attrs MAP(VARCHAR, VARCHAR), one row per measurement.
    keys = [f"'in_c{i}'" for i in range(in_cols)] + [f"'out_c{i}'" for i in range(out_cols)]
    vals = [f"CAST(in_c{i} AS VARCHAR)" for i in range(in_cols)] + [
        f"CAST(out_c{i} AS VARCHAR)" for i in range(out_cols)
    ]
    con.execute(
        f"CREATE TABLE map_idx AS SELECT measurement_id, "
        f"MAP([{', '.join(keys)}], [{', '.join(vals)}]) AS dynamic_attrs FROM wide"
    )

    # long shape: one row per (measurement, name) with a typed lane.
    in_un = " UNION ALL ".join(
        f"SELECT measurement_id, 'in' AS side, 'in_c{i}' AS name, in_c{i} AS value_double, "
        f"NULL::VARCHAR AS value_text FROM wide"
        for i in range(in_cols)
    )
    out_un = " UNION ALL ".join(
        f"SELECT measurement_id, 'out' AS side, 'out_c{i}' AS name, out_c{i} AS value_double, "
        f"NULL::VARCHAR AS value_text FROM wide"
        for i in range(out_cols)
    )
    con.execute(f"CREATE TABLE long_idx AS {in_un} UNION ALL {out_un}")
    con.execute("CREATE INDEX idx_long_name ON long_idx(name)")
    con.execute("CREATE INDEX idx_long_mid ON long_idx(measurement_id)")


def _median_ms(con: duckdb.DuckDBPyConnection, sql: str, rounds: int = 9) -> float:
    con.execute(sql).fetchall()
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
        "enum   (DISTINCT in_c0)": {
            "typed-wide": "SELECT DISTINCT in_c0 FROM wide",
            "map": "SELECT DISTINCT dynamic_attrs['in_c0'][1] FROM map_idx",
            "long": "SELECT DISTINCT value_double FROM long_idx WHERE name='in_c0'",
        },
        "filter (in_c0 = v)": {
            "typed-wide": f"SELECT count(*) FROM wide WHERE in_c0 = {val}",
            "map": f"SELECT count(*) FROM map_idx "
            f"WHERE TRY_CAST(dynamic_attrs['in_c0'][1] AS DOUBLE) = {val}",
            "long": f"SELECT count(*) FROM long_idx WHERE name='in_c0' AND value_double = {val}",
        },
        "cpk    (avg/stddev out_c0 BY in_c0)": {
            "typed-wide": "SELECT in_c0, avg(out_c0), stddev(out_c0) FROM wide GROUP BY in_c0",
            "map": "SELECT TRY_CAST(dynamic_attrs['in_c0'][1] AS DOUBLE) g, "
            "avg(TRY_CAST(dynamic_attrs['out_c0'][1] AS DOUBLE)), "
            "stddev(TRY_CAST(dynamic_attrs['out_c0'][1] AS DOUBLE)) FROM map_idx GROUP BY g",
            "long": "SELECT i.value_double g, avg(o.value_double), stddev(o.value_double) "
            "FROM long_idx i JOIN long_idx o USING (measurement_id) "
            "WHERE i.name='in_c0' AND o.name='out_c0' GROUP BY g",
        },
    }

    print(f"\nrows={rows:,}  in_cols={in_cols}  out_cols={out_cols}  rounds={rounds}")
    print(
        f"{'query':<38} {'typed-wide':>11} {'map':>10} {'long':>10}  {'map/tw':>7} {'long/tw':>8}"
    )
    print("-" * 92)
    for label, variants in cases.items():
        tw = _median_ms(con, variants["typed-wide"], rounds)
        mp = _median_ms(con, variants["map"], rounds)
        lg = _median_ms(con, variants["long"], rounds)
        print(
            f"{label:<38} {tw:>9.2f}ms {mp:>8.2f}ms {lg:>8.2f}ms  {mp / tw:>6.1f}x {lg / tw:>7.1f}x"
        )
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=200_000)
    ap.add_argument("--in-cols", type=int, default=8)
    ap.add_argument("--out-cols", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=9)
    args = ap.parse_args()
    _run(args.rows, args.in_cols, args.out_cols, args.rounds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
