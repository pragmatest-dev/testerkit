# Ideal Data Architecture — Living Design Notes

**Status:** WIP. Notes from an architectural discussion, not a committed direction. Update as decisions firm up.

## Why this document exists

The current Litmus data layer is built on correct primitives (Apache Arrow + Parquet + Arrow IPC + Arrow Flight + DuckDB) but assembled in a way that has drifted from both:

1. What the docs already claim (`docs/concepts/three-stores.md`: "Parquet files are a materialized view of the event stream")
2. What the design philosophy demands (local-first, customer-owned files, vendor-neutral integration)

The drift shows up as:

- Producers write parquet files directly. Daemons are passive indexers, not writers. There is no API boundary between them — discovery is `rglob("*.parquet")`, schema agreement is "we both import the same Pydantic models," mismatches → file quarantined.
- Events and artifact storage can disagree. The orphan sweep writes parquets but doesn't emit `RunEnded`, so the events DB grows zombies forever even when the parquet side is consistent.
- "Files as a local database" is delivered without the metadata layer that makes files-as-a-database actually integrate with downstream systems. Bare parquet ≠ a table to Snowflake / Databricks / Athena.
- Bulk channel data, audit events, and persisted artifacts have different design pressures and shouldn't share one writer pattern.

This document captures the design that emerges when the architecture is re-derived from the principles.

## Design principles

1. **Keep it simple** — small surface area, readable, debuggable.
2. **Open source** — every layer replaceable by the customer; nothing proprietary on the critical path.
3. **Easy for non-IT to administer** — no DBAs, no services to tune. The customer's mental model is "I have my data and can copy it around."
4. **Performant** — kHz live data plane today, with a path to MHz when needed.
5. **Local-first; centralize later** — same bytes, different topology. Local FS → shared NAS → S3 → cloud catalog as a smooth growth path. **Hard form: some test machines are air-gapped or offline by policy** (defense, aerospace, regulated industries, secure labs, manufacturing floors with no outbound network). The architecture must function indefinitely with no network, no cloud catalog, no phone-home — that's a correctness constraint, not a preference.
6. **Integrate with what customers chose** — Snowflake, Databricks, BigQuery, S3, GCS, customer-internal stacks. Don't write N integrations; emit one open table format and let downstream tools read it.
7. **Don't reinvent; use existing ecosystems** — inherit improvements from upstream communities for free.

## Three roles, all persistent

The architecture has three distinct data roles, each with its own design pressures. **All three persist to disk** — the distinction is *what they're persisted for*, not whether they're persisted.

|  | Event log (events) | Channel data (channels) | Artifact tables (runs/steps/measurements) |
|---|---|---|---|
| Question it answers | "What happened, in detail, in order?" | "What did the instruments record?" | "What's the result of this run, ready for analysis?" |
| Audience | Operator UI, slot orchestrator, debug, audit, crash recovery | Live operator UI (decimated), archive consumers, analysis tools | QA, yield, audit, cloud warehouses, customers, regulators |
| Velocity | Low (typed records, ms-rate) | High (kHz–MHz waveform samples) | Low (one crystallization per `RunEnded`) |
| Persistence | Arrow IPC files (durable, local) | Arrow IPC files (durable, local, segment-rotated) | Lakehouse table format (parquet + metadata) |
| Ships to integration boundary? | **No** — internal nervous system, local-only | **Yes** — claim-check'd from artifact tables, ships as part of bundle | **Yes** — primary integration boundary; what cloud warehouses read |
| Mutability | Append-only | Append-only segments | Append-only at run grain (atomic snapshots) |
| Schema | Tight (typed Pydantic events) | Per-channel Arrow schema, inferred at first write | Wide (denormalized rows ready for analytics) |
| Right substrate | Arrow IPC + DuckDB + Arrow Flight (bespoke) | Arrow IPC + Arrow Flight (with mmap fast-tier when needed) | Lakehouse table format (Delta Lake leaning) |

Trying to make one storage stack serve all three was the source of today's drift. They are different problems and deserve different layers.

## Architecture (target shape)

