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

A second section (``_run_repoint``) measures the **analytical repoint as it
ships**: the daemon keeps the ``dynamic_attrs`` MAP on the core ``measurements``
view, and the analytical query layer now resolves a dynamic ``in_*``/``out_*``
column by LEFT JOINing ``measurements_dynamic`` on the vector key + side/name
(the core-anchored join), instead of ``TRY_CAST(dynamic_attrs['k'][1])`` over the
MAP. It compares MAP-on-core vs the EAV correlated-subquery form (what an inline
``_col_expr`` would emit) vs the EAV core-anchored-join form (what ships). The
join form wins the single-dynamic-column patterns (enum, filter) by multiples;
the two-dynamic-column bar (avg/stddev of one dynamic output grouped by a dynamic
input) lands at parity — the correlated-subquery form is rejected (it does not
decorrelate). The real ``cpk()``/``yield``/``pareto`` methods aggregate the fixed
``measurement_value`` and never touch the EAV at all.

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


def _build_repoint(con: duckdb.DuckDBPyConnection, rows: int, in_cols: int, out_cols: int) -> None:
    """Model the REAL daemon shapes the analytical queries actually run against.

    - ``measurements`` — the core view: one row per measurement, carries
      ``measurement_value`` (fixed) and a ``dynamic_attrs`` MAP (today's read path).
      Keyed by the natural vector identity ``(run_id, step_index, vector_index,
      vector_retry)`` — exactly what ``measurements_dynamic`` joins on.
    - ``measurements_dynamic`` — the EAV long table (vector grain), the repoint
      target. Typed lanes; ``name`` index only (matches the live daemon: the
      high-cardinality vector key is NOT indexed — it OOMs at scale and hash
      joins don't use it).

    So this bench measures the analytical repoint as it ships: MAP-on-core vs
    EAV-via-correlated-subquery vs EAV-via-join, on the real vector key.
    """
    rng = np.random.default_rng(99)
    sweep = np.linspace(3.0, 5.5, IN_CARDINALITY)
    # Vector grain: each core measurement row carries a UNIQUE vector identity, so
    # the EAV join is 1:1 (measurements_dynamic is DISTINCT per (vector,side,name)).
    run_ids = np.array([f"run-{i % 200:04d}" for i in range(rows)])
    cols: dict[str, np.ndarray] = {
        "run_id": run_ids,
        "step_index": (np.arange(rows) % 5).astype(np.int64),
        "vector_index": np.arange(rows, dtype=np.int64),
        "vector_retry": np.zeros(rows, dtype=np.int64),
        "measurement_value": rng.normal(3.3, 0.05, size=rows),
        "run_outcome": np.where(rng.random(rows) < 0.9, "passed", "failed"),
    }
    in_vals = {f"in_c{i}": rng.choice(sweep, size=rows) for i in range(in_cols)}
    out_vals = {f"out_c{i}": rng.normal(3.3, 0.05, size=rows) for i in range(out_cols)}
    con.register("_gen", pa.table({**cols, **in_vals, **out_vals}))

    keys = [f"'in_c{i}'" for i in range(in_cols)] + [f"'out_c{i}'" for i in range(out_cols)]
    vals = [f"CAST(in_c{i} AS VARCHAR)" for i in range(in_cols)] + [
        f"CAST(out_c{i} AS VARCHAR)" for i in range(out_cols)
    ]
    con.execute(
        "CREATE TABLE measurements AS SELECT run_id, step_index, vector_index, vector_retry, "
        "measurement_value, run_outcome, "
        f"MAP([{', '.join(keys)}], [{', '.join(vals)}]) AS dynamic_attrs FROM _gen"
    )

    in_un = " UNION ALL ".join(
        f"SELECT run_id, step_index, vector_index, vector_retry, 'in' AS side, "
        f"'c{i}' AS name, in_c{i} AS value_double, NULL::VARCHAR AS value_text FROM _gen"
        for i in range(in_cols)
    )
    out_un = " UNION ALL ".join(
        f"SELECT run_id, step_index, vector_index, vector_retry, 'out' AS side, "
        f"'c{i}' AS name, out_c{i} AS value_double, NULL::VARCHAR AS value_text FROM _gen"
        for i in range(out_cols)
    )
    con.execute(f"CREATE TABLE measurements_dynamic AS {in_un} UNION ALL {out_un}")
    con.unregister("_gen")
    con.execute("CREATE INDEX idx_md_name ON measurements_dynamic(name)")


_VKEY = (
    "md.run_id = m.run_id AND md.step_index = m.step_index "
    "AND md.vector_index = m.vector_index "
    "AND md.vector_retry IS NOT DISTINCT FROM m.vector_retry"
)


