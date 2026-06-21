# Store + Query interface consistency

**Status:** PROPOSAL, 2026-06-21 (reframed). Two *internal* consistency passes — NOT adding
classes, NOT collapsing layers. Needs a decision before any build. Nothing here is built.

---

## 1. The model (settled)

Two distinct layers, each correct as-is; they are **not** meant to be 1:1:

- **`*Query` = a specific entity / analytical band.** `RunsQuery`, `StepsQuery`,
  `MeasurementsQuery` help you ask *questions within a grain* (aggregations, cross-row reads
  over the DuckDB daemon). The existing three are right. You do **not** add a `*Query` per
  storage type.
- **`*Store` = all the stored data of one kind.** `RunStore`, `EventStore`, `ChannelStore`,
  `FileStore` are the persistence layer (write + read + lifecycle for that data). The four
  are right.

Consequences (corrections to earlier framing):
- **No `ChannelQuery`/`FileQuery`/`EventQuery`** — those are storage kinds, not analytical
  entity-bands. A query there was a category error.
- **`RunStore` vs `RunsQuery` is NOT a duplication to collapse** — they're correctly
  *different layers* (storage access vs analytical band). The overlap on `list`/`get` is
  fine; they serve different purposes.

So the only real work is two consistency passes, each *within* a layer.

## 2. Pass A — Store interface consistency

The four stores grew separately; their interfaces diverge on construction + lifecycle (not on
*what* they store, which legitimately differs):

| | ctor arg | singleton | open | close |
|---|---|---|---|---|
| `RunStore` | `_data_dir` | — | — | `close() -> None` |
| `EventStore` | `_data_dir` | `get_shared()` | — | `close() -> None` |
| `ChannelStore` | (complex multi-arg) | — | `open()` | `close() -> int` |
| `FileStore` | `data_dir` | — | — | (none) |

Inconsistencies: ctor arg name (`_data_dir` vs `data_dir` vs multi-arg); `get_shared` only on
Event; `open()` only on Channel; `close()` returns `int` on Channel, `None`/absent elsewhere;
no shared base/Protocol. Read-method naming also differs per store (`get_run`/`list_runs` vs
`events`/`sessions` vs `list_channel_info`/`query` vs `read`/`read_range`).

**Target:** a consistent store contract — uniform ctor, uniform lifecycle (decide one:
context-manager vs explicit `close`; whether `get_shared` is the norm or the exception), a
shared `Store` Protocol/base for the common shape, and a naming convention for read methods.
Behavior unchanged — interface alignment only.

**DECIDED — `data_dir` is internal (2026-06-21).** Infrastructure paths resolve from
`ProjectConfig`; the public API must not expose `data_dir` for callers to rely on (matches
the CLAUDE.md no-data-dir-leak rule). Convention: resolve from config internally; the only
param is a **private keyword-only `_data_dir`** override (tests/benchmarks). Status per store:
- `RunStore`, `EventStore`, all `*Query` — already comply (`_data_dir`). ✓
- `FileStore` — the offender: public optional `data_dir` → rename to private `_data_dir`
  (only `benchmark/` passes it; production already resolves). Clean fix.
- `ChannelStore` — `data_dir` is a *required positional* (a producer-process construction
  detail passed by the harness/benchmarks, never by users — already "internal" in the
  reliance sense). Aligning it to resolve-from-config + `_data_dir` override is a deeper
  change on the tuned machine; lower urgency, handle carefully if at all.

## 3. Pass B — Query interface consistency / purpose

The three `*Query` classes share lifecycle (`__init__(*, _data_dir=)`, `close`, `__enter__` —
already consistent ✓) but drift on naming + return shape:

- "list" naming: `RunsQuery.list_recent` vs `StepsQuery.list_for_run` / `list_for_session` vs
  `RunsQuery.find_for_session` — "list" vs "find", `_recent` vs `_for_X`.
- `MeasurementsQuery.pareto` vs `RunsQuery`/`StepsQuery`.`failure_pareto` — same operation,
  two names.
