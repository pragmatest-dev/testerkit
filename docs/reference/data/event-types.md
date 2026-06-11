# Event types reference

Every record in the Litmus event log inherits from `EventBase`. This page enumerates every event class, its `event_type` discriminator string, and the fields the class adds beyond the base.

The tables below are generated from source — `src/litmus/data/events.py`. To regenerate after touching the models, run:

```bash
uv run python scripts/generate_reference_docs.py event-types
```

The pre-commit hook runs the same generator in `--check` mode, so source / docs drift fails the commit.

## Base fields (every event)

<!-- GENERATED:event-types-base-fields:start -->
| Field | Type | Default |
|---|---|---|
| `id` | `UUID` | *via* `uuid4()` |
| `occurred_at` | `datetime` | *via* `_utcnow()` |
| `received_at` | `datetime \| None` | `None` |
| `session_id` | `UUID` | *via* `uuid4()` |
| `run_id` | `UUID \| None` | `None` |
<!-- GENERATED:event-types-base-fields:end -->

`event_type` is the discriminator — every subclass declares it as a `Literal` with a fixed string value (shown as the section heading below).

<!-- GENERATED:event-types-by-category:start -->
## Session events

### `session.started` — `SessionStarted`

Emitted once at the start of a session (interactive or test orchestrator).

| Field | Type | Default |
|---|---|---|
| `session_type` | `str` | `'test_run'` |
| `station_id` | `str \| None` | `None` |
| `station_name` | `str \| None` | `None` |
| `station_type` | `str \| None` | `None` |
| `station_location` | `str \| None` | `None` |
| `station_hostname` | `str \| None` | `None` |
| `pid` | `int \| None` | `None` |
| `client` | `str` | *via* `_detect_client()` |
| `operator_id` | `str \| None` | `None` |
| `operator_name` | `str \| None` | `None` |
| `fixture_id` | `str \| None` | `None` |
| `slot_count` | `int` | `1` |

### `session.ended` — `SessionEnded`

Emitted at the end of a session. Must NOT carry run_id.

| Field | Type | Default |
|---|---|---|
| `outcome` | `str \| None` | `None` |

## Run events

### `run.started` — `RunStarted`

Emitted once per test run. Contains full run context.

| Field | Type | Default |
|---|---|---|
| `station_id` | `str \| None` | `None` |
| `station_name` | `str \| None` | `None` |
| `station_type` | `str \| None` | `None` |
| `station_location` | `str \| None` | `None` |
| `station_hostname` | `str \| None` | `None` |
| `slot_id` | `str \| None` | `None` |
| `slot_index` | `int \| None` | `None` |
| `pid` | `int \| None` | `None` |
| `client` | `str` | *via* `_detect_client()` |
| `dut_serial` | `str` | `''` |
| `dut_part_number` | `str \| None` | `None` |
| `dut_revision` | `str \| None` | `None` |
| `dut_lot_number` | `str \| None` | `None` |
| `part_id` | `str \| None` | `None` |
| `part_name` | `str \| None` | `None` |
| `part_revision` | `str \| None` | `None` |
| `operator_id` | `str \| None` | `None` |
| `operator_name` | `str \| None` | `None` |
| `fixture_id` | `str \| None` | `None` |
| `test_phase` | `str \| None` | `None` |
| `project_name` | `str \| None` | `None` |
| `git_commit` | `str \| None` | `None` |
| `git_branch` | `str \| None` | `None` |
| `git_remote` | `str \| None` | `None` |
| `environment_json` | `str \| None` | `None` |
| `custom_metadata` | `dict[str, Any]` | `{}` |

### `run.ended` — `RunEnded`

Emitted at the end of a test run.

| Field | Type | Default |
|---|---|---|
| `outcome` | `str \| None` | `None` |

### `run.materialized` — `RunMaterialized`

Emitted by a materializer after a run's state has been written to a durable, query-optimized backend.

