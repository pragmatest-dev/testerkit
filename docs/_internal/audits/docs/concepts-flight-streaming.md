# Page audit: docs/concepts/flight-streaming.md

**Quadrant:** Concepts / Explanation (Arrow Flight streaming + DuckDB daemon for cross-process event queries)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 1 | 2 | 2 |
| Accuracy | 2 | 4 | 2 |
| Gaps | 1 | 4 | 3 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **5** | **16** | **14** |

---

## Ordering

### WARNING: "How `connect()` Starts the Server" section talks about a method that is not called `connect()` and is never introduced

The page's H1 is "Flight Cross-Process Model" and the lead frames the topic as Arrow Flight + a DuckDB daemon. Section 4 ("How `connect()` Starts the Server") then describes what happens "When `EventStore` is created" — there is no `connect()` method on EventStore (its constructor calls `duckdb_manager.acquire(...)`; see `src/litmus/data/event_store.py:137`). The section heading promises an explanation of a public API the page never defines, and the body silently switches to `EventStore.__init__`. Out-of-order naming makes the section read as if it answers a different question than it does. Either rename to "How EventStore acquires the daemon" or move this content into the Architecture diagram explanation directly above.

### WARNING: Daemon lifecycle is introduced twice, in different shapes

The Architecture section's three numbered bullets (line 32–36) describe the ref-counted daemon at a high level. The "How `connect()` Starts the Server" section (line 38–46) describes essentially the same lifecycle again from the caller's side. The two passes use different vocabulary ("First caller spawns the daemon" vs. "Checks for an existing daemon at the events directory") and a reader has to mentally merge them. One unified lifecycle subsection (ordered: discovery → spawn → ref-count → idle exit) would carry both readings without the duplication.

### SUGGESTION: "Channel Queries with LTTB" sits awkwardly inside a page about EventStore Flight access

The page is structurally about the events-side Flight model (EventStore, DuckDB daemon, dual-write). The ChannelStore LTTB section drops in at the end as a sibling concept, but ChannelStore uses a *separate* Flight server (see `litmus/data/channels/flight_manager.py`) with no DuckDB layer. Either promote it to a peer section earlier ("Two Flight servers: events and channels") or move the LTTB content to the channels-focused how-to and link to it. The current ordering implies the channel server is a small extension of the events server, which it isn't.

### SUGGESTION: The "Dual-Write Path" section duplicates ground covered better in `event-log.md`

`docs/concepts/event-log.md` already has its own "Dual-Write Pattern" section (lines 152–159) that covers the same Arrow IPC + Flight `do_put` story. This page's "Dual-Write Path" section adds the daemon-rebuild-on-restart detail but otherwise overlaps. Consider trimming the dual-write content here to a one-paragraph reference + link, so this page focuses on Flight specifically and event-log.md stays the canonical dual-write source.

---

## Voice

### WARNING: Page slips between "Litmus uses…" and imperative-architecture register without a consistent narrator

The opening sentence is "Litmus uses Apache Arrow Flight for cross-process data access" — third-person platform description. The Architecture section pivots to ASCII diagrams and bullet-list mechanics. "Channel Queries with LTTB" then shifts again to a how-to register with a Python snippet labelled "Query with decimation for visualization." Pick one voice for a concepts page: explain *why* the design is this way, with mechanism diagrams subordinate. The Python sample at line 61–68 reads like a how-to fragment that escaped from `querying-channels.md`.

### SUGGESTION: "without serialization overhead" overstates "zero-copy"

The bullet "Zero-copy — Arrow record batches transfer between processes without serialization overhead" is a common Arrow-Flight oversell. In practice, do_put sends batches over gRPC, which still pays HTTP/2 framing and a localhost TCP hop. The codebase comment in `_duckdb_flight_server.py:8–9` describes "~1.6ms → ~0.02ms per write" for the persistent-stream optimization — clearly nonzero. Suggest softening to "Arrow record batches transfer between processes in their wire format without re-serialization to JSON/Protobuf."

### SUGGESTION: Plain prose can replace some of the bullet lists

The "Why Arrow Flight" section is four bullets, three of which are sentence-length. A concepts page reads better as connected prose: "Arrow Flight provides zero-copy Arrow batch transfers across processes, language-agnostic clients, and a SQL-queryable surface via the embedded DuckDB instance behind the server." The bullet form fragments related claims.

### SUGGESTION: "real-time" is used twice without definition

The lead says "real-time queries from any process" and the dual-write section implies the same with "immediate SQL access." For a concepts page, define what "real-time" means in this system (sub-second after `do_put` ack? after `drain()`?). The codebase actually has interesting semantics here: `_flight_put` is non-blocking, `drain()` is the read-after-write barrier (`event_store.py:248–263`). The concepts page could explain that real-time means "as soon as the writer chooses to drain."

