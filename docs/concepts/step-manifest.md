# Step Manifest & StepsDiscovered

The step manifest provides a complete view of all planned test steps — including those that never ran due to early abort, `--maxfail`, or skip markers.

## The Problem

Without a manifest, you only know about steps that actually executed. If a test run aborts after step 3 of 10, the Parquet file only has 3 rows. There's no record that 7 other steps were planned but never ran.

This matters for:

- **Yield analysis** — A 3/3 pass result looks different from a 3/10 partial result
- **Coverage tracking** — Which steps are consistently skipped or never reached?
- **Compliance** — Auditors need to know the full test plan, not just what ran

## StepsDiscovered Event

The `StepsDiscovered` event is emitted after instruments connect but before any steps execute. It carries the complete list of pytest-collected items:

```python
class StepsDiscovered(EventBase):
    event_type: Literal["test.steps_discovered"] = "test.steps_discovered"
    items: list[dict[str, str | None]] = Field(default_factory=list)
```

Each item in `items` contains:

| Field | Description |
|-------|-------------|
| `node_id` | pytest node ID (e.g., `tests/test_power.py::test_voltage`) |
| `name` | Test function name |
| `file` | Source file path |
| `module` | Python module name |
| `class_name` | Test class name (if any) |
| `function` | Function name |

## How It Flows

```
pytest collection
    │
    ▼
StepsDiscovered event  ──► EventLog.emit()
    │                           │
    ▼                           ▼
ParquetSubscriber          Arrow IPC file
    │
    ▼
Step manifest in Parquet metadata
(litmus_collected_items key)
```

The `ParquetSubscriber` stores the collected items in Parquet file-level metadata under the `litmus_collected_items` key. When the run ends, any items that never produced a `StepStarted` event get `not_started` status rows appended.

## `not_started` Status

After `RunEnded`, the subscriber compares the discovered items against actually-executed steps. Missing steps get synthetic rows with:

- `outcome`: `"not_started"`
- All measurement fields: `None`
- Step metadata: populated from the collected item

This means every Parquet file has a complete picture — executed steps with real data, plus `not_started` entries for steps that were planned but never ran.

## Querying the Manifest

From Parquet files:

```python
import pyarrow.parquet as pq

meta = pq.read_metadata("results/runs/2026-03-10/run.parquet")
items = meta.metadata[b"litmus_collected_items"]
# JSON list of collected items
```

From the event store:

```python
store.events(event_type="test.steps_discovered", session_id=sid)
```

## See Also

- [Event Log Architecture](event-log.md) — How events are stored
- [Parquet Schema Reference](../reference/parquet-schema.md) — Full Parquet schema details
