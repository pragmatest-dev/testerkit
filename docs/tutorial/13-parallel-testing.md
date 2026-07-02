# Step 13: Parallel Testing

**Goal:** Scale one bench from one UUT to two, tested at the same time, with zero changes to test code.

## Prerequisites

- [Step 7: Real Instruments](07-real-instruments.md) — station YAML, instrument channels
- [Step 9: Production Ready](09-production.md) — fixture YAML, pin-to-instrument mapping, sidecar limits

## The scenario

One bench, two UUT positions, tested in parallel. Each position is a **site** — the same parallel-position concept STDF calls `SITE_NUM` and NI TestStand calls a "test socket." A fixture that declares two or more sites turns a bare `pytest` invocation into a parallel run: no `--fixture` flag, no test rewrite, no per-UUT script.

A site's identity is its **0-based position in the fixture's `sites:` list** — `site_index`. It's never a field you set; it's just where the site sits in the list. `site_index 0` gets an optional `name:` label — a human-readable tag like `left` that has no bearing on execution order.

## Growing a fixture to two sites

A single-UUT fixture wires `connections:` directly. A multi-UUT fixture replaces that with `sites:` — an ordered list, one entry per UUT position, each with its own `connections:`:

```yaml
# fixtures/dual_site_bench.yaml
id: dual_site_bench
name: Dual-Site Bench Fixture
description: >-
  Two-UUT parallel fixture — left/right sites share the bench's psu
  and dmm but land on different instrument channels.
part_id: dual_rail_uut
station_types: [bench]
sites:
  - name: left
    connections:
      vout:
        name: vout
        instrument: dmm
        instrument_channel: "1"
        instrument_terminal: hi
        uut_pin: TP_VOUT
        net: VOUT_3V3
      vin:
        name: vin
        instrument: psu
        instrument_channel: "1"
        instrument_terminal: hi
        uut_pin: TP_VIN
        net: VIN_5V
  - name: right
    connections:
      vout:
        name: vout
        instrument: dmm
        instrument_channel: "2"
        instrument_terminal: hi
        uut_pin: TP_VOUT
        net: VOUT_3V3
      vin:
        name: vin
        instrument: psu
        instrument_channel: "2"
        instrument_terminal: hi
        uut_pin: TP_VIN
        net: VIN_5V
```

`left` is `site_index 0`, `right` is `site_index 1` — list position, not an authored field. Both sites route to the same instrument roles (`dmm`, `psu`) on the station, but each lands on its own `instrument_channel` — `left` on channel `"1"`, `right` on channel `"2"`. That's the whole trick: one physical DMM, one physical PSU, two UUT positions, no collision.

