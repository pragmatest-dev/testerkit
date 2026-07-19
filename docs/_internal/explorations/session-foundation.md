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
explicit data** (the existing `_TESTERKIT_SESSION_ID` env hand-off is correct — context propagation).

**Identity & envelope (CloudEvents).** `source = testerkit:session/<session_id>`; `subject =
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
  long stream emits nothing durable between `FileStarted`/`FileEnded`. Resolve with a coarse
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
  `testerkit.yaml`/`ProjectConfig` (per-project) → the will on `SessionStarted` (per-session) → a marker
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

**Design decision (refined — see "Session ownership, multi-process & cleanup" below):** a
**producer** nominally has **one** session (its one shared context), reused; that id can be
**shared outward** to collaborators (multi-DUT workers attach; UIs may share). The **N** only
appears at the **service** (the EventStore/runs daemon), which multiplexes all producers' sessions
— that's infra, not a producer. The primitive is producer-side + context-local. Close authority is
the **P3 reaper (derived)**; explicit `SessionEnded` is a quiescence-proven fast-path only.
`connect()` block-exit releases instruments + ends the **run**, never force-closes the session.

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
  session_id + data_dir + event_log, then `set_channel_store`). **MINIMAL scope: defer the daemon
  spin only — NOT the id-as-tag store-constructor refactor. That convergence is a separate follow-on;
  do not pull it into 2b (it ballooned here once already).**
- **2c — Write-needs-open-session gate.** `_resolve_store()` (channels) and `files._resolve_session_id`
  raise a typed `SessionRequired` if no OPEN session (not merely no store). Kills orphans.
- **2d — connect()-exit decoupling.** Split `stop()`: block-exit releases instruments + ends the RUN
  (reconnect `__exit__`'s outcome to the run end — fixes P1's dead `stop(outcome=)` param); do NOT
  emit `SessionEnded` on block-exit; the session ends at process-exit (atexit) / P3 lease.

## Session ownership, multi-process & cleanup (refined model)

1. **Uniform model: correlation root with a *derived* close.** A session holds no scarce resource
   (OTel-shaped); its terminal state is **derived by the single reaper** (all participants quiescent
   + lease). **No peer ever force-closes a session** → multi-process sharing is safe by construction.
2. **Ownership = originator; explicit close is a quiescence-proven fast-path only.** Emitted only by
   a context that can prove no one else writes — a sole producer, or an orchestrator post-join.
   Otherwise the reaper derives it. The reaper is always the authority.
3. **Producer = 1 session; service = N.** A producer nominally has one session (its shared context);
   the **N concurrent sessions live at the service** (EventStore/runs daemon — the shared spine all
   producers multiplex through), which is infra, not a producer. Forms: *Multi-DUT* = one session
   shared outward, owner spawns+injects+**joins** workers then fast-path-closes (+ lease backstop);
   the join reclaims **run** instruments, not the session. *Interactive UIs* = mostly separate (one
   per UI), may share an id if desired; each self-closes (lease) or fast-path-closes. *Distributed
   peers (HIL)* = one logical activity sharing the id; works because close is derived (no owner to
   join). *Not legitimate:* sharing a session merely to correlate independent producers — correlation
   is metadata/query (campaign/DUT/date), never shared lifecycle.
4. **Runs are the specialization** (scarce instruments, structured join/close, outcome, pid-death
   force-close + cascade) — kept out of the session model.
5. **Cleanup — instrument locks are pid-local and fully decoupled from sessions.** The instrument
   **lock self-releases via OS `flock` on process death** incl. SIGKILL (same-host) — no leak; the
   `.lock` file is cosmetic. A held lock does NOT keep a session open (liveness = spine recency,
   not lock-holding); session close does NOT release a lock (the OS does, on pid death); a session
   may be reaped while a hung-but-alive process still holds its lock — correct, don't yank a lock
   from a live process. The only link is the uniform one: instrument *activity* emits session-tagged
   events that renew the lease like any operation. So locks impose **zero concerns on session
   management.** Graceful close additionally calls `pool.disconnect_all()` as a courtesy; the **session
   + UI "in-use" indicator** clear via the **derived `SessionEnded`** (the indicator keys off
   `session.ended`), independent of the lock. **Residual (not a session concern):** hardware
   safe-state on abrupt death → next-acquirer re-init (follow-on #36).
6. **Build:** primitive is producer-side + context-local (nominally 1 session per producer; the
   daemon aggregates N); P3 reaper = real close authority; explicit close = fast-path; instrument
   lifecycle stays a run concern; 2a keeps today's explicit closes.

## Cross-store consistency (HARD REQUIREMENT — the day's lesson)

The four primary stores (Event, Channel, File, Run) must behave **identically** on the axes the
session/event model touches — no per-store corners. No phase lands a fix for one store without the
same shape for all. Verified state:

- **Emission (LANDED):** `EventLog.emit()` is the **sole** emitter. Every store builds its events
  inline and emits via `EventLog`. The only justified extra layer is the instrument
  `InstrumentEventBuilder` (translates dynamic driver calls; protocol-agnostic — observers
  self-register via `__init_subclass__`, nothing enumerates them).
- **Lifecycle grammar (NOT built — follow-on):** the global data verbs (`write`/`stream`) must drive
  a **uniform, verb-keyed** event vocabulary — same `<Entity>Started`/`<Entity>Ended` everywhere; a
  one-shot `write` emits a discrete event everywhere. Today a one-shot **file write emits nothing**;
  channels vs file-streams use differently-named `Channel*` vs `Stream*` for the same verb.
- **Streaming mechanism (NOT converged — broken contract, follow-on):** channels + files run
  **parallel duplicate** producer/consumer relays (bounded queue + drop-oldest + gap-count +
  drain-coalesce); `files/catalog_manager._FrameRelay` literally "mirrors the channel push relay."
  The channels refactor was meant to be the shared mechanism files sit on; it isn't. Converge it
  (daemons may stay separate; the optimization must be ONE shared component).
- **DI vs ContextVar (KEEP DI — it is the consistent choice):** stores take `event_log` by
  injection because the event-store ContextVar lives in `execution/_state.py` and the **data layer
  imports nothing from execution**. Resolving from the ContextVar would invert that clean boundary.
  Do not "fix" stores to pull from context.

## Run vs session ownership — the keystone's author surface (decided 2026-06-14)

Separating the keystone from the author surface exposed that the pytest-side session lifecycle
is welded to the **run**, through a misnamed object. Verified against source:

**Three different things are named "logger" — independently confirmed against source (2026-06-14).**
This collision is the whole knot; it cost hours of apparent contradiction because the bare word
"logger" was used for all three:

| name | what it is | opens/closes the session? | is the run? | where |
|------|------------|---------------------------|-------------|-------|
| `TestRunLogger` (class → `RunScope`) | the run-controller object | **NO** — class body has no `open_session`/`SessionStarted`/`SessionEnded` | **YES** — builds `TestRun`, owns steps/outcome, emits `RunEnded` | `logger.py:377` (RunEnded `:1220`) |
| `logger` (the `@pytest.fixture`) | the fixture **function** | **YES** — `open_session` `:402→:249`, `SessionStarted` `:403`, `emit_ended()`+`close_stores()` `:363-364` (teardown `:411`) | no — it just *yields* the run | `pytest_plugin/__init__.py:367` |
| `logger` (local var in that fixture) | a `TestRunLogger` instance | no | it *is* row 1 | `__init__.py:398` |

A test writing `def test_x(logger)` receives the `TestRunLogger` object (row 1 — the run controller),
`yield`ed at `:407`. The session is opened/closed by the fixture **function** (row 2), **never** by the
object. So: the run controller does not touch the session; a pytest fixture does. The rename
(`TestRunLogger`→`RunScope`) + lifting `open_session` into a session fixture exists to kill this exact
three-way name collision.

- **`TestRunLogger` IS the run controller, not a logger.** It constructs the `TestRun`
  (`logger.py:452`), owns step/vector/measurement lifecycle + the outcome rollup (`finalize()`),
  and emits `RunStarted`/`StepStarted`/`RunEnded`. The injected `event_log` is the only
  "logging" in it; the `context`/`run_context` author fixtures derive from it
  (`__init__.py:977`/`:421`). The name describes ~5% of the object and hid the rest.
- **Session lifecycle is bolted onto the run's *fixture*, not the class.** The session-scoped
  autouse `logger` fixture calls `open_session` at setup and `emit_ended()`+`close_stores()` at
  teardown (`__init__.py:402` / `_teardown_logger`). So a **run ending emits `SessionEnded`** —
  the exact session=run coupling this overhaul exists to undo. The class is clean; the fixture is the bug.
- **`logger` leaks to authors only because two ops were never promoted.** The powerful verbs
  (`verify`/`observe`/`stream`) are public (`testerkit.verbs`, route through `Context`, never the run
  controller). `measure` (record-only measurement) and `record` (key/value) have no verb, so
  authors reach `logger.measure`/`logger.record` — the sole reason the fixture is author-facing.
- **Verb relationships (verified):** `verify` = `measure` + judgment — `verify` with no limit
  literally falls through to `logger.measure(DONE)` when the profile permits, else `MissingLimitError`
  (`harness.py:599-603`). `measure` never judges (always DONE) and never errors on a missing limit —
  it is the "this is never judged" intent. `observe`→`out_*`/channels/files; `configure`→`in_*`;
  both distinct from the measurement row.
- **`record` is effectively dead.** `test.record` is NOT in the accumulator's `_EVENT_CLASSES`
  whitelist (`_accumulator_pool.py:59`); `dispatch` drops unmapped types (`:122`). So `RecordEvent`
  reaches no run/step/measurement projection — it survives only as a raw event. (Unverified: whether
  any reader surfaces `test.record` at all.)

**Decisions:**
1. **`TestRunLogger` → `RunScope`** — symmetric with `SessionScope` (trace-owner ↔ span-owner);
   names the parallel in the types. Owns `RunStarted`/`RunEnded` + the `TestRun` record. **`TestRun`
   stays** the public, queryable record (don't rename — it's what `testerkit show`/API/client return).
   `RunScope` deliberately drops the `Test*` prefix: it's the live owner, not a peer data model of
   `TestStep`/`TestVector`.
2. **Session open/close lifts OUT of the run fixture.** Session is established at run-start (the
   standalone/orchestrator/worker determination stays) but **not closed by the run** — closed by
   derivation (P3 reaper/lease). The run fixture stops emitting `SessionEnded`. This is the
   pytest-side of 2d.
3. **Promote `measure` to a public verb** — record-only peer of `verify`, same row primitive
   underneath. Then **de-expose the `logger` fixture**: no test requests it, it becomes the pure
   internal `RunScope`.
4. **`SessionRequired` / 2c dropped.** It's a typed rename of the existing no-setup error, not a
   real fence — it does not catch the explicit-`session_id` orphan (a client can't know a session_id
   was legitimately opened; that's spine-side). Reintroduce a typed exception in **P4** when the
   reopen-UX has something to `except` it. (Lighter consistency win available now if wanted: unify the
   channels+files no-session error to one message — no new type.)
5. **`record`: decide home or delete** — OPEN. Needs the "does anything read `test.record`" check first.

**Verified foundation bug (load-bearing for #2 — must land regardless of the rename):** the
session-store ContextVars (`_event_store_var`, `_channel_store_var`) are the **only** mutable
session-scoped vars managed with `set(value)`/`set(None)` instead of token push/reset — every other
one (context, active_connection, step, vector) uses `push_*`/`reset_*(token)`. So a nested/sequential
session **clobbers** the outer: `connect.stop()` / `close_stores()` call `set_event_store(None)`
mid-session, wiping the outer pytest-session binding to `None` for every later test (order-dependent).
A user who nests `connect()` inside a session loses their session binding on block exit. **Fix:** token
push/reset in the primitive, restoring the prior binding on close. This is the determinism that makes
"a session is open" a trustworthy signal at all.

## Session lifecycle model — refined (2026-06-14)

Refinements that converged this session; they supersede parts of the original contract above and
simplify the P3/P4 machinery.

**No `SessionRequired` — auto-root instead (supersedes P2c).** A producer write with no active
session does not error; it mints one (OTel: the first write roots a new trace). Justified by our own
"holds no scarce resource" → auto-root, never a lease-ceremony. The only typed exception is
`SessionExpired` = a write through a held reference to an *already-sealed* session (P4) — "hit it once
and learn." `session_id` is automatic by default, explicit only to **share** across processes.

**Provenance = ownership.** Mint the id → owner (emits `SessionStarted`, anchors the session). Handed
the id → participant (attaches, emits no `SessionStarted`, never closes). Collapses `session_id` +
`emit_lifecycle` into one signal: `open_session()` (no id) mints+owns+emits; `open_session(session_id=X)`
attaches silently. Owner = the mint site, always; a **store can never own** (DI boundary forbids it
reaching `open_session`, and a session must outlive any single store — it spans channels + files + runs).

**One active `SessionScope` in one token-managed ContextVar** (supersedes "token-discipline the two
store vars"). The active session is one object carrying id + spine (+ lazily the channel store);
`session_id` and `event_log` both resolve from it. Kills the clobber (event_store/channel_store were
the only non-token contextvars and could disagree). The ambient ContextVar is the *test-author*
convenience only; interactive UIs/services hold the explicit `SessionScope`/connection handle and write
through it — no ambient magic outside the framework build.

**Close/seal = a spine-derived `Started − Ended` balance (refcount) — REPLACES the separate
live→suspect→abandoned + terminal-seal machinery.** One counter yields both liveness and finality:
- `Started > Ended` → **open** (lease backstops a leaked ref).
- `Started == Ended` → **sealed**: terminal, **writes fenced, reads never fenced**. A late write is
  rejected; the writer opens a NEW session (ZK — no revival). This *is* the P4 fence, derived.
- The **owner is the anchor ref** — first `Started` (its `SessionStarted`, carries the will), last
  `Ended`; it holds the count above zero until the last participant leaves, so the balance can't seal
  prematurely. Matches "owner spawns + injects + joins + closes last."
- The **lease supplies the decrement a crash swallowed**: a `kill -9`'d participant never sends its
  `Ended`, so the count never reaches zero on its own → the reaper, seeing all open refs quiescent past
  the lease, emits the missing `Ended`(s) → balance hits zero → sealed. Refcount = clean path; lease =
  the crash-time decrement. **Both mandatory** — a bare refcount leaks on crash (the exact failure the
  overhaul exists to kill).
- All **derived from the durable log** — count off the spine; a daemon restart re-derives, no in-memory
  counter. Taxonomy: the owner's `SessionStarted` is the anchoring +1; each participant (attacher/run)
  emits a lightweight **join/leave** pair (counted); the reaper's synthetic close is the missing leave.

**Why lock this shape now — it's additively future-proof (no client-code refactor for new participants).**
Because the model **sums a balance** rather than enumerating participants, new `Started`-emitting kinds
slot in additively: a future collaborator type / attach surface just emits the same join/leave pair and
the projection counts it. Adding multiple `Started` sources later is zero client-code churn — never a
rewrite. That open-endedness is the reason to commit to the balance model over bespoke state machines.

**Deployment (YAGNI line):** ship the `count(Started) > count(Ended)` *condition* now — today it
degenerates to the single-owner case (one `SessionStarted`, one `SessionEnded`/lease-decrement, balance
only toggles 0↔1, behaviorally identical to today's "has `SessionEnded`?" check). **Do not build the
multi-participant join/leave emitters now** — they're the additive follow-on, triggered only when
close-correctness annoyance is reported (people can't get explicit closes right → let the count carry it).
Build on the balance abstraction today; wire the extra `Started` producers the day the pain is real.

**Constrained ownership now (open later):** today = single owner anchors — one `SessionStarted`, one
`Ended` (owner fast-path or lease-decrement), balance caps at 0↔1. Workers/attachers stay tracked as
**runs** (their own `RunStarted`/`RunEnded`) and renew the lease by writing, but are not yet session-refs;
the owner anchors the session open until they finish. Opening it later = letting participants emit
symmetric session-level join/leave pairs so the balance can exceed 1. **Symmetry is lease-enforced, not
client-trusted:** every `Started` gets its matching `Ended` from either a clean leave or the reaper's
synthetic decrement, so the balance always converges and the seal is guaranteed even across `kill -9`.

## Wave D — P3 liveness: locked design + build plan (2026-06-15)

Shaped in a design session; this is the contract for Wave D. The big realization: **liveness is
a byproduct of normal emission, not a separate mechanism.** Every existing emit site
(`RunStarted`, `StepStarted`, every `measure`/`observe`, `ChannelStarted`/`ChannelEnded`,
instrument connects) already flows through the spine; a reaper watching the spine resets that
session's lease on each one — *no emit site gets a keep-alive call*. Emitting **is** asserting
liveness (DDS assert-by-write). The **one** path that emits nothing durable while active is a
**stream** (frames are off-spine), so it is the *sole* new emitter.

**The model (all three compose):**
- **Recency lease** — the session is alive while any spine event tagged its `session_id` is recent;
  the reaper resets a per-session timer on every such event and emits an additive synthetic
  `SessionEnded{reason, derived}` at `lease + grace`. The will (D1) supplies `idle_lease_seconds` /
  `abandon_grace_seconds` / `abandon_reason`, read off `SessionStarted` (never config).
- **Structural balance** — open spans (`Started − Ended > 0`) are the *north-star* close signal
  (instant clean close, crash-safe via the lease-synthesized `Ended`). YAGNI: ship the recency
  condition now (single-owner degenerates to "has `SessionEnded`?"); build on the balance seam so
  open-span counting slots in additively later. Do NOT build multi-participant join/leave now.
- **Stream auto-checkpoint** — the sink, **on write**, emits one durable spine event carrying
  offset-so-far *iff* `now − last_spine_event > cadence`. Piggyback on the write (no timer thread),
  interval-gated so a 1 MHz stream costs the spine **one** checkpoint per cadence, not per sample.
  A stream silent past the lease emits nothing and correctly ages out. Prior art: DDS liveliness +
  MQTT keepalive (assert-by-write) · Kafka offset-commit + WAL checkpoint (carry an offset) ·
  Flink/Chandy-Lamport (periodic snapshot markers). Data is NOT lost — samples stay durable in the
  IPC/blob segments; the checkpoint is the low-rate control-plane marker over them.

**Placement — the reaper lives on the events daemon (the spine owner).** `EventStore`
(`event_store.py`) is the per-data_dir singleton whose `_ensure_watcher` consumes the **raw** spine
(every event, ordered, *before* the runs accumulator's `_EVENT_CLASSES` filter — which is why the
runs daemon was blind to channel events). The session reaper is a thread in the **events daemon**
(singular per data_dir, persistent, holds the durable log → restart-clean re-derivation). The
**runs daemon keeps its run reaper**; the same-host pid-death **session** sweep moves out (pid-death
is run-only, per the model). Each reaper lives with the projection it owns.

**Tunables (all producer-side / testerkit.yaml, overridable):**

| knob | home | read by | default |
| --- | --- | --- | --- |
| `idle_lease_seconds` | `SessionOptions` | reaper (via will) | 900 (test) / 3600 (interactive floor) |
| `abandon_grace_seconds` | `SessionOptions` | reaper | 300 |
| `abandon_reason` | `SessionOptions` | reaper | `abandoned` |
| `checkpoint_cadence` | `StreamTuning` | stream sink | `lease / 3`, invariant `< lease` |

**Build order (each green, each committed) — ALL LANDED:**
- **D1 — will + SessionOptions** — `180e6d1`.
- **D-checkpoint** — `StreamTuning` (testerkit.yaml `stream:`) + `ChannelCheckpoint` / `FileCheckpoint` spine events + interval-
  gated emit on the channel producer (`ChannelStore._maybe_checkpoint`) and the file sink
  (`files/streaming.py`, set post-construction so custom formats need not accept it); cadence resolved
  producer-side via `StreamTuning.resolve_cadence` (default `lease/3`, invariant `< lease`). — `a3a185a`.
- **D-reaper** — stateless session reaper on the **events daemon** (`_session_reaper.py`): recency =
  `max(occurred_at)`, will read off `SessionStarted.json`; startup + periodic + shutdown scan; per-reap
  loopback `EventStore` emit (transient self-PID ref, no idle pin); `SessionEnded` gains `reason`/`derived`;
  pid-death **session** sweep removed from the runs daemon (pid-death is run-only). — `4463b1d`.
- **D2 — run orphan-timeout `3600 → 900`** via shared `RUN_ORPHAN_TIMEOUT_SECONDS`; `SessionOptions`
  validates `idle_lease_seconds ≥ RUN_ORPHAN_TIMEOUT_SECONDS` (a session outlives its runs). — this commit.

**Reaper key property (locked in design):** stateless over the durable spine — no in-memory lease state,
so a daemon spin re-derives the same verdict (daemon-down tolerance) and an index wipe rebuilds from the
IPC outbox. The daemon idles (300s) before any lease (≥900s), so reaps are lazy (next-spin), correctness
preserved, only timeliness deferred. Structural-balance (open-span refcount) is the northstar close signal;
shipped the recency condition now, built on the balance seam (YAGNI — no multi-participant emitters yet).

## Producer DI contract — decided 2026-06-14

**Send the `event_log`; derive everything else. Never inject `session_id` alongside it.**

- **`event_log: EventLog` — REQUIRED** on every producer. It is both the writer *and* the single
  source of `session_id` (`event_log.session_id`, public, set at open, immutable — `event_log.py:192`).
- **`session_id` — DERIVED, never passed.** It is *not* an emit-tag — it's the **partition/identity
  key** embedded in the data layout of BOTH stores (verified):
  - Channels: a `session_id` **column on every sample row** (`models.py:239`; stamped `store.py:711/730`)
    + segment filename `{channel_id}_{session_short}.arrow` + `channel://…?session=` URI + dedup key
    `(session_id, offset)` + index PK `(channel_id, session_id)` + query filter.
  - Files: the **directory partition** `{data_dir}/files/{date}/{session_id}/…` (`files/store.py:421`)
    + `file://{date}/{session_id}/…` URI + sidecar metadata.
  - **Why derive, not inject:** two independent sources of the same id is a *correctness hazard*, not
    mere redundancy. If injected `session_id` ≠ `event_log.session_id`, the **data** is stamped/partitioned
    under one id while its **lifecycle events** emit under another → split correlation root: `?session=`
    URIs don't resolve against the spine, materialization can't join data↔events, orphaned/unqueryable
    artifacts — silently. Deriving from the writer makes them equal *by construction* (same anti-drift
    rule as runs: one source, all paths derive, no drift).
