# Event Log Architecture

The event log is Litmus's unified record of everything that happens during testing — sessions, instrument connections, measurements, diagnostics, and more. It replaces the earlier journal and StreamingDestination patterns with a single, typed event stream.

## Why a Unified Event Stream

Previous approaches split test data across multiple systems: a journal for text logs, streaming destinations for live data, and Parquet files for results. This made it hard to reconstruct what happened during a test, correlate instrument reads with measurements, or monitor tests in real time.

The event log unifies all of this into one ordered stream. Every significant action emits a typed event. Subscribers process events for their own purposes — writing Parquet files, updating the UI, streaming to Grafana.

## Event Hierarchy

All events inherit from `EventBase`, which provides:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique event identifier |
| `occurred_at` | datetime | When the event happened |
| `received_at` | datetime | When EventLog.emit() processed it |
| `session_id` | UUID | Which session this event belongs to |
| `run_id` | UUID | Which test run (if applicable) |

Each event type adds a `Literal` `event_type` field used as a discriminator for deserialization.

## Event Categories

Litmus defines events across 8 categories:

### Session (2 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `SessionStarted` | `session.started` | Session-wide metadata: station, operator, fixture |
| `SessionEnded` | `session.ended` | Session outcome |

### Run (2 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `RunStarted` | `run.started` | Full run context: DUT, product, operator, config snapshots |
| `RunEnded` | `run.ended` | Run outcome |

### Fixture (5 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `InstrumentConnected` | `fixture.instrument_connected` | Instrument identified and connected |
| `IdentityVerified` | `fixture.identity_verified` | Expected vs actual instrument identity |
| `CalibrationWarning` | `fixture.calibration_warning` | Calibration due date approaching |
| `DutScanned` | `fixture.dut_scanned` | DUT serial barcode scanned |
| `InstrumentDisconnected` | `fixture.instrument_disconnected` | Instrument released during teardown |

### Test (5 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `StepsDiscovered` | `test.steps_discovered` | Full list of collected test items |
| `StepStarted` | `test.step_started` | A test step begins execution |
| `MeasurementRecorded` | `test.measurement` | A single measurement with limits and outcome |
| `RecordEvent` | `test.record` | A key/value record from `harness.record()` |
| `StepEnded` | `test.step_ended` | A test step finishes |

### Instrument (3 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `InstrumentRead` | `instrument.read` | Driver read via proxy (scalars inline, arrays as claim-check URIs) |
| `InstrumentSet` | `instrument.set` | Driver set via proxy |
| `InstrumentConfigure` | `instrument.configure` | Driver configure via proxy |

### Diagnostic (2 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `DiagnosticWarning` | `diagnostic.warning` | Non-fatal warning |
| `DiagnosticError` | `diagnostic.error` | Error condition |

### Stream (3 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `StreamStarted` | `stream.started` | A data stream begins |
| `StreamEnded` | `stream.ended` | A data stream ends |
| `StreamFrameIndex` | `stream.frame_index` | Frame count update |

### Dialog (2 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `DialogOpened` | `dialog.opened` | Operator dialog shown, execution paused |
| `DialogResponded` | `dialog.responded` | Operator responded to dialog |

## Event Timeline

A typical test session emits events in this order:

```
SessionStarted          # Session-wide metadata (station, operator)
├── RunStarted          # Run context (DUT, product, config snapshots)
├── InstrumentConnected # One per instrument role
├── IdentityVerified    # Optional identity check
├── StepsDiscovered     # Full list of collected test items
├── StepStarted         # First test step
│   ├── InstrumentRead  # Instrument interactions
│   ├── InstrumentSet
│   └── MeasurementRecorded
├── StepEnded
├── StepStarted         # Next test step...
│   └── ...
├── StepEnded
├── RunEnded            # All steps complete
├── InstrumentDisconnected
└── SessionEnded        # Cleanup complete
```

## Push Model: emit() → internal materializers

The `EventLog` class manages the write path:

1. **`emit(event)`** stamps `received_at`, buffers the event for batched Arrow IPC writes
2. **Internal materializers** are notified immediately — `ParquetSubscriber` for the canonical run parquet, `LiveRunsSubscriber` for the in-daemon ingest path. The `litmus export` CLI replay path drives these same materializers post-hoc against stored events.
3. **IPC flush** happens every 50 events (configurable), writing a batch to the Arrow IPC file

The `EventSubscriber` base class is internal scaffolding for these materializers — not a public extension protocol. Adding a new format requires editing `litmus.data.exporters`, not a third-party plugin.

## Storage

Events are stored as Arrow IPC files, date-partitioned:

```
results/events/
├── 2026-03-10/
│   ├── {session_id}.arrow
│   └── {session_id}_ref/     # Large data (waveforms, images)
└── 2026-03-11/
    └── ...
```

Each `.arrow` file contains index columns (`id`, `event_type`, `occurred_at`, `received_at`, `session_id`, `run_id`) plus a `json` column with the full serialized event for lossless replay.

## Dual-Write Pattern

The `EventStore` layer adds queryability on top of `EventLog`:

1. **Arrow IPC file** — crash-safe append-only storage
2. **In-memory DuckDB via Flight** — immediate SQL queryability

On each flush, batches are pushed to the DuckDB daemon via Arrow Flight `do_put`. Queries go through Flight `do_get` with SQL, so you get read-after-write consistency.

## HARD contract — additive evolution only

The event WAL is a **HARD contract** alongside the parquet artifact —
events are written append-only and consumers can replay arbitrary
history, so the wire format has to evolve additively. Until the 1.0
cut, the following invariants hold and the project must not break them:

- **New event types only.** Every release may add event types. The
  existing event-type discriminator strings (e.g.
  `"test.step_started"`, `"test.measurement"`, `"run.started"`) and
  their `Literal` tags are stable across 0.x.
- **New optional fields only.** Existing event types may grow new
  fields; they must be optional (have a default) so older events
  (replayed from disk or read from older daemons) still validate
  against the current schema. Required fields are frozen for 0.x.
- **No type changes** on existing fields.
- **`event_number` monotonicity** is part of the contract: insert-order
  monotonic per-daemon, used as the watcher cursor for live subscribers.
- **JSON column preserves the full serialized event** for lossless
  replay, regardless of which index columns the daemon's DuckDB
  schema happens to project. Consumers that need the full payload
  read from JSON and don't depend on the index column set.

Breaking event-shape changes (renaming, removing, type-narrowing
required fields) defer to the 1.0 cut. See
[API stability framing](../explorations/api-stability-and-versioning.md)
for the broader HARD vs SOFT picture.

## See Also

- [Three Stores Architecture](three-stores.md) — How EventStore fits with ChannelStore and ParquetBackend
- [Sessions](sessions.md) — What sessions are and why they exist
- [Event Types Reference](../reference/event-types.md) — Complete field reference for all event types
