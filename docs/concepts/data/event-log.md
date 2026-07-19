# The Event Log

The event log is TesterKit's unified record of everything that happens during testing — sessions, instrument connections, measurements, diagnostics, and more.

## One stream, in order

Every significant action emits a typed event into a single ordered stream. That stream lets you reconstruct what happened during a test, line up instrument reads with measurements, and watch a test live as it runs.

## Event Hierarchy

Every event carries the same common fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique event identifier |
| `occurred_at` | datetime | When the event happened |
| `received_at` | datetime | When the log processed it for storage |
| `session_id` | UUID | Which session this event belongs to |
| `run_id` | UUID | Which test run (if applicable) |

Each event also carries an `event_type` string (e.g. `test.measurement`) naming which kind of event it is.

## Event Categories

TesterKit defines events across 12 categories.

### Session (2 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `SessionStarted` | `session.started` | Session-wide metadata: station, operator, fixture |
| `SessionEnded` | `session.ended` | Session outcome |

### Run (3 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `RunStarted` | `run.started` | Full run context: UUT, part, operator, config snapshots |
| `RunEnded` | `run.ended` | Run outcome |
| `RunMaterialized` | `run.materialized` | Emitted after the run's Parquet file is durably written; signals that the run is ready for downstream consumers |

### Site (2 events — multi-UUT)
| Event | Type String | Description |
|-------|-------------|-------------|
| `SiteStarted` | `site.started` | A multi-UUT site subprocess begins |
| `SiteCompleted` | `site.completed` | A multi-UUT site subprocess finishes |

### Sync (2 events — multi-UUT)
| Event | Type String | Description |
|-------|-------------|-------------|
| `SyncArrived` | `sync.arrived` | A worker reached a synchronization barrier |
| `SyncRelease` | `sync.release` | All workers arrived; barrier released |

### Route (2 events — signal switching)
| Event | Type String | Description |
|-------|-------------|-------------|
| `RouteClosed` | `route.closed` | Switch route closed (signal connected) |
| `RouteOpened` | `route.opened` | Switch route opened (signal disconnected) |

### Fixture (5 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `InstrumentConnected` | `fixture.instrument_connected` | Instrument identified and connected |
| `IdentityVerified` | `fixture.identity_verified` | Expected vs actual instrument identity |
| `CalibrationWarning` | `fixture.calibration_warning` | Calibration due date approaching |
| `UutScanned` | `fixture.uut_scanned` | UUT serial barcode scanned |
| `InstrumentDisconnected` | `fixture.instrument_disconnected` | Instrument released during teardown |

### Test (7 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `StepsDiscovered` | `test.steps_discovered` | Full list of collected test items |
| `StepStarted` | `test.step_started` | A test step begins execution |
| `MeasurementRecorded` | `test.measurement` | A single measurement with limits and outcome |
| `StepEnded` | `test.step_ended` | A test step finishes |
| `Observation` | `test.observation` | An environmental or contextual reading recorded during a step |
| `VectorStarted` | `test.vector_started` | A parametric sweep vector begins |
| `VectorEnded` | `test.vector_ended` | A parametric sweep vector finishes |

### Instrument (2 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `InstrumentSet` | `instrument.set` | Driver set via proxy |
| `InstrumentConfigure` | `instrument.configure` | Driver configure via proxy |

### Diagnostic (2 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `DiagnosticWarning` | `diagnostic.warning` | Non-fatal warning |
| `DiagnosticError` | `diagnostic.error` | Error condition |

### Channel (3 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `ChannelStarted` | `channel.started` | A channel received its first sample in this session |
| `ChannelEnded` | `channel.ended` | A channel was sealed for this session |
| `ChannelCheckpoint` | `channel.checkpoint` | Liveness + progress marker from an active channel producer |

### File (3 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `FileStarted` | `file.started` | A data stream begins |
| `FileEnded` | `file.ended` | A data stream ends |
| `FileCheckpoint` | `file.checkpoint` | Liveness + progress marker from an active file sink |

### Dialog (2 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `DialogOpened` | `dialog.opened` | Operator dialog shown, execution paused |
| `DialogResponded` | `dialog.responded` | Operator responded to dialog |

## Event Timeline

A typical test session emits events in this order:

```
SessionStarted          # Session-wide metadata (station, operator)
├── RunStarted          # Run context (UUT, part, config snapshots)
├── InstrumentConnected # One per instrument role
├── IdentityVerified    # Optional identity check
├── StepsDiscovered     # Full list of collected test items
├── StepStarted         # First test step
│   ├── InstrumentSet
│   └── MeasurementRecorded
├── StepEnded
├── StepStarted         # Next test step...
│   └── ...
├── StepEnded
├── RunEnded            # All steps complete
├── RunMaterialized     # Parquet file durably written
├── InstrumentDisconnected
└── SessionEnded        # Cleanup complete
```

## How the log is written

When an event is written:

1. **`received_at` is stamped** and the event is buffered for batched writes to an Arrow file (a fast columnar on-disk format)
2. **On `RunEnded`** the run's Parquet file is written. The same path runs when you replay stored events with `testerkit export`.
3. **Flush** happens every 50 events (configurable), writing a batch to the Arrow file

The set of writers (Parquet, the live UI feed) is built in; adding a new output format is a change to TesterKit itself, not a drop-in plugin.

## Storage

Events are stored as Arrow files, date-partitioned:

```
<data_dir>/events/
├── 2026-03-10/
│   ├── {session_id}-{pid}.arrow
│   └── {session_id}-{pid}_0001.arrow   # rotation for large sessions
└── 2026-03-11/
    └── ...
```

Each Arrow file contains index columns (`id`, `event_type`, `occurred_at`, `received_at`, `session_id`, `run_id`) plus a `json` column with the full serialized event for lossless replay.

## Dual-Write Pattern

Events are also loaded into an in-memory database as they're written, so you can query them with SQL right away:

1. **Arrow file** — crash-safe append-only storage
2. **In-memory DuckDB** — immediate SQL queryability

Each batch is loaded into an in-memory SQL database as it's written, so a query sees an event the instant after it's written.

## What stays stable across releases

Once an event type and its fields exist, they don't change or disappear within 0.x. Only new event types and new optional fields are added. A query or report you write today keeps working as the platform evolves.

## See also

**Same topic, other quadrants:**

- [Reference → Event types](../../reference/data/event-types.md) — generated field reference for every event class
- [Reference → Parquet schema](../../reference/data/parquet-schema.md) — the materialized projection of these events
- [How-to → Querying events](../../how-to/data/querying-events.md) — MCP / HTTP / Python recipes for reading the event log
- [Operator UI → Events](../../reference/operator-ui/events.md) — the browser view of the event log
- [Tutorial → Step 10: Live monitoring](../../tutorial/10-live-monitoring.md) — first hands-on with events as they happen

**Sibling concepts:**

- [Event sourcing](event-sourcing.md) — why the platform is event-sourced rather than mutation-based
- [Data stores](data-stores.md) — how EventStore fits with ChannelStore, FileStore, and RunStore
- [Sessions](sessions.md) — the observation window the event log keys events by