- **`run_id: UUID | None` — OPTIONAL, per-call** (not constructor). A session has N runs and the writer
  can't know which is active; `None` outside any run (interactive / bringup / daemon writes).
- **`data_dir: Path` — REQUIRED** for persisting stores (location root, from the `SessionScope`).
- **`channel_store: ChannelStore | None` — OPTIONAL**, injected into the instrument/`observe` path only.

**Consequence / dependency:** `event_log` flips optional→required on the producer path. The *only*
holder of a store-without-event_log today is the **index/reader** (sentinel `UUID(int=0)`, writes
nothing, reads session_ids *off the rows*). So the precondition for "required" is **splitting the
reader out as a session-less type (P6)** — a producer *stamps* its id from the writer; a reader *reads*
ids from data and has none of its own.

## Foundation refinements — closing the model (2026-06-14)

- **`session_id` is read-only on `EventLog`.** Make it a getter-only property (private backing), not a
  writable attribute (today it's a plain `self.session_id =`). It's the **single source** the whole chain
  derives from, so it must be immutable or the derive-don't-inject guarantee is defeatable by a reassignment.
  Set once at open, read thereafter.
- **`EventStore` is the `EventLog` factory; `open_session` orchestrates.** `open_session` ensures/owns the
  `EventStore`, calls `get_event_log(session_id)` which constructs the **wired** `EventLog`
  (`on_emit`→subscribers, `on_flush`→Flight). A leaf store can't make its own `EventLog` (no `EventStore` to
  wire to) and `EventLog` can't auto-mint a `session_id` (would be an orphan with no `SessionStarted`) — session
  creation is `open_session`'s job, at the top, handed down. `SessionScope.session_id` should be a **property**
  over `event_log.session_id` — no stored second copy on the scope either.