```
                     ┌────────────────────────────────────────┐
                     │         EVENT STORE (the spine)        │
                     │  Arrow IPC durable log                 │
                     │  DuckDB index for queries              │
                     │  Flight RPC: emit / subscribe / query  │
                     │                                         │
                     │  Source of truth for everything that    │
                     │  happened. Many publishers, many        │
                     │  subscribers. Open: any process joins.  │
                     │                                         │
                     │  Persistent (Arrow IPC files), but      │
                     │  scoped to the local nervous system —   │
                     │  not part of the customer artifact.     │
                     └──────────▲─────────────────────▲────────┘
                                │ emit                │ subscribe
       ┌────────────────────────┼─────────────────────┼─────────────────────┐
       │                        │                     │                     │
PUBLISHERS (any process)        │                     │           SUBSCRIBERS (any process)
─────────────────────────       │                     │           ──────────────────────────
pytest plugin    ──── emit ─────┤                     ├─ subscribe ── live operator UI
station connect  ──── emit ─────┤                     ├─ subscribe ── artifact-table writer
slot orchestrator ─── emit ─────┤                     ├─ subscribe ── CSV/STDF/HDF5/ATML exporters
multi-DUT runner ──── emit ─────┤                     ├─ subscribe ── transport queue (S3/Snowflake)
custom CLI       ──── emit ─────┤                     ├─ subscribe ── slot coordinator
fixture script   ──── emit ─────┘                     ├─ subscribe ── cross-station replicator
                                                      ├─ subscribe ── audit / compliance log
                                                      └─ subscribe ── (any future subscriber)

high-velocity bulk data
─────────────────────
producer instrument reads ────► CHANNEL STORE
   (kHz–MHz waveforms)           ─────────────
                                Arrow IPC + Flight (network tier)
                                Arrow IPC + mmap (same-host fast tier, future)
                                Segment rotation
                                LTTB decimation taps for live UI

                                Persistent (Arrow IPC files);
                                ships as part of the artifact bundle
                                ▲
                                │ returns channel:// URI
                                │
                                └─ producer embeds URI in MeasurementRecorded event

                                Lifecycle hook: ChannelOpened / ChannelClosed
                                events on the event bus (so subscribers can
                                discover channels without polling)


persisted artifacts (target shape)
─────────────────────────────────
                                ARTIFACT TABLES (proposal: Delta Lake)
                                ───────────────────────────────────────
artifact-table writer subscribes to event store; on RunEnded, commits one
atomic snapshot to:
   runs/_delta_log + runs/*.parquet
   steps/_delta_log + steps/*.parquet
   measurements/_delta_log + measurements/*.parquet

Customer ships the artifact bundle (Delta tables + channel files) anywhere.
Snowflake/Databricks/BigQuery/etc. read the Delta tables natively (or via
UniForm-as-Iceberg). Channel _refs resolve via relative paths.
```

The event store is the only thing producers and subscribers agree on. Producers don't know who's listening. Subscribers don't know who's emitting. New subscribers can be added by any process at any time without touching producers. That is the "open architecture" property.

## Finalized decisions

When a decision is settled, move it here from "Outstanding decisions" with a short rationale. Each entry: what was decided, why, and (if relevant) date or PR.

> Nothing here is yet *implemented* — these are agreed-upon design intents. Implementation tracks separately (per-spec PRs, RFCs, etc.).

### FD-1. The architecture has three persistent roles, not two layers.

Event log, channel data, and artifact tables are three distinct roles. **All three persist to disk.** The differences are velocity, audience, and whether they ship to the customer's integration boundary. Don't try to make one substrate serve all three.

**Why:** The previous "live vs persisted" framing was wrong — events and channels persist via Arrow IPC files just like the artifact layer persists via parquet. The right cut is by *role*, not by durability.

---

### FD-2. The event store is the right pub/sub approach for Litmus.

The event store as today's design (typed Pydantic events, in-process and cross-process subscribers, append-only) is the right shape for the live nervous system. Many publishers, many consumers, open architecture (any process can join), supports multiple message types and live subscriptions.

**Why:** This is the architecture that supports test-platform-shaped problems — coordination across pytest, station REPLs, slot orchestrators, custom CLIs, and operator UIs all on the same machine. The pattern works.

---

### FD-3. The channel store is the right approach for high-velocity time-series.

Per-channel Arrow IPC writers with segment rotation, claim-check URIs in events, live subscriptions via callback + Flight, decimation taps for UI consumers. Don't replace.

**Why:** kHz waveforms don't fit the event log (would flood it) and don't belong in tabular artifact rows (too bulky). The claim-check pattern is the right separation: lifecycle on the event bus, bulk data in dedicated segment files. Arrow-native end-to-end.

