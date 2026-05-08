# Litmus Data Schemas

Canonical schemas for the three data planes — events, run parquets, channels — as of 2026-05-08, branch `feat/unified-run-parquet`. Schema version `1.0`.

All Pydantic event payloads are JSON-serialised; storage shapes (DuckDB tables, Arrow IPC, Parquet) are listed alongside.

---

## 1. Events

### 1.1 Event log envelope (Arrow IPC + DuckDB row)

Per-process Arrow IPC files at `results/events/{date}/{session_id}-{pid}.arrow` carry the canonical event log. The events daemon's DuckDB `events` table mirrors this shape with the same columns. The full Pydantic event JSON lives in the `json` column for lossless replay; the other columns are indexes for filtering.

```python
# from src/litmus/data/event_log.py
_IPC_SCHEMA = pa.schema([
    ("id",          pa.string()),                       # event UUID (PK)
    ("event_type",  pa.string()),                       # discriminator: 'session.started', etc.
    ("occurred_at", pa.timestamp("us", tz="UTC")),     # producer-stamped
    ("received_at", pa.timestamp("us", tz="UTC")),     # EventLog-stamped on emit
    ("session_id",  pa.string()),
    ("run_id",      pa.string()),                       # nullable — session-level events have None
    ("json",        pa.string()),                       # full event payload, model_dump_json()
])
```

The events daemon's `events` table adds operational columns and indexes:

```sql
CREATE TABLE events (
    id           VARCHAR PRIMARY KEY,
    event_type   VARCHAR NOT NULL,
    occurred_at  TIMESTAMPTZ NOT NULL,
    received_at  TIMESTAMPTZ,
    session_id   VARCHAR,
    run_id       VARCHAR,
    json         VARCHAR
)
-- + ALTER TABLE ADD COLUMN IF NOT EXISTS for any future fields
```

### 1.2 Event base type

```python
class EventBase(BaseModel):
    id:          UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=_utcnow)
    received_at: datetime | None = None     # set by EventLog.emit()
    session_id:  UUID
    run_id:      UUID | None = None         # null on session-level events
```

### 1.3 Concrete event types

Grouped by category (the same groupings used in `events.py`):

#### Session events

| Type | Discriminator | Carries |
|---|---|---|
| `SessionStarted` | `session.started` | session-wide metadata: station_id/name/type/location/hostname, pid, client, operator_id/name, fixture_id, slot_count |
| `SessionEnded` | `session.ended` | (signals session termination; minimal payload) |

#### Run events

| Type | Discriminator | Carries |
|---|---|---|
| `RunStarted` | `run.started` | full run context: station, dut (serial/part_number/revision/lot_number), product, operator, fixture_id, test_phase, project_name, git_commit/branch/remote, environment_json, custom_metadata, channel_refs, slot_id |
| `RunEnded` | `run.ended` | `outcome: str \| None` |

#### Slot events (multi-DUT orchestration)

| Type | Discriminator | Carries |
|---|---|---|
| `SlotStarted` | `slot.started` | slot_id, dut_serial |
| `SlotCompleted` | `slot.completed` | slot_id, outcome, error_message |
| `SyncArrived` | `sync.arrived` | slot_id, name (sync point) |
| `SyncRelease` | `sync.release` | name |

#### Fixture events

| Type | Discriminator | Carries |
|---|---|---|
| `InstrumentConnected` | `fixture.instrument_connected` | role, instrument_id, driver, resource, protocol, manufacturer, model, serial, firmware, cal_due/last/certificate/lab, mocked |
| `IdentityVerified` | `fixture.identity_verified` | role, expected, actual, matches, mismatches |
| `CalibrationWarning` | `fixture.calibration_warning` | role, instrument_id, days_until_due, message |
| `DutScanned` | `fixture.dut_scanned` | dut_serial, scan_source |
| `InstrumentDisconnected` | `fixture.instrument_disconnected` | role, instrument_id |

#### Test events (the bulk of the log)