---

## Audience

### CRITICAL: Page is pitched at framework contributors, not the platform's nominal audience

CLAUDE.md frames Litmus's audience as test engineers (with a learning-Python user in the same MEMORY.md). The page assumes the reader already knows: gRPC, ref counting, detached processes, `do_get`/`do_put`, IPC files vs Parquet vs Arrow stream format, LTTB, claim-check pattern. None of these are defined. A test-engineer reader who landed here from `docs/concepts/index.md` ("cross-process data access via Arrow Flight") has no on-ramp. Either:

1. Mark this page as `_internal/` and link from there, or
2. Add a 2–3 sentence preamble for a non-systems reader: "Litmus stores events as files; multiple processes (the test runner, the operator UI, the AI agent) need to see each other's events without polling files. This page explains how those processes share one DuckDB index over a small gRPC server."

### WARNING: Code snippet uses `channel_store.query(...)` but never says where `channel_store` comes from

Line 61–68's Python sample drops in a fully-instantiated `channel_store` with no construction or import. A test engineer reading concepts-first won't know that ChannelStore takes `(data_dir, session_id, …)` and needs `serve=True` to enable Flight (see `litmus/data/channels/store.py:184–214`). Either include the imports + construction or remove the snippet entirely from a concepts page.

### WARNING: Terms `EventStore`, `EventLog`, `ChannelStore`, `RunStore` appear interchangeably with no introduction

The page uses `EventLog.emit()` in the architecture diagram (line 21) but then talks about `EventStore.events()` and "When `EventStore` is created" (line 23, 40). A reader who hasn't read `three-stores.md` first will conflate the two. State up top: "This page uses the `EventStore` and `EventLog` names from [three-stores](three-stores.md); EventLog is the per-session writer, EventStore is the cross-process query API."

### SUGGESTION: "any process — the operator UI, CLI tools, AI agents, or Grafana" misleads on Grafana

The lead lists Grafana as a Flight consumer. Per `docs/how-to/grafana-dashboards.md:13`, Grafana actually reads Litmus data through a separate pgwire (PostgreSQL wire protocol) server (`litmus grafana serve`), not Arrow Flight. Naming Grafana here gives a test engineer a false mental model — they'll expect to point Grafana at the Flight port. Either drop Grafana from the list or footnote that Grafana uses its own pgwire bridge.

### SUGGESTION: The "Language-agnostic" bullet is technically true but irrelevant for the audience

Arrow Flight supports Java/Rust/Go clients — but Litmus doesn't expose Flight as a public surface, and no non-Python client documentation exists. For the test-engineer audience, this bullet creates expectations that aren't backed by docs. Either link to an example non-Python client or drop the bullet.

---

## Accuracy

### CRITICAL: Architecture diagram shows EventLog.emit doing `Flight do_put` — code shows EventLog does NOT call Flight

The ASCII diagram (line 18–30) puts `Flight do_put` directly under `EventLog.emit()`. The actual implementation: `EventLog.emit()` (`src/litmus/data/event_log.py:191–232`) only does the IPC append + calls `_on_emit` / `_on_flush` callbacks. The `do_put` is fired by `EventStore._flight_put` via the `on_flush` callback wired in `EventStore.get_event_log` (`event_store.py:215–228`). The push happens on **flush**, not on every emit. The diagram conflates EventLog and EventStore and implies do_put is per-event, when it's per-batch (batched at flush threshold 50, per `event_log.py:44`). This is the kind of mental-model bug that will mislead anyone debugging "why is my event not in the UI yet" — they'll suspect Flight when the answer is "the batch hasn't flushed."

### CRITICAL: "On release, the ref is decremented; daemon exits after idle timeout" — release is now a no-op, daemon exits via PID monitoring + idle window

The lifecycle bullet at line 36 says "On release, the ref is decremented; daemon exits after idle timeout." Actual code: `DaemonManager.release` is now an explicit no-op — see `_daemon_lifecycle.py:153–157`: "No-op. The daemon prunes dead client PIDs itself via monitor_refs() every poll cycle — no blocking lock needed on the caller's exit path." Refs are pruned by `monitor_refs()` (`_daemon_lifecycle.py:270–307`), which polls the state file and removes PIDs that are no longer alive, then starts the idle countdown when the live ref list is empty. The page's description matches an older, blocking-release implementation. Also, the idle timeout is **300 seconds** by default (`_daemon_lifecycle.py:34`), not unstated.

### WARNING: "First caller spawns the daemon" omits that an existing-PID-alive daemon is reused even on version mismatch *only if* the running version is newer

`_daemon_lifecycle.py:114–142` shows a version check: if the daemon on disk is older than the calling process's installed version, the daemon is killed and respawned with a `UserWarning`. Same-or-newer daemons are reused. This is a meaningful concept point (it's how multi-version coexistence works — also surfaced in `docs/concepts/results-storage.md` "Mixed versions on one machine"), and the page omits it entirely.