| Field | Type | Default |
|---|---|---|
| `materializer` | `str` | *required* |
| `destination` | `str` | *required* |
| `materialized_at` | `datetime` | *via* `_utcnow()` |
| `row_counts` | `dict[str, int] \| None` | `None` |

## Slot (multi-DUT) events

### `slot.started` — `SlotStarted`

Emitted when a DUT slot begins execution.

| Field | Type | Default |
|---|---|---|
| `slot_id` | `str` | *required* |
| `dut_serial` | `str` | *required* |

### `slot.completed` — `SlotCompleted`

Emitted when a DUT slot finishes execution.

| Field | Type | Default |
|---|---|---|
| `slot_id` | `str` | *required* |
| `outcome` | `str` | *required* |
| `error_message` | `str \| None` | `None` |

### `sync.arrived` — `SyncArrived`

Emitted by a child process when it reaches a named sync point.

| Field | Type | Default |
|---|---|---|
| `slot_id` | `str` | *required* |
| `name` | `str` | *required* |

### `sync.release` — `SyncRelease`

Emitted by the orchestrator to unblock all slots at a sync point.

| Field | Type | Default |
|---|---|---|
| `name` | `str` | *required* |

## Fixture events

### `fixture.instrument_connected` — `InstrumentConnected`

Emitted when an instrument is connected and identified.

| Field | Type | Default |
|---|---|---|
| `role` | `str` | *required* |
| `instrument_id` | `str` | *required* |
| `driver` | `str \| None` | `None` |
| `resource` | `str` | *required* |
| `protocol` | `str` | `'visa'` |
| `manufacturer` | `str \| None` | `None` |
| `model` | `str \| None` | `None` |
| `serial` | `str \| None` | `None` |
| `firmware` | `str \| None` | `None` |
| `cal_due` | `str \| None` | `None` |
| `cal_last` | `str \| None` | `None` |
| `cal_certificate` | `str \| None` | `None` |
| `cal_lab` | `str \| None` | `None` |
| `mocked` | `bool` | `False` |

### `fixture.identity_verified` — `IdentityVerified`

| Field | Type | Default |
|---|---|---|
| `role` | `str` | *required* |
| `expected` | `dict[str, Any]` | `{}` |
| `actual` | `dict[str, Any]` | `{}` |
| `matches` | `bool` | `True` |
| `mismatches` | `list[str]` | `[]` |

### `fixture.calibration_warning` — `CalibrationWarning`

| Field | Type | Default |
|---|---|---|
| `role` | `str` | *required* |
| `instrument_id` | `str` | *required* |
| `days_until_due` | `int \| None` | `None` |
| `message` | `str` | `''` |

### `fixture.dut_scanned` — `DutScanned`

| Field | Type | Default |
|---|---|---|
| `dut_serial` | `str` | *required* |
| `scan_source` | `str \| None` | `None` |

### `fixture.instrument_disconnected` — `InstrumentDisconnected`

Emitted when an instrument is disconnected during teardown.

| Field | Type | Default |
|---|---|---|
| `role` | `str` | *required* |
| `instrument_id` | `str` | *required* |

## Test events

### `test.step_started` — `StepStarted`

| Field | Type | Default |
|---|---|---|
| `step_name` | `str` | *required* |
| `step_index` | `int` | *required* |
| `step_path` | `str` | `''` |
| `parent_path` | `str` | `''` |
| `description` | `str \| None` | `None` |
| `vector_index` | `int` | `0` |
| `inputs` | `dict[str, Any]` | `{}` |
| `node_id` | `str \| None` | `None` |
| `file` | `str \| None` | `None` |
| `module` | `str \| None` | `None` |
| `class_name` | `str \| None` | `None` |
| `function` | `str \| None` | `None` |

### `test.step_ended` — `StepEnded`

