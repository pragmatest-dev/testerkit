# Fixtures

A **fixture** in Litmus is a YAML file at `fixtures/<name>.yaml` that maps DUT pins to station instruments. It's the bridge that lets a test say "measure the voltage at pin `VOUT`" without knowing which DMM channel `VOUT` happens to be wired to on this particular bench.

> **Naming collision.** "Fixture" overloads. Throughout this page, "fixture" means **hardware test fixture** — the YAML pin-map. When the test signature has `def test_x(pins, dmm, verify): ...`, the names `pins`, `dmm`, `verify` are **pytest fixtures** — Python objects the [pytest plugin](../../reference/litmus-fixtures.md) synthesizes (in part from your hardware fixture YAML). When this page needs the pytest sense it says "pytest fixture".

## What fixtures model

Three things have to line up before a test can measure anything:

1. The **product** declares pins (`VIN`, `VOUT`, `GND`) and their measurable characteristics (output voltage, current draw, etc.).
2. The **station** declares instruments by role (`dmm`, `psu`, `eload`) and where each is physically connected (a VISA address, a serial port).
3. The **fixture** declares which station instrument (and channel) is currently wired to which product pin.

```mermaid
%%{init: {'flowchart': {'curve': 'stepBefore'}}}%%
flowchart LR
    subgraph Product
        VIN[VIN]
        VOUT[VOUT]
    end

    subgraph Fixture
        F_VIN["VIN → psu.1"]
        F_VOUT["VOUT → dmm"]
    end

    subgraph Station
        PSU[psu]
        DMM[dmm]
    end

    VIN --- F_VIN
    VOUT --- F_VOUT
    F_VIN --- PSU
    F_VOUT --- DMM
```

The fixture is the only piece that changes when you move a board from one bench to another. The product stays the same (it's the device). The station stays the same (it's the bench). The fixture re-maps which pins are on which channels — and every test runs unchanged.

This is also what makes a measurement traceable: every value flows through a named fixture connection (`VOUT`, not `dmm channel 1`), and the recorded measurement row carries the DUT-side name. Six months later you can ask "which board's `VOUT` was reading 3.5 V?" — the connection name is the join key.

## When you need a fixture

