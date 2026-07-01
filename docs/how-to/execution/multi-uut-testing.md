# Multi-UUT Testing

Litmus runs multiple UUTs in parallel, one **site** per UUT — the same parallel-position concept STDF calls `SITE_NUM` and NI TestStand calls a "test socket." Each site is isolated, and a shared instrument (one physical DMM or PSU) can drive every site without sites colliding on it. This page shows how to define the sites and run them.

> **Prerequisites.** Single-UUT tests already working against your station — multi-UUT is a layer on top, not a replacement (see [tutorial step 7](../../tutorial/07-real-instruments.md)). A fixture YAML defining at least two sites (template in this page). Instruments that can be channel-shared or one physical instrument per site.

## Creating a Multi-Site Fixture

Define sites in your [fixture YAML](../../concepts/configuration/fixtures.md). Sites are an ordered list — each entry represents one UUT position, and its 0-based position in the list is its `site_index`:

```yaml
# fixtures/dual_board.yaml
id: dual_board
sites:
  - name: left
    uut_resource: /dev/ttyUSB0
    connections:
      vout:
        name: vout
        instrument: dmm
        instrument_channel: "1"
      vin:
        name: vin
        instrument: psu
        instrument_channel: "1"
  - name: right
    uut_resource: /dev/ttyUSB1
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

`uut_resource:` is a sibling of `name:` and `connections:` on each site — a per-site UUT control connection string (a COM port, a USB serial number). It's optional; set it when your test code talks to the UUT directly (e.g. to read a serial number over a debug UART) rather than only through instruments.

The `name:` on each site is optional — omit it and the site is referred to as "site N" by its index. Sites run in parallel, in list order. The `instrument_channel` mappings route each site to its own channel on a shared instrument.

## Running Multi-UUT Tests

Pass `--fixture` with a multi-site fixture (2+ sites) to run sites in parallel:

```bash
pytest tests/ \
  --fixture=fixtures/dual_board.yaml \
  --station=stations/my_station.yaml \
  --uut-serials 0=SN001,1=SN002
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--fixture` | Path to fixture YAML (2+ sites → parallel sites) |
| `--uut-serial` | Single serial applied to all sites (with warning) |
| `--uut-serials` | Per-site assignment — positional (`SN001,SN002`), by index (`0=SN001,1=SN002`), or by name (`left=SN001,right=SN002`); see [Serial Assignment](#serial-assignment) |
| `--site` | Target one site, by index or name, for a single-process run — useful for debugging one UUT position in isolation. Setting `--site` always runs single-process, even against a multi-site fixture; pair it with `--uut-serial` for that site's identity, not `--uut-serials` (see below). |
| `--mock-instruments` | Use mock instruments (each site gets independent mocks) |

## Serial Assignment

`--uut-serials` takes one string, auto-detected as one of three forms. All three are **dense** — every site the fixture config declares needs a serial in the string, or the run fails at launch with `No UUT serial for site N`. There is no way to leave a configured site idle for one launch; the fixture's `sites:` list is what defines which sites exist for every run against it.

**Positional** — no `=`, one serial per site in list order (first serial → `site_index 0`):
```bash
--uut-serials SN001,SN002
```
Requires the serial count to match the site count exactly — positional means "all of them, in order."

**Indexed** — `site_index=serial` pairs, any order:
```bash
--uut-serials 0=SN001,1=SN002
```

**Named** — `site_name=serial` pairs, resolved against each site's `name:` in the fixture YAML:
```bash
--uut-serials left=SN001,right=SN002
```
Requires every site referenced by name to actually have a `name:` set — a token that doesn't parse as an integer is matched against `name:` only, never against the index. Against a fixture with unnamed sites this fails with `Unknown site name 'left'. Available: [0], [1]`. Use the indexed form (`0=SN001,1=SN002`) for fixtures whose sites have no `name:`.

A single `--uut-serials` string is positional **or** keyed, never both — any `=` in the string switches every entry to keyed parsing, so `SN001,1=SN002` is a parse error rather than a silent partial match.

**Single serial:** Using `--uut-serial` with multiple sites applies the same serial to all sites and emits a warning. This is useful for development but not recommended for production.

## Reading Per-Site Results

After a multi-UUT run, the terminal shows a per-site summary:

```
============================================================
Multi-UUT Results
============================================================
  site[0]: PASS  1 passed in 2.34s
  site[1]: FAIL  1 failed in 2.51s
============================================================
```

### Execution Timeline

The results UI includes an "Execution Timeline" tab for multi-UUT runs, showing a Gantt chart of step execution across sites. This visualizes:

- Parallel execution across sites (time savings vs sequential)
- Per-step duration and outcome
- Speedup factor (sequential estimate / parallel time)

Access via: `litmus serve` then navigate to a multi-UUT result detail page.

### Parquet Data

Each measurement row includes `site_index` and `site_name` columns for multi-UUT runs. Query with DuckDB:

```sql
SELECT site_index, site_name, step_name, m.outcome, m.value
FROM read_parquet('<data_dir>/runs/**/*.parquet'), UNNEST(measurements) AS t(m)
WHERE record_type = 'vector'
  AND site_index IS NOT NULL