| Field | Type | Default |
|---|---|---|
| `step_name` | `str` | *required* |
| `step_index` | `int` | *required* |
| `step_path` | `str` | `''` |
| `parent_path` | `str` | `''` |
| `outcome` | `str \| None` | `None` |
| `vector_index` | `int` | `0` |
| `vector_outcome` | `str \| None` | `None` |
| `inputs` | `dict[str, Any]` | `{}` |
| `outputs` | `dict[str, Any]` | `{}` |
| `node_id` | `str \| None` | `None` |
| `file` | `str \| None` | `None` |
| `module` | `str \| None` | `None` |
| `class_name` | `str \| None` | `None` |
| `function` | `str \| None` | `None` |

### `test.measurement` — `MeasurementRecorded`

A single measurement. Normalized: carries only measurement-specific fields.

| Field | Type | Default |
|---|---|---|
| `step_name` | `str` | *required* |
| `step_index` | `int` | *required* |
| `step_path` | `str` | `''` |
| `vector_index` | `int` | `0` |
| `retry` | `int` | `0` |
| `measurement_name` | `str` | *required* |
| `measurement_timestamp` | `datetime \| None` | `None` |
| `value` | `float \| None` | `None` |
| `units` | `str \| None` | `None` |
| `outcome` | `str \| None` | `None` |
| `limit_low` | `float \| None` | `None` |
| `limit_high` | `float \| None` | `None` |
| `limit_nominal` | `float \| None` | `None` |
| `limit_comparator` | `str \| None` | `None` |
| `characteristic_id` | `str \| None` | `None` |
| `spec_ref` | `str \| None` | `None` |
| `dut_pin` | `str \| None` | `None` |
| `fixture_connection` | `str \| None` | `None` |
| `instrument_name` | `str \| None` | `None` |
| `instrument_resource` | `str \| None` | `None` |
| `instrument_channel` | `str \| None` | `None` |
| `inputs` | `dict[str, Any]` | `{}` |
| `outputs` | `dict[str, Any]` | `{}` |
| `custom` | `dict[str, Any]` | `{}` |

### `test.record` — `RecordEvent`

A key/value record emitted by harness.record().

| Field | Type | Default |
|---|---|---|
| `step_name` | `str` | *required* |
| `step_index` | `int` | *required* |
| `key` | `str` | *required* |
| `value` | `Any` | *required* |

### `test.observation` — `Observation`

Emitted by ``Context.observe(key, value)``.

| Field | Type | Default |
|---|---|---|
| `step_name` | `str` | `''` |
| `step_index` | `int` | `0` |
| `step_path` | `str` | `''` |
| `vector_index` | `int` | `0` |
| `retry` | `int` | `0` |
| `name` | `str` | *required* |
| `value` | `Any` | `None` |

### `test.steps_discovered` — `StepsDiscovered`

Emitted after instruments connect, before steps execute.

| Field | Type | Default |
|---|---|---|
| `items` | `list[dict[str, str \| int \| None]]` | `[]` |

## Route (switching) events

### `route.closed` — `RouteClosed`

Emitted when switch channels are closed to activate a route.

| Field | Type | Default |
|---|---|---|
| `connection_name` | `str` | *required* |
| `switch_role` | `str` | *required* |
| `channels` | `list[str]` | *required* |

### `route.opened` — `RouteOpened`

Emitted when switch channels are opened to deactivate a route.

| Field | Type | Default |
|---|---|---|
| `connection_name` | `str` | *required* |
| `switch_role` | `str` | *required* |
| `channels` | `list[str]` | *required* |

## Instrument (proxy traffic) events

### `instrument.set` — `InstrumentSet`

Emitted when a driver set method is called via proxy.

| Field | Type | Default |
|---|---|---|
| `instrument_role` | `str` | *required* |
| `channel_id` | `str` | *required* |
| `attribute` | `str` | *required* |
| `value` | `Any` | `None` |
| `units` | `str \| None` | `None` |
| `resource` | `str` | `''` |

