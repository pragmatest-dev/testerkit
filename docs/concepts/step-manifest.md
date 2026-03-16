# Step Results & StepsDiscovered

Step results provide a complete view of all planned test steps — including those that never ran due to early abort, `--maxfail`, or skip markers.

## The Problem

Without step results, you only know about steps that actually executed. If a test run aborts after step 3 of 10, the Parquet file only has 3 rows. There's no record that 7 other steps were planned but never ran.

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
step_results in Parquet file-level metadata
```

The `ParquetSubscriber` caches the collected items in memory. When the run ends, it builds step results: executed steps get real outcomes, and any items that never produced a `StepStarted` event get `not_started` status. The combined list is stored in Parquet metadata under the `step_results` key.

## `not_started` Status

After `RunEnded`, the subscriber compares the discovered items against actually-executed steps. Missing steps get synthetic entries with:

- `outcome`: `"not_started"`
- All measurement fields: `None`
- Step metadata: populated from the collected item

This means every Parquet file has a complete picture — executed steps with real data, plus `not_started` entries for steps that were planned but never ran.

## Querying Step Results

From Parquet files:

```python
from litmus.data.backends.parquet import read_step_results

results = read_step_results(Path("results/runs/2026-03-10/run.parquet"))
for step in results:
    print(f"{step['name']}: {step['outcome']}")
```

From the event store:

```python
store.events(event_type="test.steps_discovered", session_id=sid)
```

## See Also

- [Event Log Architecture](event-log.md) — How events are stored
- [Parquet Schema Reference](../reference/parquet-schema.md) — Full Parquet schema details