### WARNING: "Returns the `grpc://host:port` location string" — the actual location is always `grpc://127.0.0.1:<port>`

The page says "Returns the `grpc://host:port` location string" in `connect()`/section 4. In practice, the events daemon hard-codes `grpc://127.0.0.1:0` (`_duckdb_flight_server.py:298`, `_duckdb_daemon.py:298`), so the location is always `grpc://127.0.0.1:<random-port>`. The channels Flight daemon takes host/port but also defaults to 127.0.0.1 (`flight_manager.py:28–29`). For a concepts page that's discussing cross-process access, the localhost-only nature is a meaningful constraint (rules out cross-machine use without an SSH tunnel or rebind). Currently misleading.

### WARNING: "DuckDB Daemon (in-memory)" — the daemon's DuckDB is persistent on disk, not in-memory

The Architecture diagram labels "DuckDB Daemon (in-memory)." Look at `_duckdb_daemon.py:288`: `index_path = events_dir / "_index.duckdb"`; `conn = _open_index(index_path)`. The DuckDB is opened against an on-disk file (`_index.duckdb`), not an in-memory DB. The `_ingested` table tracks already-loaded files so restarts are O(new files), not full rebuilds. The page also says (line 14) "can't provide real-time access to buffered (unflushed) data" — but it also says (line 32) "the in-memory DuckDB index is rebuilt from IPC files on every daemon start," contradicted by the actual on-disk persistence model. The accurate framing: "persistent DuckDB index, incrementally synced from IPC files, hot for cross-process SQL."

### WARNING: "ChannelStore has its own Flight server for time-series data" — true, but the LTTB code shown runs in-process, not via Flight

Line 59 says "ChannelStore has its own Flight server for time-series data. Queries support LTTB…" and then shows `channel_store.query(...)`. `ChannelStore.query` (`store.py:475–576`) is a local-process method that reads IPC files directly and applies LTTB in-process (`store.py:99–123`). Flight is used only for the push side (`_flight_push`, `store.py:597–619`) and for cross-process subscribers via the `ChannelClient` (referenced in `querying-channels.md:62`). The Python sample doesn't demonstrate the Flight-served query path it's introduced as illustrating.

### SUGGESTION: "writes the gRPC location to a lock file" — file is the port file, not the lock file

Section 4 step 2: "spawns one and writes the gRPC location to a lock file." Per `_daemon_lifecycle.py`, the lock file is `_duckdb.lock` (a FileLock sentinel), the state file is `_duckdb.json`, and the location-bearing file is the port file `_duckdb_flight_port`. The location goes into state JSON via `mgr.update_state(location=location)` (`_duckdb_flight_server.py:315`), with the port file as a synchronization point. The page's "lock file" terminology is incorrect.

### SUGGESTION: Add `event_number` as the daemon-stamped monotonic cursor

The page never mentions `event_number`, but it's central to how cross-process subscribers work (`_duckdb_daemon.py:33–79`, `event_store.py:438–490`). This is a concept-page-worthy invariant — strictly monotonic with INSERT order under the put-hook lock, replacing `received_at` as the cursor. The event-log.md page mentions it under the HARD contract; flight-streaming.md should at minimum reference it as "how subscribers know they've seen all events."

---

## Gaps

### CRITICAL: No mention of `flush()` / `drain()` — the read-after-write contract is hidden

The most operationally important question on a cross-process page is "if I emit, when can I query and see it?" Code answer: `EventStore.flush()` flushes IPC + drains the put stream (`event_store.py:247–263`), and `events()` calls both before querying (`event_store.py:290–297`). The page never names `flush()`, never names `drain()`, never describes the persistent-stream + per-batch ack mechanism that `FlightPutStream` implements (`_duckdb_flight_server.py:38–117`). Without it, users will write code like emit-then-query in a different process and see "missing" data. Add a "Read-after-write consistency" section.

### WARNING: No mention of multi-process EventLog (per-PID file naming)

The page treats writes as flowing from "Process A (pytest)" as a single emitter. Real code (`event_log.py:154–186`) writes to `{date}/{session_id}-{pid}.arrow` — each process gets its own file so concurrent orchestrator + worker processes don't clobber each other's IPC. This matters for multi-DUT slot subprocesses (a Litmus first-class concept; see `event-log.md` Slot events). Without it, a reader doesn't understand how parallelism interacts with the dual-write.

### WARNING: No mention of `event_number` cursor / catch-up subscription semantics

The page lists the `EventStore.events()` query path but says nothing about subscriptions. `EventStore.on_event` (`event_store.py:411–545`) is the centerpiece of cross-process live monitoring (with the catch-up replay, the watcher cursor, and the de-dup against in-process emits). It is the cross-process Flight model's whole reason for existing for operator-UI use. Currently completely absent.