ORDER BY site_index, step_index
```

Per-run parquet files live under `<data_dir>/runs/{date}/{timestamp}_{run_id8}_{serial}.parquet`. `<data_dir>` is the active project's data dir — resolved from `--data-dir` → project `litmus.yaml` → `LITMUS_HOME` → platform default. See [reference/parquet-schema.md](../../reference/data/parquet-schema.md) for the column shape and the `record_type` discriminator (`run` / `step` / `vector`); measurements are nested under the vector rows.

### Events

`site_index` / `site_name` aren't columns bolted onto the query layer after the fact — they're stamped at emit time and carried through:

- The parent process that dispatches the per-site subprocesses (the **orchestrator**) records the total site count in its own session-open event, before any site subprocess (a **worker**) starts.
- Each worker emits its own run-start event carrying **its** `site_index` and `site_name` — this is the freeze point. Rename a site in the fixture YAML next month and this run's row still reads the name that was active when it ran.
- Per-site start/end events mark when each worker begins and finishes, independent of the individual test steps inside it.

See [event-types reference](../../reference/data/event-types.md) for the full field list on each event.

## Sharing One Instrument Across Sites

When two sites map to the same instrument role, Litmus connects it once and lets every site use it safely — calls are serialized so two sites never talk to it at the same time. You write your test exactly as in the single-UUT case; the shared connection is transparent.

Mock instruments are NOT shared — each site gets its own mock so mock state never leaks between sites.

## Sync Points

Use the [`sync`](../../reference/pytest/fixtures.md#sync-session) fixture to hold all sites at a named point until every site arrives:

```python
def test_thermal_soak(dmm, sync):
    # All sites wait here until every site arrives
    if sync:
        sync.wait("thermal_soak", timeout=300)

    # Now all sites measure simultaneously
    v = dmm.measure_voltage()
```

`sync.wait("label", timeout=...)` blocks each site until every site reaches the same labeled point, then releases them together. If a site fails or exits before reaching the point, Litmus releases the remaining sites automatically so the run does not get stuck.

## Debugging Failures

### Environment Variables

Inside a site's test process these identify the UUT, so your test or a serial-port helper can read them (see also [reference/cli.md → Environment variables](../../reference/cli.md#environment-variables) for the full platform-wide list):

| Variable | Description |
|----------|-------------|
| `LITMUS_UUT_SERIAL` | UUT serial for this site |
| `LITMUS_UUT_PART_NUMBER` | UUT part number (shared across sites) |
| `LITMUS_UUT_REVISION` | UUT revision (shared across sites) |
| `LITMUS_UUT_LOT_NUMBER` | UUT lot / batch (shared across sites) |
| `LITMUS_UUT_RESOURCE` | Per-site UUT control connection (e.g. `/dev/ttyUSB0`) from the site's `uut_resource:` field |

### Viewing Per-Site Output

Site stdout is prefixed with `[site:N]` in the terminal output:

```
[site:0] PASSED test_voltage
[site:1] FAILED test_voltage - AssertionError: 3.2 < 3.0
```

### Common Issues

**Sites appear to hang:** A `sync.wait()` may be waiting on a site that already failed. Litmus releases the other sites automatically when a site exits, but shorten a too-long `timeout=` if the wait is the bottleneck.

**Same serial warning:** If you see "Single --uut-serial applied to all N sites", use `--uut-serials` for per-site assignment.

**`--uut-serials` looks ignored when `--site` is also set:** `--site` always runs single-process against the one site you named — it never reads `--uut-serials`. Use `--uut-serial` for that site's identity instead.

**Sparse launches aren't supported yet:** every site the fixture config declares needs a serial in `--uut-serials` (or all sites share one `--uut-serial`). There is no way to launch a parallel run that leaves some configured sites out and only exercises a subset — target one site at a time with `--site` instead.

**Shared instrument is the bottleneck:** Sites queue for a shared instrument — check the Execution Timeline to see whether sites are waiting on instrument access.

**Orphaned site processes:** On normal teardown or Ctrl-C, Litmus terminates every site subprocess automatically. A hard kill (e.g. `kill -9` on the parent) bypasses this cleanup and can leave orphaned site processes behind.


## See also

- [Litmus fixtures → `sync`](../../reference/pytest/fixtures.md#sync-session) — the sync-point fixture's full API
- [CLI reference](../../reference/cli.md) — every flag and environment variable used on this page
- [Configuration reference → Fixture YAML](../../reference/configuration.md#fixture-yaml) — field-by-field `sites:` / `connections:` schema
- [Operator UI → Results detail](../../reference/operator-ui/results/detail.md) — the Execution Timeline tab in the browser
- [Event types reference](../../reference/data/event-types.md) — full field list for `SiteStarted` / `SiteCompleted` / `SessionStarted` / `RunStarted`

**Related quadrants:**

- [Concepts → Execution](../../concepts/execution/index.md) — concepts entry point for this category
- [Reference](../../reference/index.md) — reference entry point for this category
- [Integration](../../integration/index.md) — integration entry point for this category
- [Tutorial](../../tutorial/index.md) — tutorial entry point for this category
