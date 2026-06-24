# Step Results & the Step Manifest

The step manifest is the full list of planned steps for a run — including the ones that never ran. Step results give a complete view of every planned test step (early abort, `--maxfail`, skip markers).

## Why record planned steps

Without explicit step records, you only know about steps that actually executed. If a run aborts after step 3 of 10, the Parquet file would only have 3 rows of evidence. There's no record that 7 other steps were planned but never ran.

That matters for:

- **Yield analysis** — A 3/3 pass result is not the same as a 3/10 partial result.
- **Coverage tracking** — Which steps are consistently skipped or never reached?
- **Compliance** — Auditors need to know the full test plan, not just what ran.

## StepsDiscovered event

The `StepsDiscovered` event fires after instruments connect but before any steps execute. It carries the complete list of pytest-collected items:

```python
class StepsDiscovered(EventBase):
    event_type: Literal["test.steps_discovered"] = "test.steps_discovered"
    items: list[dict[str, str | int | None]] = Field(default_factory=list)
```

Each item in `items` contains the pytest identity and collection-time manifest data:

| Field | Description |
|-------|-------------|
| `node_id` | The step's pytest address — file plus test name, e.g. `tests/test_power.py::test_voltage` |
| `file` | Source file path |
| `module` | Python module name |
| `class_name` | Test class name (if any) |
| `function` | Test function name |
| `markers` | pytest markers on the item |
| `step_path` | Hierarchical step identifier, matching executed step events |
| `parent_path` | Parent step path (class container, if any) |
| `step_index` | Position within the parent sequence |
| `vector_index` | 0-based position within the sweep expansion |
| `vector_count_planned` | Total vectors collected for this logical step — drives placeholder row synthesis for unrun vectors |

## How it flows

When the run ends, Litmus writes one row per planned step — executed steps with their real outcome and timing, and a placeholder row for any step that never started.

## Storage

There is **one parquet file per run**. Run, step, and vector records share the same file; the [`record_type`](../../reference/data/parquet-schema.md) column says which kind each row is. Measurements are stored inside each vector row:

```
<data_dir>/runs/{date}/
└── {timestamp}_{serial}.parquet          # All rows for one run
   ├── record_type='run'                  # exactly one row, run-level metadata
   ├── record_type='step'                 # one row per (step_path, vector_index, retry)
   └── record_type='vector'               # one row per execution; nests the measurements list
```

Key step-row columns (full list in [Parquet schema](../../reference/data/parquet-schema.md)):

- `step_name`, `step_path`, `step_index`, `parent_path`, `step_node_id`
- `step_started_at`, `step_ended_at`, `step_vector_count`
- `step_outcome` (rollup), `vector_outcome` (per vector), `run_outcome` (run-wide)
- Run context repeated on every step row (so a step row is self-contained): `run_id`, `uut_serial`, `station_id`, `session_id`

## Steps that never ran {#never-ran}

A run can finish before every planned step executes — an early abort, `--maxfail`, or a skip. Litmus still records those steps so the run shows the full plan, not just what ran. Each unrun step gets a placeholder row with a blank outcome and no start/end time (query hint: `step_outcome IS NULL`).

- `step_outcome`: NULL
- Timing fields: NULL
- Step identity columns: populated from the collected item

Every run thus has a complete picture — executed steps with real data, plus placeholder rows for the rest.

## Querying step results

With DuckDB:

```sql
-- Step summary for one run
SELECT step_name, step_outcome, step_started_at, step_ended_at
FROM read_parquet('data/runs/**/*.parquet')
WHERE record_type = 'step'
  AND run_id = 'abc123'
ORDER BY step_index;

-- Find steps that are frequently skipped or never run
SELECT step_name,
       COUNT(*) AS total,
       SUM(CASE WHEN step_outcome IS NULL THEN 1 ELSE 0 END) AS never_ran
FROM read_parquet('data/runs/**/*.parquet')
WHERE record_type = 'step'
GROUP BY step_name
HAVING never_ran > 0;
```

From Python (via `RunStore`):

```python
from litmus.data.run_store import RunStore

steps = RunStore().get_steps("abc123")
for step in steps:
    print(f"{step['step_name']}: {step['outcome']}")
```

From the event store:

```python
store.events(event_type="test.steps_discovered", session_id=sid)
```

## See also

- [Event log](../data/event-log.md) — how events get to Parquet
- [Parquet schema](../../reference/data/parquet-schema.md) — full column list
- [Data stores](../data/data-stores.md) — EventStore, ChannelStore, FileStore, RunStore
