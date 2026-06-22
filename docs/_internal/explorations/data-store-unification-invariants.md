# Data-store unification — invariants design (build against this, not by discovery)

> Why this exists: removing the coarse `threading.Lock` for parallelism + push
> silently drops correctness properties the lock provided. Every bug in the
> 2026-06-06 session was one of those surfacing by being broken. This document
> enumerates the invariants the locked system relies on, per store, so the
> rewrite restores them explicitly instead of discovering them at runtime.

## The core lesson

`DuckDBFlightServer._lock` (and the daemons' `write_lock`) was doing **two**
jobs; we only credited it with one:

1. **Write serialization** — the throughput bottleneck (what we set out to fix).
2. **Read-vs-write atomicity** — because `do_get` holds the *same* lock as the
   multi-statement materializer, a reader can never observe a partially-written
   logical unit. DuckDB auto-commits **every `execute()` separately**, so a
   "run" (run row + N steps + N measurements across 3 tables) lands as 4–6
   independent commits. The lock is the only thing making them atomic to readers.

Lock-free reads (cursor-per-thread) + push are correct **only if** every
multi-statement write is made atomic explicitly. That is the spine of this plan.

## Per-store invariant tables

### EVENTS — mostly safe lock-free; the risk is push-loss, not read-atomicity
| Property | Truth (file:line) | Restoration needed |
|---|---|---|
| Write unit | 1 `executemany` per batch, each event = 1 row (`_duckdb_daemon.py:93-99,293,333`) | none — atomic already |
| Reader sees subset of events | semantically fine — events are independent; watcher reads by monotonic `event_number` | none |
| `event_number` monotonic w/ commit order | `nextval('event_seq')` inside the INSERT; DuckDB serializes writes at the catalog layer (`_duckdb_daemon.py:77-99,162`) | none — DB-level guarantee survives lock removal |
| **Cross-process delivery to the runs materializer** | today: 500ms poll with `_delivered_ids` dedup + at-least-once re-fetch (`event_store.py:597-671`) | **CRITICAL**: push must NOT lose `RunEnded` (drop-on-full would orphan a run). Push needs replay-from-cursor on (re)subscribe + dedup, OR a no-drop queue for the materializer subscription. |

**Rule E1:** Events at-rest `do_get` may go lock-free/cursor-per-thread as-is.
**Rule E2:** The events→runs push subscription must be **lossless**: subscribe carries an `event_number` cursor; server replays `> cursor` from the warm index, then streams live (gap-free). Never silently drop the materializer subscriber.

### RUNS — the hard store: multi-row logical unit, lock-free reads break it
| Property | Truth (file:line) | Restoration needed |
|---|---|---|
| Write unit per run | 4–6 separate auto-committed INSERTs across `runs_materialized` / `steps_materialized` / `measurements_materialized` (+ stats/io/refs) (`_runs_duckdb_daemon.py:1115-1147,1494-1495`) | **wrap the whole per-run ingest in ONE `BEGIN…COMMIT`** so readers see all-or-nothing |
| Reader needs run+steps+measurements together | UNION views `runs/steps/measurements` = materialized TABLE ∪ inflight overlay (`:1153-1240`) | transaction (above) + ordered handoff (below) |
| Inflight→materialized handoff | pool evicted only after `RunMaterialized` emitted; pre_query re-snapshots on generation bump (`_accumulator_pool.py:197-232`, `_runs…:1378-1415`) | **evict only after the ingest transaction COMMITS** so there's never a gap where neither inflight nor materialized shows the full run |
| pre_query registers inflight on the query handle | today on shared `conn` under lock | with cursor-per-thread: register on the **calling cursor** (session-scoped); a generation-keyed shared snapshot cache builds the Arrow tables once per pool generation; `None` sentinel distinguishes "never registered" from empty-pool gen `-1` |

**Rule R1:** Per-run materialization is one transaction (`BEGIN; insert run+steps+measurements+stats+io+refs+_ingested; COMMIT`). Both ingest entry points (`_materialize_and_emit`, `_on_put`) and the background sweep use it. No nested transactions (helper at the call site, not inside `_index_unified_parquet`).
**Rule R2:** Pool eviction happens after COMMIT, not before emit.
**Rule R3:** pre_query registers inflight tables per-cursor with the shared-snapshot cache; first touch always registers.

### CHANNELS — index-once is safe; the trap is the unflushed buffer
| Property | Truth (file:line) | Restoration needed |
|---|---|---|
| Closed segments immutable | `_on_flush` appends to `_closed_paths`, never rewritten (`channels/store.py:178-185`) | ingest-once + checkpoint like events — safe |
| **`store.query` merges flushed segments + in-memory buffer** | `:597-616` reads buffer, `:618-633` globs files | **a warm index sees only flushed segments**; the daemon (which *owns* the ChannelStore + its writer buffers) must merge index ∪ live-buffer in its `do_get`, so clients keep read-after-write |
| Query clients bypass the daemon | ephemeral `ChannelStore(serve=False)` + local glob in `mcp/tools.py:1320,1374`, `ui/shared/services.py:1426`, `api/app.py`, `materialize.py` | reroute all *operator/consumer* query sites through the daemon; keep internal maintenance (materialize/retention) local |
| Date-partitioned files | `channels/{date}/{id}_{sid}.arrow` | index + query prune by date range (scale-to-full-disk) |
| Live push | queue fan-out, orthogonal to query (`channels/server.py:49-67`) | unchanged |

**Rule C1:** The channels daemon's `do_get` historical answer = warm-index query **UNION the live in-memory buffer of any open writer** (the daemon holds both), so a sample written but not yet flushed is still returned. This is the channels analogue of the runs inflight overlay.
**Rule C2:** Reroute consumer query sites to the daemon; index ingests closed segments once, checkpointed; queries prune by date.

### FILES — emit the index row only after durability
| Property | Truth (file:line) | Restoration needed |
|---|---|---|
| One-shot artifact write | **not atomic** — `serializer.write(value, dest)` direct (`files/store.py:147`) | make artifact write atomic (temp+rename) **or** never expose it until the sidecar lands |
| Sidecar write | atomic temp+rename one-shot (`:160-171`); **non-atomic** in streaming finalizer (`streaming.py:238-246`) | make the streaming sidecar atomic too |
| artifact↔sidecar pair | **not atomic** — ~22-line window; reader can see artifact w/o metadata | **index row emitted only AFTER the sidecar atomic-rename (one-shot) / after `FileEnded` (streaming)** |
| resolve_uri / list | O(days) date-walk + full `rglob` per call, no index (`store.py:277-305`, `ui/shared/services.py:1663-1721`) | warm index over sidecar metadata; queries prune by date |
| live-read | `StreamFrameIndex` event **does not exist** (docstring-only); consumer rides the 500ms event poll; HTTP `/files` claims Range but reads whole file (`api/app.py:318-341`) | add `StreamFrameIndex` event + emit per write; consumer rides events push (Phase 2); implement real HTTP Range |

**Rule F1:** Index visibility trails durability: emit the metadata `do_put` only after the artifact+sidecar are durable (one-shot: after rename; streaming: at `FileEnded`). A query must never return a URI whose bytes/metadata aren't complete.
**Rule F2:** Make both the artifact and the streaming sidecar atomic (temp+rename).
**Rule F3:** Add `StreamFrameIndex`; real HTTP Range; route resolve/list through the index.

## Implementation checklist (derived — each phase must satisfy its rules)

- **Phase 1 (server)** — additive SUB + `_publish` + `shutdown()` drain. DONE; no lock removed yet, so no invariant is at risk. (Verified: 3 in-process tests.)
- **Phase 2 (events push + events lock-free)** — E1, E2. The events→runs subscription is lossless (cursor replay + dedup); only then drop the events daemon lock.
- **Phase 3 (channels index)** — C1, C2. Daemon merges index ∪ live buffer; reroute clients; date-pruned ingest.
- **Phase 4 (files index + live-read)** — F1, F2, F3.
- **Runs lock-free reads** — R1, R2, R3. Land the per-run transaction + per-cursor inflight **in the same change** that makes runs reads lock-free. Never remove the runs read lock without R1 in the same commit.
- **Cross-cutting** — scale-to-full-disk: every index query prunes by date partition; every ingest is bounded-memory/checkpointed (never load the whole dataset on spawn).

## The one rule that would have prevented today

**Never remove a read lock in a commit that doesn't also make the corresponding
multi-statement write atomic.** Lock removal and transaction-wrapping are a
single inseparable change, per store.
