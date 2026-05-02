# Multi-DUT Testing

Litmus supports parallel testing of multiple DUTs (Devices Under Test) using a subprocess-per-slot architecture. Each DUT slot runs in its own process with isolated environment, while shared instruments are served centrally via an InstrumentServer.

## Creating a Multi-Slot Fixture

Define slots in your fixture YAML. Each slot represents one DUT position:

```yaml
# fixtures/dual_board.yaml
id: dual_board
slots:
  slot_1:
    connections:
      vout:
        instrument: dmm
        instrument_channel: "1"
      vin:
        instrument: psu
        instrument_channel: "1"
  slot_2:
    connections:
      vout:
        instrument: dmm
        instrument_channel: "2"
      vin:
        instrument: psu
        instrument_channel: "2"
```

Slots are executed in definition order (not alphabetical). The instrument channel mappings route each slot to different physical channels on shared instruments.

## Running Multi-DUT Tests

Pass `--fixture` to activate multi-DUT mode:

```bash
pytest tests/ \
  --fixture=fixtures/dual_board.yaml \
  --station=stations/my_station.yaml \
  --dut-serials slot_1=SN001,slot_2=SN002
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--fixture` | Path to fixture YAML (triggers multi-DUT mode) |
| `--dut-serial` | Single serial applied to all slots (with warning) |
| `--dut-serials` | Per-slot assignment: `slot_1=SN001,slot_2=SN002` |
| `--mock-instruments` | Use mock instruments (each slot gets independent mocks) |

## Serial Assignment

**Per-slot (recommended):**
```bash
--dut-serials slot_1=SN001,slot_2=SN002
```

**Single serial:** Using `--dut-serial` with multiple slots applies the same serial to all slots and emits a warning. This is useful for development but not recommended for production.

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

The `SyncCoordinator` in the orchestrator process handles sync point coordination via EventStore events. If a slot dies, the coordinator unblocks remaining slots to prevent deadlocks.

## Reading Per-Slot Results

After a multi-DUT run, the terminal shows a per-slot summary:

```
============================================================
Multi-DUT Results
============================================================
  slot_1: PASS  1 passed in 2.34s
  slot_2: FAIL  1 failed in 2.51s
============================================================
```

### Execution Timeline

The results UI includes an "Execution Timeline" tab for multi-DUT runs, showing a Gantt chart of step execution across slots. This visualizes:

- Parallel execution across slots (time savings vs sequential)
- Per-step duration and outcome
- Speedup factor (sequential estimate / parallel time)

Access via: `litmus serve` then navigate to a multi-DUT result detail page.

### Parquet Data

Each measurement row includes a `slot_id` column for multi-DUT runs. Query with DuckDB:

```sql
SELECT slot_id, step_name, outcome, value
FROM read_parquet('results/**/*.parquet')
WHERE slot_id IS NOT NULL
ORDER BY slot_id, step_index
```

## Debugging Failures

### Environment Variables

Each worker subprocess has these env vars for debugging:

| Variable | Description |
|----------|-------------|
| `_LITMUS_SLOT_ID` | Slot identifier (e.g., `slot_1`) |
| `LITMUS_DUT_SERIAL` | DUT serial for this slot |
| `_LITMUS_SESSION_ID` | Shared session ID across all slots |
| `_LITMUS_SLOT_COUNT` | Total number of slots |
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

**Same serial warning:** If you see "Single --dut-serial applied to all N slots", use `--dut-serials` for per-slot assignment.

**Shared instrument contention:** The InstrumentServer uses per-resource locking. If tests are slow, consider whether instrument access is the bottleneck (check the execution timeline).

**Orphaned processes:** If the orchestrator crashes, worker processes are automatically terminated in the cleanup handler.
