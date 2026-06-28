# Event Log WAL Format — Maintainer Reference

**Audience:** Litmus maintainers. Not a public consumer contract.

Consumers read events via the daemon index and the Query API, never the raw
Arrow IPC files directly. This document describes the on-disk format for
maintainers who need to understand or evolve the event WAL.

---

## Overview

The event WAL is an **Arrow IPC stream** per writing process. The
`EventLog` class (`src/litmus/data/event_log.py`) buffers typed
`EventBase` events and flushes them as multi-row Arrow RecordBatches via
`_EventIPCWriter` (a subclass of `BufferedIPCWriter` in
`src/litmus/data/_ipc_writer.py`). The IPC writer calls
`pyarrow.ipc.new_stream(sink, schema)` on first flush, embedding the
schema — including its metadata — in the stream header. Every subsequent
flush writes a RecordBatch into that stream.

---

## Path Layout

```
{data_dir}/events/{date}/{session_id}-{pid}[_{segment:04d}].arrow
```

- `{date}` — ISO 8601 date when the EventLog was constructed
  (`date.today().isoformat()`).
- `{session_id}` — UUID of the session this log writes for.
- `{pid}` — OS process ID of the writing process. Each process gets its
  own file so concurrent orchestrator + worker processes never clobber
  each other's streams.
- `[_{segment:04d}]` — optional segment suffix (e.g., `_0001`). Present
  when a single session exceeds `_DEFAULT_MAX_ROWS_PER_SEGMENT` (10 000)
  rows. Each closed segment has a valid Arrow EOS marker and is fully
  readable. The current (open) segment has no suffix.

---

## `_IPC_SCHEMA` — Column Definitions

Defined at module level in `src/litmus/data/event_log.py`. All columns
are written for every event row. Sparse columns (absent on a given event
type) carry `None`.

### Envelope columns

| Column | Arrow type | Description |
|---|---|---|
| `id` | `string` | UUID of this event instance. |
| `event_type` | `string` | Dotted event discriminator (e.g. `"run.started"`). |
| `occurred_at` | `timestamp[us, UTC]` | Client wall-clock at event construction. |
| `received_at` | `timestamp[us, UTC]` | Stamped by `EventLog.emit()` at receipt. |
| `session_id` | `string` | UUID of the session that owns this event. |
| `run_id` | `string` | UUID of the run, or `None` for session-scope events. |

### Emit-order columns

| Column | Arrow type | Description |
|---|---|---|
| `writer_key` | `string` | UUID assigned once per `EventLog` instance. Disambiguates concurrent writers of the same session. |
| `event_offset` | `int64` | Monotonic emit position within this writer (from `itertools.count`). |

Consumer ordering: `(session_id, writer_key, event_offset)`. This order
lives in the data, making it immune to the `do_put`/ingest insert race
(#228) and safe across a backend swap. `event_number` (a daemon-side
`nextval`) is the insert-order resume cursor only, never an emit-order key.

### Payload column

| Column | Arrow type | Description |
|---|---|---|
| `json` | `string` | Lossless `model_dump_json()` of the full event. All fields always present here. |

### Promoted typed columns (`TYPED_PAYLOAD_COLUMNS`)

Defined in `src/litmus/data/events.py:63`. These columns duplicate a
subset of payload values from `json` as top-level VARCHAR columns so the
daemon can push `WHERE` filters into DuckDB without fetching and
post-filtering on `json`. All are `string` type; absent = `None`.

Current set (as of `EVENT_LOG_SCHEMA_VERSION = "1.0"`):

```
file_id, dialog_id, channel_id, slot_id,
uut_serial_number, station_hostname,
instrument_id, node_id, step_path, fixture_id, operator_id, station_id,
step_name, measurement_name, name,
role, instrument_role,
outcome, reason, format, dialog_type, response_type
```

The daemon ingest path (`src/litmus/data/_duckdb_daemon.py`,
`_EVENT_COLUMNS_FROM_IPC`) selects columns by name from the loaded Arrow
table. Adding new columns to `TYPED_PAYLOAD_COLUMNS` does not break
existing daemon code — the daemon's `INSERT` narrows to exactly the
columns it knows about.

---

## `schema_version` Stamp

```python
# src/litmus/data/event_log.py
EVENT_LOG_SCHEMA_VERSION = "1.0"

_IPC_SCHEMA = pa.schema(
    [...],
    metadata={b"schema_version": EVENT_LOG_SCHEMA_VERSION.encode()},
)
```

Every written event IPC file carries `schema_version` in its Arrow schema
metadata (the stream header). This is an **internal migration/ingest key**
— it identifies the column layout for tooling that reads raw files (e.g.,
offline migration scripts, debug utilities). It is NOT a published consumer
contract and does not appear in the Query API or operator UI.

**Reading the version from a file:**

```python
import pyarrow as pa, pyarrow.ipc as ipc

reader = ipc.open_stream(pa.OSFile("path/to/file.arrow", "rb"))
version = reader.schema.metadata.get(b"schema_version")  # e.g. b"1.0"
```

### Version history

| Version | When introduced | What changed |
|---|---|---|
| `1.0` | v0.2.x (C3 stamp) | First version stamp. Column layout: envelope + `writer_key`/`event_offset` + `json` + `TYPED_PAYLOAD_COLUMNS` as defined above. |

### When to bump

Bump `EVENT_LOG_SCHEMA_VERSION` (and add a row to the table above) when
`_IPC_SCHEMA` changes in a backward-incompatible way — a column removed,
renamed, or type-changed. Adding new promoted columns to
`TYPED_PAYLOAD_COLUMNS` is additive and does not require a version bump
(the daemon ingest path already handles wider IPC schemas gracefully).

---

## Ingest Path Safety

The daemon reads IPC files via DuckDB's Arrow registration, then narrows
to `_EVENT_COLUMNS_FROM_IPC` before inserting. The ingest code
(`src/litmus/data/_duckdb_daemon.py`, around line 83) explicitly
documents: "The IPC file's schema may be wider than what we INSERT — this
narrows it to only the columns the daemon's events table cares about."

No code in the ingest path performs schema equality comparison that
includes metadata. Adding `schema_version` to `_IPC_SCHEMA` metadata is
safe for all existing readers:

- `EventReader` (`src/litmus/data/_event_reader.py`) — accesses columns
  by name (`batch.column("json")`). No schema comparison.
- `EventAccumulator` (`src/litmus/data/backends/_event_accumulator.py`) —
  pure in-memory projection from event objects. Never reads IPC.
- `_duckdb_daemon.py` ingest — selects by name via `_EVENT_COLUMNS_FROM_IPC`.
- `read_ipc_batches` (`src/litmus/data/_ipc_writer.py:168`) — opens the
  stream and iterates batches; metadata is carried transparently.

---

## Segment Rotation

`_EventIPCWriter._on_flush` (called after each write) checks whether the
cumulative row count has reached `max_rows_per_segment` (default 10 000).
When it has, the current stream is closed (writing a valid Arrow EOS) and
the next flush opens a new segment file at `{stem}_{segment:04d}.arrow`.

Closed segments are tracked in `_EventIPCWriter.all_paths`. Crash loss is
bounded to at most `max_rows_per_segment + flush_threshold - 1` rows.