| Setup | Fixture? |
|---|---|
| One DUT, one bench, you remember which instrument is on which pin | Optional — the `dmm` / `psu` per-role pytest fixtures from your station YAML are enough |
| Multiple products on the same bench, or one product across multiple benches | Required — the pin-map is what lets the test code stay portable |
| Multiple DUTs running in parallel | Required — see [Multi-DUT scaling](#multi-dut-scaling-slots-shared-instruments-switching) |
| Production traceability — every measurement records its DUT-side pin | Required — `dut_pin` is the connection field that flows into the parquet row |

For development without any fixture, see [Mock mode](../../how-to/configuration/mock-mode.md) and the per-role auto-fixtures in [Litmus fixtures](../../reference/litmus-fixtures.md#per-role-auto-fixtures).

## Data model

A fixture YAML loads into a `FixtureConfig` (in `src/litmus/models/test_config.py`). Two top-level shapes:

- **Single-DUT** — fields directly on the fixture
- **Multi-DUT** — `slots:` with one `FixtureSlot` per DUT position

Both share the same `FixtureConnection` shape underneath.

### `FixtureConfig` fields

| Field | Description |
|---|---|
| `id` | Unique fixture identifier |
| `name` | Optional display name |
| `product_id` | Specific product this fixture is wired for (preferred) |
| `product_family` | Or product family — for fixtures that work for multiple products in a line |
| `product_revision` | Optional — for fixtures that differ by board revision |
| `station_types` | Optional — abstract station-type layouts this fixture can wire against. Validated at session start against the active profile's `station_type`. Empty list = "any station". |
| `dut_resource` | Optional DUT-side connection string (a COM port, USB serial number, etc.) for tests that talk to the DUT directly |
| `connections` | DUT-pin ↔ instrument-channel pairings. Single-DUT shape. |
| `slots` | Per-DUT-position connections for multi-DUT fixtures. Multi-DUT shape. |
| `description` | Free-form documentation |

`connections` and `slots` are mutually exclusive — the validator (`extra="forbid"`) **rejects** fixtures that set both.

### `FixtureConnection` fields

A connection is the addressable unit — a name that identifies one DUT-side signal path:

| Field | Description |
|---|---|
| `name` | The connection's identifier (test code uses this; parquet rows record this) |
| `instrument` | Station role (must match a key in `station.instruments`) |
| `instrument_channel` | Channel on the instrument (`"1"`, `"CH2"`, `"ai0"`) |
| `instrument_terminal` | Physical terminal on the channel (`hi`, `lo`, `sense_hi`, `sense_lo`, `signal`). Optional. |
| `dut_pin` | Product pin this connection is wired to (must match a `pins.<name>` key in the product spec) |
| `net` | Schematic net name. Alternative to `dut_pin` when matching by net rather than physical pin. |
| `function` | Optional [`MeasurementFunction`](capabilities.md#measurementfunction) the connection is for. When set, the resolver matches by `(dut_pin, function)` — see [Function as a routing dimension](#function-as-a-routing-dimension). |
| `route` | Optional `SwitchRoute` for switched signal paths — see [Switched routing](#switched-routing). |
| `description` | Free-form documentation |

## Single-DUT shape

The simplest fixture: each DUT pin gets one connection, one instrument, one channel.

```yaml
# fixtures/power_board_fixture.yaml
id: power_board_fixture
name: "Power Board Test Fixture"
product_id: power_board

connections:
  VIN:
    name: VIN
    dut_pin: VIN
    net: VIN_5V
    instrument: psu
    instrument_channel: "1"
    instrument_terminal: hi
  VOUT:
    name: VOUT
    dut_pin: VOUT
    net: VOUT_3V3
    instrument: dmm
    instrument_channel: "CH1"
  GND:
    name: GND
    dut_pin: GND
    instrument: psu
    instrument_channel: "GND"
```

A test addresses each connection by its `dut_pin` through the `pins` [pytest fixture](../../reference/litmus-fixtures.md#pins-session):

```python
def test_output_voltage(pins, verify):
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()
    verify("output_voltage", pins["VOUT"].measure_voltage())
```

`pins["VIN"]` resolves to the connected `psu` instrument (because the fixture says `VIN → psu`). The measurement row records `dut_pin=VIN`, the connection's `instrument_channel`, and the resolved instrument identity — the test body never sees those details.

## How a measurement reaches the row

When `verify("output_voltage", pins["VOUT"].measure_voltage())` runs:

1. `pins["VOUT"]` looks up the fixture connection named `VOUT` → finds `{instrument: dmm, instrument_channel: "CH1"}`.
2. The proxy resolves `dmm` from the connected station instruments and dispatches `measure_voltage()` against the right channel.
3. `verify()` records the measurement row. Because the active connection is `VOUT`, the row carries `dut_pin=VOUT`, `instrument_channel=CH1`, and the resolved `instrument_name` / `instrument_resource` automatically.

That auto-population is the traceability payoff: tests stay clean, parquet rows know exactly which signal path each measurement came through.

## Function as a routing dimension

One DUT pin can route to different instruments for different measurement functions. Set `function:` on each connection and the resolver matches `(dut_pin, function)` instead of `dut_pin` alone:

```yaml
connections:
  vout_dc:
    name: vout_dc
    dut_pin: VOUT
    function: dc_voltage      # DMM measures the DC level
    instrument: dmm
  vout_ac:
    name: vout_ac
    dut_pin: VOUT
    function: ac_voltage      # Scope captures the ripple
    instrument: scope
    instrument_channel: "1"
```

A test asking for `VOUT` with no function context falls back to first-match by pin. A test bound to a specific characteristic (via `litmus_characteristics`) picks the connection whose `function` matches.

When unset, the resolver uses the first connection for that pin — backward-compatible for fixtures that don't need per-function routing.

## Multi-DUT scaling: slots, shared instruments, switching

Three orthogonal mechanisms scale the single-DUT shape:

### Slots — parallel DUT positions

When the bench has multiple identical positions and you test them in parallel, use `slots` instead of `connections`. Each slot has its own `FixtureConnection` map:

```yaml
# fixtures/dual_board_fixture.yaml
id: dual_board_fixture
product_family: power_board

slots:
  slot_1:
    description: Left-side board
    dut_resource: /dev/ttyUSB0
    connections:
      vout_measure:
        name: vout_measure
        dut_pin: VOUT
        instrument: dmm
        instrument_channel: "1"
  slot_2:
    description: Right-side board
    dut_resource: /dev/ttyUSB1
    connections:
      vout_measure:
        name: vout_measure
        dut_pin: VOUT
        instrument: dmm
        instrument_channel: "2"
```

The orchestrator spawns a worker per slot. Each worker sees a flat fixture with just its slot's connections. Per-slot `dut_resource` overrides the fixture-level value. See [Multi-DUT testing](../../how-to/execution/multi-dut-testing.md) for the operational guide.

### Shared instruments

When multiple slots reference the same instrument role (e.g. both slots' `dmm` connections point at the bench's single DMM), the orchestrator detects it as **shared**. The instrument connects once in a host process and is exposed to worker subprocesses via `InstrumentServer` — a `multiprocessing.connection`-based RPC server (not raw TCP). Workers see `RemoteInstrumentProxy` objects that look like normal driver instances; method calls cross the process boundary.

Locking is per **resource** (the VISA address, COM port, or other connection identifier registered with `InstrumentServer`), so roles sharing one physical session serialize while roles on independent sessions run in parallel.

### Switched routing

For a single instrument fanned out to multiple DUT positions through a relay matrix, add a `SwitchRoute` to the connection. The platform closes the listed switch channels before activating the instrument, waits the settling time, then runs the measurement:

```yaml
slot_1:
  connections:
    vout_measure:
      name: vout_measure
      dut_pin: VOUT
      instrument: dmm
      route:
        switch: matrix          # role of the switch instrument
        channels: ["r0c0"]      # crosspoints to close
        settling_ms: 10
```

Switch routes activate lazily — the first method call on the resolved instrument triggers route closure, settling, then dispatch. Multiple slots can share one instrument through different routes, with the switch as the coordinator. Switches participate in locking differently from measurement instruments (their `concurrent=True` flag exempts them from serialization, since closing channels in parallel is what makes the matrix useful).

## Selecting a fixture at run time

Stations do not pin a fixture themselves. The active fixture is chosen per session via the `--fixture` CLI flag (or a [profile](../../how-to/execution/profiles.md) that sets it):

```bash
pytest tests/ \
  --station=bench_1 \
  --fixture=fixtures/power_board_fixture.yaml \
  --dut-serial=SN001
```

The fixture's `product_id` / `product_family` are scoping fields — the resolver uses them to pick the right fixture when multiple are present, but the plugin does not currently cross-check them against the active product spec.

## Worked example

A complete single-DUT setup, four files:

```yaml
# products/power_board.yaml
id: power_board
pins:
  VIN:  {name: "J1.1", role: power}
  VOUT: {name: "J1.3", role: signal}
  GND:  {name: "J1.2", role: ground}
characteristics:
  output_voltage:
    function: dc_voltage
    direction: output
    units: V
    pin: VOUT
    bands:
      - value: 3.3
        accuracy: {pct_reading: 5}
```

```yaml
# stations/bench_1.yaml
id: bench_1
instruments:
  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
```

```yaml
# fixtures/power_board_fixture.yaml
id: power_board_fixture
product_id: power_board
connections:
  VIN:
    name: VIN
    dut_pin: VIN
    instrument: psu
    instrument_channel: "1"
  VOUT:
    name: VOUT
    dut_pin: VOUT
    instrument: dmm
  GND:
    name: GND
    dut_pin: GND
    instrument: psu
    instrument_channel: "GND"
```

```python
# tests/test_power_board.py
def test_output_voltage(pins, verify):
    pins["VIN"].set_voltage(5.0)
    pins["VIN"].enable_output()
    verify("output_voltage", pins["VOUT"].measure_voltage())
```

Run it:

```bash
pytest tests/ \
  --product=products/power_board.yaml \
  --station=stations/bench_1.yaml \
  --fixture=fixtures/power_board_fixture.yaml \
  --dut-serial=SN001
```

The recorded measurement row carries `dut_pin=VOUT`, `instrument_name=dmm`, `characteristic_id=output_voltage` — all pulled through the fixture connection automatically.

## See also

- [Products](products.md) — what pins and characteristics get declared on the DUT side
- [Stations](stations.md) — what instruments and roles get declared on the bench side
- [Capabilities](capabilities.md) — the function / direction / signal model that drives matching (and the `function:` field on connections)
- [Tutorial step 9 — Production ready](../../tutorial/09-production.md) — first hands-on with fixtures + sidecar config
- [How-to — Configuring stations](../../how-to/configuration/configuring-stations.md) — the station YAML reference
- [How-to — Multi-DUT testing](../../how-to/execution/multi-dut-testing.md) — slots, shared instruments, parallel workers in practice
- [Litmus fixtures](../../reference/litmus-fixtures.md) — the `pins`, `instruments`, `instrument`, `fixture_manager`, `connections` pytest fixtures that read this YAML
- [Configuration reference](../../reference/configuration.md) — fixture YAML schema field-by-field
