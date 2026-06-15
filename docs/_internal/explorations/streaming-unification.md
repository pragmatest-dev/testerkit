# Streaming unification — promote DuckDBFlightServer for all four stores

> **Status:** IN PROGRESS (2026-06-14) — branch `spike/streaming-unification`
> (worktree, based on `spike/session-overhaul` HEAD e508bf0).
> **Scope:** FULL (approved). Promote the shared Flight server for all four
> stores; end the "each store reimplements the other's half" duplication.
> Internal doc — file:line citations and private names are fine here.

Companion docs (read first): `data-store-unification-invariants.md` (the
per-store invariant tables + the one rule that prevents read/write races),
`channels-real-stream-handoff.md` (the channels streaming resolution this
builds on), `data-store-backends.md` (the six-requirement service contract),
`channels-followups.md` #7 (the `StreamTuning` follow-up folded in here).

## Why this exists

Each data store reimplemented the *other's* half of one streaming machine:

- The **lossy-live producer push relay** was generalized on **channels**
  (`_push_loop`); the files store reimplemented it as `_FrameRelay`
  (`catalog_manager.py` docstring even says it "mirrors the channel push
  relay… the same disease channels had").
- The **server + subscribe fan-out** was generalized as the shared
  `DuckDBFlightServer` (events + runs + files-catalog all sit on it);
  **channels** reimplemented it as a bespoke `ChannelFlightServer` +
  `_SubscriberRing`.

So neither is simply "the duplicate" — each store went bespoke on the axis
the other had already shared. FULL fixes both axes by converging on the
shared server and one shared producer relay.

## The verified duplication map (read against source)

### Producer-side push transport — THREE implementations, TWO contracts

| Impl | Where | Contract | Disposition |
|---|---|---|---|
| `ChannelStore._push_queue`+`_push_loop`+`_flight_push_batch` (`queue.Queue(maxsize=10_000)`, `_PUSH_MAX_ROWS=1000`/`_PUSH_MAX_WAIT=0.005`, `_push_drops`) | `channels/store.py:339,1711,1751` (consts `:1170-1171`) | **lossy-live**: non-blocking enqueue, coalesce, held `do_put`, drop on overflow (live = from-now; durable segment is whole) | → shared `PushRelay` |
| `_FrameRelay` (`queue.Queue(maxsize=1024)`, `_MAX_ROWS=256`/`0.05s`, `_dropped`, explicit drop-oldest) | `files/catalog_manager.py:239` | **lossy-live** (same shape; the explicit drop-oldest+count is the better-written half) | → shared `PushRelay` (delete `_FrameRelay`) |
| `FlightPutStream` (held stream, `_unacked` resend, per-batch ack, `_MAX_UNACKED_BATCHES=256`, reacquire-on-kill) | `_duckdb_flight_server.py:57` | **lossless-acked**: every write blocks on an ack; resend after daemon kill | **STAYS SEPARATE** — different durability contract; do NOT merge into the relay |

The lossy-live relay and the lossless-acked stream are deliberately
different machines. "Unify streaming" collapses the two lossy relays into
`PushRelay`; it does not touch `FlightPutStream`.

### Server-side subscribe fan-out — TWO implementations

| Impl | Where | Features | Disposition |
|---|---|---|---|
| `_SubscriberRing` + `_flight_subscribers` + `_relay_batch` + `_live_stream` in bespoke `ChannelFlightServer` | `channels/server.py:31,97,101,190` | `collections.deque`, **drain-coalesce** (LMAX catch-up: one read drains all queued batches), **ALL/LATEST policy**, **gap count** | → lift the ring INTO the shared server; retire `ChannelFlightServer` |
| `_subscribers` plain `queue.Queue(maxsize=_SUB_QUEUE_MAX=10_000)` + `_do_subscribe` + `_publish` | `_duckdb_flight_server.py:263,405,306` | drop-on-full (removes the subscriber), **`replay_sql` cursor catch-up** (lossless across the subscribe boundary) | gains the ring's drain-coalesce + policy |

The shared `_do_subscribe` already has the lossless replay-then-live
catch-up the channels ring lacks; the channels ring has the drain-coalesce +
policy the shared one lacks. The lift merges both strengths into the shared
server — events/runs/files all benefit.

### Client-side subscribe reader — TWO implementations

| Impl | Where | Disposition |
|---|---|---|
| `ChannelClient.on_channel` / `on_channel_batch` (held `do_get` + reader thread + `batch_row_to_sample` per row) | `channels/client.py:88,129` | → shared `subscribe()` |
| `subscribe_frames` (held `do_get` + reader thread + dict per row) | `files/catalog_manager.py:326` | → shared `subscribe()` |

### Tuning knobs — scattered (follow-up #7)

Nine knobs across four files: `_ChannelSink._FLUSH_ROWS`/`_FLUSH_INTERVAL`,
`ChannelStore(flush_threshold=)`, the **unreachable**
`BufferedIPCWriter.flush_interval` (never plumbed through `_ChannelWriter`),
`_pending_threshold`, push queue `maxsize=10_000`,
`_PUSH_MAX_ROWS`/`_PUSH_MAX_WAIT`, `_SubscriberRing maxsize=1024`,
`_FrameRelay maxsize`/`_MAX_ROWS`. → one shared `StreamTuning`.

## What the shared server already gives us (the seams exist)

The write + fan-out side channels needs is already on `DuckDBFlightServer`:

- `register_put_hook(db_name, hook)` (`:329`) — custom `do_put` handler that
  bypasses the default DuckDB insert and may RETURN rows to fan out. Files
  frames use `lambda table: table`; runs ingests parquet-by-path. Channels'
  `ingest_batch` (`server.py:125`) is exactly this shape.
- `register_subscribe_schema(db_name, schema, replay_sql=)` (`:275`) — live
  push with optional lossless cursor-replay catch-up.
- `_publish` (`:306`) + `has_subscribers` (`:297`) — the fan-out gate.

## The one real gap

`do_get` is **SQL-string-or-`__SUBSCRIBE__` only** (`:378`). There is a
`register_put_hook` but **no** matching query hook. Channels' read is not
SQL-over-one-table: `store.query(channel_id, start=, end=, last_n=,
max_points=, session_id=)` does range/last-N/decimate + `channel_index ∪
live.channel_live` deduped on `(session_id, offset)` **in pyarrow** (DuckDB's
window-over-cross-DB-union errored — see the channels resolution), plus the
`__registry__` discovery verb (`query_registry`). So channels needs a new
opt-in seam: `register_query_hook` (parallel to `register_put_hook`).
Events/runs/files keep plain SQL `do_get` — the hook is opt-in.

## Invariants discipline (blast radius)

Phases 3–5 touch the server events/runs/files **share**. Per
`data-store-unification-invariants.md`, the high-risk rule:

> **Never remove a read lock in a commit that doesn't also make the
> corresponding multi-statement write atomic.**

The ring lift (Phase 3) changes the shared subscribe path. It must preserve:
events' lossless events→runs materializer subscription (Rule E2: cursor
replay + dedup, never silently drop the materializer), and runs' locked-mode
reads (Rule R1, runs is not `parallel=True`). The ring change is fan-out
mechanics, not the read/insert path, but any subscriber-drop semantics change
gets verified against E2 before landing.