- `RunsQuery.distinct_filter_values` vs `MeasurementsQuery.distinct_values`.
- `describe_columns` → `list[dict]` on Runs/Steps but typed **`ColumnSchema`** on Measurements
  (the typed model, added in the role redesign, is the better target — align the other two up
  to it).
- **Untyped aggregate returns** (from the role-redesign design review, deferred here): the
  `MeasurementsQuery` aggregates `yield_summary`/`pareto`/`cpk`/`trend`/`retest`/`time_loss`
  return `list[dict[str, Any]]`, while `parametric`/`histogram` return typed rows
  (`ParametricRow`/`HistogramRow`). Inconsistent + no stable field contract. Target: model them
  (`YieldRow`/`ParetoRow`/`CpkRow`/…) in `measurement_facets.py`. NOTE consumer blast radius —
  cli/metrics, mcp, ui index those dicts (`row["fail_count"]` etc.); typing them ripples to
  every consumer, which is exactly why this belongs in the consistency pass, not the
  role-redesign branch.

**Target:** a consistent query vocabulary — one verb for "list by run/session" (`list_for_*`),
one name for the pareto/distinct/describe operations, and `describe_columns -> ColumnSchema`
everywhere. Purpose stays "analytical reads over the daemon for entity X"; only the surface
aligns. Behavior unchanged.

## 3c. Pass C — module naming (`logger.py` → `run_scope.py`)

`src/litmus/execution/logger.py` is **misnamed**. Its contents are `RunScope` (run lifecycle +
event emission + measurement recording + step management — lines ~376-1186), `RunContext`
(run-level `custom_metadata`), and measurement/limit/traceability helpers. None of it is a
"logger" in the `logging` sense (that's the unrelated per-module `_log = logging.getLogger`).
The class was historically `TestRunLogger`, **renamed to `RunScope`** (with the logger-fixture
→ `_run_scope` change), but the file kept the old name.

**Target:** rename `logger.py` → `run_scope.py` (or `scope.py`) — module name matches the
`RunScope` it centers on. ~8 import sites to update (`execution/__init__.py`,
`instrument_events.py`, `verify.py`, `harness.py`, `_state.py`, `pytest_plugin/autouse.py` +
`__init__.py`, `_row_helpers.py` comment). Pure rename, no behavior change.

This also dissolves design-review finding 5.1 ("move `RunContext` out of `logger.py`"): once the
file is `run_scope.py`, `RunContext` sitting beside `RunScope` is correct — both are run-scope
concepts. So the real fix is the file rename, not moving the class.

## 4. Backend-portability guardrail (unchanged by this)

The `*Query` classes embed raw DuckDB-dialect SQL (`_YIELD_SQL`/`_PARETO_SQL`/…); mostly
Postgres-compatible (`DISTINCT ON`/`DATE_TRUNC`/`FILTER (WHERE)`/`STDDEV_SAMP`/`::casts`), a
couple need a dialect swap (`QUANTILE_CONT`, `EPOCH()`). The engine-specific machinery
(`MAP`/`UNNEST`/`read_parquet`) is in the daemon projection layer. A backend swap =
re-implement the projection + substitute those few functions; the EAV schema and the `*Query`
*public API* (and all consumers) stay unchanged (see
[`query-by-role-name.md`](query-by-role-name.md) §9). **Guardrail:** keep DuckDB SQL contained
behind the engine-neutral `*Query` public surface — these consistency passes are interface
alignment and must not leak dialect/types through the API.

## 5. Open decisions (yours)

1. **Lifecycle convention for stores** — context-manager (like the `*Query` classes) or
   explicit `open`/`close`? Is `get_shared` (process-shared singleton) the norm or an
   Event-specific exception? This is the load-bearing store decision.
2. **Naming convention** — `list_for_*` for both layers? `pareto` vs `failure_pareto`? a
   single `distinct_values` name?
3. **`describe_columns -> ColumnSchema`** on `RunsQuery`/`StepsQuery` too (align up)?
4. **Pre-1.0 rename freedom** — both passes are pure renames/signature alignment with no
   users, so they're cheap now and breaking later. Do both now, or park?

## 6. Scope guard

Interface alignment only — no storage, schema, or write-path *behavior* changes; no new
classes; no layer collapse. Pure consistency, nothing blocked.
