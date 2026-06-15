# Streaming unification — promote DuckDBFlightServer for all four stores

> **Status:** COMPLETE (2026-06-15) — branch `spike/streaming-unification`
> (worktree, based on `spike/session-overhaul` HEAD e508bf0). All phases 0–7
> landed; channels is on the shared `DuckDBFlightServer`, the bespoke
> `ChannelFlightServer` is gone, producer relay + consumer reader are both
> shared, and the channel write/write_many/stream throughput is preserved and
> improved (benchmark below). Full suite green (2084 passed, 17 skipped).
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
| `_subscribers` plain `queue.Queue(maxsize=_SUB_QUEUE_MAX=10_000)` + `_do_subscribe` + `_publish` | `_duckdb_flight_server.py:263,405,306` | drop-on-full (removes the subscriber → client reconnects + replays), **`replay_sql` cursor catch-up** | gains drain-coalesce; overflow branches on `replay_sql` (events/runs unchanged) |

**Durability is never in question here.** Every producer writes the durable
segment (channels) / artifact (files) BEFORE any fan-out
(`store.py:657-660`, `streaming.py:428-430`), so a subscribe buffer overflowing
cannot lose data — only the *in-memory live tail* drops, and a consumer always
recovers the rest from the durable store (`_maybe_scan_disk:1333-1336`; files
range-read). What the two implementations actually differ on is **how a lagging
live consumer catches up**, and the two overflow behaviors that follow are
**mutually exclusive at the buffer level** — you cannot serve both with one:

- **Replay-backed (events → runs):** on overflow the buffer **drops the
  subscriber** (ends its stream). That drop *is* the recovery trigger — the
  client reconnects and replays from its `replay_sql` cursor (in-band catch-up).
  This consumer builds the runs view, so it must see every event in order;
  keeping it attached while dropping old batches would advance its cursor past
  rows it never refills.
- **Live tail (channels / files frames):** on overflow the buffer **drops the
  oldest batch, keeps the subscriber, counts a gap**. The consumer is a viewer;
  it recovers missed data out-of-band by re-reading the durable store (using its
  `offset` / `byte_offset` cursor to resume + dedup). Dropping *this* subscriber
  would be wrong — there is no `replay_sql` replay on these subscriptions, so
  ending the stream just churns reconnects for a consumer that re-syncs by a
  separate query regardless. Keeping replay *off* the live path is also what
  keeps the relay a cheap dumb tickerplant (`store.py:824`) — no per-subscriber
  cursor bookkeeping competing with live fan-out on the hot path.

