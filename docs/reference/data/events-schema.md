# Event Log Storage Schema

Every TesterKit event — session start, a measurement, a dialog response — lands in the **event log** as one row in an **Arrow IPC stream file** (`.arrow` extension). These are standard Apache Arrow IPC format files: DuckDB, pandas, Polars, and PyArrow all read them directly without TesterKit.

This page covers the on-disk layout, the envelope columns every event row carries, and the version stamp. For what each event type carries inside its payload, see [Event types](event-types.md).

## On-disk layout

```
<data_dir>/events/{date}/
├── {session_id}-{pid}.arrow           # First segment for this writer
├── {session_id}-{pid}_0001.arrow      # Rotation: written once the segment hits 10,000 events
├── {session_id}-{pid}_0002.arrow      # Further rotations as the session runs long
└── ...
```

`{date}` is the UTC date the writer opened its first file for the session (`YYYY-MM-DD`). `{session_id}` is the session UUID; `{pid}` is the OS process ID of the writer. A session with one process — a single `pytest` invocation — produces one filename stem. A multi-process session, such as an orchestrator plus per-site worker processes on a multi-UUT run, produces one file per process, all under the same session and date. No two processes ever write the same file.

**Segment rotation.** Instead of one file growing without bound, a writer closes the current segment and opens a new numbered one once the segment has accumulated 10,000 events. A closed segment carries a valid Arrow end-of-stream and is immediately readable. Reading a session's complete event history means reading every segment for every writer PID under that session — not just the first file.

## Arrow IPC format

Each `.arrow` file is an Arrow IPC **stream** (not a random-access file). Open it with `pyarrow.ipc.open_stream`:

```python
import pyarrow.ipc as ipc
import pyarrow as pa

reader = ipc.open_stream(
    pa.OSFile("data/events/2026-06-01/3f6b1a2c-0d4e-4f8a-b2c7-1e3d5a7f9b0e-83421.arrow", "rb")
)
table = reader.read_all()
print(table.schema)
print(table.to_pandas())
```

Or with DuckDB, globbing across every writer and segment for one session in a single query:

```sql
SELECT * FROM read_ipc_stream(
    'data/events/2026-06-01/3f6b1a2c-0d4e-4f8a-b2c7-1e3d5a7f9b0e-*.arrow'
)
ORDER BY session_id, writer_key, event_offset;
```

## Envelope columns

Every event row — regardless of `event_type` — carries this fixed set of columns:

| Column | Arrow type | Description |
|--------|-----------|-------------|
| `id` | `utf8` | Event UUID. Unique across the whole log |
| `event_type` | `utf8` | Discriminator string, e.g. `"session.started"`, `"test.measurement"`. Full catalog: [Event types](event-types.md) |
| `occurred_at` | `timestamp[us, UTC]` | When the event happened, stamped by the code that raised it |
| `received_at` | `timestamp[us, UTC]` | When the event log accepted it for writing |
| `session_id` | `utf8` | Session UUID this event belongs to |
| `run_id` | `utf8` (nullable) | Run UUID, if the event is scoped to a run. Null for session-scoped events (`session.started`, `session.ended`) |
| `writer_key` | `utf8` | UUID of the writer that emitted this event — one per process per session. Concurrent processes writing the same session never share a `writer_key` |
| `event_offset` | `int64` | This writer's emit position, starting at 0 and increasing by one per event. Combined with `writer_key`, gives the true emit order for one process, independent of how batches later land on disk |
| `json` | `utf8` | The full event payload — every field of the typed event class — as a JSON object. This is the lossless record; nothing is dropped here even when a field is also promoted to its own column below |

To recover one event's full, typed payload, parse its `json` column:

```python
from testerkit.data.events import Event

event = Event.model_validate_json(row["json"])   # picks the subclass by event_type
```

### Promoted identifier and name columns

Every event row also carries a fixed set of `utf8` columns promoted out of the JSON payload, so a query can filter on them directly instead of unpacking `json` first. A column is null on any event type whose payload doesn't define that field — the value, when present, also still lives inside `json`.

| Column | Carries |
|--------|---------|
| `file_id`, `dialog_id`, `channel_id` | Pairing IDs — an "opened" event and its matching "closed"/"responded" event share the same value |
| `uut_serial_number`, `station_hostname` | The identifiers an operator actually filters by |
| `instrument_id`, `node_id`, `step_path`, `fixture_id`, `operator_id`, `station_id` | Other identifiers used to scope a query |
| `step_name`, `measurement_name`, `name` | Human-readable names |
| `role`, `instrument_role` | Instrument role — the same value under two field names, kept for filter compatibility |
| `outcome`, `reason`, `format`, `dialog_type`, `response_type` | Small enums used in routine filters |

## Ordering

Events from one writer land in `event_offset` order, but a session with multiple writers — an orchestrator plus workers — interleaves multiple offset sequences. Sort by `(session_id, writer_key, event_offset)` to reconstruct each writer's true emit order. Sort by `received_at` to reconstruct arrival order across writers.

## Version stamping

Each `.arrow` file carries two keys in its Arrow schema metadata:

| Key | Description |
|-----|-------------|
| `schema_version` | Version of the envelope — the column layout described above. Current: `"0.1"` |
| `event_catalog_version` | Version of the event payload catalog — the set of `event_type` values and what each carries inside `json`. Current: `"0.1"` |

These two version numbers move independently. A new event type, or a new field on an existing event type, is a catalog change — it rides inside `json`, additive — not an envelope change. The envelope only changes when the column layout itself changes.

Read both stamps in Python:

```python
import pyarrow.ipc as ipc
import pyarrow as pa

reader = ipc.open_stream(
    pa.OSFile("3f6b1a2c-0d4e-4f8a-b2c7-1e3d5a7f9b0e-83421.arrow", "rb")
)
meta = reader.schema.metadata
print(meta[b"schema_version"])           # b"0.1"
print(meta[b"event_catalog_version"])    # b"0.1"
```

A file with no `schema_version` stamp, or a stamp TesterKit doesn't recognize, is skipped rather than blocking the rest of the log — one bad or unstamped file never stalls ingestion of the good ones. Regenerate an unstamped file if you need its events read.

## See also

- [Event types](event-types.md) — every `event_type`, its fields, and defaults
- [Event log concept](../../concepts/data/event-log.md) — why event sourcing, and how the log is consumed
- [Channels schema](channels-schema.md) — the sibling Arrow IPC format for streaming numeric channels
- [Files schema](files-schema.md) — the sibling blob + sidecar format for file artifacts
- [Query API](query-api.md) — how to query events through TesterKit instead of reading files directly