## Phase plan

0. **Design contract** (this doc). Committed.
1. **Extract `PushRelay`** — `src/litmus/data/_push_relay.py`, generic over
   item type + a `coalesce(items)->[(descriptor, table)]` hook + a
   `descriptor_for`/group key (channels → `channel_id`; files → constant).
   Keep files' explicit drop-oldest+count. Collapse `_FrameRelay` and
   `_push_loop` onto it. Unit tests: overflow, coalesce, close-drain.
   *Lowest blast radius — no shared-server change.*
2. **`StreamTuning`** — collect the 9 knobs; plumb `flush_interval`; both
   relays + writers read it; surface `flush_threshold`/`flush_interval`
   toward `litmus.yaml`.
3. **Lift `_SubscriberRing` into `DuckDBFlightServer`** — deque +
   drain-coalesce + ALL/LATEST policy + gap count, replacing the plain
   `queue.Queue`. Verify E2 (events materializer) + R1 (runs locked). *Cross-
   store blast radius.*
4. **`register_query_hook` seam** — typed read tickets for channels
   (range/last-N/decimate + `__registry__`); SQL stays the default.
5. **Channels adopts `DuckDBFlightServer`** — daemon
   (`_flight_daemon.py`/`flight_manager.py`) starts the shared server;
   `do_put`→put_hook (`ingest_batch`), `do_get`→query_hook, subscribe→shared
   ring; `list_flights`/`get_flight_info`/descriptor-absorb/`__registry__`
   fold into discovery. Delete `channels/server.py`
   (`ChannelFlightServer` + bespoke `_SubscriberRing`). Update `ChannelClient`.
6. **Shared `subscribe()` client reader** — fold `on_channel*` and
   `subscribe_frames` onto it; per-row decode stays pluggable.
7. **Benchmark + docs** — `litmus benchmark --full` before/after to guard the
   channel throughput ratios (no regression vs the pre-unification numbers);
   update this diary + `channels-followups.md` #7 (mark done). Full suite
   green.

## What stays out of scope

- `FlightPutStream` (lossless-acked) is not merged into the relay.
- No `pyarrow.dataset` / index-substrate change (the channels resolution
  ruled it OUT: cuts against the backend swap).
- The req6 serving-tier swap hook stays deferred (dead env vars not shipped).
- Track 2 (channels discovery/identity/ticket) is adjacent, not part of this.