### WARNING: No mention of pre-query hooks / runs daemon

A second DuckDB daemon — the runs daemon (`runs_duckdb_manager.py`, `_runs_duckdb_daemon.py`) — uses the same Flight scaffolding (`start_flight_server_in_daemon`, `_duckdb_flight_server.py:265`), with `pre_query_hook` to refresh in-flight overlay tables before each query. The page treats the events daemon as the only Flight server; in reality there are at least three (events, runs, channels), and the runs daemon is what powers `litmus runs`, the runs page, etc. Either mention the broader set or rename the page to "Events Flight server."

### WARNING: No mention of crash-quarantine / `_ingested` status

`_duckdb_daemon.py:225–270` shows that bad / torn IPC files are quarantined (status=`quarantined`, error stored, ingest thread survives). This is a key operability concept — the page touts "crash safety" via dual-write but doesn't explain how a partial-write IPC file is contained and reported. Add: "Bad IPC files are quarantined in the `_ingested` table; queries continue."

### SUGGESTION: No discussion of "what failure modes are visible to the operator"

Cross-process systems fail in interesting ways (daemon crash, port collision, version mismatch, IPC file corruption). The page makes vague reassurances ("If the Flight push fails, data is still safe in IPC files. The daemon rebuilds its state from files on restart") without anchoring them. A short "Failure modes" table — `Flight push fails` → warned, IPC still written; `Daemon dies` → next acquire respawns, all IPC re-ingested; `Bad IPC file` → quarantined in `_ingested` — would replace the vague reassurance with operator-actionable knowledge.

### SUGGESTION: No mention of the daemon log

`_daemon_lifecycle.py:318–353` shows daemons append stdout/stderr to `_daemon.log` in the daemon's directory. This is the single most useful diagnostic anyone reading this concepts page would want to know about. Add a one-liner: "Daemons append all warnings to `_daemon.log` in their data directory — `tail -f` it to see why a query is empty / slow / wrong."

### SUGGESTION: No mention of `LITMUS_DAEMON_IDLE_TIMEOUT` / `LITMUS_DAEMON_SPAWN_TIMEOUT`

Per `_daemon_lifecycle.py:28–44`, the idle timeout (300s default) and spawn timeout (30s default) are env-var-tunable. For a concepts page, mentioning these belongs in a "tuning" callout — they're the only knobs a user has on daemon lifecycle.

---

## Cross-links

### CRITICAL: Page never links to `event-log.md` despite leaning on its concepts

The dual-write pattern, `received_at` semantics, the HARD-contract `event_number` invariant, the IPC file layout — all are defined in `docs/concepts/event-log.md`. This page reinvents fragments of them inline. Add an early "Prerequisites: read [event-log](event-log.md) and [three-stores](three-stores.md) first" — the page is currently navigable only top-to-bottom for a reader who already knows the model.

### WARNING: "See Also" omits `results-storage.md` and `querying-events.md`

`results-storage.md` covers the related question "where do these files actually live and how does multi-version coexistence work" — directly relevant to the daemon-version-upgrade logic this page describes (but omits). `docs/how-to/querying-events.md` is the user-facing application of the Flight query path described here. Both should be in "See Also."

### WARNING: No backlink from `event-log.md` "Dual-Write Pattern" section to this page

`event-log.md:152–159` describes dual-write with Flight `do_put` / `do_get` and offers no link to this page where the Flight model is explained. Conversely this page's dual-write section doesn't link back. Bidirectional links between the two would let a reader follow either entry point.

### WARNING: `three-stores.md` already has the canonical pointer to this page; this page should reciprocate

`three-stores.md:17` says: "see [flight-streaming](flight-streaming.md) for `do_put`/`do_get` / DuckDB daemon details." This page's "See Also" links to `three-stores.md` correctly, but the body of this page never returns the reference for "EventStore vs ChannelStore vs ParquetBackend" — when this page mentions ChannelStore (line 59), a link to `three-stores.md#channelstore--time-series-data` would close the loop.

### SUGGESTION: Link out for the Apache Arrow Flight protocol itself

The page mentions "Apache Arrow Flight" in the lead and never links to the upstream spec. For test engineers who want to verify a third-party-tool claim ("can my Go service connect?"), a single external link to `https://arrow.apache.org/docs/format/Flight.html` is high-value.

### SUGGESTION: Link `litmus/data/_internal/explorations/api-stability-and-versioning.md` for the daemon-upgrade story

The version-upgrade behavior of the daemon (kill older, spawn newer) is part of the same HARD/SOFT contract framing referenced in `event-log.md:185–187` and `results-storage.md:71–73`. If this page documents the upgrade behavior (it should — see Accuracy WARNING above), link the same exploration doc the other concepts pages link.