- **`SessionScope` travels in execution; decompose at the data boundary.** The active session is the one
  `SessionScope` (one token contextvar). Stores can't take it (data imports nothing from execution) — at the
  store boundary, unbundle to `event_log`. `SessionScope` above the boundary, `event_log` below.
- **Strict-first sequencing.** Build the foundation requiring an explicit session — a clean "no active session"
  surface error (the restored `no_active_resource_error`, **not** the killed `SessionRequired` store-gate).
  **Auto-root is the additive follow-on**, deferred until the foundation is solid. Strict→permissive is additive;
  the reverse is breaking. Same philosophy as constrained-ownership and `Started>Ended`-now.
- **Three independent axes:** **station** (instruments — `connect(station)`, local or remote-proxied) ·
  **server** (where data lands — config, `files_backend`/req-6 swap; FileStore IS swap-ready via `BlobBackend`) ·
  **session** (correlation root — auto/shared). They compose; remote instruments = connect to that station +
  share the `session_id`. `connect(session_id=X)` is the attach/share path (provenance: handed id ⇒ participant).

## Overloaded names — the pile (resolve deliberately; mostly deferred)

Names this overhaul surfaced as overloaded. Track here; resolve at the noted phase, not ad-hoc.

- **`logger` → three different things** (see the disambiguation table above): the `TestRunLogger`
  **class** (the run controller — it *owns* the run, it is NOT a logger) → rename **`RunScope`**; the
  session-scoped pytest **fixture** named `logger` (opens/closes the session); the local **variable**
  `logger`. Decided: `RunScope` rename + de-expose the fixture.