| Type | Discriminator | Carries |
|---|---|---|
| `StepsDiscovered` | `test.steps_discovered` | items: list of `{node_id, step_index, vector_index, vector_count_planned, file, module, class_name, function, markers}` — full pytest collection at run start |
| `StepStarted` | `test.step_started` | step_name, step_index, step_path, parent_path, description, vector_index, inputs (in_*), node_id, file, module, class_name, function |
| `MeasurementRecorded` | `test.measurement` | step_name, step_index, step_path, vector_index, attempt, measurement_name, measurement_timestamp, value, units, outcome, limit_low/high/nominal/comparator, characteristic_id, spec_ref, dut_pin, fixture_connection, instrument_name/resource/channel, inputs, outputs, custom |
| `StepEnded` | `test.step_ended` | step_name, step_index, step_path, parent_path, outcome, vector_index, vector_outcome, inputs, outputs, node_id, file, module, class_name, function |
| `RecordEvent` | _custom_ | freeform user record |

#### Diagnostic events

| Type | Discriminator | Carries |
|---|---|---|
| `DiagnosticWarning` | `diagnostic.warning` | source, message, details |
| `DiagnosticError` | `diagnostic.error` | source, message, details |

#### Route events (signal switching)

| Type | Discriminator | Carries |
|---|---|---|
| `RouteClosed` | `route.closed` | (matrix routing details) |
| `RouteOpened` | `route.opened` | (matrix routing details) |

#### Instrument I/O events

| Type | Discriminator | Carries |
|---|---|---|
| `InstrumentRead` | `instrument.read` | instrument identity + read payload |
| `InstrumentSet` | `instrument.set` | instrument identity + set payload |
| `InstrumentConfigure` | `instrument.configure` | configuration change |

#### Stream events (binary blob streams — waveforms, etc.)

| Type | Discriminator | Carries |
|---|---|---|
| `StreamStarted` | `stream.started` | stream_id, format, path |
| `StreamEnded` | `stream.ended` | stream_id |
| `StreamFrameIndex` | `stream.frame_index` | stream_id, frame_count |

#### Dialog events (operator interaction)

| Type | Discriminator | Carries |
|---|---|---|
| `DialogOpened` | `dialog.opened` | dialog_id, dialog_type, title, message, step_name, blocking |
| `DialogResponded` | `dialog.responded` | dialog_id, dialog_type, response_type, duration_seconds, value, choice |

### 1.4 Event ↔ row-kind correspondence

Events drive parquet row construction:

- `RunStarted` + `RunEnded` → run-level columns on every row in the parquet
- `StepStarted` + `StepEnded` (for one `(step_index, vector_index)` pair) → one `record_type='step'` row + step context on associated measurement rows
- `MeasurementRecorded` → one `record_type='measurement'` row each
- `StepsDiscovered` → fills in unrun-vector entries when a step was planned but never started

---

## 2. Run parquets — `RUN_ROW_SCHEMA`

`SCHEMA_VERSION = "1.0"`. One parquet per run, sealed at end-of-run. Two row kinds in one schema, discriminated by an explicit `record_type` column.

### 2.1 Row kinds

| `record_type` | Emitted when | Populated columns |
|---|---|---|
| `'measurement'` | One per `MeasurementRecorded` event | All run / step / vector / measurement columns |
| `'step'` | One per `(step_path, vector_index)` execution (always — including planned-but-unrun vectors) | All run / step / vector columns; measurement_* columns are NULL |

A step that records N measurements emits **1 step row + N measurement rows**. Both kinds share the denormalized run / DUT / station / fixture / step context. Measurement payload columns are NULL on step rows.

### 2.2 Schema (PyArrow)

