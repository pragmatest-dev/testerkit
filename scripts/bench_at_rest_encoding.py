"""At-rest encoding bench — VARIANT vs nested LIST<STRUCT> vs JSON.

The measurement-storage redesign stores a measurement's output bag (and
inputs/custom) as ONE semi-structured column at rest, then projects it into a
LONG/EAV index for query. Three candidate at-rest encodings:

- **json**    — ``to_json`` text. Universal, but stringifies and parses.
- **variant** — DuckDB-native typed binary. Lossless incl. int-vs-float.
- **liststruct** — nested ``LIST<STRUCT<name, kind, value lanes>>``. Typed,
  ancient parquet feature, and the EAV lanes live as struct fields.

What decides it for us is not just size — it's **rebuild-to-long**, the core
projection op (and the cold-rebuild path). LIST<STRUCT> unnests to long
natively; VARIANT has no dynamic-key enumeration (no ``variant_keys``, can't
``UNNEST`` a variant) so its only rebuild route is variant→JSON→MAP→unnest,
text-mediated. This measures on-disk bytes AND rebuild cost for each, and
checks the round-trip is lossless (including ``int`` surviving as int, which
the current wide schema collapses to float64).

Usage::

    uv run --with duckdb==1.5.3 python scripts/bench_at_rest_encoding.py
    uv run --with duckdb==1.5.3 python scripts/bench_at_rest_encoding.py --entities 100000 --outs 12
"""

from __future__ import annotations

import argparse
import os
import statistics
import tempfile
import time

import duckdb

# The EAV value lanes, one per scalar kind we support (observation_kind()):
# scalar:int -> vi, scalar:float -> vd, scalar:bool -> vb, scalar:str/uri -> vt.
# A row carries exactly one populated lane; the rest are NULL.
_KINDS = ("scalar:int", "scalar:float", "scalar:bool", "scalar:str")


def _build_truth(con: duckdb.DuckDBPyConnection, entities: int, outs: int) -> None:
    """Build the LONG truth table: one row per (entity, output name).

    Each output name cycles through the scalar kinds so the corpus is genuinely
    mixed-type (the collision case). Exactly one value lane is populated per row.
    """
    con.execute(
        f"""
        CREATE TABLE truth AS
        SELECT
            e AS ent,
            'out' || k AS name,
            list_element({list(_KINDS)}, (k % {len(_KINDS)}) + 1) AS kind,
            CASE WHEN k % {len(_KINDS)} = 0 THEN (e * 7 + k)::BIGINT END AS vi,
            CASE WHEN k % {len(_KINDS)} = 1 THEN 3.3 + (k % 50) / 100.0 END AS vd,
            CASE WHEN k % {len(_KINDS)} = 2 THEN (e % 2 = 0) END AS vb,
            CASE WHEN k % {len(_KINDS)} = 3 THEN 'lbl' || (k % 8) END AS vt
        FROM range({entities}) s(e), range({outs}) t(k)
        """
    )


# value-as-JSON expression, used to build the variant/json encodings from truth.
_VAL_JSON = (
    "CASE kind WHEN 'scalar:int' THEN to_json(vi) WHEN 'scalar:float' THEN to_json(vd) "
    "WHEN 'scalar:bool' THEN to_json(vb) ELSE to_json(vt) END"
)


def _build_encodings(con: duckdb.DuckDBPyConnection) -> None:
    """Derive the three at-rest encodings from the truth table."""
    # liststruct: nested typed lanes, one struct per output
    con.execute(
        """
        CREATE TABLE enc_liststruct AS
        SELECT ent, list(struct_pack(name := name, kind := kind,
                                     vi := vi, vd := vd, vb := vb, vt := vt)) AS outs
        FROM truth GROUP BY ent
        """
    )
    # json text: name -> value-as-json object
    con.execute(
        f"CREATE TABLE enc_json AS SELECT ent, json_group_object(name, {_VAL_JSON}) AS outs "
        f"FROM truth GROUP BY ent"
    )
    # variant: the json object cast to native VARIANT (typed binary)
    con.execute(
        f"CREATE TABLE enc_variant AS "
        f"SELECT ent, json_group_object(name, {_VAL_JSON})::VARIANT AS outs "
        f"FROM truth GROUP BY ent"
    )


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    row = con.execute(sql).fetchone()
    return row[0] if row else None