---

### FD-4. Both stores stay on Apache Arrow + Flight.

Event store and channel store both use Arrow IPC for durability and Arrow Flight for cross-process subscription. Don't migrate either to NATS / Kafka / ZeroMQ / Aeron.

**Why:** Arrow Flight is the standard substrate for Arrow-native local-first cross-process streaming. Arrow-nativity is the load-bearing property — it's why DuckDB / pandas / Polars / any Arrow-aware tool reads the IPC files directly with no codec. The bespoke parts of the substrate (per-subscriber fan-out, lifecycle hooks) are small additions on top of a correct foundation. Choosing this satisfies principles 1, 2, 4, and 7 simultaneously.

**Reopen if:** sub-µs determinism becomes a hard requirement (would push to Aeron-class), or multi-station live federation requires cross-host pub/sub semantics that Flight doesn't make easy. See OD-6 and OD-5.

---

### FD-5. Both stores get a short-window replay capability for late-join subscribers.

Subscribers that join after a stream has started — most importantly UIs — must be able to catch up to recent state without missing the live tail. Implement a small-window replay (last N seconds, or last K events / samples) on both the event store and the channel store. Sufficient for "operator opens UI mid-run and sees the last 30 seconds without poll-and-merge gymnastics."

**Why:** This is the gap that closes "subscriber rejoins live" cleanly. Today the partial path (`replay="active_runs"` for events; segment-file scan for channels) works but is awkward; a small window is enough for UI use cases without the full operational cost of NATS JetStream-grade durable replay.

**Out of scope for this decision:** unbounded / durable replay for archival or compliance purposes. That's a different problem and stays under OD-6.

---

### FD-6. The sweep emits `RunEnded` as the immediate stopgap.

The narrow change to `_sweep_once` to emit a synthetic `RunEnded` alongside the existing `_write_orphan_parquet` makes today's two-writer architecture self-consistent. It does not conflict with the eventual structural realignment; it just makes the current layer correct in the meantime.

**Why:** Land the small fix first to stop the zombie accumulation; do the structural migration as its own initiative without the bug fix held hostage to architectural negotiation. Approved during the plan-mode discussion that originated this design conversation.

---

## Outstanding decisions / open work

Living list. Each item has the question, options, current lean (if any), and what would tip the decision. Update as discussions happen.

### OD-1. Parquet writer location: in-process producer vs daemon-side

**The question.** Today the producer's `ParquetSubscriber` writes the parquet on `RunEnded` synchronously, in-process. The daemon's `LiveRunsSubscriber._sweep_once` is the only daemon-side writer and only fires for orphans. The "two writers, two code paths" structure is the source of the events-DB-vs-parquet inconsistency. Where should the canonical writer live?

**Options.**
1. **Status quo + the FD-6 stopgap.** Producer keeps writing the happy-path parquet sync; daemon's orphan path stays. Sweep also emits `RunEnded` so events DB closes. Two writers stay.
2. **Daemon-only writer.** Producer stops registering `ParquetSubscriber`. Daemon's `AccumulatorPool` becomes a `ParquetSubscriber` and writes on `RunEnded` (real or sweep-synthesized) for *all* runs. Single writer. Producer exits as soon as `RunEnded` is acked.
3. **Daemon-only writer + sync ack.** Like option 2, but the producer blocks on session end until the daemon confirms the parquet is written, preserving the file-ready-on-exit invariant for reports.
4. **Hybrid.** Producer keeps writing belt-and-suspenders for the happy path (preserves invariant, fast for reports). Daemon also writes on `RunEnded` events, deduplicated by `run_id`. Some redundancy in normal operation.

**Current lean.** Strong lean toward option 3 (daemon owns writes; producer waits for ack on session end), because it's the precondition for atomic Delta-table commits and collapses the orphan path into the normal path. But the file-ready-on-exit invariant for `run_configured_outputs` (reports) needs OD-2 resolved first.

**What would tip the decision.** Resolving OD-2.

---

### OD-2. Where do reports run?

**The question.** `run_configured_outputs` (`pytest_plugin/__init__.py:388`) runs reports synchronously after `RunEnded` on the producer process, requiring the parquet to be on disk before pytest exits. This is the load-bearing reason the producer is a writer. If reports moved off the producer's lifecycle, the writer could move to the daemon cleanly.

