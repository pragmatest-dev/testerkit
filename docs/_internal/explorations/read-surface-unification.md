# Store + Query interface consistency

**Status:** PLAN — all decisions locked (2026-06-21). Executing on
`feat/0.2.0-interface-consistency` (off the merged `c5a901e`). Three *internal* consistency
passes — NOT adding classes, NOT collapsing layers; interface/naming alignment only, behavior
unchanged. See §7 for the execution plan + progress log.

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

The `list_for_*` / `find_for_*` methods are just **filter-by-parent-key shortcuts**
(`SELECT * WHERE run_id|session_id = X`), so this is naming alignment only — `tree_for_run`
(nested tree shape) and the aggregates (`pareto`/`cpk`/`yield` — compute, not filter) stay
distinct. **DRY item (found 2026-06-21):** `StepsQuery.list_for_run` re-implements the
`in_`/`out_` `dynamic_attrs` un-fuse that also lives in `RunStore.get_measurements` (the
role-redesign audit missed it — `steps_query.py` was out of scope). Extract one shared
decoder (`_decode_dynamic_attrs_map`) and use it in both.

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

1. **Lifecycle convention for stores** — **DECIDED (2026-06-21): (c) optional-close handle.**
   Researched prior art (boto3 / SQLAlchemy Engine / redis-py / PyMongo / httpx all converge on
   a long-lived construct-once-reuse object, close optional) + our own code. Clincher: `close()`
   on the read path (`RunsQuery`/`StepsQuery`/`MeasurementsQuery`/`RunStore`) is **already a
   no-op** — the daemon is a separate process, ref-tracked by PID-poll + a 5-min idle timeout;
   the `FlightClient` is process-pooled. We already have SQLAlchemy's topology (daemon = the
   long-lived self-cleaning engine; query objects = cheap handles). So:
   - **Blessed pattern = construct-and-reuse**, no `close()` needed (notebook-friendly —
     `with` can't span cells). `with`/`close()` stay **optional** opt-in for scripts/tests.
   - Add **`weakref.finalize` + `atexit`** to the classes holding a *real* in-process resource
     (`EventStore` watcher thread + write stream; `ChannelStore` `serve=True` streams) so the
     lazy way is fool-proof everywhere, not just the read path. (Likely eases #11.)
   - `get_shared()` stays **Event-specific** (it shares the watcher thread across UI renders —
     not a general lifecycle pattern). NOT broadened to other stores.
   - `open()`/`close()→int` on `ChannelStore` align to the optional-close contract; the lazy
     `open()` (first-write) behavior is preserved (tuned machine).
2. **Naming convention** — **DECIDED yes (2026-06-21)**: `list_for_*` (verb always `list`),
   collapse `find_for_session → list_for_session`, `failure_pareto → pareto`, one
   `distinct_values`. These are filter-by-parent-key shortcuts; naming alignment only.
3. **`describe_columns -> ColumnSchema`** on `RunsQuery`/`StepsQuery` too — **DECIDED yes (2026-06-21)**: align Runs/Steps up to the typed `ColumnSchema` that Measurements already returns.
4. **Pre-1.0 rename freedom** — **DECIDED: do now** on `feat/0.2.0-interface-consistency`
   (pure pre-1.0 renames; cheap now, breaking later).

## 6. Scope guard

Interface alignment only — no storage, schema, or write-path *behavior* changes; no new
classes; no layer collapse. Pure consistency, nothing blocked.

## 7. Execution plan (risk-ordered) + progress log

Branch `feat/0.2.0-interface-consistency`. Each phase: full suite + `uv run pyright` (0) gate;
behavior unchanged; design-review the branch before merge to `feat/0.2.0-data-improvements`.

**Phase C — module rename `logger.py` → `run_scope.py`** *(first; safest, no decision)*
- `git mv src/litmus/execution/logger.py src/litmus/execution/run_scope.py`; update the ~8
  import sites (`execution/__init__.py`, `instrument_events.py`, `verify.py`, `harness.py`,
  `_state.py`, `pytest_plugin/autouse.py` + `__init__.py`) + the `_row_helpers.py` comment.
- Pure rename, no behavior change.

**Phase A1 — `data_dir` internal** *(trivial; decided)*
- `FileStore`: public `data_dir` → private keyword-only `_data_dir` (only `benchmark/` passes
  it; production resolves from config). `RunStore`/`EventStore`/`*Query` already comply.

**Phase B — query interface consistency**
- Rename to locked vocabulary: `find_for_session → list_for_session`; `failure_pareto →
  pareto`; one `distinct_values`. Update all call sites (cli/mcp/ui/api/tests).
- `describe_columns → ColumnSchema` on `RunsQuery`/`StepsQuery` (align up to Measurements).
- Type the aggregate returns (`yield_summary`/`pareto`/`cpk`/`trend`/`retest`/`time_loss` →
  Row models in `measurement_facets.py`). ⚠️ **consumer blast radius** — cli/metrics, mcp, ui
  index those dicts; move each to attribute access.
- DRY: extract one `_decode_dynamic_attrs_map` shared by `RunStore.get_measurements` +
  `StepsQuery.list_for_run` (the `in_`/`out_` un-fuse is duplicated).

**Phase A2 — store lifecycle = optional-close handle** *(per §5.1 decision)*
- Uniform contract: construct-and-reuse blessed; `with`/`close()` optional; a shared `Store`
  Protocol/base for the common surface; read-method naming convention.
- Add `weakref.finalize` + `atexit` cleanup to `EventStore` (watcher thread + write stream)
  and `ChannelStore` (`serve=True` streams) so forgetting `close()` never leaks.
- `get_shared()` stays Event-specific. Doc the construct-and-reuse pattern.
- ⚠️ **`ChannelStore` is the tuned machine** — enumerate every behavior as a checklist FIRST;
  preserve lazy first-write `open()`; STOP-and-ask before any behavior-affecting change. Do
  this sub-phase LAST and carefully.

**Sequence:** C → A1 → B → A2 (ChannelStore last). Guardrail (§4): DuckDB SQL stays behind
the engine-neutral `*Query` surface.

### Progress log
- **2026-06-21** — Decisions locked (§5: lifecycle=(c) optional-close handle; naming=`list_for_*`;
  `describe_columns→ColumnSchema` all three; do-now). Branch created off `c5a901e`. Plan
  written.
- **2026-06-21** — Phase C done (`221293b`): `logger.py`→`run_scope.py`, ~18 sites, suite 2156.
- **2026-06-21** — Phase A1 done: FileStore `data_dir`→private `_data_dir`, 26 call sites,
  suite 2156, pyright 0.
- **2026-06-21** — Phase B1 done: query renames (`find_for_session→list_for_session`,
  `failure_pareto→pareto`, `distinct_filter_values→distinct_values`) + extracted shared
  `_decode_dynamic_attrs_map`. The two un-fuse sites differed — `RunStore` recovered floats
  only, `StepsQuery` recovered bools+floats. Unified on bool+float (user-approved): `RunStore`
  now recovers native bools too (latent under-coercion fixed). suite 2156, pyright 0.
- **2026-06-21** — Phase B2 done (Phase B complete): typed returns — `describe_columns→ColumnSchema`
  on Runs/Steps; 6 MeasurementsQuery aggregates (`yield_summary`/`pareto`/`cpk`/`trend`/`retest`/
  `time_loss`) → Row models (fields mirror SQL keys); consumers (cli/api/mcp/ui/tests) → attribute
  access + `model_dump()` at JSON boundaries. Reference docs regenerated. suite 2156, pyright 0.
  OPEN (flagged): `RunsQuery.pareto`/`StepsQuery.pareto`/`usage_stats`/`count_by_outcome`
  remain `list[dict]`.
- **2026-06-21** — B correction (pareto naming): `pareto` over-claimed — all three are *failure*
  paretos (RunsQuery: "Pareto of failing runs"; StepsQuery: failed_count by step_path;
  MeasurementsQuery: "top failure modes by count"). B1 had unified on the less-accurate name.
  Fix: rename `pareto → failure_pareto` on all three + `ParetoRow → FailureParetoRow`. Runs/Steps
  `failure_pareto` stay `list[dict]` for now — their bucket shape (`bucket/failed_count/total/
  fail_rate`) differs from Measurements' multi-dim shape; typing + shape-unification rides with
  the generic-pareto work below (resolves the B3 flag as deferred).

### Northstar — generic `pareto` (0.3.0)
Reserve the bare name `pareto` for a *generic* primitive; do NOT squat it with failure-only
logic. Pareto = the 80/20 "what's worth investigating": rank top-N contributors **by a chosen
measure** (failures, time loss, retest churn, cycle time …), descending, with cumulative % and
an **"Other"** bucket summarizing the tail. Shape like `parametric` — **the caller specifies
what to count and how to group; the result is shaped to the ask** (long-format rows), not a
fixed failure schema. It would unify today's `pareto` (failures) / `time_loss` / `retest` as
measures behind one call.

**`ParetoRow` does NOT evolve into this — design a fresh row in 0.3.0.** Today's `ParetoRow`
(`part`/`station`/`step_name`/`measurement_name` + `total_count`/`fail_count`/`fail_rate`) bakes
in (a) the failure measure and (b) a fixed multi-dimensional grouping. The generic version wants
a measure-agnostic shape: a single chosen grouping (`bucket` + `group_by`), a `value`/`total`/
`share`, plus `cumulative` and an `is_other` tail flag. So 0.3.0 designs that row fresh
(alongside the `by=`/grouping/Other semantics — "shaped to the ask"), NOT an evolution of
`ParetoRow`. **Low severity** (decided 2026-06-21, not reshaping now): `ParetoRow` is a *pre-1.0
query return type*, so the 0.3.0 change is rename-the-row + update cli/mcp/ui consumers — no
storage/wire lock-in (the wire returns `model_dump()` dicts; the external name `pareto` is
already stable). Note: `RunsQuery`/`StepsQuery.pareto`'s existing `bucket`/value shape is already
closer to the generic target than the multi-dim `ParetoRow`.

- **2026-06-21** — Phase B3 done: `pareto → failure_pareto` (method, 3 classes, 9 call sites) +
  `ParetoRow → FailureParetoRow`. Ref docs regenerated. suite 2156, pyright 0. OPEN (flagged):
  the *external* metric NAME `"pareto"` (MCP/CLI/HTTP dispatch `case "pareto":`) was left as-is —
  only the internal method renamed.
  **DECISION (2026-06-21): keep the external name `pareto`** (concise; `failure-pareto` is too
  verbose for the CLI/API/MCP surface). It returns failures *by default* today — backed by the
  internal `failure_pareto` — and gains a `by`/measure option later (the 0.3.0 generic version,
  see northstar above). So external `pareto` is the forward-looking name; the internal
  `failure_pareto` is its current failure-measure implementation. The attempted external rename
  was reverted.
- **2026-06-21** — Phase A2a done: optional-close contract (`__enter__`/`__exit__` on
  RunStore/EventStore; close() untouched) + lean `Store` Protocol (`data/_store.py`, lifecycle
  only) + queries.py docstring leads with construct-and-reuse. suite 2156, pyright 0.
- **2026-06-21** — Phase A2b done: EventStore finalizer net — `weakref.finalize` bound to the
  resource objects (`_put_stream`/`_watcher_stop`/`_event_logs`), never `self`; fires on GC OR
  interpreter exit; `close()` detaches it. FIXED a latent ref-cycle (the `reacquire` lambdas
  captured `self` → would've blocked GC → finalizer never fires; now capture the `_events_dir`
  Path). 4 new tests incl. a GC-fires assertion; 10/10 resilience-test hammer; suite 2160, pyright 0.
- **2026-06-21** — Phase A2c done (A2 COMPLETE): ChannelStore — `__enter__`/`__exit__` (no eager
  open) + finalizer net. Insight: the PushRelay **thread** is the only resource that won't
  self-clean on GC; the finalizer just stops it (writers/client cascade-collect; daemon prunes
  the dead ref). Required the same cycle fix as EventStore — the relay's `flush` back-ref was a
  bound method pinning the store; now weakref'd so the store is collectible. All ChannelStore
  behavior preserved (lazy first-write open, flush thresholds, `close()→None`). New GC-fires
  test; channel tests 3×46; suite 2163, pyright 0. BENCHMARK (clean back-to-back, A2c stashed vs
  restored — the first baseline was contaminated by concurrent agents): **no regression** —
  throughput metrics flat within run-to-run noise (block/query/stream@1k, capture, ingest all
  ±~10%, several faster with the change); only the sub-ms `@1` micro-cases swing (jitter).
- **2026-06-21** — B3 REVERTED → unify on `pareto` everywhere. With the decision to keep the
  external name `pareto` (failures-by-default, gains a `by`/measure option later), B3's internal
  `failure_pareto` left a Python-vs-wire split. Renamed the identifier back: `failure_pareto →
  pareto` (method on all 3 query classes + every call site in mcp/api/cli/ui/tests) and
  `FailureParetoRow → ParetoRow`. Wire names were already `pareto` (untouched). One forward-looking
  name across Python + wire; not an over-claim ("pareto, defaulting to failures, generalizes
  later"). Ref docs regenerated; suite 2163, pyright 0, ruff clean.
