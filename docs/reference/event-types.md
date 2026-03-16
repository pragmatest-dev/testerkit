# Event Types Reference

Complete reference for all Litmus event types. All events inherit from `EventBase`.

**Source:** `litmus/data/events.py`

## Base Fields (all events)

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique event identifier (auto-generated) |
| `occurred_at` | datetime | When the event was created |
| `received_at` | datetime | When `EventLog.emit()` processed it |
| `session_id` | UUID | Session this event belongs to |
| `run_id` | UUID \| None | Test run (if applicable) |

## Session Events

### `session.started` — SessionStarted

Emitted once at session start. Contains session-wide metadata only. Run-level fields (DUT, config snapshots) live in `RunStarted`.

| Field | Type | Default |
|-------|------|---------|
| `session_type` | str | `"test_run"` |
| `station_id` | str | *required* |
| `station_name` | str \| None | |
| `station_type` | str \| None | |
| `station_location` | str \| None | |
| `station_hostname` | str \| None | |
| `pid` | int \| None | |
| `client` | str | auto-detected |
| `operator_id` | str \| None | |
| `operator_name` | str \| None | |
| `fixture_id` | str \| None | |
| `slot_count` | int | `1` |

### `session.ended` — SessionEnded

| Field | Type | Default |
|-------|------|---------|
| `outcome` | str | `"pass"` |

## Run Events

### `run.started` — RunStarted

Emitted once per test run. Contains full run context (DUT, product, config snapshots). In single-DUT mode, one `RunStarted` follows `SessionStarted`. In multi-DUT mode, each worker emits its own `RunStarted`.

| Field | Type | Default |
|-------|------|---------|
| `station_id` | str | *required* |
| `station_name` | str \| None | |
| `station_type` | str \| None | |
| `station_location` | str \| None | |
| `station_hostname` | str \| None | |
| `slot_id` | str \| None | |
| `pid` | int \| None | |
| `client` | str | auto-detected |
| `dut_serial` | str | `""` |
| `dut_part_number` | str \| None | |
| `dut_revision` | str \| None | |
| `dut_lot_number` | str \| None | |
| `product_id` | str \| None | |
| `product_name` | str \| None | |
| `product_revision` | str \| None | |
| `operator_id` | str \| None | |
| `operator_name` | str \| None | |
| `fixture_id` | str \| None | |
| `sequence_id` | str \| None | |
| `test_phase` | str | `"production"` |
| `git_commit` | str \| None | |
| `environment_json` | str \| None | |
| `custom_metadata` | dict | `{}` |
| `channel_refs` | list[str] | `[]` |

### `run.ended` — RunEnded

Emitted at the end of a test run.

| Field | Type | Default |
|-------|------|---------|
| `outcome` | str | `"pass"` |

## Fixture Events

### `fixture.instrument_connected` — InstrumentConnected

| Field | Type | Default |
|-------|------|---------|
| `role` | str | *required* |
| `instrument_id` | str | *required* |
| `driver` | str \| None | |
| `resource` | str | *required* |
| `protocol` | str | `"visa"` |
| `manufacturer` | str \| None | |
| `model` | str \| None | |
| `serial` | str \| None | |
| `firmware` | str \| None | |
| `cal_due` | str \| None | |
| `cal_last` | str \| None | |
| `cal_certificate` | str \| None | |
| `cal_lab` | str \| None | |
| `mocked` | bool | `False` |

### `fixture.identity_verified` — IdentityVerified

| Field | Type | Default |
|-------|------|---------|
| `role` | str | *required* |
| `expected` | dict | `{}` |
| `actual` | dict | `{}` |
| `matches` | bool | `True` |
| `mismatches` | list[str] | `[]` |

### `fixture.calibration_warning` — CalibrationWarning

| Field | Type | Default |
|-------|------|---------|
| `role` | str | *required* |
| `instrument_id` | str | *required* |
| `days_until_due` | int \| None | |
| `message` | str | `""` |

### `fixture.dut_scanned` — DutScanned

| Field | Type | Default |
|-------|------|---------|
| `dut_serial` | str | *required* |
| `scan_source` | str \| None | |

### `fixture.instrument_disconnected` — InstrumentDisconnected

| Field | Type | Default |
|-------|------|---------|
| `role` | str | *required* |
| `instrument_id` | str | *required* |

## Test Events

### `test.steps_discovered` — StepsDiscovered

Emitted after instruments connect, before steps execute. Carries the full list of collected test items.

| Field | Type | Default |
|-------|------|---------|
| `items` | list[dict] | `[]` |

Each item dict contains: `node_id`, `name`, `file`, `module`, `class_name`, `function`.

### `test.step_started` — StepStarted