- **"Store" has no consistent scope.** The event model is the clean rule — `EventStore` = per-process
  multiplexer (cross-session), `EventLog` = per-session writer. The others break it: **`ChannelStore`**
  is overloaded as BOTH a *per-session producer/writer* AND a *cross-session reader/index*; `FileStore`
  is a singleton tagged per-write; `RunStore` is query-only. So "store" means cross-session in one place
  and session-scoped in another. **Rule to adopt:** "Store" = the cross-session corpus/factory; the
  session-scoped thing is a **writer/log** (`EventLog` is the precedent). Surfaces at **P6** (the
  producer/reader split inherently names the two pieces). Deferred — no rename before then.
- **`StationConnection → Session` (P7) is suspect** — collides with `SessionScope`, and the connect
  handle is a *station connection* (instruments + a session), not the correlation root itself. Revisit.
- **Verb overloads:** `verify` = `measure` + judgment (one wraps the other — not peers); `record` looks
  like `observe` but writes a `RecordEvent` the run materialization drops (not an `out_*` peer); `measure`
  isn't a public verb. Resolution is the measure-promotion + record-decision work items, not a pure
  rename — but the conceptual overlap belongs on the pile.

## ▶ RESUME HERE (fresh session, 2026-06-14)

Foundation landed + green (see entries below). Execute the rest in this order, agents for speed,
committing each wave green; **stop only at the P4 instrument-lock-scope decision** (escalation gate —
ask the user). Open design calls already settled this session:
- **`record` → DELETE.** Confirmed dead: `test.record`/`RecordEvent` is emitted by `logger.record`
  (`logger.py:1183`) but **nothing reads it** — the accumulator drops it (not in `_EVENT_CLASSES`), and
  the only other hits are the class def + a type-union annotation (`events.py`). No reader. Remove
  `logger.record`, `RecordEvent`, and `channels`/`files` `record` surfaces in the Step-4 wave.
