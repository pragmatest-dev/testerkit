# Multi-UUT Testing

Litmus supports parallel testing of multiple UUTs (Devices Under Test) using a subprocess-per-slot architecture. Each UUT slot runs in its own process with isolated environment, while shared instruments are served centrally so multiple slots can drive one physical instrument without colliding.

> **Prerequisites.** Single-UUT tests already working against your station — multi-UUT is a layer on top, not a replacement (see [tutorial step 7](../../tutorial/07-real-instruments.md)). A fixture YAML defining at least two slots (template in this page). Instruments that can be channel-shared or one physical instrument per slot.

## Creating a Multi-Slot Fixture

Define slots in your [fixture YAML](../../concepts/configuration/fixtures.md). Each slot represents one UUT position:

```yaml
# fixtures/dual_board.yaml
id: dual_board
slots:
  slot_1:
    connections:
      vout:
        name: vout
        instrument: dmm
        instrument_channel: "1"
      vin:
        name: vin
        instrument: psu
        instrument_channel: "1"
  slot_2:
    connections:
      vout:
        name: vout
        instrument: dmm
        instrument_channel: "2"
      vin:
        name: vin
        instrument: psu
        instrument_channel: "2"
```

Every connection block needs a `name:` field — Litmus doesn't auto-fill it from the dict key. Omit it and the file fails to load at session start with a clear error pointing at the missing field.

Slots are spawned in YAML order and **run in parallel**, one subprocess each. The instrument channel mappings route each slot to different physical channels on shared instruments.

## Running Multi-UUT Tests

Pass `--fixture` to activate multi-UUT mode:

```bash
pytest tests/ \
  --fixture=fixtures/dual_board.yaml \
  --station=stations/my_station.yaml \
  --uut-serials slot_1=SN001,slot_2=SN002
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--fixture` | Path to fixture YAML (triggers multi-UUT mode) |
| `--uut-serial` | Single serial applied to all slots (with warning) |
| `--uut-serials` | Per-slot assignment: `slot_1=SN001,slot_2=SN002` |
| `--slot` | Run a single slot in a non-orchestrator process (e.g. for debugging one slot in isolation against the multi-UUT fixture). Supplying `--slot` and `--uut-serials` together is an error. |
| `--mock-instruments` | Use mock instruments (each slot gets independent mocks) |

## Serial Assignment

**Per-slot (recommended):**
```bash
--uut-serials slot_1=SN001,slot_2=SN002
```

**Single serial:** Using `--uut-serial` with multiple slots applies the same serial to all slots and emits a warning. This is useful for development but not recommended for production.

## Shared Instruments and InstrumentServer

When multiple slots reference the same instrument (e.g., a shared DMM or PSU), Litmus automatically:

1. Detects shared instrument roles across slots
2. Connects shared instruments once in the orchestrator process
3. Starts an InstrumentServer with per-resource locking
4. Workers get `RemoteInstrumentProxy` objects for shared roles

Mocked instruments are NOT shared -- each worker gets its own independent mock instance so mock state doesn't leak between slots.

## Sync Points

Use the `sync` fixture to coordinate between slots:

```python
def test_thermal_soak(dmm, sync):
    # All slots wait here until every slot arrives
    if sync:
        sync.wait("thermal_soak", timeout=300)

    # Now all slots measure simultaneously
    v = dmm.measure_voltage()
```

The `SyncCoordinator` (an internal helper that brokers `sync.wait()` rendezvous between slot workers) in the orchestrator process handles sync point coordination via [EventStore](../../concepts/data/event-log.md) events. If a slot dies, the coordinator unblocks remaining slots to prevent deadlocks.

## Reading Per-Slot Results

After a multi-UUT run, the terminal shows a per-slot summary:

```
============================================================
Multi-UUT Results
============================================================
  slot_1: PASS  1 passed in 2.34s
  slot_2: FAIL  1 failed in 2.51s
============================================================
```

### Execution Timeline

The results UI includes an "Execution Timeline" tab for multi-UUT runs, showing a Gantt chart of step execution across slots. This visualizes:

- Parallel execution across slots (time savings vs sequential)
- Per-step duration and outcome
- Speedup factor (sequential estimate / parallel time)

Access via: `litmus serve` then navigate to a multi-UUT result detail page.

### Parquet Data

Each measurement row includes a `slot_id` column for multi-UUT runs. Query with DuckDB:

```sql
SELECT slot_id, step_name, m.outcome, m.value
FROM read_parquet('<data_dir>/runs/**/*.parquet'), UNNEST(measurements) AS t(m)
WHERE record_type = 'vector'
  AND slot_id IS NOT NULL
ORDER BY slot_id, step_index
```

Per-run parquet files live under `<data_dir>/runs/{date}/{timestamp}_{serial}.parquet`. `<data_dir>` is the active project's data dir — resolved from `--data-dir` → project `litmus.yaml` → `LITMUS_HOME` → platform default. See [reference/parquet-schema.md](../../reference/data/parquet-schema.md) for the column shape and the `record_type` discriminator (`run` / `step` / `vector`); measurements are nested under the vector rows.

## Debugging Failures

### Environment Variables

Each worker subprocess has these env vars for debugging:

| Variable | Description |
|----------|-------------|
| `_LITMUS_SLOT_ID` | Slot identifier (e.g., `slot_1`) |
| `_LITMUS_SLOT_INDEX` | 0-based index of this slot in the orchestrator's spawn order |
| `_LITMUS_SLOT_COUNT` | Total number of slots |
| `_LITMUS_SESSION_ID` | Shared session ID across all slots |
| `LITMUS_UUT_SERIAL` | UUT serial for this slot (orchestrator sets per-slot from `--uut-serials`) |
| `LITMUS_UUT_PART_NUMBER` | UUT part number (shared across slots) |
| `LITMUS_UUT_REVISION` | UUT revision (shared) |
| `LITMUS_UUT_LOT_NUMBER` | UUT lot / batch (shared) |
| `LITMUS_UUT_RESOURCE` | Per-slot UUT control connection (e.g. `/dev/ttyUSB0`) from the slot's `uut_resource:` field |
| `LITMUS_FIXTURE_SLOT` | JSON-serialized slot configuration |
| `_LITMUS_INSTRUMENT_SERVER` | InstrumentServer address (if shared instruments) |
| `_LITMUS_SHARED_ROLES` | Comma-separated shared instrument roles |

### Viewing Per-Slot Output

Worker stdout is prefixed with `[slot_id]` in the orchestrator's output:

```
[slot_1] PASSED test_voltage
[slot_2] FAILED test_voltage - AssertionError: 3.2 < 3.0
```

### Common Issues

**Slots appear to hang:** Check if a sync point is waiting for a dead slot. The coordinator auto-unblocks after a slot dies, but custom timeouts in `sync.wait()` may need adjustment.

**Same serial warning:** If you see "Single --uut-serial applied to all N slots", use `--uut-serials` for per-slot assignment.

**Shared instrument contention:** The InstrumentServer uses per-resource locking. If tests are slow, consider whether instrument access is the bottleneck (check the execution timeline).

**Orphaned processes:** If the orchestrator crashes, worker processes are automatically terminated in the cleanup handler.


## See also

**Related quadrants:**

- [Concepts → Execution](../../concepts/execution/index.md) — concepts entry point for this category
- [Reference](../../reference/index.md) — reference entry point for this category
- [Integration](../../integration/index.md) — integration entry point for this category
- [Tutorial](../../tutorial/index.md) — tutorial entry point for this category
