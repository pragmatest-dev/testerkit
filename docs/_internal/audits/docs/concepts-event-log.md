# Page audit: docs/concepts/event-log.md

**Quadrant:** Concepts/Explanation (event log as the source of truth, event-sourcing architecture)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 0 | 3 | 2 |
| Accuracy | 2 | 4 | 2 |
| Gaps | 0 | 3 | 3 |
| Cross-links | 1 | 2 | 2 |
| **Total** | **3** | **15** | **14** |

---

## Ordering findings

### WARNING — "Push Model" and "Storage" sections appear after the long event-category table dump
**Location:** lines 27–125 (Event Categories + Timeline) precede lines 127–151 (Push Model + Storage)

A Concepts/Explanation page should establish the *mental model* (what the event log IS, how data flows through it) before diving into a 70-line catalog of every event type. Today the page leads with "Why a Unified Event Stream" (good), then immediately drops into a Field-by-Field base table and an 11-category enumeration. By the time the reader gets to "Push Model: emit() → internal materializers" — the actual architectural mechanism this page is supposed to explain — they have already absorbed ~80 lines of reference material that belongs in `reference/event-types.md` (and is already duplicated there). Move the Push Model + Storage + Dual-Write + HARD-contract sections up before the Event Categories table, or trim Event Categories to a one-line-per-category summary and link out to the reference.

### WARNING — "Dual-Write Pattern" follows "Storage" but logically precedes it
**Location:** lines 137–159

"Storage" describes the Arrow IPC files on disk. "Dual-Write Pattern" then describes how the *same* events also land in DuckDB-via-Flight. The dual-write IS the storage story — splitting them suggests they are distinct concerns. Either merge them under one "Storage and queryability" heading, or order Dual-Write → Storage (the pattern explains why there are two parallel paths; the IPC layout is one of those paths).

### SUGGESTION — "Event Timeline" sits between two reference-style sections and breaks the flow
**Location:** lines 104–125

The ASCII timeline is the most *Concepts*-shaped content on the page (it shows the model in action). Yet it is sandwiched between the Event Categories tables and the Push Model section, so it reads as a tail-end addendum to the catalog. Either lead with it (after "Why a Unified Event Stream") as the canonical example of "what the event log looks like in practice," or move it down next to Push Model where it can illustrate the emit-and-dispatch flow.

### SUGGESTION — "HARD contract" section is buried at the bottom
**Location:** lines 161–187

The append-only-evolution contract is one of the most important architectural commitments the page makes — it justifies why event sourcing is viable in production. Placing it last (after Storage and Dual-Write) makes it read like a footnote. Promote it to a sibling of "Why a Unified Event Stream" so readers grasp the durability contract before they study individual event types.

---

## Voice findings

### WARNING — "Litmus's unified record" / "Litmus defines" — third-person product voice mixed with prose
**Location:** lines 3, 27

The page mostly uses architectural-noun voice ("The event log unifies all of this", "The `EventLog` class manages the write path") which is appropriate for Concepts. But the opener says "Litmus's unified record" and "Litmus defines events across 11 categories" — third-person product voice that reads marketing-adjacent. Replace with "The event log is the unified record …" / "Events fall into 11 categories" — keeps the architectural framing consistent.

### SUGGESTION — "It replaces earlier text-log + streaming patterns" reads as release-note history, not concept
**Location:** line 3

Concepts pages explain how something works *now*, not what it superseded. The "Why a Unified Event Stream" section that follows already does the comparative framing properly ("Previous approaches split test data across multiple systems"). The opening paragraph should describe what the event log is and does, not what it replaced. Drop the second sentence or fold it into "Why a Unified Event Stream."

### SUGGESTION — Inconsistent backtick treatment of event type strings
**Location:** throughout

Sometimes `session.started` is in backticks (line 174), sometimes the event class `SessionStarted` (lines 31, 60). The Event Categories tables put the class in plain backticks and the type string in backticks too, which works. But prose mentions toggle between styles (e.g. line 105 "`SessionStarted`" but line 152 "`EventLog`" vs unquoted "Arrow IPC" on line 139). Standardise: class names in backticks, event-type strings in backticks, file formats in plain text.

### SUGGESTION — "(defined but not currently in the `Event` discriminated union)" — parenthetical implementation note in a reference table
**Location:** line 40

This caveat is correct (verified — `RunMaterialized` is not in the union) but the *parenthetical* form weakens it. Either elevate it to a dedicated note under the Run table ("Note: `RunMaterialized` is emitted by materializers but is not yet part of the public `Event` discriminator. Treat it as semi-private in 0.x.") or move it to the HARD contract section as an explicit caveat. Burying a real semantic gap in a parenthetical is the kind of thing accuracy audits flag.