```python
# from src/litmus/data/schemas.py
RUN_ROW_SCHEMA = pa.schema([
    # Discriminator — 'step' or 'measurement'
    ("record_type",         pa.string()),

    # Identity & timing
    ("session_id",          pa.string()),
    ("run_id",              pa.string()),
    ("slot_id",             pa.string()),
    ("run_started_at",      pa.timestamp("us", tz="UTC")),
    ("run_ended_at",        pa.timestamp("us", tz="UTC")),

    # Step / vector context (denormalized onto every row)
    ("step_name",           pa.string()),
    ("step_index",          pa.int64()),
    ("step_path",           pa.string()),
    ("parent_path",         pa.string()),               # "" for root steps
    ("step_started_at",     pa.timestamp("us", tz="UTC")),
    ("step_ended_at",       pa.timestamp("us", tz="UTC")),
    ("step_node_id",        pa.string()),
    ("step_module",         pa.string()),
    ("step_file",           pa.string()),
    ("step_class",          pa.string()),
    ("step_function",       pa.string()),
    ("step_markers",        pa.string()),
    ("step_vector_count",   pa.int32()),                # 1 for non-swept; N for sweep
    ("vector_index",        pa.int64()),
    ("vector_attempt",      pa.int64()),                # 1-based; per measurement
    ("vector_started_at",   pa.timestamp("us", tz="UTC")),
    ("vector_ended_at",     pa.timestamp("us", tz="UTC")),

    # Operator
    ("operator_id",         pa.string()),
    ("operator_name",       pa.string()),

    # DUT
    ("dut_serial",          pa.string()),
    ("dut_part_number",     pa.string()),
    ("dut_revision",        pa.string()),
    ("dut_lot_number",      pa.string()),

    # Product
    ("product_id",          pa.string()),
    ("product_name",        pa.string()),
    ("product_revision",    pa.string()),

    # Station
    ("station_id",          pa.string()),
    ("station_name",        pa.string()),
    ("station_type",        pa.string()),
    ("station_location",    pa.string()),
    ("station_hostname",    pa.string()),

    # Fixture
    ("fixture_id",          pa.string()),

    # Test context
    ("test_phase",          pa.string()),
    ("project_name",        pa.string()),
    ("git_commit",          pa.string()),
    ("git_branch",          pa.string()),
    ("git_remote",          pa.string()),

    # Measurement core (NULL on record_type='step')
    ("measurement_name",        pa.string()),
    ("measurement_timestamp",   pa.timestamp("us", tz="UTC")),
    ("measurement_value",       pa.float64()),
    ("measurement_units",       pa.string()),
    ("measurement_outcome",     pa.string()),

    # Limits (NULL on record_type='step')
    ("limit_low",           pa.float64()),
    ("limit_high",          pa.float64()),
    ("limit_nominal",       pa.float64()),
    ("limit_comparator",    pa.string()),

    # Spec traceability
    ("characteristic_id",   pa.string()),
    ("spec_ref",            pa.string()),

    # Signal path (NULL on record_type='step')
    ("dut_pin",             pa.string()),
    ("fixture_connection",  pa.string()),
    ("instrument_name",     pa.string()),
    ("instrument_resource", pa.string()),
    ("instrument_channel",  pa.string()),

    # Outcome rollup
    ("step_outcome",        pa.string()),
    ("vector_outcome",      pa.string()),
    ("run_outcome",         pa.string()),

    # Environment traceability
    ("python_version",      pa.string()),
    ("litmus_version",      pa.string()),
    ("env_fingerprint",     pa.string()),
])
```

Plus dynamic columns inferred per file:

| Prefix | Source | Notes |
|---|---|---|
| `in_*` | Vector inputs (commanded sweep parameters) | One column per parameter; type inferred from first non-null value |
| `out_*` | Vector outputs (observed values) | Large blobs become `_ref/` URIs |
| `instr_*` (`step_instruments_*` list-typed) | Instrument arrays from `InstrumentConnected` events | `pa.list_(pa.string())` for most; `pa.list_(pa.bool_())` for `mocked` |
| `custom_*` | User-defined custom fields | Type inferred per file |

### 2.3 Identity / PK semantics

- Step rows: unique by `(run_id, step_path, vector_index)`
- Measurement rows: unique by `(run_id, step_path, vector_index, measurement_name)`
- `vector_attempt` is a **column on measurement rows only**, 1-based, marking retry attempts. The PK does *not* include vector_attempt — retries overwrite the prior attempt's `step_outcome` (final state wins).

### 2.4 File-level Parquet metadata

Each parquet carries Parquet file-level KV metadata:

| Key | Value |
|---|---|
| `schema_version` | `"1.0"` |
| `litmus_version` | the package version that wrote the file |
| `environment_json` | environment snapshot from `RunStarted` |
| `step_results` | JSON-encoded `build_step_manifest` output (full pytest-collected list) |
| `profile_facets_json` | profile-resolution facets, if a profile was used |

### 2.5 Reference data — `_ref/` directories

Large outputs (waveforms, images) are written as raw blobs alongside the parquet:

```
results/runs/{date}/{timestamp}_{serial}.parquet
results/runs/{date}/{timestamp}_{serial}_ref/
   └── {vector_id_short}/
       ├── {key}.npy        # waveform arrays (numpy)
       ├── {key}.png        # images
       └── {key}.bin        # opaque blobs
```

The parquet `out_{key}` column carries a URI string like `_ref/{vector_id}/{key}.npy` referencing the file. Resolution is `{parquet_dir}/{ref_path}`.

### 2.6 DuckDB derived tables (runs daemon)

These tables in `results/runs/_index.duckdb` are **derived** from the parquet files; they auto-rebuild via the runs daemon's ingest sweep and auto-migrate via `ALTER TABLE ADD COLUMN IF NOT EXISTS`.

```sql
-- One row per run
CREATE TABLE runs_persisted (
    run_id            VARCHAR PRIMARY KEY,
    file_path         VARCHAR,
    session_id        VARCHAR,
    slot_id           VARCHAR,
    dut_serial        VARCHAR,
    dut_part_number   VARCHAR,
    dut_lot_number    VARCHAR,
    station_id        VARCHAR,
    station_name      VARCHAR,
    station_hostname  VARCHAR,
    fixture_id        VARCHAR,
    outcome           outcome_kind,
    started_at        TIMESTAMPTZ,
    ended_at          TIMESTAMPTZ,
    num_measurements  INTEGER,        -- COUNT(*) FILTER (WHERE record_type='measurement')
    num_steps         INTEGER,        -- COUNT(*) FILTER (WHERE record_type='step')
    test_phase        VARCHAR,
    product_id        VARCHAR,
    operator_id       VARCHAR,
    project_name      VARCHAR
);

-- One row per (run_id, step_path, vector_index)
CREATE TABLE steps_persisted (
    run_id            VARCHAR NOT NULL,
    step_path         VARCHAR NOT NULL,
    vector_index      BIGINT  NOT NULL DEFAULT 0,
    step_index        INTEGER,
    file_path         VARCHAR,
    session_id        VARCHAR,
    slot_id           VARCHAR,
    step_name         VARCHAR,
    parent_path       VARCHAR,
    outcome           outcome_kind,
    started_at        TIMESTAMPTZ,
    ended_at          TIMESTAMPTZ,
    duration_s        DOUBLE,
    has_measurements  BOOLEAN,
    measurement_count INTEGER,
    vector_count      INTEGER,
    markers           VARCHAR,
    dut_serial        VARCHAR,
    station_id        VARCHAR,
    PRIMARY KEY (run_id, step_path, vector_index)
);

-- One row per measurement (raw rows packed with dynamic columns into MAP)
CREATE TABLE measurements_persisted (
    file_path             VARCHAR NOT NULL,
    record_type           VARCHAR NOT NULL DEFAULT 'measurement',
    -- (full RUN_ROW_SCHEMA columns + dynamic_attrs MAP(VARCHAR, VARCHAR))
    -- See _runs_duckdb_daemon.py:233
);

-- Per-(file, step, measurement) statistics
CREATE TABLE measurement_stats (
    file_path           VARCHAR NOT NULL,
    run_id              VARCHAR,
    session_id          VARCHAR,
    step_index          INTEGER,
    step_name           VARCHAR,
    measurement_name    VARCHAR NOT NULL,
    measurement_units   VARCHAR,
    limit_low           DOUBLE,
    limit_high          DOUBLE,
    limit_nominal       DOUBLE,
    count               INTEGER NOT NULL,
    pass_count          INTEGER NOT NULL,
    fail_count          INTEGER NOT NULL,
    min_value           DOUBLE,
    max_value           DOUBLE,
    mean_value          DOUBLE
);

-- Dynamic-column inventory per file (for /explore type discovery)
CREATE TABLE measurement_io_schema (
    file_path     VARCHAR NOT NULL,
    step_index    INTEGER,
    column_name   VARCHAR NOT NULL,
    category      VARCHAR NOT NULL    -- 'input' | 'output' | 'custom'
);

-- Reference URIs extracted from out_* columns
CREATE TABLE measurement_refs (
    file_path        VARCHAR NOT NULL,
    step_index       INTEGER,
    measurement_name VARCHAR,
    col_name         VARCHAR NOT NULL,
    row_idx          INTEGER NOT NULL,
    uri              VARCHAR NOT NULL,
    channel_id       VARCHAR NOT NULL,
    session_short    VARCHAR NOT NULL
);

-- File ingest ledger
CREATE TABLE _ingested (
    path         VARCHAR PRIMARY KEY,
    mtime        DOUBLE NOT NULL,
    size         BIGINT NOT NULL,
    row_count    BIGINT NOT NULL DEFAULT 0,
    status       VARCHAR NOT NULL DEFAULT 'ok',
    error        VARCHAR,
    last_attempt TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The `runs` / `steps` / `measurements` views combine `_persisted` rows with the in-flight overlay (`inflight_runs`, `inflight_steps`, `inflight_measurements`):

```sql
CREATE OR REPLACE VIEW runs AS
SELECT * FROM runs_persisted
UNION ALL BY NAME
SELECT … FROM inflight_runs
WHERE run_id NOT IN (SELECT run_id FROM runs_persisted);
-- analogous for steps, measurements
```

---

## 3. Channels

The channels store is a separate Flight-served daemon for time-series instrument data — waveforms, scalar streams, anything that's not a discrete measurement.

### 3.1 Pydantic models

```python
class ChannelDescriptor(BaseModel):
    """Metadata for a single channel, written once when first seen."""
    channel_id:       str
    data_type:        str = "scalar"        # 'scalar' | 'array'
    instrument_role:  str = ""
    resource:         str = ""
    units:            str | None = None
    properties:       dict[str, Any] = Field(default_factory=dict)
    first_seen:       datetime