def _median_ms(con: duckdb.DuckDBPyConnection, sql: str, rounds: int = 7) -> float:
    con.execute(sql).fetchall()
    times = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        con.execute(sql).fetchall()
        times.append(time.perf_counter() - t0)
    return statistics.median(times) * 1000.0


def _run(entities: int, outs: int, rounds: int) -> None:
    con = duckdb.connect(":memory:")
    _build_truth(con, entities, outs)
    _build_encodings(con)
    n_rows = _scalar(con, "SELECT count(*) FROM truth")
    print(f"\nentities={entities:,}  outs/entity={outs}  long rows={n_rows:,}  rounds={rounds}")

    # On-disk parquet (zstd, as the runs store writes).
    with tempfile.TemporaryDirectory() as d:
        sizes = {}
        for enc in ("liststruct", "variant", "json"):
            p = os.path.join(d, f"{enc}.parquet")
            con.execute(f"COPY enc_{enc} TO '{p}' (FORMAT parquet, COMPRESSION zstd)")
            sizes[enc] = os.path.getsize(p)
        base = sizes["liststruct"]
        print("\non-disk parquet (zstd):")
        for enc in ("liststruct", "variant", "json"):
            print(f"   {enc:<11} {sizes[enc]:>12,} B  ({sizes[enc] / base:.2f}x liststruct)")

    # Rebuild-to-long: the core projection op. liststruct = native UNNEST;
    # variant/json = the only available route (->JSON->MAP->unnest entries).
    rebuild = {
        "liststruct (UNNEST)": (
            "SELECT ent, u.name, u.kind, u.vi, u.vd, u.vb, u.vt "
            "FROM enc_liststruct, UNNEST(outs) t(u)"
        ),
        "variant (->json->map)": (
            "SELECT ent, e.key AS name, json_type(e.value) AS jt, e.value "
            "FROM enc_variant, UNNEST(map_entries(outs::JSON::MAP(VARCHAR,JSON))) t(e)"
        ),
        "json (->map)": (
            "SELECT ent, e.key AS name, json_type(e.value) AS jt, e.value "
            "FROM enc_json, UNNEST(map_entries(outs::MAP(VARCHAR,JSON))) t(e)"
        ),
    }
    print("\nrebuild-to-long:")
    base_ms = _median_ms(con, rebuild["liststruct (UNNEST)"], rounds)
    for label, sql in rebuild.items():
        ms = _median_ms(con, sql, rounds)
        print(f"   {label:<24} {ms:>8.2f} ms  ({ms / base_ms:.1f}x liststruct)")

    # Lossless check: int must survive as int (current wide schema collapses to float64).
    ls_int = _scalar(
        con,
        "SELECT typeof(u.vi) FROM enc_liststruct, UNNEST(outs) t(u) "
        "WHERE u.kind='scalar:int' LIMIT 1",
    )
    var_int = _scalar(
        con,
        "SELECT json_type(e.value) FROM enc_variant, "
        "UNNEST(map_entries(outs::JSON::MAP(VARCHAR,JSON))) t(e) "
        "WHERE e.key = (SELECT name FROM truth WHERE kind='scalar:int' LIMIT 1) LIMIT 1",
    )
    print("\nlossless int check (current wide schema collapses int->float64):")
    print(f"   liststruct vi lane typeof : {ls_int}")
    print(f"   variant    int json_type  : {var_int}")
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entities", type=int, default=50_000)
    ap.add_argument("--outs", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=7)
    args = ap.parse_args()
    _run(args.entities, args.outs, args.rounds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