---

## Audience findings

### WARNING — Page assumes the reader already knows what "Arrow IPC", "claim-check", "WAL", and "Flight do_put/do_get" mean
**Location:** lines 80, 132, 139, 159, 163

Concepts pages should be readable by a test engineer who knows pytest and YAML but not the data-architecture stack. The page name-drops `BufferedIPCWriter`, "Arrow IPC files", "claim-check URIs", "Flight `do_put`", and "WAL" without ever defining them. `three-stores.md` defines "claim-check URI" inline; this page does not. Either add brief inline glosses ("Arrow IPC — Apache Arrow's on-disk batch format; see [flight-streaming](flight-streaming.md)") or push these terms into a `See Also` rather than using them inline.

### WARNING — "Internal materializers" / "EventSubscriber base class is internal scaffolding" — audience mismatch
**Location:** lines 127, 135

The Push Model section spends three lines telling the reader that `EventSubscriber` is *not* a public extension point and they cannot add a format without editing `litmus.data.exporters`. That is platform-developer information, not test-engineer information. A test engineer reading this Concepts page wants to know "events flow through subscribers that build the parquet and the live UI"; they do not need to be told they can't write their own subscriber. Move the "not a public extension protocol" note to the reference (or to an internal architecture doc) and replace the Push Model prose with a test-engineer-shaped explanation of what subscribers *do*.

### WARNING — HARD contract section reads as 1.0-release internals, not a concept explanation
**Location:** lines 161–187

The section is written in the voice of a platform maintainer telling other platform maintainers what they cannot change before 1.0: "Until the 1.0 cut, the following invariants hold and the project must not break them". For a test engineer or operator, this is irrelevant — they should be told "you can rely on these guarantees" not "the project must not break them." Reframe in the second person: "What you can rely on across 0.x releases: …"

### SUGGESTION — Event Categories tables are reference-shaped, not concept-shaped
**Location:** lines 29–102

A 70-line enumeration with per-event one-liners is reference material (and is already covered in `reference/event-types.md`). For a concepts page the right shape is "events cluster into ~11 categories — session lifecycle, run lifecycle, fixture wiring, test execution, instrument I/O, diagnostics, streaming, dialogs. See the reference for fields." Keeping the table here forces the reader to absorb implementation detail before they grasp the model.

### SUGGESTION — "EventLog buffers events, flushes as batched Arrow IPC writes" — drop into prose summary
**Location:** line 132

The reader does not need the `emit(event)` → `received_at` → buffer → batched IPC sequence at this level of detail. A two-sentence summary ("Every emit is dispatched synchronously to subscribers and asynchronously batched to durable storage. Batch size is tunable; the default flushes every 50 events.") is plenty for Concepts; the line-by-line lifecycle belongs in reference.

---

## Accuracy findings

### CRITICAL — `LiveRunsSubscriber` does not exist in the codebase
**Location:** line 132

The Push Model section claims "`ParquetSubscriber` for the canonical run parquet, `LiveRunsSubscriber` for the in-daemon ingest path." Verified:

```
$ grep -rn "class LiveRunsSubscriber" /home/ryanf/repos/litmus/src/
(no matches)
```

The runs daemon (`src/litmus/data/_runs_duckdb_daemon.py`) uses an `AccumulatorPool` (`src/litmus/data/_accumulator_pool.py` line 71), driven directly by the daemon's event consumer — there is no class named `LiveRunsSubscriber`. The name appears only in `docs/` (this page, `concepts/why-event-sourcing.md`, `reference/outputs.md`, and several `_internal/explorations/` files). This is the same class-name in three audience-facing docs. Fix: replace with the actual mechanism — "the runs daemon's `AccumulatorPool`, fed by its event watcher" — or describe by role rather than class name.

### CRITICAL — Storage layout in "Storage" section omits the `-{pid}` and segment suffix
**Location:** lines 139–148

The page shows storage as:

```
results/events/
├── 2026-03-10/
│   ├── {session_id}.arrow
```

Verified against `src/litmus/data/event_log.py` line 178:

```python
path=date_dir / f"{session_id}-{os.getpid()}.arrow",
```

and `_EventIPCWriter.path` (line 84) which rotates segments to `{session_id}-{pid}_{NNNN}.arrow`. The module docstring (line 9) correctly states `{session_id}-{pid}[_{segment}].arrow`. The page's tree is wrong on two counts: it omits the `-{pid}` (which is the entire reason "concurrent orchestrator + worker processes never clobber each other" works) and the segment rotation. The same incorrect path appears in `concepts/three-stores.md` line 19, but this page is the deeper-dive page and should be authoritative.

