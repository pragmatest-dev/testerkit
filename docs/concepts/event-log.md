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

Litmus defines events across 7 categories:

### Session (2 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `SessionStarted` | `session.started` | Full run context: station, DUT, operator, configs |
| `SessionEnded` | `session.ended` | Session outcome |

### Fixture (5 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `InstrumentConnected` | `fixture.instrument_connected` | Instrument identified and connected |
| `IdentityVerified` | `fixture.identity_verified` | Expected vs actual instrument identity |
| `CalibrationWarning` | `fixture.calibration_warning` | Calibration due date approaching |
| `DutScanned` | `fixture.dut_scanned` | DUT serial barcode scanned |
| `InstrumentDisconnected` | `fixture.instrument_disconnected` | Instrument released during teardown |

### Test (6 events)
| Event | Type String | Description |
|-------|-------------|-------------|
| `StepsDiscovered` | `test.steps_discovered` | Full list of collected test items |
| `StepStarted` | `test.step_started` | A test step begins execution |
| `MeasurementRecorded` | `test.measurement` | A single measurement with limits and outcome |
| `RecordEvent` | `test.record` | A key/value record from `harness.record()` |
| `StepEnded` | `test.step_ended` | A test step finishes |
| `RunEnded` | `test.run_ended` | All steps complete |

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
SessionStarted          # Station, DUT, operator context
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

## Push Model: emit() → Subscribers

The `EventLog` class manages the write path:

1. **`emit(event)`** stamps `received_at`, buffers the event for batched Arrow IPC writes
2. **Subscribers** are notified immediately — each subscriber declares which `event_types` it handles
3. **IPC flush** happens every 50 events (configurable), writing a batch to the Arrow IPC file

```python
# Subscriber protocol
class EventSubscriber(Protocol):
    format_name: str
    event_types: set[type]

    def open(self) -> None: ...
    def on_event(self, event: EventBase) -> None: ...
    def close(self) -> None: ...
```

Built-in subscribers include `ParquetSubscriber` (materializes Parquet result files) and `SessionSubscriber` (tracks session metadata).

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

## See Also

- [Three Stores Architecture](three-stores.md) — How EventStore fits with ChannelStore and ParquetBackend
- [Sessions](sessions.md) — What sessions are and why they exist
- [Event Types Reference](../reference/event-types.md) — Complete field reference for all event types
