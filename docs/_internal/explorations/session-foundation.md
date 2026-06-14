# Session foundation — sessions as correlation roots on the event spine

> **Status:** design contract for the `spike/session-overhaul` branch. This is the durable,
> cross-session source of truth; the execution progress log lives at the end. Internal doc —
> file:line citations and internal names are intentional.

## Why

"Liveness" (a trustworthy live / closed / abandoned signal, needed by the channels & files
live UIs) cannot be built over today's session model, and the model is misaligned with the
prior art the platform should be grounded in. Today a session is a `StationConnection` object
whose lifecycle is a Python `with`/`atexit` block; it carries an outcome; and it ends reliably
only on cooperative close or same-host pid-death. In a harsh local environment
(locally-spawned singleton daemons, `kill -9`, power loss, idle-shutdown) cooperative end is
the *least* trustworthy signal there is.

Two things are already in our favor:

1. **A single durable source of truth.** The client-side `EventLog` writes Arrow IPC locally
   *first*, then async-pushes to the daemon — this **is** the transactional outbox in its
   "log-is-the-outbox" event-sourcing form (crash-safe, at-least-once, idempotent projection
   via `ON CONFLICT DO NOTHING`). Daemons are disposable projections.
2. **The session is OpenTelemetry-shaped.** It holds no scarce server resource, so it needs no
   server-side lease/lock — it is a pure correlation root, recorded by one spine event.

**Goal:** rebuild sessions on the converged prior-art pattern so liveness is *derived* from the
durable spine, the end is *announced for* dead producers, and store daemons never synchronously
depend on one another.

## Prior art (the converged pattern — we are not inventing this)