### WARNING — "IPC flush happens every 50 events (configurable)" — direction of "configurable" is unclear
**Location:** line 133

The `_DEFAULT_FLUSH_THRESHOLD = 50` is configurable per-EventLog via the `flush_threshold` constructor arg, but there is no user-facing config for it (no env var, no `litmus.yaml` key — verified). "Configurable" suggests the user can tune it; in practice it is a constructor-arg knob used internally. Either add the actual lever ("internally tunable via `EventLog(flush_threshold=...)`") or drop "configurable."

### WARNING — "On each flush, batches are pushed to the DuckDB daemon via Arrow Flight `do_put`" — true but understates the queryability path
**Location:** line 159

The dual-write description says queries go through Flight `do_get` for read-after-write consistency. Verified in `event_store.py` lines 247–263 (`flush()` calls `_put_stream.drain()` to wait for daemon ack). But the page does not mention that `events()` *itself* calls `log.flush()` and `drain()` on every query call (lines 290–297) — that is the actual mechanism that guarantees read-after-write. Worth saying explicitly: "Reads call flush+drain before querying, so a caller sees their own writes."

### WARNING — "11 categories" but the source's `ALL_EVENTS` aggregates 10 category constants
**Location:** line 27

The page enumerates 11 sections (Session, Run, Slot, Sync, Route, Fixture, Test, Instrument, Diagnostic, Stream, Dialog). The source defines 10 category constants in `events.py` lines 667–682 (`SLOT_EVENTS` merges Slot + Sync). This is a small framing difference — both are defensible — but the doc claim "Litmus defines events across 11 categories" is wrong if read against `ALL_EVENTS`. Either drop the count or clarify "events fall into 11 functional groupings."

### WARNING — Base fields claim `id`, `occurred_at`, `received_at`, `session_id`, `run_id` are universal — but `received_at` defaults to `None`
**Location:** lines 14–22

The table calls `received_at` a `datetime` field, but `EventBase` (line 50 of `events.py`) defines it as `received_at: datetime | None = None  # Set by EventLog.emit()`. An event constructed in isolation has `received_at = None`. The table should say `datetime | None` or note that it is only stamped after `emit()`.

### SUGGESTION — `RunMaterialized` lifecycle and the eviction handshake are not mentioned in the page proper
**Location:** line 40

`RunMaterialized` gets a one-line entry in the Run table plus a parenthetical that it is not in the discriminated union. The actual semantics — the materializer pool evicts the run, retention can prune events — are in the `events.py` docstring (lines 226–238) and the `_internal/explorations/data-architecture.md` file. The Concepts page is the natural place for this since it is what makes "materialized view" mean anything concrete. Either bring the eviction-handshake explanation into the page or remove `RunMaterialized` from the table entirely until it is part of the public contract.

### SUGGESTION — "`InstrumentRead` … (scalars inline, arrays as claim-check URIs)" — verified but worth a one-line "why"
**Location:** line 81

The serializer logic is in `events.py` lines 541–586. The reader is told "scalars inline, arrays as claim-check URIs" with no explanation of *why* — keeping the JSON column compact (line 530). One sentence would close the gap: "Large arrays are written to ChannelStore; only a URI reference travels in the event log, so the events column stays compact and queryable."

---

## Gaps findings

### WARNING — No mention of how `event_number` (the monotonic cursor) is assigned
**Location:** absent (HARD contract mentions monotonicity at line 178 but does not say where the number comes from)

The page tells the reader that `event_number` monotonicity is part of the contract and is used by watcher cursors. It does not explain that the events daemon stamps it via `nextval('event_seq')` under the put-hook lock (verified in `_duckdb_daemon.py` lines 65–77). For a Concepts page on the event log, "how do live subscribers know what's new without races" is exactly the kind of question that deserves an answer. Add a short paragraph: "Live subscribers track an `event_number` cursor — the events daemon assigns it under the same lock that commits the row, so a strictly-monotonic insert order is guaranteed even when wall-clock timestamps overlap across processes."

### WARNING — No mention of cross-process file watching / live subscription mechanism
**Location:** absent

The page describes the *write* path (emit → buffer → flush → dispatch) but never explains how a different process (the operator UI, a CLI) sees those events live. `event_store.py` lines 547–644 implement a polling Flight watcher that picks up `event_number > cursor` rows every 500ms. This is the core of "live monitoring works" — a real concept, not an implementation detail. The reader is told "Subscribers process events for their own purposes — writing Parquet files, updating the UI" but never shown the mechanism that gets events from one process to another.