| Field | Type | Default |
|-------|------|---------|
| `step_name` | str | *required* |
| `step_index` | int | *required* |
| `step_path` | str | `""` |
| `parent_path` | str | `""` |
| `description` | str \| None | |
| `node_id` | str \| None | |
| `file` | str \| None | |
| `module` | str \| None | |
| `class_name` | str \| None | |
| `function` | str \| None | |

### `test.measurement` — MeasurementRecorded

| Field | Type | Default |
|-------|------|---------|
| `step_name` | str | *required* |
| `step_index` | int | *required* |
| `step_path` | str | `""` |
| `vector_index` | int \| None | |
| `attempt` | int \| None | |
| `measurement_name` | str | *required* |
| `measurement_timestamp` | datetime \| None | |
| `value` | float \| None | |
| `units` | str \| None | |
| `outcome` | str \| None | |
| `low_limit` | float \| None | |
| `high_limit` | float \| None | |
| `nominal` | float \| None | |
| `comparator` | str \| None | |
| `spec_id` | str \| None | |
| `spec_ref` | str \| None | |
| `meas_dut_pin` | str \| None | |
| `meas_fixture_point` | str \| None | |
| `meas_instrument` | str \| None | |
| `meas_instrument_resource` | str \| None | |
| `meas_instrument_channel` | str \| None | |
| `inputs` | dict | `{}` |
| `outputs` | dict | `{}` |
| `custom` | dict | `{}` |

### `test.record` — RecordEvent

| Field | Type | Default |
|-------|------|---------|
| `step_name` | str | *required* |
| `step_index` | int | *required* |
| `key` | str | *required* |
| `value` | Any | *required* |

### `test.step_ended` — StepEnded

| Field | Type | Default |
|-------|------|---------|
| `step_name` | str | *required* |
| `step_index` | int | *required* |
| `step_path` | str | `""` |
| `outcome` | str | `"pass"` |
| `node_id` | str \| None | |
| `file` | str \| None | |
| `module` | str \| None | |
| `class_name` | str \| None | |
| `function` | str \| None | |

## Instrument Events

### `instrument.read` — InstrumentRead

Emitted when a driver read method is called via proxy. Array data is serialized as a `channel://` URI claim-check.

| Field | Type | Default |
|-------|------|---------|
| `instrument_role` | str | *required* |
| `channel_id` | str | *required* |
| `method` | str | *required* |
| `value` | Any | `None` |
| `units` | str \| None | |
| `resource` | str | `""` |

### `instrument.set` — InstrumentSet

| Field | Type | Default |
|-------|------|---------|
| `instrument_role` | str | *required* |
| `channel_id` | str | *required* |
| `attribute` | str | *required* |
| `value` | Any | `None` |
| `units` | str \| None | |
| `resource` | str | `""` |

### `instrument.configure` — InstrumentConfigure

| Field | Type | Default |
|-------|------|---------|
| `instrument_role` | str | *required* |
| `method` | str | *required* |
| `parameters` | dict | `{}` |
| `resource` | str | `""` |

## Diagnostic Events

### `diagnostic.warning` — DiagnosticWarning

| Field | Type | Default |
|-------|------|---------|
| `source` | str | `""` |
| `message` | str | `""` |
| `details` | dict | `{}` |

### `diagnostic.error` — DiagnosticError

| Field | Type | Default |
|-------|------|---------|
| `source` | str | `""` |
| `message` | str | `""` |
| `details` | dict | `{}` |

## Stream Events

### `stream.started` — StreamStarted

| Field | Type | Default |
|-------|------|---------|
| `stream_id` | UUID | *required* |
| `format` | str | `""` |
| `path` | str \| None | |

### `stream.ended` — StreamEnded

| Field | Type | Default |
|-------|------|---------|
| `stream_id` | UUID | *required* |

### `stream.frame_index` — StreamFrameIndex

| Field | Type | Default |
|-------|------|---------|
| `stream_id` | UUID | *required* |
| `frame_count` | int | `0` |

## Dialog Events

### `dialog.opened` — DialogOpened

| Field | Type | Default |
|-------|------|---------|
| `dialog_id` | UUID | *required* |
| `dialog_type` | str | *required* |
| `title` | str | *required* |
| `message` | str | *required* |
| `step_name` | str \| None | |
| `blocking` | bool | `True` |

### `dialog.responded` — DialogResponded

| Field | Type | Default |
|-------|------|---------|
| `dialog_id` | UUID | *required* |
| `dialog_type` | str | *required* |
| `response_type` | str | *required* |
| `duration_seconds` | float | *required* |
| `value` | str \| None | |
| `choice` | int \| None | |

## Discriminated Union

All event types are combined into a discriminated union for deserialization:

```python
from litmus.data.events import Event

# Deserialize any event from JSON
event = Event.model_validate(json_data)
```

The `event_type` field is the discriminator.