- **OpenTelemetry traces/spans** — the correlation root is an *id*, not a connection; it carries
  **no status** (only spans do); it propagates across processes as explicit context, decoupled
  from any transport connection.
  [traces](https://opentelemetry.io/docs/concepts/signals/traces/) ·
  [context propagation](https://opentelemetry.io/docs/concepts/context-propagation/) ·
  [W3C Trace Context](https://www.w3.org/TR/trace-context/)
- **OPC UA** — the **Session is independent of the SecureChannel** (survives reconnects); end is
  explicit `CloseSession` **or server timeout reap**; liveness derived from existing traffic
  (keep-alives only when idle). [OPC UA Part 4 §5.6, reference.opcfoundation.org]
- **Kafka** — consumer-group membership and producer identity are coordinator/epoch concepts over
  many connections, not a connection; the group has **no outcome** (only the transaction does);
  zombies are **fenced by epoch**, not by a synchronous validity check.
  [consumer configs](https://kafka.apache.org/41/configuration/consumer-configs/) · KIP-98, KIP-345
- **MQTT** — keep-alive is **assert-by-traffic** (PINGREQ only when otherwise idle); the
  **Last Will & Testament** is pre-registered so the broker announces the end *for* a dead client;
  **Will-Delay-Interval** debounces reconnects.
  [OASIS MQTT v5.0](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html)
- **etcd / ZooKeeper / Chubby** — session = a **lease**; lease-scoped state auto-vanishes on expiry
  and the disappearance *is* the signal; an expired session cannot be revived (reconnect = new
  session). [etcd lease API](https://etcd.io/docs/v3.4/learning/api/) ·
  [ZooKeeper](https://zookeeper.apache.org/doc/current/zookeeperProgrammers.html)
- **DDS Liveliness QoS** — liveliness is **asserted by the data you already write**
  (`lease_duration`, assert-during-idle only); readers learn death via a status change.
  [OMG DDS LIVELINESS] · [RTI Connext LIVELINESS QosPolicy]
- **CloudEvents** — make each event **self-describing** (`source`/`subject`/`id`/`type` + extensions)
  so projections correlate by reading the envelope, no coordinator.
  [spec](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md)
- **Transactional Outbox** — durable local write first, async relay, idempotent consumer; at-least-once
  + dedupe, never exactly-once. [microservices.io](https://microservices.io/patterns/data/transactional-outbox.html)

**Converged rules:** the root is an *id* decoupled from transport; the root carries *no outcome*
(its children do); a richer child nests and can outlive it; the id propagates as explicit data;
end is dual (best-effort explicit + authoritative TTL/derived backstop); liveness is derived from
real traffic (heartbeats are the idle-only fallback); coordination is correlation-id, never
synchronous cross-service calls.

## The anti-patterns we are sitting on (verified against source)

1. **Session welded to a connection object + `with` block.** `StationConnection` *is* the session
   (`connect.py`); lifecycle = object lifetime. Breaks "the root is an id, not a connection."
2. **The root carries an outcome.** `SessionEnded.outcome` (`data/events.py`). Traces/groups/OPC-UA
   sessions carry none — only their children do. **Sessions have NO outcome.**
3. **Cooperative close is the only reliable end.** Sessions reaped only on same-host pid-death
   (`data/_runs_duckdb_daemon.py:1789` deliberately excludes the idle timeout runs get). Remote /
   hung / abandoned sessions never close → nothing for liveness to derive.
4. **Inconsistent scope→store binding.** `ChannelStore` is session-bound at construction (and eagerly
   spins a daemon); `FileStore` is a singleton taking `session_id` per-write. The id-as-tag model
   (FileStore's) is correct; the held per-session handle re-creates #1.
5. **Eager capability load.** Opening a session eagerly opens the channels daemon even with zero
   channels. A correlation root should hold nothing.
6. **Session is forgeable, no existence/fencing notion.** Any process stamps any `session_id` with
   no `SessionStarted` (the orphans; the benchmark). Can't reject a write under a never-opened or
   already-ended session.
7. **Trap to avoid:** a cross-daemon "is this session open?" gate. Coordinate by correlation-id +
   spine, never synchronous cross-service calls. Daemons are correctly decoupled today (only edge:
   runs **subscribes** to events — event-driven, not request/response).

## Target model

**Session = OTel trace. Run = span.** A session is a client-minted correlation **id**, recorded by
one `SessionStarted` event on the spine, carrying **no outcome**, independent of any connection or
`with` block (those become best-effort sugar). A run nests inside it and **carries the outcome**
(pass/fail/abort), N per session, may outlive siblings. The id **propagates across processes as
explicit data** (the existing `_LITMUS_SESSION_ID` env hand-off is correct — context propagation).

**Identity & envelope (CloudEvents).** `source = litmus:session/<session_id>`; `subject =
<run|channel|stream>/<id>`; `id` unique per source → `(source,id)` is the idempotency key (matches
`ON CONFLICT DO NOTHING`); `type` = reverse-DNS lifecycle; flat `sessionid`/`runid` extensions for
cross-store filtering. `traceparent` stays reserved for real transport tracing, never business
correlation. **Sequence = the existing per-writer `writer_key` + `event_offset`** (already stamped
in `data/event_log.py`); gap detection = per-writer contiguity (a `kill` mid-flush truncation
surfaces as a detectable hole, not silent loss); ordering is per-session. Adopting the formal
naming is the *pattern*, not necessarily column renames.

**Emission = transactional outbox (log-is-the-outbox).** Keep client-side IPC-first + async push +
idempotent insert. Guarantee = **at-least-once + effectively-once projection** (not exactly-once).
Daemons are disposable; restart replays the durable log.

**Liveness & end — derived from the spine, announced for the dead.** No scarce resource held, so no
server lease; everything reads the spine:
- **Lease = recency of any durable spine event tagged `session_id`** — renewed by every
  `RunStarted`/measurement/`ChannelStarted`/`RunEnded` ("operations are heartbeats unto themselves";
  DDS assert-by-write). No dedicated heartbeat in the common case.
- **Off-spine-streaming wrinkle:** high-rate frames ride the ephemeral fan-out, not the spine, so a
  long stream emits nothing durable between `StreamStarted`/`StreamEnded`. Resolve with a coarse
  **idle-only keep-alive** the **stream sink emits automatically**, **piggybacked on the write call**
  (on write, if > interval since last spine event → emit one durable spine event via `event_log`,
  carrying byte-offset-so-far) — **not a per-stream timer thread** (thread-budget hazard). Tunable
  cadence in the shared `StreamTuning`, default ~lease ÷ 3 (DDS `assertions_per_lease_duration`=3).
  Covers the *actively-writing* stream; a stream held open while the producer goes silent emits
  nothing and correctly ages to abandoned (unless the producer issues an explicit idle keep-alive).
- **Two thresholds:** a short display-only "live/idle" recency paints the UI badge (emits nothing —
  the channels `last_updated` pattern); the longer lease + grace governs the synthetic abandonment.
- **Default:** run orphan-timeout → **900s**; `idle_lease_seconds` anchors to it (900s; comment the
  "never shorter than the run timeout" relationship). **Tunable, layered:** platform default →
  `litmus.yaml`/`ProjectConfig` (per-project) → the will on `SessionStarted` (per-session) → a marker
  (per-run). Most-specific wins.
- **Will pre-registered on `SessionStarted`:** `producer_identity` (host + pid + process_uuid),
  `idle_lease_seconds`, `abandon_grace_seconds`, `abandon_reason`.
- **One reaper projection over the spine:** `live` (recency ≤ lease) → `suspect` (within grace; a
  late event/reconnect rescues it) → `abandoned` (emit additive synthetic
  `SessionEnded{reason=abandoned, derived=true}`, operator-visible, never silent) → `closed`
  (explicit `SessionEnded` suppresses the synthetic).
- **pid-death is RUN-only, removed from sessions.** A run has one owning process, an outcome, and
  closures that must complete → pid-death force-closes it (DDS AUTOMATIC). A session has no single
  pid, no outcome, spans processes → pid-death is wrong and hyper-local. The tiers compose: a
  `RunEnded` from a pid-death sweep is itself session traffic that renews the session lease, so
  pid-death touches a session only indirectly.

**Terminal state is final — cascade on close + reject post-terminal.** `RunEnded`/`SessionEnded`
seal the scope; cascade releases scope-owned resources (run-scoped instrument locks + open streams;
session-scoped resources), and the **pid-death force-close path runs the same cascade**. Ids are
uuid4 and never reused, so the **terminal event is the fence**: a post-terminal event for a sealed
id is a contract violation → error (never silently accepted = revival; never silently dropped).
Enforced two layers, no cross-store gate: client-side the open handle raises on write-after-close;
spine-side the projection (which already tracks terminal state) rejects/records a post-terminal
event from any process. Reconciliation: the grace window rescues a slow session; past the seal, no
revival — a johnny-come-lately must open a NEW session (ZK semantics). The reaper is the one allowed
foreign writer of a terminal event.

**Producers vs consumers.** Sessions are **producer-only**. Readers (operator UI, MCP, CLI,
materializer) hold a **session-less service connection**, query/subscribe across sessions, and emit
no lifecycle events (as today — the query path never calls `emit()`). **Reader → writer is
explicit:** a reader that decides to write makes a deliberate `open_session()` call (never an
implicit side-effect of the first write).

**Session-expired UX — typed signal + per-client policy, never silent revive.** A write to a sealed
session raises a typed `SessionExpired`. Interactive UI: catch it, deliberately open a NEW session,
re-acquire instruments, surface only genuine changes (the abandonment already cascaded — instruments
released). Batch/test producer: fail loud. The old session stays dead; its data stays readable
(finality fences writes, not reads).

**Lazy capability attach + transport/logical separation.** The session is a bare root; channels/files
attach lazily on first write via the **id-as-tag** model. The locally-spawned Flight daemon
(transport, disposable) is separate from the session (logical, durable on the spine).

## Local-environment obligations

- **Daemon-down tolerance:** the reaper is a projection; if killed, it re-derives abandonment from the
  durable spine on restart; synthetic `SessionEnded` deduped by `(source,id)`. No session depends on
  any daemon being alive at crash time.
- **Truncated final IPC record on kill:** recovery reads up to the last complete record; the per-writer
  offset detects the gap.
- **Sessions never block on daemon acquisition** (singleton-launch races / power loss are orthogonal
  daemon-lifecycle hygiene).
- **Server-backend translation:** every mechanism is backend-neutral and tightens, not loosens, on a
  stable server backend — no local-only shortcut that would need a rewrite.

## Blast radius — claim-tickets bind `session_id` into URIs and paths

`session_id` is embedded in `channel://…?session=<id>` URIs (`data/ref.py`, regex-extracted in
`data/_runs_duckdb_daemon.py`), in channel segment filenames (`{channel_id}_{session_short}.arrow`),
and in `file://<date>/<session_id>/…`. The claim-ticket/ref system requires the spine. Therefore
**`session_id` stays a stable, client-minted UUID** — the redesign changes session *lifecycle and
semantics*, not the id shape, so tickets/paths/regex are untouched. Finality fences *writes*, not
*reads*: a ticket resolves identically whether the session is live, closed, or abandoned.

## Scope reality — the spine already exists; this branch is session SEMANTICS

The event log already provides the envelope (`session_id`/`run_id`), the per-event id + per-writer
sequence (`writer_key`+`event_offset`), the outbox (IPC-first → async push), and idempotent dedupe.
**This branch does not rebuild the spine** — it adds session-lifecycle semantics on top and makes
projections *use* what the spine already records.

**No migration / no backcompat (0.2.0 is breaking):** may wipe the local data dir and break schemas
cleanly — no ALTER-migrations, no dual-shape reads, no `hasattr`/`isinstance` fallbacks. Existing
local event/parquet data is disposable.

## Phases (dependency-ordered; commit between phases)

0. **This doc** — the contract.
1. **Drop session outcome** — remove `outcome` from `SessionEnded`; update emit sites (`connect.py`,
   `execution/slot_runner.py`, pytest plugin) + the runs-daemon synthetic end; remove the
   `slot_runner` aggregate-outcome rollup. Session "health" is *derived* from runs, never stored.
2. **Correlation-root primitive + behavioral core (KEYSTONE)** — extract a session/correlation
   primitive emitting `SessionStarted`/`SessionEnded` on the spine, independent of
   `StationConnection`/`with` (sugar over explicit `open_session()`/`close()`). Lazy capability
   attach (stop eager `ChannelStore`). Write-needs-open-session gate (raise). `connect()`-exit
   decoupling: release instruments + end run, leave the session to process-end + lease.
3. **Will + spine-only reaper** — set run orphan-timeout to 900s; add the will fields to
   `SessionStarted` (tunable via `ProjectConfig`/will/marker); single reaper projection
   (live/suspect/abandoned/closed) emitting additive synthetic `SessionEnded{abandoned}`; remove
   the same-host pid-death *session* sweep (pid → runs-only); stream auto keep-alive via `StreamTuning`.
4. **Terminal finality + cascade** — seal on `RunEnded`/`SessionEnded`; cascade release (run-scoped
   locks/streams; session-scoped); pid-death force-close runs the same cascade; reject post-terminal
   client-side + spine-side. **ESCALATION-PRONE:** instrument locks are session-scoped in the pool
   today — making them run-scoped-releasable is a design detail to STOP + escalate on.
5. **Envelope discipline + per-writer gap detection** — adopt the naming pattern; projections track
   per-writer `event_offset` → flag holes; document at-least-once / effectively-once.
6. **Producer/reader split + first-class reader entry** — session-less reader/service-connection entry.
7. **Rename `StationConnection` → `Session`** — `connect()` returns a `Session` (+ optional
   instruments); readers get the session-less reader client. Wide call-site churn, isolated.
8. **Liveness projection → UI/MCP/HTTP** — the trustworthy live/closed/abandoned signal; unblocks
   the channels & files live UIs (and the liveness-as-MCP/HTTP follow-up).

**Dependencies & parallelism:** `0` gates all. `1`, `5`, and the 900s constant are independent. `2`
(keystone) blocks `4`/`6`/`7`; `3` is mostly independent; `4` deps `2`+`3` (escalation-prone); `6`
deps `2`; `7` deps `2`+`6` (last); `8` deps `3`+`5`. If `4` blocks, keep `1`/`5`/`6` and `2`→`7` moving.

## Verification

- **Reaper:** `kill -9` a producer (no graceful close, daemon also killed), restart the runs daemon,
  confirm one additive synthetic `SessionEnded{abandoned}` derived from the durable spine; confirm a
  working session is never reaped; confirm a reconnect within grace rescues a `suspect` session.
- **Idempotency/gap:** replay the IPC log into a fresh daemon → identical projections; inject a
  sequence gap → detected, not silently absorbed.
- **Decoupling:** bring up channels/files daemons with events/runs daemons down → writes succeed (no
  cross-store gate), projections catch up on return.
- **Tickets:** `channel://…?session=` resolution + materialization unchanged (id shape preserved).

## Critical files

`data/events.py` (SessionStarted/Ended fields, will), `data/event_log.py` + `data/event_store.py`
(envelope, offsets, outbox), `data/_accumulator_pool.py` + `data/_runs_duckdb_daemon.py` (open-set,
reaper, sweep composition), `connect.py` + `pytest_plugin/__init__.py` + `execution/slot_runner.py`
(session primitive, drop outcome, lazy attach), `data/channels/store.py` + `data/files/store.py`
(id-as-tag convergence). Untouched: `data/ref.py`, ticket regex/paths (id shape preserved).

## P2 detail (keystone) — spec + sub-steps

**Recon (verified):** channels write resolves the store via `channels.py:_resolve_store()` →
`get_channel_store()` (contextvar), raising `no_active_resource_error("ChannelStore")` if None;
the `ChannelStore` is created **eagerly** in `connect.start()` (`connect.py:82-88`) and the pytest
plugin (`pytest_plugin/__init__.py:255-257`), both via `set_channel_store(...)`. Files resolve the
`FileStore` singleton (`get_filestore()`) + `_resolve_session_id(..., fallback_to_active=True)`
(`files.py:50-69`). Session contextvars: `set_event_store`/`set_channel_store` in
`execution/_state.py`; `session_id` via `resolve_session_id`.

**Design decision (Option A — process-scoped session, reused):** the first producer in a process
lazily opens **one** process session; `connect()`, the pytest plugin, and bare writes all attach
to / reuse it; it closes at process-exit (atexit) or via the P3 lease. `connect()` block-exit
releases instruments + ends the **run**, never the session. Matches pytest (one session/process)
and "one correlation root per producer process." **Watch-point / escalate if it conflicts:** the
pytest plugin and slot orchestrator already own a session — the process owner must unify with them
(first-opener owns; others attach), not double-open.

**Sub-steps:**
- **2a — Session primitive (behavior-preserving extraction).** Introduce an internal session-scope
  / `open_session()` that mints `session_id`, creates EventStore+EventLog, wires `set_event_store`,
  emits `SessionStarted`, registers atexit/SIGTERM cleanup, and `close()` → emits `SessionEnded` +
  clears vars. `connect.start()/stop()` + pytest plugin setup/teardown call it instead of inlining
  (keep current close-on-exit timing for now — 2d changes it). Slot workers attach to the injected
  id, no re-open. **Opus designs the primitive's shape; Sonnet does the mechanical extraction under
  review** (high blast radius — connect.py + plugin + slot_runner + _state).
- **2b — Lazy ChannelStore.** Remove eager `ChannelStore`/`set_channel_store` from `connect.start()`
  + the pytest plugin; create on first need in `_resolve_store()` (build from the open session's
  session_id + data_dir + event_log, then `set_channel_store`).
- **2c — Write-needs-open-session gate.** `_resolve_store()` (channels) and `files._resolve_session_id`
  raise a typed `SessionRequired` if no OPEN session (not merely no store). Kills orphans.
- **2d — connect()-exit decoupling.** Split `stop()`: block-exit releases instruments + ends the RUN
  (reconnect `__exit__`'s outcome to the run end — fixes P1's dead `stop(outcome=)` param); do NOT
  emit `SessionEnded` on block-exit; the session ends at process-exit (atexit) / P3 lease.

## Progress log (keep current)

- [x] 0 — design doc committed (this file)
- [x] 1 — drop session outcome — `7edc01d` (removed SessionEnded.outcome + slot rollup; readers keyed off event existence not .outcome; stop(outcome=) left for P2)
- [ ] 2 — correlation-root primitive + behavioral core (KEYSTONE)
- [ ] 3 — will + spine-only reaper
- [ ] 4 — terminal finality + cascade
- [ ] 5 — envelope discipline + per-writer gap detection
- [ ] 6 — producer/reader split + first-class reader entry
- [ ] 7 — rename `StationConnection` → `Session`
- [ ] 8 — liveness projection → UI/MCP/HTTP

_On each completion: append a one-line "what landed" note + commit sha._

## Follow-on (sequenced after the session core)

- **Auto-capture station info at session creation (task #35).** At session open, automatically
  stamp richer STATION context onto `SessionStarted` (beyond station_id/hostname/type/location):
  instruments present + roles/resources, fixture, calibration/asset refs, a station-config
  snapshot — however much the process can provide. The session exists to provide context; richer
  auto-captured metadata lets us ask deeper questions of the events/data captured under it.
  Automatic in the session primitive (rides the P3 will), not hand-threaded per producer; degrades
  gracefully when a field is unavailable (bare script vs full station connect). Blocked on P2.