- **Naming renames deferred to their phases** (`RunScope`, P6 writer/store, P7) — see the names pile.

Remaining waves: **(A — LANDED `da6b86e`)** Step 4 author surface — `measure` promoted to a public verb
(peer of `verify`; top-level + fixture + `Context.measure`, all logger-routed via `_perform_measure`),
`logger` fixture de-exposed (no test/example requests it for recording), `record` deleted (was dead).
**(B — LANDED `92768b2`+`c8d3225`)** `RunScope` rename + session lifecycle lifted out of the run fixture
(opens at `pytest_sessionstart`, closes at `pytest_sessionfinish`; runner-neutral `build_session_started`).
**(C — split LANDED `f15a06b`)** P6 producer/reader split — the warm DuckDB index extracted to a
session-less `ChannelIndex` (`data/channels/index.py`), composed by `ChannelStore`. **DI flip + naming
DEFERRED** (decisions below). **(D)** P3 reaper (refcount close + will-fields + 900s). **(E)** ⚠ P4
terminal finality — STOP at the instrument-lock scope decision.

## Progress log (keep current)

- [x] 0 — design doc committed (this file)
- [x] 1 — drop session outcome — `7edc01d` (removed SessionEnded.outcome + slot rollup; readers keyed off event existence not .outcome; stop(outcome=) left for P2)
- [~] 2 — KEYSTONE (mostly landed). **2a** (`8a6edf3`/`a15bdb6`) + slot consolidation: the
  `open_session`/`SessionScope` primitive; all producer session-opens go through it.
  **2b lazy ChannelStore** (`def605f`, 2026-06-14): `open()` deferred to first write (Option B — DI chain
  untouched; the Flight daemon spins on the first channel write, not at session open).
  **Foundation refinements** (`28b0e01`, 2026-06-14): **the cross-session ContextVar CLOBBER is FIXED** —
  `SessionScope` owns the EventStore + ChannelStore ContextVar *tokens*, push-on-open / reset-on-close
  restores the outer session (was `set(None)` clobber); `EventLog.session_id` is now **read-only** (the
  single id source). Regression test added (`test_session_scope_tokens.py`); full pre-commit gate green.
  **2c (the old `SessionRequired` write-gate) DROPPED** — superseded by the auto-root / strict-first model
  (see "Session lifecycle model — refined"). **REMAINING in/after 2:** the DI contract (drop the
  `session_id` constructor param, derive from `event_log`) + its precondition the **P6 producer/reader
  split** (extract the index/reader `ChannelStore` — sizeable: ~600 lines of index/query code interwoven
  in `store.py:1053-1700`); then `RunScope` rename + lift session-open into `pytest_sessionstart`.