### WARNING — No mention of how a crashed producer's events are handled (orphan finalization)
**Location:** absent

`why-event-sourcing.md` line 26 mentions "orphan-finalization path emits a synthetic `RunEnded(aborted)`" and `three-stores.md` line 45 mentions a "close-time fallback writes whatever rows reached the materializer with `run_outcome = aborted`." The event-log page should at minimum say that crash safety is a property of the IPC append-only format: events written before the crash are durably on disk and can be replayed. As written, "Arrow IPC file — crash-safe append-only storage" (line 156) is the only nod to this, with no follow-through.

### SUGGESTION — No mention of the `_ref/` sidecar subdirectory's purpose
**Location:** line 145

The storage tree shows `{session_id}_ref/` with the comment "Large data (waveforms, images)" but never explains the role: this is the on-disk landing for `EventLog.save_ref()` calls (verified `event_log.py` line 247), which is how subscribers stash payloads too large for the JSON column. A one-line gloss in the surrounding paragraph would close the loop.

### SUGGESTION — No mention of `EventStore.get_shared()` and the process-wide-shared pattern
**Location:** absent

`event_store.py` lines 116–129 establish that UI page handlers should use `EventStore.get_shared()` so the watcher thread count stays flat. This is a usability-shaping detail of the event store and worth surfacing in a Concepts page that already describes the dual-write pattern. Without it, a reader who skims the page and writes `EventStore()` everywhere will (correctly) wonder why they have a thread-per-page.

### SUGGESTION — No mention of retention / event eviction
**Location:** absent (the `RunMaterialized` parenthetical hints at it but does not explain)

`event_store.py` references retention pruning and `data/retention.py` exists. The Concepts page should at least mention that the event log is not unbounded — events persist until retention prunes them, gated by `RunMaterialized`. Otherwise readers reasonably assume the WAL grows forever.

---

## Cross-links findings

### CRITICAL — Page is missing a link to `concepts/why-event-sourcing.md`, its direct conceptual prerequisite
**Location:** lines 189–193 (See Also)

`why-event-sourcing.md` opens with "The companion pages cover the *what*: see [Three Stores Architecture] and [Event Log Architecture]." That page is the *why* and links to this one. This page is the *what* and links to three-stores and sessions and event-types — but not back to `why-event-sourcing.md`. Readers landing on event-log first should be directed to the why-page; readers who navigated from the why-page get no return link. Add: `[Why event sourcing](why-event-sourcing.md) — why the log is the source of truth in the first place`.

### WARNING — "API stability framing" link points to an `_internal/explorations/` page
**Location:** line 186

The HARD contract section links to `../_internal/explorations/api-stability-and-versioning.md`. The path `_internal/` is by convention not user-facing — it's exploration / scratchpad material. Either promote that page to a real `concepts/` or `reference/` doc, or drop the link from a public page. Linking the audience-facing page into internal explorations leaks the boundary.

### WARNING — Inbound links target this page from tutorial and step-manifest, but the page itself has no outbound link back to those entry points
**Location:** See Also section, lines 189–193

Verified inbound references:
- `docs/tutorial/index.md` line 19 links here as "[events](../concepts/event-log.md)"
- `docs/concepts/step-manifest.md` line 117 links here as "[Event log] — how events get to Parquet"
- `docs/concepts/why-event-sourcing.md` lines 5, 69 link here twice

But the See Also section only routes to three-stores, sessions, and event-types — it does not surface step-manifest (which is closely related: steps emit events) or the live-monitoring tutorial. Add at least: `[Step manifest](step-manifest.md) — events emitted per step` and `[Live Monitoring tutorial](../tutorial/10-live-monitoring.md)`.

### SUGGESTION — "Flight Streaming" is mentioned in `three-stores.md` for `do_put`/`do_get` glossing, but this page references the same terms without that link
**Location:** lines 132, 159

When the page uses `do_put` and Flight terminology, it could link `flight-streaming.md` inline the same way three-stores.md does (line 17). Saves the reader from having to chase down the terminology.

### SUGGESTION — "EventStore" is named in the Dual-Write section but linked nowhere
**Location:** line 154

`EventStore` is a real public API (described in `event_store.py` docstring line 10–19). The Concepts page mentions it but does not link to a reference (e.g., the API reference or `three-stores.md` which describes it). Add either `[EventStore](three-stores.md#eventstore--source-of-truth)` or a reference link.