Two or more sites makes this fixture **multi-site**, which is what fans a bare `pytest` invocation into parallel execution. One site is just the single-UUT case you already know — `connections:` directly on the fixture is shorthand for one unnamed site. For the full field-by-field schema, see [Fixture YAML reference](../reference/configuration.md#fixture-yaml); for the design rationale behind sites and shared instruments, see [Fixtures → Multi-UUT scaling](../concepts/configuration/fixtures.md#multi-uut-scaling-sites-shared-instruments-switching).

## The station and the test don't change

The station YAML declares `psu` and `dmm` exactly as a single-UUT bench would — it has no idea there are two sites:

```yaml
# stations/bench_dual.yaml
id: bench_dual
name: Dual-Site Bench
station_type: bench
instruments:
  psu:
    type: psu
    driver: drivers.PSU
    resource: TCPIP::192.168.1.101::INSTR
    mock_config:
      set_voltage: 5.0
      set_current: 0.5
      measure_voltage: 5.0
      measure_current: 0.042
  dmm:
    type: dmm
    driver: drivers.DMM
    resource: TCPIP::192.168.1.102::INSTR
    mock_config:
      measure_dc_voltage: 3.3
      measure_dc_current: 0.042
```

Neither does the test:

```python
# tests/test_dual_rail.py

def test_vout_within_spec(verify, psu, dmm, context) -> None:
    """5 V PSU input -> ~3.3 V DMM readback, on this site's connections."""
    psu.set_voltage(5.0)
    psu.set_current(0.5)
    for _ in context.connections:
        verify("vout", dmm.measure_dc_voltage())
```

```yaml
# tests/test_dual_rail.yaml — sidecar
tests:
  test_vout_within_spec:
    connections: [vout]
    limits:
      vout: {low: 3.2, high: 3.4, unit: V}
```

`context.connections` iterates the connections the sidecar's `connections: [vout]` list resolved for *this site* — see the [`connections` fixture reference](../reference/pytest/fixtures.md#connections-function). The test never names `left`, `right`, a channel, or an index; it just reads `dmm` and `psu` through its own site's wiring. The site-specific channel routing lives entirely in the fixture YAML from the previous section — that's what let the station and test files stay unmodified while the fixture grew from one site to two.

## Launching a parallel run

`pytest.ini` assigns a serial to each named site by default, so a bare `pytest` is already a complete parallel run:

```ini
# pytest.ini
[pytest]
addopts = --mock-instruments --uut-serials left=SN-A,right=SN-B
```

```cli
cd examples/12-parallel-sites
uv run pytest -q
```

```
[site:0] .                                                                        [100%]
[site:0] 1 passed in 0.43s
[site:1] .                                                                        [100%]
[site:1] 1 passed in 0.43s

============================================================
Multi-UUT Results
============================================================
  site[0]: PASS  1 passed in 0.43s
  site[1]: PASS  1 passed in 0.43s
============================================================
```

A fixture with 2+ sites is what triggers this: the parent process (the **orchestrator**) spawns one **worker** subprocess per site, each running the full test session against its own site's connections. `[site:N]` prefixes each worker's stdout as it streams back — the two lines interleave as the workers run concurrently, so `[site:1]` sometimes reports before `[site:0]`; the order isn't guaranteed. Below the per-site output, the `Multi-UUT Results` block is the orchestrator's own summary once both workers exit.

You'll also see pytest print `no tests ran` from the orchestrator process itself — it collected zero tests directly (both workers did the real collecting), so this line is harmless.

## Reading the result

Two workers means two independent runs, each with its own serial:

```cli
uv run litmus runs --limit 5
```

```
Run ID     Started                    UUT Serial      Project              Station              Outcome
----------------------------------------------------------------------------------------------------------
437bdc0e   2026-07-01T18:00:00-0600   SN-B            parallel-sites-example bench_dual           passed
7fa161df   2026-07-01T18:00:00-0600   SN-A            parallel-sites-example bench_dual           passed
```

Site 0 (`left`) ran as `SN-A`, site 1 (`right`) ran as `SN-B`. Each run's parquet carries the `site_index` / `site_name` it was frozen with at start — `SN-A` recorded `site_index=0, site_name="left"`, `SN-B` recorded `site_index=1, site_name="right"`. That freeze point matters: rename a site in the fixture YAML next month and this run's rows still read the name that was active when it ran. See [Multi-UUT testing → Parquet Data](../how-to/execution/multi-uut-testing.md#parquet-data) for the DuckDB query shape over those columns.

Give it a few seconds before querying — ingest lands a beat after the summary prints. If `litmus runs` comes back empty right after the run, re-run the command.

## Experimenting with serial assignment

`pytest.ini`'s `addopts` is a default, not a lock — any `--uut-serials` / `--site` / `--uut-serial` on the command line overrides it for that invocation.

**Positional** — one serial per site, in fixture list order (`left` gets the first):

```cli
uv run pytest -q --uut-serials SN1,SN2
```

```
[site:0] .                                                                        [100%]
[site:0] 1 passed in 0.42s
[site:1] .                                                                        [100%]
[site:1] 1 passed in 0.42s

============================================================
Multi-UUT Results
============================================================
  site[0]: PASS  1 passed in 0.42s
  site[1]: PASS  1 passed in 0.42s
============================================================
```

**Indexed** (`site_index=serial`, any order) and **named** (`site_name=serial`) produce the same 2-worker run — only how the serial maps to a site differs:

```cli
uv run pytest -q --uut-serials 0=SN1,1=SN2
uv run pytest -q --uut-serials left=SN1,right=SN2
```

**Single-lane** — target one site, single-process, no orchestrator:

```cli
uv run pytest -q --site left --uut-serial SN1
```

```
.                                                                        [100%]
1 passed in 0.43s
```

No `[site:N]` prefix, no `Multi-UUT Results` banner — this ran in the current process. It still records exactly as `left` would inside a parallel run (`site_index=0, site_name="left"`), which makes it the right tool for debugging one UUT position without waiting on its neighbor. `--site` always runs single-process, even against a multi-site fixture; pair it with `--uut-serial` (singular), not `--uut-serials`.

## Why the shared instrument doesn't collide

`psu` and `dmm` are one physical box each in this example, shared by both sites. Litmus serializes calls to a shared instrument so `left` and `right` never talk to it at the same instant — you didn't write a lock anywhere in `test_dual_rail.py` for that to be true. Mock instruments are the one exception: under `--mock-instruments` each site gets its own mock state, so a fault injected on one site's `dmm` never leaks into the other's.

For sync points, per-site environment variables, and debugging a hung or failing site, see [Multi-UUT testing](../how-to/execution/multi-uut-testing.md) — the full reference this chapter walked through.

## You've completed the tutorial

You've taken a suite from a bare `conftest.py` with one mock fixture through production traceability, live monitoring, and now parallel execution across multiple UUT positions on one bench. The full worked example for this step lives in [`examples/12-parallel-sites`](https://github.com/pragmatest-dev/litmus/tree/main/examples/12-parallel-sites) — clone it, wipe its `data/` directory, and re-run any of the commands above against real output of your own.

## Next Steps

- [Multi-UUT testing](../how-to/execution/multi-uut-testing.md) — the full recipe: sync points, environment variables, debugging failures
- [Fixtures → Multi-UUT scaling](../concepts/configuration/fixtures.md#multi-uut-scaling-sites-shared-instruments-switching) — the design behind sites, shared instruments, and switched routing
- [Configuration reference](../reference/configuration.md#fixture-yaml) — fixture YAML field-by-field
- [CLI reference](../reference/cli.md) — every `litmus` command

← [Step 12: Continuous Monitoring](12-continuous-monitoring.md)  |  [Tutorial index](index.md)
