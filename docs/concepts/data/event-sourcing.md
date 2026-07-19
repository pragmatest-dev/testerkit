# Event sourcing

The platform stores test execution data as an immutable, append-only log of events. The queryable Run / Step / Measurement views (parquet, DuckDB index) are *views built from* that log — derived from the events, rebuildable at any time, never the source of truth themselves.

The companion pages cover the **what**: [Event log](event-log.md) for the event-type taxonomy and dispatch; [Data stores](data-stores.md) for the on-disk layout of events, channels, files, and the run parquet. This page covers the **why** — what a traditional CRUD test-result schema (one where you create a run row and update it in place) forces on a platform, and what changes when you flip the model.

## Why one mutable row is awkward for a live run

A traditional test-result schema models a Run as a single row, with steps and measurements as child rows in a hierarchy. To record what happened, you have one of two options, each with a real cost:

**Option A — UPDATE-style.** Insert the run row at start (with `outcome=NULL, ended_at=NULL`), then UPDATE both fields when the run finishes. Simple, but updating a live row in place brings race conditions when something reads it mid-update, audit-trail difficulties (when did the row become `passed`?), and a category of bugs where consumers see partially-updated state.

**Option B — Post-processing stance.** Buffer everything in memory until the run ends, then INSERT the whole hierarchy in one transaction. No live visibility during the run; total data loss if the producer crashes mid-flight.

Both shapes force the schema to be the source of truth, which means one row has to represent both "the run is in progress" and "the run is finished" — and that's what forces the in-place updates.

## The inversion: events as primary, runs as projections

TesterKit emits a separate immutable event for each thing that happens: `RunStarted`, `StepStarted`, `MeasurementRecorded`, `StepEnded`, `RunEnded`. The event log is the source of truth. The Run/Step/Measurement views (parquet, DuckDB index) are derived projections — regenerable from the events at any time.

Most data systems treat the entity (Run) as primary and the audit log (events) as a secondary concern. TesterKit inverts that: events are primary, the entity is a projection. That single inversion is what sidesteps the whole problem. Nothing ever needs an UPDATE because the view isn't authoritative — it's rebuilt from the events.

## Properties that fall out

- **Live visibility during a run.** The UI subscribes to the event log and surfaces in-progress runs as events arrive. No "wait for end" behavior, no NULL outcomes in queries.
- **Crash recovery is automatic.** A producer that crashes mid-run leaves a `RunStarted` event with no matching `RunEnded`. The orphan-finalization path emits a synthetic `RunEnded(aborted)` and the view rebuilds normally. No partially-written run row to reconcile.
- **Replay is free.** Want a new analytical view three years from now — different schema, new format, additional aggregations? Replay the event log into a new projection. Traditional CRUD requires migration scripts; here it's just consumption.
- **Audit log = primary data path.** Some regulated industries (medical, aerospace, defense) require immutable audit trails of test runs. In CRUD systems that's a parallel write you bolt on; here it's the architecture.
- **Time-travel queries are natural.** "What did the system know at 14:32:17?" is just `WHERE occurred_at <= '14:32:17'` grouped by `run_id`. CRUD systems need temporal-database extensions to answer that.
- **Cross-system correlation comes for free.** An async temperature probe doesn't have to be a first-class participant in execution. It writes channel samples (or emits events) on its own schedule; any consumer can correlate against any time window. Producers don't have to know each other exist — that's a property of log-based architectures that integration-by-database can't get.
- **Composable consumers.** New analytics view, new format, new dashboard? Subscribe to (or replay) the event log and build your own view. The producer side doesn't change.

## The principled split

Not everything is event-sourced — that would be the wrong shape for some data. TesterKit splits along a clean line:

| Data shape | Pattern | Why |
|---|---|---|
| Configuration (`testerkit.yaml`, `station.yaml`, parts, catalog) | CRUD via YAML, hand-edited | Operators evolve them deliberately over time |
| Test execution data (runs, steps, measurements, events) | Append-only events → derived projections | Immutable historical record |
| Channel sample data (high-rate time-series) | Append-only sample streams (event log carries metadata) | Same domain semantics, different physics — too large for the event log |

Configuration *should* be mutable: you add a station, change a limit, update a part spec. Execution data *shouldn't* be mutable: a run happened on a date with an outcome, and that doesn't change. Channel data is execution data with a size exception — sample streams at kHz–MHz rates can't fit through the event log, so they get their own append-only log with event-log metadata referencing them.

Annotations, retroactive flags, and RMA links don't break this — they're "new facts about old runs," not edits. The event-sourced answer is to emit a new event type for the new fact (e.g. a future `RunFlagged` or `MeasurementAnnotated`) and let projections incorporate it. You're not mutating the past, you're recording new observations about it.

## Materializers run in whatever process cares

In a CRUD world, "the database" is a single shared mutable structure every consumer reads and writes against. In an event-sourced world, the *events* are the shared contract — and each consumer can run *its own* materializer in *its own* process, on whatever cadence makes sense for that consumer. Different processes can care about different projections without coordinating.

That's why TesterKit's materializers take the shape they do:

- The runs daemon cares about an always-on, queryable view of recent runs, so it runs as a long-lived consumer: it collects events per in-flight run and, on `RunEnded`, writes the canonical per-run parquet. It also takes over for runs whose runner crashed. Same view, different trigger.
- Each exporter (CSV, JSON, HDF5, STDF, …) replays events after the fact via `testerkit export`, each in its own process at whatever cadence the operator invokes.
- Any future consumer — a Grafana exporter, a Snowflake pipeline, an analytics view — would run its own materializer in its own process at whatever cadence it likes (per-event, per-run, batched hourly). Sync or async, in-process or out, ephemeral or long-running — those are local choices, not architectural commitments.

There's no "the materialization service" everyone has to wait on, no central writer that becomes a bottleneck. Each consumer's timing is local to its process; from the system's perspective they're all running independently, all deriving their own views from the same shared event log. That's the property that makes log-based architectures composable without coordination.

## Trade-offs

- **Eventual consistency on projections.** Between event emission and projection materialization there's a window where queries see stale data. For test execution this window is short (per-event for live views; end-of-run for the canonical parquet) and the live event view fills the gap, but it's a real property to be aware of.
- **More upfront design work.** You have to choose event types deliberately. Adding a new dimension means adding an event field (additive, fine) or a new event class (also fine). But the design conversation is heavier than "add a column."
- **Harder mental model for newcomers.** Most developers expect to query a `runs` table directly. The "events are primary, this query hits a projection" framing requires explanation. This page is part of paying that tax.

## See also

**Same topic, other quadrants:**

- [Reference → Event types](../../reference/data/event-types.md) — every event class the runtime emits, generated from source
- [Reference → Parquet schema](../../reference/data/parquet-schema.md) — the materialized projection's column-level reference
- [How-to → Export results](../../how-to/data/export-results.md) — exporters as event-replay consumers (one materializer per format)

**Sibling concepts:**

- [Event log](event-log.md) — the *what*: event types, dispatch, durability
- [Data stores](data-stores.md) — events / channels / files / run projection; on-disk layout, `data_dir` resolution, schema-evolution contract