The overflow behaviors are mutually exclusive, but the choice needs **no new
policy type** — it follows directly from whether the db registered `replay_sql`,
a fact the server already holds (`self._subscribe_replay`). `replay_sql` present
→ drop-subscriber (the consumer can replay); absent → drop-oldest + gap (it
can't, and re-syncs from the durable store). The two are coupled — there is no
in-band replay without a cursor, and no point keeping a cursorless subscriber
alive across a gap it can never refill — so a separate policy enum would be
redundant and could only ever mirror `replay_sql`. Drain-coalesce (the LMAX
read-side catch-up) is an orthogonal read optimization, safe for every subscriber.

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

## Channels behavior inventory — preserve ALL (the Phase 5 contract)

Channels is a **painfully tuned machine** (`channels-write-scaling.md`,
`channels-real-stream-handoff.md`). Every behavior below is load-bearing and
must survive the migration onto the shared server. Each maps to where it is
preserved; **any behavior that would change or drop is a STOP-and-ask, not an
implementation detail.**

| Behavior | Where it survives |
|---|---|
| Durable-segment-first append; write/write_many/stream unified on `_append_and_publish` | ChannelStore — untouched by the server move |
| Scalar columnar fast path (no per-sample `ChannelSample`/dict) | ChannelStore — untouched |
| `ChannelSample` materialized only when a subscriber/index consumes | ChannelStore — untouched |
| Held per-channel `do_put` writers (one handshake per channel) | producer transport — kept; only the descriptor becomes the shared server's `db_name\0…` form (Phase 5) |
| Async push relay off the capture path | shared `PushRelay` (Phase 1, done) |
| Batch-buffering `_ChannelWriter` + rotation + idle flush | ChannelStore — untouched |
| `offset` cursor (per-(channel,session) monotonic) + dedup | ChannelStore — untouched |
| **drain-coalesce (LMAX: one read drains all queued)** | ported into the shared subscribe buffer (Phase 3) |
| **`ALL`/`LATEST` conflation** | stays in `ChannelFlightServer` through Phase 4; ports onto the shared live-tail branch (Phase 5) |
| **gap count on drop** | ported into the shared buffer (Phase 3) |
| drop-oldest, keep-subscriber overflow | the no-`replay_sql` branch (Phase 3) |
| `channel_index ∪ live overlay` dedup-on-`(session_id,offset)` | `store.query`, routed via `register_query_hook` (Phase 4) |
| runtime segment-scan recovery (`_maybe_scan_disk`) | `store.query` path — untouched |
| range/last-N/decimate/`max_points`/`session_id` query verb + value decode | `register_query_hook` (Phase 4) |
| async/batched index feed (`ingest_batch`) | put-hook path (Phase 5) |
| descriptor-absorb on `do_put` open | put-hook path (Phase 5) |
| `client.channels()` enumeration (was `list_flights`) | **query-hook verb** `__channels__` → descriptor table (Phase 5) |
| `client.channel_registry()` | **query-hook verb** `__registry__` (Phase 5) |
| liveness probe (`list_flights` responds empty) | **free** — `FlightServerBase.list_flights`, generic; `list_flights` goes back to being *only* the probe |
| `get_flight_info` (per-channel schema) | **dropped** — no client caller (justified) |

Phase 5 does not land until every row here is demonstrably preserved (benchmark
ratios + the channels test suite green, Phase 7).

## Channel baseline — before the cutover (`litmus benchmark`, WSL2, trust ratios)

Captured on `spike/streaming-unification` with channels still on
`ChannelFlightServer` (Intel Ultra 9 275HX, 24c, duckdb 1.5.0, pyarrow 23.0.0):

- Write sensor sample: **6.09k/s** (0.16 ms) · Write waveform block:
  **8.45M points/s** (0.21 ms) · Read channel data: **213k/s** (2.2 ms).
- channels concurrent-write capacity: 7.28k → 9.04k → 10.1k /s (1/2/4 writers),
  scaling 0.35.

The cutover re-runs the same `litmus benchmark` and compares. Note: this guards
the producer write/query path (what the channel-optimization work tracked); the
daemon-side live-subscribe filter cost is not in this benchmark — exercise it
separately if a number looks off.

## Phase 5 subscribe routing — server-side filter (decided)

Channels' per-`channel_id` isolation is preserved by **promoting filtering to
the server**, not client-side broadcast (which would flood consumers with every
channel's traffic). A per-subscription **equality-predicate filter** on the
shared server — one mechanism every store can use:

- Ticket: `db_name\0__SUBSCRIBE__\0<cursor>\0<filter>`, where `<filter>` is a
  urlencoded predicate set (`channel_id=dmm.voltage`; events later:
  `event_type=run.ended&role=dmm`). **Empty filter = all rows** — the
  materializer's exact path, so E2 is untouched.
- `_SubscriberBuffer` holds the predicates; `_publish` masks each batch to
  matching rows (pyarrow equality, ANDed) and puts only those; the replay
  applies the same filter. Channels' push batches are single-`channel_id`
  (`_push_flush` does one `do_put` per channel), so the predicate is a one-shot
  whole-batch check — the goal is **parity with today's server-side routing**,
  verified by benchmark (no perf dip).
- Maps every store onto one filter: channels → `channel_id` (empty = the `"*"`
  wildcard), events (follow-up) → `event_type`/`role`/`session_id`/`run_id`,
  files frames → `file_id`/`uri`. Expressiveness stays at equality-predicates
  (ANDed) — the simplest thing covering every current need; not a general WHERE.

## Invariants discipline (blast radius)

Phases 3–5 touch the server events/runs/files **share**. Per
`data-store-unification-invariants.md`, the high-risk rule:

> **Never remove a read lock in a commit that doesn't also make the
> corresponding multi-statement write atomic.**

Phase 3 adds drain-coalesce + replay-derived overflow to the shared subscribe
buffer. Because events/runs are replay-backed, their overflow branch is exactly
today's — drop-subscriber → client reconnects + cursor-replays (Rule E2); the
new drop-oldest+gap branch is reached only by cursorless (channels/files frames)
subscriptions, so the events→runs path is untouched by construction. No data is
at risk either way — every store is durable-first; this governs only how a
lagging *live* consumer re-syncs. Runs stays locked-mode (Rule R1).

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
3. **Replay-derived overflow + drain-coalesce in `DuckDBFlightServer`** — replace
   the plain `queue.Queue` subscriber with a buffer that:
   - **drain-coalesces on read for ALL subscribers** (one read drains every
     queued batch — LMAX catch-up); and
   - **branches overflow on `replay_sql` presence** — no new policy type, the
     server already stores `self._subscribe_replay`:
     - replay-backed (events, runs) → drop the subscriber so the client
       reconnects + replays from its cursor;
     - not replay-backed (channels, files frames) → drop the oldest batch +
       count a gap, keep the subscriber; it re-syncs from the durable store.

   No data is at risk under either branch — every store is durable-first; this
   only governs how a lagging *live* consumer re-syncs. **Conflation is a
   separate axis and is NOT dropped:** channels' client-chosen `ALL`/`LATEST`
   (`server.py:57`) stays in its own `ChannelFlightServer` untouched through
   Phases 3–4, and ports onto the shared server's live-tail branch when channels
   migrates in **Phase 5**. *Cross-store: changes the shared buffer events/runs
   depend on — replay-backed stays drop-subscriber, and the events→runs path is
   verified unchanged.*
4. **`register_query_hook` seam** — typed read tickets for channels
   (range/last-N/decimate + `__registry__`); SQL stays the default.
5. **Channels adopts `DuckDBFlightServer`** — **DONE.** The daemon
   (`_flight_daemon.py`) + in-process helper start the shared server
   (`parallel=True`); `channels/server.py` is now the adapter
   (`register_channel_hooks`): `do_put`→put-hook (`ingest_batch`),
   `do_get`→query-hook (range/last-N/decimate + `__registry__` + `__channels__`
   enumeration + `__ping__` liveness), subscribe→shared buffer with a
   `channel_id` filter and `LATEST`→`conflate`. Fan-out rides one `on_batch`
   bridge → `server._publish` (gated on `has_subscribers`). `ChannelFlightServer`
   + `_SubscriberRing` deleted; `get_flight_info` dropped; `list_flights` retired
   in favour of `__channels__` + `probe_sql(payload="__ping__")` (the one shared
   liveness probe; `probe_flights` deleted). `ChannelClient` rewired.

   **Two perf regressions caught by `scripts/bench_channel_scaling.py` (before/
   after; the bundled `litmus benchmark` sweep doesn't cover write_many/stream)
   and fixed — the shared server now beats the bespoke one on every write mode:**
   - *Per-batch `_absorb_descriptor`* killed `ingest_batch`'s columnar fast path
     (empty `_registry` → not-scalar → slow per-row path) and churned
     `_index_lock`. → absorb **once per channel** (gated on `_registry`), matching
     the bespoke once-per-stream. Restored write_many 52k→180k+, stream→179k+.
   - *`probe_sql` ran `store.query`* → took `_index_lock` → false-failed under
     write load → daemon kill/respawn mid-run (the concurrent-write collapse +
     bimodality). → lock-free `__ping__` verb (no store/index touch). Restored
     `channels.write` scaling.

   Final vs the pre-everything original (`e508bf0`), 1/2/4 writers:
   write_many 154/162/152k → **180/190/188k**; stream 150/155/155k →
   **179/183/184k**; write 7.6/10.6/9.8k → **7.6/13.5/13.2k**.
6. **Shared `subscribe()` client reader** — **DONE.**
   `_flight_subscribe.subscribe(client, ticket, on_batch, …)` owns the held
   `do_get` + daemon reader thread + stop/unsub once; `ChannelClient.on_channel`
   / `on_channel_batch` and files `subscribe_frames` pass a pluggable decode
   callback. Per-caller differences ride flags (`client_stop` for the channels
   client-wide close, `swallow_errors` for the files contract, `on_close` for
   files' client-close + daemon-release) — no copies of the loop. Removes the
   triplicated thread/stop/try-except boilerplate and the drift between the three.
7. **Benchmark + docs** — **DONE.** `scripts/bench_channel_scaling.py`
   before/after (the bundled `litmus benchmark` sweep doesn't cover
   write_many/stream — recorded as a follow-up). Result: shared server beats the
   pre-everything original on every write mode (write_many 154→205k, stream
   150→198k, write scales further). Full suite green: **2084 passed, 17 skipped**.
   Diary updated; the `StreamTuning` knob-consolidation (`channels-followups.md`
   #7) landed as the per-store `ChannelOptions`/`FileOptions` in Phase 2.

## What stays out of scope

- `FlightPutStream` (lossless-acked) is not merged into the relay.
- No `pyarrow.dataset` / index-substrate change (the channels resolution
  ruled it OUT: cuts against the backend swap).
- The req6 serving-tier swap hook stays deferred (dead env vars not shipped).
- Track 2 (channels discovery/identity/ticket) is adjacent, not part of this.
- **Events server-side filtering** — migrating the client-side broadcast+`matches`
  (`event_store.py:218`) onto the new per-subscription filter is a verified
  fast-follow once channels proves the mechanism, not part of this effort.

## Follow-ups after this refactor

- **The daemon write path (`serve=True`) does not scale across concurrent
  writers — parallelize the daemon ingest.** Every writer funnels into ONE
  daemon that ingests under one `_index_lock`, so aggregate is flat regardless
  of writer count (`bench_channel_scaling.py`: write_many ~200k, stream ~190k,
  scalar write ~15k — all factor → ~0.25 at 4w). This is **pre-existing**
  (`e508bf0` and the merge both show it) and **documented** in
  `channels-write-scaling.md` (the "scaling problem" hand-off, daemon path = 0.36
  vs the producer path's 0.91). The streaming unification neither caused nor
  fixed it — it was scoped to share the machine + preserve absolute throughput,
  not to break the single-consumer ingest ceiling. NB: `bench_channel_scaling.py`'s
  docstring asks for factor ~1.0, which is the *producer* (`serve=False`,
  own-IPC-file, ~4×) path — the daemon path has never hit that. Fixing it =
  parallel daemon ingest (per-channel ingest shards / multiple cursors /
  lock-free index append), the open work in `channels-write-scaling.md`.
- **`litmus benchmark` concurrent sweep only covers `channels.write`.** The
  per-store concurrent-write sweep (`runner.py` `sweep_specs`) measures
  `channels.write` only — NOT `channels.write_many` or the `stream` sink, the
  high-throughput batched paths. The Phase 5 cutover regressed write_many/stream
  ~3× (154k→52k) and the CLI never showed it; the regression was only caught by
  the standalone `scripts/bench_channel_scaling.py` (which sweeps all three modes
  across 1/2/4 writers). Fold those modes into the CLI sweep so the headline
  command guards every write path. Until then, run `bench_channel_scaling.py`
  whenever the producer push / daemon do_put / liveness-probe path changes.
- **Merge worktrees back, return to files.** This effort runs in the
  `litmus-streaming` worktree off `spike/session-overhaul`. When it lands, merge
  the worktrees back together and resume the files-store work.
- **Files streaming perf gate is not trusted.**
  `test_perf.py::TestFileStreamPerf::test_stream_raw_near_io_ceiling` is
  `@pytest.mark.skip`'d — the user doesn't believe we actually hit the stated
  io-ceiling numbers, and it flakes under load. Revisit files streaming perf
  (re-measure honestly, fix or re-baseline the gate) as part of getting back to
  files.