- **Detour — event-emitter consistency (NOT an original phase; landed this session).** `bdaf2a7`
  domain-prefixed every emitter (no bare `EventEmitter`); `fb2f365` made **`EventLog` the sole
  emitter** — deleted the `ChannelEventEmitter`/`FileEventEmitter` protocols (thin EventLog aliases),
  retyped `event_log` params to `EventLog`, renamed `InstrumentEventEmitter`→`InstrumentEventBuilder`
  (it builds; EventLog emits); test fakes subclass `EventLog`. Full gate green. See Cross-store consistency.
- **Wave A — author surface (Step-4 pytest side; `da6b86e`, 2026-06-15).** `measure` promoted to a
  public verb (top-level `testerkit.measure` + bare `measure` fixture + `Context.measure`, all routed
  through the runner-neutral `_perform_measure`→`get_current_logger().measure` like `verify`/`observe`;
  `Context.measure` no longer needs a harness). `measure` stays symmetric with `verify` (no
  `allow_repeat` — `vectors` is the loop idiom; `allow_repeat` kept as a run-scope primitive escape
  hatch). `logger` **fixture de-exposed** — no test/example requests it for recording (primitive unit
  tests via direct `TestRunLogger(...)` untouched). `record` **deleted** (confirmed dead): `logger.record`,
  `harness.record`, `RecordEvent` (+ TEST_EVENTS/Event union), the ontology entry, the generator
  category, both record test files; `event-types.md` regenerated. Full gate green. **NOTE — deferred:**
  prose-docs + skills-template `logger.measure`→`measure` migration folded into the post-Wave-B docs
  pass (those pages also rename `logger`→`RunScope`, so migrate once, not twice — per-page-audit cost).
