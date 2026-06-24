# Multi-UUT Testing

Litmus runs multiple UUTs in parallel, one slot per UUT. Each slot is isolated, and a shared instrument (one physical DMM or PSU) can drive every slot without slots colliding on it. This page shows how to define the slots and run them.

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

Slots run in parallel, in YAML order. The `instrument_channel` mappings route each slot to its own channel on a shared instrument.

## Running Multi-UUT Tests

Pass `--fixture` with a multi-slot fixture (2+ slots) to run slots in parallel:

```bash
pytest tests/ \
  --fixture=fixtures/dual_board.yaml \
  --station=stations/my_station.yaml \
  --uut-serials slot_1=SN001,slot_2=SN002
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--fixture` | Path to fixture YAML (2+ slots → parallel slots) |
| `--uut-serial` | Single serial applied to all slots (with warning) |
| `--uut-serials` | Per-slot assignment: `slot_1=SN001,slot_2=SN002` |
| `--slot` | Run just one slot of the fixture by itself — useful for debugging a single UUT position in isolation. Cannot be combined with `--uut-serials`. |
| `--mock-instruments` | Use mock instruments (each slot gets independent mocks) |

## Serial Assignment

**Per-slot (recommended):**
```bash
--uut-serials slot_1=SN001,slot_2=SN002
```

**Single serial:** Using `--uut-serial` with multiple slots applies the same serial to all slots and emits a warning. This is useful for development but not recommended for production.

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

Per-run parquet files live under `<data_dir>/runs/{date}/{timestamp}_{run_id8}_{serial}.parquet`. `<data_dir>` is the active project's data dir — resolved from `--data-dir` → project `litmus.yaml` → `LITMUS_HOME` → platform default. See [reference/parquet-schema.md](../../reference/data/parquet-schema.md) for the column shape and the `record_type` discriminator (`run` / `step` / `vector`); measurements are nested under the vector rows.

## Sharing One Instrument Across Slots

When two slots map to the same instrument role, Litmus connects it once and lets every slot use it safely — calls are serialized so two slots never talk to it at the same time. You write your test exactly as in the single-UUT case; the shared connection is transparent.

Mock instruments are NOT shared — each slot gets its own mock so mock state never leaks between slots.

## Sync Points

Use the `sync` fixture to hold all slots at a named point until every slot arrives:

```python
def test_thermal_soak(dmm, sync):
    # All slots wait here until every slot arrives
    if sync:
        sync.wait("thermal_soak", timeout=300)

    # Now all slots measure simultaneously
    v = dmm.measure_voltage()
```

`sync.wait("label", timeout=...)` blocks each slot until every slot reaches the same labeled point, then releases them together. If a slot fails or exits before reaching the point, the remaining slots are released automatically so the run does not get stuck.

## Debugging Failures

### Environment Variables

Inside a slot's test process these identify the UUT, so your test or a serial-port helper can read them:

| Variable | Description |
|----------|-------------|
| `LITMUS_UUT_SERIAL` | UUT serial for this slot |
| `LITMUS_UUT_PART_NUMBER` | UUT part number (shared across slots) |
| `LITMUS_UUT_REVISION` | UUT revision (shared across slots) |
| `LITMUS_UUT_LOT_NUMBER` | UUT lot / batch (shared across slots) |
| `LITMUS_UUT_RESOURCE` | Per-slot UUT control connection (e.g. `/dev/ttyUSB0`) from the slot's `uut_resource:` field |
| `LITMUS_FIXTURE_SLOT` | JSON-serialized slot configuration |

### Viewing Per-Slot Output

Slot stdout is prefixed with `[slot_id]` in the terminal output:

```
[slot_1] PASSED test_voltage
[slot_2] FAILED test_voltage - AssertionError: 3.2 < 3.0
```

### Common Issues

**Slots appear to hang:** A `sync.wait()` may be waiting on a slot that already failed. Litmus releases the other slots automatically when a slot exits, but shorten a too-long `timeout=` if the wait is the bottleneck.

**Same serial warning:** If you see "Single --uut-serial applied to all N slots", use `--uut-serials` for per-slot assignment.

**Shared instrument is the bottleneck:** Slots queue for a shared instrument — check the Execution Timeline to see whether slots are waiting on instrument access.

**Orphaned slot processes:** On normal teardown or Ctrl-C, all slots are shut down automatically, so you shouldn't be left with orphaned slot processes. A hard kill (e.g. `kill -9` on the parent) can bypass this cleanup.


## See also

**Related quadrants:**

- [Concepts → Execution](../../concepts/execution/index.md) — concepts entry point for this category
- [Reference](../../reference/index.md) — reference entry point for this category
- [Integration](../../integration/index.md) — integration entry point for this category
- [Tutorial](../../tutorial/index.md) — tutorial entry point for this category