### `instrument.configure` — `InstrumentConfigure`

Emitted when a driver configure method is called via proxy.

| Field | Type | Default |
|---|---|---|
| `instrument_role` | `str` | *required* |
| `method` | `str` | *required* |
| `parameters` | `dict[str, Any]` | `{}` |
| `resource` | `str` | `''` |

## Channel (lifecycle) events

### `channel.started` — `ChannelStarted`

A channel received its first sample in this session.

| Field | Type | Default |
|---|---|---|
| `channel_id` | `str` | *required* |
| `units` | `str \| None` | `None` |
| `instrument_role` | `str \| None` | `None` |
| `method` | `str \| None` | `None` |
| `resource` | `str \| None` | `None` |

### `channel.closed` — `ChannelClosed`

A channel was sealed for this session.

| Field | Type | Default |
|---|---|---|
| `channel_id` | `str` | *required* |
| `reason` | `str` | *required* |

## Diagnostic events

### `diagnostic.warning` — `DiagnosticWarning`

| Field | Type | Default |
|---|---|---|
| `source` | `str` | `''` |
| `message` | `str` | `''` |
| `details` | `dict[str, Any]` | `{}` |

### `diagnostic.error` — `DiagnosticError`

| Field | Type | Default |
|---|---|---|
| `source` | `str` | `''` |
| `message` | `str` | `''` |
| `details` | `dict[str, Any]` | `{}` |

## Stream events

### `stream.started` — `StreamStarted`

Emitted when a FileStore streaming sink opens.

| Field | Type | Default |
|---|---|---|
| `stream_id` | `UUID` | *required* |
| `name` | `str` | `''` |
| `format` | `str` | `''` |

### `stream.ended` — `StreamEnded`

Emitted when a FileStore streaming sink closes.

| Field | Type | Default |
|---|---|---|
| `stream_id` | `UUID` | *required* |
| `uri` | `str \| None` | `None` |
| `size_bytes` | `int \| None` | `None` |

## Dialog events

### `dialog.opened` — `DialogOpened`

Emitted when an operator dialog is shown, pausing test execution.

| Field | Type | Default |
|---|---|---|
| `dialog_id` | `UUID` | *required* |
| `dialog_type` | `str` | *required* |
| `title` | `str` | *required* |
| `message` | `str` | *required* |
| `step_name` | `str \| None` | `None` |
| `blocking` | `bool` | `True` |

### `dialog.responded` — `DialogResponded`

Emitted when an operator dialog receives a response.

| Field | Type | Default |
|---|---|---|
| `dialog_id` | `UUID` | *required* |
| `dialog_type` | `str` | *required* |
| `response_type` | `str` | *required* |
| `duration_seconds` | `float` | *required* |
| `value` | `str \| None` | `None` |
| `choice` | `int \| None` | `None` |
<!-- GENERATED:event-types-by-category:end -->

## Discriminated union

Every event class above is folded into the `Event` discriminated union for deserialization:

```python
from litmus.data.events import Event

event = Event.model_validate(json_payload)   # picks the right subclass by event_type
```

`ALL_EVENTS` (a set of every class) and the per-category sets (`SESSION_EVENTS`, `RUN_EVENTS`, `SLOT_EVENTS`, `FIXTURE_EVENTS`, `TEST_EVENTS`, `ROUTE_EVENTS`, `INSTRUMENT_EVENTS`, `DIAGNOSTIC_EVENTS`, `STREAM_EVENTS`, `DIALOG_EVENTS`) are also exported from `litmus.data.events` for subscribers that filter by category.

## See also

- [Event log concept](../../concepts/data/event-log.md) — why event sourcing, and how the log is consumed
- [Three stores](../../concepts/data/three-stores.md) — where events, runs, and channels each live
- [Querying events](../../how-to/data/querying-events.md) — DuckDB / Python recipes
- [Parquet schema](parquet-schema.md) — the materialized row shape derived from these events