- **Wave B — RunScope rename + session lifecycle lift (`92768b2` + `c8d3225`, 2026-06-15).**
  **B1** (`92768b2`): mechanical class rename `TestRunLogger`→`RunScope` (module `logger.py` unchanged;
  `TestRun` record unchanged). **B2** (`c8d3225`): session opens at `pytest_sessionstart`, closes at
  `pytest_sessionfinish`; the run fixture emits only `RunStarted`/`RunEnded` (no longer brackets
  `SessionStarted`/`SessionEnded`). New `build_session_started()` is the runner-neutral
  `StationConfig`→`SessionStarted` factory shared by `connect()` + pytest (store-path station resolution,
  not the pytest fixture — works for any producer incl. a future producer-UI; down payment on #35).
  **Hard-won correctness fix:** `pytest_sessionfinish` must finalize the run from the **per-session
  stash**, NOT `get_current_logger()` — `set_current_logger` is token-restored, so a nested in-process
  pytester run restores the var to the OUTER run on teardown; finalizing the current logger there sealed
  the outer run mid-suite (its `RunEnded` landed before its own steps), and every later step then
  re-triggered a failed materialize of the now-"complete" run (the un-evicted #37 mixed-type poison) —
  a ~2000-deep daemon retry storm that starved legit runs → subprocess materialization timeouts.
  `RunScope.finalize()` is now idempotent. Full suite green (2063 pass). **NOTE — still deferred:** the
  prose-docs + skills-template `logger.measure`→`measure` migration (folded with this rename's doc pass).
- **Wave C — producer/reader split (`f15a06b`, 2026-06-15).** The warm DuckDB index (open / scan /
  registry / insert / query — ~600 lines interwoven in `store.py`) extracted to a session-less
  **`ChannelIndex`** (`data/channels/index.py`): it reads `session_id` off the data rows and has none of
  its own (verified — the index never touched `self._session_id`; the producer stamps it). `ChannelStore`
  now **composes** an optional `ChannelIndex` (`index=True` → daemon ingest+query, or a warm-index test
  that writes + indexes through one object); `list_channel_info` / `get_channel_schema` / `query` /
  `query_registry` / `ingest_batch` delegate the index half, and the producer write path feeds it when
  present. The `_registry` duality resolved cleanly — a producer populates it via `_register`, the daemon
  via the index's `absorb_descriptor`; never mixed on one object. **Two decisions taken at the gate
  (2026-06-15):** ① **split-now / flip-later** — the `event_log` optional→required producer flip (DI
  contract, decided 2026-06-14) has a ~51-site blast radius (9 bench/example producers + 42 bare write
  tests, incl. `run_concurrency` the scaling bench drives); the split is its *precondition*, not a same-
  commit coupling, so `event_log` stays optional this wave and the flip + a test/bench EventLog factory is
  the focused follow-on. ② **defer naming** — the public class stays `ChannelStore` (the "Store" =
  cross-session corpus rename rides P6's dedicated naming pass). **Proven behavior- and throughput-neutral**
  via `scripts/bench_channel_scaling.py` (write / write_many / stream × 1/2/4 writer processes): baseline
  vs after held within WSL2 run-to-run variance (write_many ~197k→214k/s 1w, stream ~200k/s 1w; aggregate
  holds/climbs with writers; scaling factors unchanged). Full gate green (pyright + suite).
- [x] 3 — will + spine-only reaper — **LANDED** (Wave D: `180e6d1` will/SessionOptions, `a3a185a`
  stream auto-checkpoint, `4463b1d` events-daemon session reaper, this commit run-timeout→900). See the
  "Wave D — P3 liveness" design+plan section above for the locked model.
- [x] 4 — terminal finality + cascade — **LANDED** (Wave E). **Escalation gate dissolved, not decided:**
  instrument locks aren't *owned* by the event log, they're *reported* to it — a lock is held by the
  acquiring process (`InstrumentPool`, OS-`flock`), the spine only records its lifecycle. By the same
  logic open streams (file handles) are reported too. So the session cascade owns only its own
  producer-local stores (closed on clean exit — Wave B; gone with the process on crash); **locks/streams
  are out of it entirely** — pid/pool-owned, OS-released, a lock can outlive a session (a process drives
  an instrument across many sessions). That collapsed E to the **terminal fence**: the events daemon
  rejects post-seal PRODUCER writes (revival) in the put-hook, but lets **`derived`** completions through
  — a run's async `RunMaterialized` and a reaper `RunEnded` carry `derived=True` and still land after the
  session seals (the bug this avoided: a blanket fence would kill `RunMaterialized`). `derived` moved to
  `EventBase` (the fence discriminator); sealed-session set loaded from the durable index + updated per
  `SessionEnded`; cheap fast-path (only parses `json` when a row targets a sealed session). No new
  `SessionExpired` event — the seal IS `SessionEnded`; a client-side typed exception is the optional
  follow-on. **No cascade, no store-teardown, no lock machinery** — the reframe deleted them.
- [~] 5 — envelope discipline + per-writer gap detection. **Gap detection LANDED** (`def605f`,
  2026-06-14): `_EventSequenceMonitor` in the runs daemon flags non-contiguous `event_offset` per
  `writer_key` (truncated/lost records) with a log + counter, no drop/block; no-ops on the column-less
  in-process path. Independently reviewed. Envelope-naming-discipline part still pending.
- [~] 6 — producer/reader split + first-class reader entry. **Split LANDED** (`f15a06b`): session-less
  `ChannelIndex` extracted, `ChannelStore` composes it. **Deferred:** the `event_log` required-flip (split-
  now/flip-later decision) + the public rename (defer-naming decision) + a first-class reader entry point.
- [ ] 7 — rename `StationConnection` → `Session`
- [ ] 8 — liveness projection → UI/MCP/HTTP

_On each completion: append a one-line "what landed" note + commit sha._

## Follow-on (sequenced after the session core)

- **Measurement storage redesign — JSON/semi-structured (tasks #37 + #38). SEQUENCED LATER:
  after this session overhaul, after the files branch, and once back on channels.** Surfaced
  during this work but pre-existing + unrelated to the session migration. Findings (all
  verified): today `out_*`/`in_*` are wide dynamic typed columns; a single run with mixed
  types in one column (`out_b: float,str`) **fails materialization and is silently dropped**
  (`_runs_duckdb_daemon.py:1552` swallows to a `logger.warning`) — a green CI still lost runs.
  Across files, `union_by_name` promotes mixed types to VARCHAR (verified: `1.5`→`'1.5'`), so a
  single varying run flips a column corpus-wide and breaks typed/Cpk queries. **Direction:**
  store `in/out/custom` as JSON/semi-structured — lossless, stable, swap-ready (maps natively to
  Snowflake VARIANT / Postgres JSONB / BigQuery JSON / DuckDB JSON; req-6 swap = dialect change).
  **Caveat (benchmarked):** raw JSON-path queries on DuckDB are **2.5× slower (distinct-enum) /
  8.9× slower (filter)** than typed columns — so the **parametric viewer** (fast input-condition
  filter + drop-down enumeration) needs a **typed derived index/projection over the JSON source**
  (the runs-daemon DuckDB index already is a derived projection — extend it). Plus **#37**: emit a
  durable `RunMaterializationFailed` event instead of swallowing, so no run is ever silently lost.
  **Also fold in: consolidate the ~14 wide instrument fields** (`step_instruments_*` parallel
  array columns, `_INSTR_ARRAY_TYPES` in `schemas.py`) into the same semi-structured representation
  — same wide-column smell, same swap-readiness win. Needs its own shaping pass. 0.2.0 breaking
  (wipe data, no backcompat).

- **Auto-capture station info at session creation (task #35).** At session open, automatically
  stamp richer STATION context onto `SessionStarted` (beyond station_id/hostname/type/location):
  instruments present + roles/resources, fixture, calibration/asset refs, a station-config
  snapshot — however much the process can provide. The session exists to provide context; richer
  auto-captured metadata lets us ask deeper questions of the events/data captured under it.
  Automatic in the session primitive (rides the P3 will), not hand-threaded per producer; degrades
  gracefully when a field is unavailable (bare script vs full station connect). Blocked on P2.

- **Emission-grammar consistency (NEW — surfaced this session).** Uniform, verb-keyed lifecycle
  events across all four stores: one `<Entity>Started`/`<Entity>Ended` grammar (converge
  `SlotCompleted`→`SlotEnded`, `ChannelClosed`→`ChannelEnded`, `RouteOpened/Closed`; fix
  `SyncArrived/Release` tense) + add the missing discrete **one-shot file-write event** (today a
  one-shot file PUT emits nothing). Noise/info balance: 2 events per span, 1 per discrete write,
  never per-sample. Needs a design pass before implementing. See Cross-store consistency.

- **Streaming-relay convergence (NEW — broken contract).** Channels and files duplicate the
  producer/consumer relay (bounded queue + drop-oldest overflow + gap count + drain-coalesce);
  `files/catalog_manager._FrameRelay` "mirrors the channel push relay" instead of reusing it (the
  channels refactor was supposed to be the shared mechanism files sit on — it wasn't). Extract ONE
  shared relay component; converge both. Daemons may stay separate; the optimization must not be
  duplicated. See Cross-store consistency.