**Options.**
1. **Reports stay in producer.** Producer must continue to have access to a written parquet on session end. Couples writer and reports to the producer process.
2. **Reports run async after producer exits.** A daemon-side or operator-invoked step picks up completed runs and renders reports. Changes when reports become visible to the operator.
3. **Reports run in daemon, synchronously triggered.** Producer fires a "render reports" RPC; daemon writes parquet, renders reports, returns. Producer waits.

**Current lean.** None yet. Industry research showed that "report file on disk synchronously at runner exit" is *not* an industry invariant — most systems split data (sync) from report (async). This option may be more open than today's design suggests.

**What would tip the decision.** A concrete look at how operators currently use the immediate-after-run reports. If they're consumed minutes later, async is fine; if they're shown on the operator screen on test completion, the latency budget shrinks.

---

### OD-3. Adopt Delta Lake (or Iceberg) for the artifact layer?

**The question.** Should the runs / steps / measurements tables become Delta Lake tables (or Iceberg)? Strong lean from the discussion, but not yet a finalized decision.

**Options.**
1. **Adopt Delta Lake** with UniForm-as-Iceberg for cloud-warehouse breadth. Files-only, no catalog required for local-first; "the directory IS the table"; native reads from Snowflake (via UniForm), Databricks, BigQuery, Athena, Trino, DuckDB, Polars.
2. **Adopt Apache Iceberg** with a local SQL catalog. More native cloud-warehouse support (especially Snowflake), more powerful schema evolution, slightly more spec surface to operate.
3. **Stay with bare parquet.** Simplest; fails principle 6 (integration) at scale beyond "rsync the directory."

**Current lean.** Option 1 (Delta + UniForm) for the local-first ergonomics that align with principles 1, 3, and 5. UniForm covers the integration breadth. But this is a substantial structural change; the team should agree before committing.

**What would tip the decision.** A specific customer wanting Snowflake / Databricks integration could move this from "lean" to "go." Or a maintenance/community-momentum signal that one format is consolidating clearly.

---

### OD-4. When to start the artifact-format migration

**The question.** If we adopt Delta (or Iceberg) per OD-3, when and in what order relative to other architectural work?

**Options.**
1. **Now (next).** After the current zombie-cleanup PR, start the migration as the next major architecture initiative.
2. **After the daemon-writer realignment.** Get to single-writer first (OD-1); then layer the new format on top.
3. **Wait until customer integration becomes a real product driver.** Defer until a customer concretely needs warehouse integration.

**Current lean.** Option 2. The daemon-writer step is a precondition for clean atomic Delta commits anyway; doing them in sequence is less risky than fusing them.

**What would tip the decision.** Resolving OD-1 (writer location) clears the way; resolving OD-3 (format) sets the target.

---

### OD-5. Channel lifecycle event emission

**The question.** `StreamStarted` / `StreamEnded` / `StreamFrameIndex` event types are defined in `events.py:584–599` but never emitted. Should we wire them up, and to what fields?

**Options.**
1. **Use the existing `StreamStarted/Ended` types** with their generic `stream_id` field; channel store maps `channel_id` to `stream_id`.
2. **Add channel-specific `ChannelOpened/Closed` event types** with `channel_id`, schema, units, instrument_role, etc. — more discoverable and self-describing on the bus.
3. **Skip event-bus lifecycle entirely**; rely on `_registry.json` polling.

**Current lean.** Option 2. Channel-specific event types are easier to subscribe to and self-document. The generic `Stream*` types feel speculative ("Phase 2+" comment in code).

**What would tip the decision.** Whether other "stream-shaped" things (non-channel data feeds, ML training data taps, etc.) want to share the same event types in the future. If yes, generic. If no, channel-specific.

---

### OD-6. Channel velocity ceiling: when do we add the mmap fast tier?

**The question.** At kHz, Arrow Flight + IPC handles the live-subscription path fine. At MHz × multi-channel × multi-slot, the gRPC serialization layer becomes a bottleneck. When do we add same-host mmap'd IPC reading as a fast tier?

**Options.**
1. **Build now.** Pre-build the fast tier so the architecture is ready when MHz becomes a customer requirement.
2. **Build when needed.** Wait for the first customer pain point or benchmark failure; the IPC files already exist on disk so the fast-tier addition is non-breaking.

