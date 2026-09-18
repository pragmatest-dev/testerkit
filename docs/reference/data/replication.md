# Replication (`testerkit.replication`)

Two functions for forwarding events from one data dir's event log to another's: `read_segments` reads a bench's new events past a cursor, `ingest_replicated` writes them into a receiving data dir exactly once. A replication forwarder — bench to central server — is a loop over these two verbs; TesterKit does not ship the loop or the transport between bench and server, only the read and ingest halves.

Both functions operate on the [event log](../../concepts/data/event-log.md)'s on-disk [Arrow IPC WAL segments](events-schema.md); `read_segments` is the only sanctioned direct reader of those raw files. Every other reader — the operator UI, `testerkit runs`, the [Query API](query-api.md) — reads through the receiving side's [DuckDB daemon](../../concepts/data/flight-streaming.md) index instead.

## `read_segments`

```python
def read_segments(events_dir: Path, *, cursor: dict[str, int] | None = None) -> pyarrow.Table | None
```

Reads every complete event batch under `events_dir` (a data dir's `events/` subdirectory — see [storage layout](../../concepts/data/data-stores.md#storage-layout)) and returns the rows past `cursor`, or `None` if there is nothing new.

- `cursor` maps `writer_key` to the last `event_offset` already read for that writer. A row is included when its `event_offset` is greater than the cursor's value for its `writer_key` (a writer absent from `cursor` starts from offset 0). Pass `cursor=None` (the default) to read everything.
- The returned table is sorted by `(writer_key, event_offset)` — the columns needed to compute the next cursor. Sort by `session_id` first if you need per-session ordering; `read_segments` does not do that grouping.
- A segment still being appended to can end in a torn (incomplete) record. `read_segments` returns only the complete batches from such a file; the torn tail is picked up on the next call once it's flushed.
- Glob scope is one directory level: `events_dir/*/*.arrow` — every date-named subdirectory and the `_replicated` subdirectory (see below), each holding writer segments.

## `ingest_replicated`

```python
def ingest_replicated(data_dir: Path, table: pyarrow.Table) -> BatchDisposition
```

Ingests a table of events — as returned by `read_segments` on the sending side — into the event store under `data_dir` (the receiving data dir itself, not its `events/` subdirectory — note the asymmetry with `read_segments`). Empty input (`table.num_rows == 0`) returns a zero-valued `BatchDisposition` without touching the receiving store.

Three things happen, in order:

1. **Stamp.** Every row's `json` payload gets `"replicated": true` added. A session that has already received a `session.ended` event is sealed on the receiver; further event writes for that session are rejected as unexpected producer activity. The `replicated` stamp exempts these rows from that rejection, since they are known events from another data dir's log arriving after the fact, not a live producer writing past the end of its own session.
2. **do_put.** The stamped batch is sent to the receiving events [DuckDB daemon](../../concepts/data/flight-streaming.md) over Arrow Flight, and its per-batch acks — folded across every input record batch into one `BatchDisposition` — are the return value. The daemon inserts by `id` with `ON CONFLICT DO NOTHING`, so re-sending a batch that already landed is a no-op rather than a duplicate row. do_put runs before the WAL write below so the `inserted` / `deduped` split reflects the true insert result.
3. **Dual-write to WAL.** The stamped batch is written as a new segment under `data_dir/events/_replicated/`, using `EVENT_WAL_SCHEMA`, so the events survive a rebuild of the receiver's DuckDB index (which is otherwise discarded and re-derived from the WAL). That segment is also picked up by the receiver's own background ingest and deduped against the rows already inserted in step 2.

Because the WAL segment is re-ingested in the background, a later call's rows can occasionally be reported as `deduped` rather than `inserted` when a background scan reads a segment before that call's do_put — both counts mean the row is durably present, so cursor advancement is unaffected (see below).

## `BatchDisposition`

Returned by `ingest_replicated`, and by the receiving daemon's do_put ack in general.

| Field | Type | Meaning |
|-------|------|---------|
| `inserted` | `int` | Rows landed as new. |
| `deduped` | `int` | Rows already present (same `id`); dropped by `ON CONFLICT DO NOTHING`. |
| `rejected_ids` | `list[str]` | Event `id`s dropped for a reason other than dedup (a producer write rejected against an already-sealed session). |

`inserted + deduped + len(rejected_ids)` accounts for every row submitted.

**Cursor advancement.** Advance the read cursor on `inserted` + `deduped` rows only, never on `rejected_ids`. Both `inserted` and `deduped` mean the event is durably present on the receiver — a resend after a crash lands in `deduped`, not `inserted`, but is equally safe to advance past. `rejected_ids` means the row did not land; do not treat it as consumed.

## Constants

| Name | Value source | Use |
|------|--------------|-----|
| `EVENT_WAL_SCHEMA` | The event log's Arrow IPC envelope schema (see [envelope columns](events-schema.md#envelope-columns)) | Pass to `pyarrow.ipc` when reading segments directly, or when constructing a table to hand to `ingest_replicated`. |
| `EVENT_LOG_SCHEMA_VERSION` | The envelope's `schema_version` stamp (see [version stamping](events-schema.md#version-stamping)) | Compare against a segment's own stamp before assuming it matches `EVENT_WAL_SCHEMA`. |
| `EVENT_CATALOG_VERSION` | The event payload catalog's `event_catalog_version` stamp | Same, for the payload catalog rather than the envelope. |

## Identity across the hop

`id` (the dedup key), `occurred_at` (when the source raised the event), `writer_key`, and `event_offset` are carried through `ingest_replicated` unchanged — a replicated row keeps the identity it had on the sending side. `received_at` and the daemon-assigned `event_number` are re-stamped by the receiving daemon: both are defined as per-daemon-local (when the receiver's store accepted the row, and that store's own insert-order position), so re-stamping them on ingest is correct rather than a loss of information.

## Usage sketch

A forwarder loop, reading a bench's `events/` dir and pushing into a server's data dir:

```python
from pathlib import Path
from testerkit.replication import read_segments, ingest_replicated

bench_events_dir = Path("/bench/data/events")
server_data_dir = Path("/server/data")

cursor: dict[str, int] = {}
while True:
    new = read_segments(bench_events_dir, cursor=cursor)
    if new is not None:
        disposition = ingest_replicated(server_data_dir, new)
        for writer_key, offset in zip(
            new.column("writer_key").to_pylist(), new.column("event_offset").to_pylist()
        ):
            cursor[writer_key] = max(cursor.get(writer_key, -1), offset)
        if disposition.rejected_ids:
            ... # log, do not advance past these
```

TesterKit does not provide the transport that moves the table returned by `read_segments` on the bench to the process calling `ingest_replicated` on the server — that hop (a file copy, a message queue, an HTTP call) is the integrator's to build.

## See also

- [Event log concept](../../concepts/data/event-log.md) — why events are the source of truth, and how the log is consumed
- [Events schema](events-schema.md) — the envelope columns, segment rotation, and version stamps carried by every WAL segment
- [Flight streaming](../../concepts/data/flight-streaming.md) — the DuckDB daemon and Arrow Flight transport `ingest_replicated` writes through
- [Data stores](../../concepts/data/data-stores.md) — the on-disk layout of a data dir, including where `events/` sits
- [Query API](query-api.md) — the read path every other consumer of replicated events uses, instead of reading WAL segments directly