class ChannelSample(BaseModel):
    """A single channel data point delivered to subscribers."""
    channel_id:       str
    timestamp:        datetime
    value:            Any                   # scalar or list
    units:            str | None = None
    sample_interval:  float | None = None
    source_method:    str = ""
```

### 3.2 Storage shape

Per-channel Arrow IPC files at `results/channels/{channel_id}_{seg}.arrow`, segmented by row count. Schema is **inferred from the first write** per channel — flexible per-channel data types.

### 3.3 Wire schema (Flight `do_put` / subscriptions)

```python
def sample_schema():
    return pa.schema([
        ("channel_id",      pa.utf8()),
        ("timestamp",       pa.timestamp("us", tz="UTC")),
        ("value",           pa.utf8()),     # JSON-encoded for flexibility
        ("source_method",   pa.utf8()),
        ("units",           pa.utf8()),
        ("sample_interval", pa.float64()),
    ])
```

`value` is JSON-encoded as a string so the wire format handles both scalar (numeric, bool, string) and array (list of floats) channels uniformly. Decoded on read into the typed column on the per-channel storage schema.

### 3.4 Fallback storage schemas

Used when querying channels with no writer schema yet (empty results, schema-introspection paths):

```python
SCALAR_SCHEMA = pa.schema([
    ("timestamp",     pa.timestamp("us", tz="UTC")),
    ("value",         pa.float64()),
    ("source_method", pa.utf8()),
    ("session_id",    pa.utf8()),
])

ARRAY_SCHEMA = pa.schema([
    ("timestamp",       pa.timestamp("us", tz="UTC")),
    ("samples",         pa.list_(pa.float64())),
    ("sample_interval", pa.float64()),
    ("source_method",   pa.utf8()),
    ("session_id",      pa.utf8()),
])
```

### 3.5 Channel registry (DuckDB)

The channels daemon's `_index.duckdb` carries a registry of known channels — descriptor metadata and the per-channel schema discovered from the first write. Used to answer `list_channel_info()` and `get_channel_schema()` queries without scanning every IPC file.

---

## Quick reference — where each plane's source-of-truth lives

| Plane | Source of truth (durable) | Derived/index |
|---|---|---|
| Events | Arrow IPC files (`events/{date}/{session_id}-{pid}.arrow`) | `events/_index.duckdb` events table |
| Runs | Per-run Parquet (`runs/{date}/{timestamp}_{serial}.parquet`) | `runs/_index.duckdb` `_persisted` tables; views combine persisted + accumulator-pool in-flight overlay |
| Channels | Per-channel Arrow IPC (`channels/{channel_id}_{seg}.arrow`) | `channels/_index.duckdb` registry |

Each derived index is rebuildable from its source-of-truth files. Auto-migration via `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN IF NOT EXISTS`. No version bump or migration script is required for additive schema changes.