**Current lean.** Option 2. Don't pre-optimize. The substrate is already MHz-capable for single channels with batching; multi-channel multi-slot can be measured and addressed if/when it bites.

**What would tip the decision.** A customer scope-trace use case at >1 MHz × 4 channels × 4 slots simultaneously, or any benchmark showing the live UI dropping samples.

---

### OD-7. NATS JetStream for the event bus (long-term option)

**The question.** Adopt NATS JetStream as the event-bus substrate, replacing today's Arrow IPC + DuckDB + Flight + custom watcher? Would give us full durable replay, durable consumers, and cross-host federation for free — beyond the small-window replay covered by FD-5.

**Options.**
1. **Stay bespoke (FD-4 + FD-5).** Current event store + small replay window is enough for current needs.
2. **Adopt NATS JetStream embedded.** Add `nats-server` (single binary) as a local service; gain unbounded replay / clustering / first-class backpressure; lose Arrow-nativity in the wire format.

**Current lean.** Option 1 for now (per FD-4). NATS is heavier than the problem currently requires.

**What would tip the decision.** Multi-station live federation as a real product requirement, durable late-join replay beyond the small window of FD-5 becoming a customer-visible feature, or recurring maintenance cost on the bespoke event store.

---

### How to use this section

- When a question is settled, move it to the "Finalized decisions" section above with a short rationale and delete the entry here.
- When a new architectural question surfaces during implementation, add an entry rather than letting it stay implicit. Even a one-liner with no options yet is better than nothing.
- Each entry should have: the question, the options, a current lean (or "no lean yet"), and what would tip the decision. The last item is the most important — it's how we'll know we're ready to decide.

## Reference comparisons (where Litmus belongs in design space)