def _corr(side: str, name: str) -> str:
    """EAV correlated-subquery lane select — the form ``_col_expr`` emits."""
    return (
        f"(SELECT md.value_double FROM measurements_dynamic md WHERE {_VKEY} "
        f"AND md.side='{side}' AND md.name='{name}')"
    )


def _run_repoint(rows: int, in_cols: int, out_cols: int, rounds: int) -> None:
    """Bench the analytical repoint: MAP-on-core vs EAV correlated-subquery / join."""
    con = duckdb.connect(":memory:")
    _build_repoint(con, rows, in_cols, out_cols)
    val = float(np.linspace(3.0, 5.5, IN_CARDINALITY)[3])
    in0_corr = _corr("in", "c0")
    out0_corr = _corr("out", "c0")

    cases = {
        # enum: parametric/explore X-axis distinct values for a dynamic input.
        "enum   (DISTINCT in_c0)": {
            "map": "SELECT DISTINCT TRY_CAST(dynamic_attrs['in_c0'][1] AS DOUBLE) "
            "FROM measurements",
            "eav-sub": "SELECT DISTINCT value_double FROM measurements_dynamic "
            "WHERE side='in' AND name='c0'",
        },
        # filter: parametric WHERE in_c0 = v (dynamic column predicate).
        "filter (in_c0 = v)": {
            "map": "SELECT count(*) FROM measurements "
            f"WHERE TRY_CAST(dynamic_attrs['in_c0'][1] AS DOUBLE) = {val}",
            "eav-sub": f"SELECT count(*) FROM measurements m WHERE {in0_corr} = {val}",
            "eav-join": "SELECT count(*) FROM measurements m JOIN measurements_dynamic md "
            f"ON {_VKEY} WHERE md.side='in' AND md.name='c0' AND md.value_double = {val}",
        },
        # cpk: parametric bar — AVG/STDDEV out_c0 grouped by dynamic in_c0.
        "cpk    (avg/stddev out_c0 BY in_c0)": {
            "map": "SELECT TRY_CAST(dynamic_attrs['in_c0'][1] AS DOUBLE) g, "
            "avg(TRY_CAST(dynamic_attrs['out_c0'][1] AS DOUBLE)), "
            "stddev(TRY_CAST(dynamic_attrs['out_c0'][1] AS DOUBLE)) "
            "FROM measurements GROUP BY g",
            "eav-sub": f"SELECT {in0_corr} g, avg({out0_corr}), stddev({out0_corr}) "
            "FROM measurements m GROUP BY g",
            "eav-join": "SELECT i.value_double g, avg(o.value_double), stddev(o.value_double) "
            "FROM measurements m "
            "JOIN measurements_dynamic i ON "
            "i.run_id=m.run_id AND i.step_index=m.step_index "
            "AND i.vector_index=m.vector_index "
            "AND i.vector_retry IS NOT DISTINCT FROM m.vector_retry "
            "JOIN measurements_dynamic o ON "
            "o.run_id=m.run_id AND o.step_index=m.step_index "
            "AND o.vector_index=m.vector_index "
            "AND o.vector_retry IS NOT DISTINCT FROM m.vector_retry "
            "WHERE i.side='in' AND i.name='c0' AND o.side='out' AND o.name='c0' GROUP BY g",
        },
    }

    print(f"\n[analytical repoint] rows={rows:,}  in_cols={in_cols}  out_cols={out_cols}")
    print(f"{'query':<38} {'MAP':>10} {'EAV-sub':>10} {'EAV-join':>10}  {'MAP/best':>9}")
    print("-" * 84)
    for label, variants in cases.items():
        mp = _median_ms(con, variants["map"], rounds)
        sub = _median_ms(con, variants["eav-sub"], rounds) if "eav-sub" in variants else None
        jn = _median_ms(con, variants["eav-join"], rounds) if "eav-join" in variants else None
        best = min(v for v in (sub, jn) if v is not None)
        sub_s = f"{sub:>8.2f}ms" if sub is not None else f"{'—':>10}"
        jn_s = f"{jn:>8.2f}ms" if jn is not None else f"{'—':>10}"
        print(f"{label:<38} {mp:>8.2f}ms {sub_s} {jn_s}  {mp / best:>7.1f}x")
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=200_000)
    ap.add_argument("--in-cols", type=int, default=8)
    ap.add_argument("--out-cols", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=9)
    args = ap.parse_args()
    _run(args.rows, args.in_cols, args.out_cols, args.rounds)
    _run_repoint(args.rows, args.in_cols, args.out_cols, args.rounds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