- **SQLite / DuckDB** — single-file local DB, customer-owned, no growth-to-server path. Litmus rejected this for reasons 5 and 6.
- **Postgres / MongoDB / DynamoDB** — server-owned writes, vendor lock-in feel, anathema to Litmus's target user. Rejected.
- **Apache Iceberg / Delta Lake / Hudi** — "files in object store + transaction log + (optional) catalog." Designed *exactly* for Litmus's principles. Delta is the local-first member of this family. Strong lean as the artifact-layer answer (see OD-3).
- **Kafka / Pulsar / EventStoreDB** — server-based event log. Rejected for the live event bus on principle 3 (admin) and principle 5 (local-first).
- **Apache Arrow Flight** — Arrow-native cross-process RPC. The right substrate for Litmus's bespoke pub/sub. **Already in use; affirmed (FD-4).**
- **NATS JetStream** — pub/sub with durable replay, single-binary embed, cluster-able. Worth considering for the event bus if/when bespoke maintenance becomes a cost. See OD-7.
- **ZeroMQ / Aeron / LMAX Disruptor** — high-velocity messaging. Wrong tradeoff for Litmus (lose Arrow-nativity, gain latency Litmus doesn't need).

## How each role migrates from local-first to central-server

Principle 5 says "start local, centralize later." The three roles have *different* migration shapes — each role's centralization story is a function of why it was local in the first place.

### Migration table

| Role | Local-first today | Central-server world | Why it migrates that way |
|---|---|---|---|
| **Event log** | Local Arrow IPC + DuckDB + Flight, per machine | **Stays local per station.** Optional: central observer subscribes to each station's Flight for live cross-station UI. Optional: events durably replicated (per-station archives, or to a central log) for federation/audit. | Live nervous system. Centralizing it breaks latency and creates a single point of failure for operator UIs. Each station's live ops must keep working when the network is down. |
| **Channel data** | Local Arrow IPC + Flight, per machine | **Stays local for live.** Archives to central object store as part of the artifact bundle on `RunEnded`. Optional: live-replicate to a central observability service for "watch all stations' scopes" use cases. | High-velocity bulk data wants locality. Streaming kHz–MHz waveforms to a central server continuously is wasteful and fragile. Crystallized end-of-run archive is where the central copy makes sense. |
| **Artifact tables** | Local parquet (today; Delta target) | **Migrates to shared object store** (Delta on S3 / GCS / Azure Blob). Multi-station writers commit to a shared Delta path; cloud warehouses (Snowflake/BigQuery/etc.) read directly. | This *is* the integration boundary. Centralization is the goal here. Customer ownership preserved — it's still their bucket, still their files. |

### The architectural property that makes migration work

**Migration is additive, not destructive.** Each station keeps its local stores fully functional even after centralization:

- Local event log keeps working — offline operation continues during network outages
- Local channel files keep working — live UI never depends on a central service being up
- Local artifact tables can stay the authoritative copy on the station; the central copy is an additional commit destination (or the only one, depending on customer's deployment shape)

Customer always owns the local files. The centralized copies (in S3 / cloud warehouses) are *additional* surfaces, not replacements.

> "Local-first; centralize later" structurally means "**add central as a tier on top of local**," not "migrate from local to central."

### What changes architecturally when centralization arrives

- **Multi-station shared Delta needs a coordinator.** Local-FS atomic rename works for single-writer; S3 needs DynamoDB LogStore or a REST catalog (Polaris / Nessie / Glue / Unity) for multi-writer atomicity. This is "centralize later" infrastructure; not needed for local-first.
- **Channel `_ref` URIs need consistent resolution rules at scale.** Local refs use relative paths (`channels/2026-05-06/scope.ch1.arrow`). Central refs need either preserved relative paths under per-station prefixes (`s3://bucket/station-1/channels/...`) or rewriting on archive. The claim-check pattern survives either way; the resolution rule needs to be explicit.
- **Cross-station event federation, if needed**, is layered on top of local event stores — not implemented by replacing them. A central subscriber attaches Flight clients to each station's event-store endpoint; a federated UI sees all stations' streams unified.

### What doesn't migrate

- **The local daemon stays per-station.** It serves local UI and manages local state. A central server runs *its own* daemon-equivalent over the centralized tables; central is not a replacement.
- **Local in-process subscribers** (operator UI, debug tools, slot orchestrator) stay attached to the local event store. They don't reach across stations unless explicitly federated.
- **Producer code is unchanged.** The pytest plugin / station REPL / slot orchestrator publish to the *local* event store. They never know whether they're in a single-station or multi-station deployment.

The migration is tractable because producers and most subscribers don't change; central surface is added as a new subscriber tier.

### The air-gap constraint (hard form of local-first)

Some test machines are air-gapped or offline by policy (defense, aerospace, regulated industries, secure labs, manufacturing floors with no outbound network). Reinforced by principle 5's hard form: the architecture must function **indefinitely** with no network, no cloud catalog, no phone-home.

This rules out architectures that *require* a central service to work and constrains every decision in this doc:

- **Event store** must function locally with no remote dependency — affirmed (FD-2, FD-4).
- **Channel store** must function locally — affirmed (FD-3, FD-4).
- **Artifact tables** must commit locally without contacting a remote catalog. This is a strong argument for Delta over Iceberg (OD-3): Delta's "the directory IS the table" mode requires zero external services; Iceberg in pure local mode requires a SQL/Hadoop catalog file but is air-gap-compatible too. Both work; Delta is simpler.
- **Customer artifact transport** is operator-driven, not required for operation. Air-gapped sites can `tar` the artifact directory and physically move it (USB stick, sneakernet, approved data diode) — the bundle is self-contained and re-readable on the receiving end without contacting Litmus or any cloud service.
- **Cloud-warehouse integration** (Snowflake / BigQuery via UniForm-as-Iceberg) is opt-in for the cloud-connected sites. It's a property of the artifact format, not a runtime dependency for local operation.
- **No license server, no telemetry, no auto-update** on the critical path. If something needs to phone home for the test rig to function, the architecture has failed the air-gap constraint.

This constraint also rules out NATS-style centralized brokers as the *primary* event bus (OD-7) — even though NATS can run embedded, the moment its design pulls toward "single shared bus across machines" you've started fighting the constraint. It stays a future option for cloud-connected fleets, never a default.

## Next steps to add to this document

- Detailed write/read sequence diagrams once OD-1 (writer location) is settled
- Schema mapping: which event types → which artifact-table columns (tied to OD-3)
- Migration plan: how to evolve from today's two-writer to single-daemon-writer (tied to OD-1 + OD-3 + OD-4)
- Channel ref resolution semantics in distributed/cloud deployment (S3 paths, etc.)
- Operational concerns: what does customer admin actually look like (table GC, retention, migration commands)
- Replay-window sizing: how much history does FD-5 cover for events vs channels?
