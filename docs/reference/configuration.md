# Configuration reference

Litmus uses YAML files for every config surface, validated by Pydantic models. This page enumerates the files, their canonical locations, and the shape of each. Every model is `extra="forbid"` — typos like `descriptin:` raise `ValidationError` at load time. Filename stems must match the `id:` field for id-keyed entities (`store.py:_check_id_matches_filename`).

For the full field-by-field reference of each model, see [models.md](models.md). For deep-dive references on catalog YAML and profile resolution, see the dedicated pages linked from each section.

## YAML files at a glance

<!-- GENERATED:configuration-file-index:start -->
| File | Pydantic model | What it carries |
|---|---|---|
| `litmus.yaml` | [`ProjectConfig`](models.md#model-projectconfig) | Project root — names, defaults, profiles, multi-slot knobs. |
| `stations/<id>.yaml` | [`StationConfig`](models.md#model-stationconfig) | Concrete station deployment — instruments, drivers, resources. |
| `stations/types/<id>.yaml` | [`StationType`](models.md#model-stationtype) | Abstract station-type template — required roles, capabilities. |
| `fixtures/<id>.yaml` | [`FixtureConfig`](models.md#model-fixtureconfig) | DUT-pin ↔ instrument-channel routing (single-DUT) or per-slot routing (multi-DUT). |
| `products/<id>.yaml` | [`Product`](models.md#model-product) | Product specification — pins, signal groups, characteristics. |
| `tests/test_<name>.yaml` | [`SidecarConfig`](models.md#model-sidecarconfig) | Sidecar test config co-located with `tests/test_<name>.py` — sweeps, limits, mocks, retry, prompts. |
| `catalog/<vendor>/<model>.yaml` | [`InstrumentCatalogEntry`](models.md#model-instrumentcatalogentry) | Instrument capability catalog — see [catalog-schema.md](catalog-schema.md) for the full reference. |
<!-- GENERATED:configuration-file-index:end -->

## Project — `litmus.yaml` {#project-litmus-yaml}

The project root. Lives at the repo root; every other YAML resolves relative to it. Validated by [`ProjectConfig`](models.md#model-projectconfig).

```yaml
name: my_project                  # required — project name
data_dir: data                    # optional — runs/, events/, channels/ subtree (default: ./data)
default_station: bench_1          # optional — fallback when no --station and no hostname match
default_fixture: power_board_fix  # optional — fallback when no --fixture and no profile binds one
default_profile: production       # optional — fallback when no --test-profile
mock_instruments: false           # optional — global mock toggle (CLI: --mock-instruments)

profiles:                         # optional — named ProfileConfig blocks (see below)
  production:
    description: "Production line config"
    facets: {phase: production}
    runner:
      addopts: "--strict-markers -p no:cacheprovider"

runner: {}                        # optional — dict[str, Any] consumed by the active runner's plugin

required_inputs:                  # optional — dict[name, PromptConfig] (operator-input prompts)
  operator_id:
    message: "Scan operator badge"
    prompt_type: input

multi_slot:                       # optional — multi-DUT orchestrator knobs
  child_grace_seconds: 5.0        # seconds from SIGTERM to SIGKILL per child pytest
```

- `runner:` is `dict[str, Any]` (default `{}`). It is *not* a string. The active runner's plugin validates the block against its own schema.
- `required_inputs:` is `dict[str, PromptConfig]`, not a list.
- `default_*` keys are CLI-overridable: explicit flag → this field → fail with a usage error if neither is present.

### Profile blocks under `profiles:`

A profile is a [`ProfileConfig`](models.md#model-profileconfig) — same flat shape as a test entry (limits / sweeps / mocks / retry / prompts apply session-wide), plus profile-only metadata. Selected at session start via `--test-profile <name>` or the `default_profile`.

```yaml
profiles:
  thermal_extended:
    description: "85 °C soak + adjacent retry on flaky thermal probe"
    facets: {phase: thermal, lab: bench_a}    # dimension-tagged for filtering
    extends: production                       # parent profile — last-wins merge
    station_type: thermal_bench               # bind to a StationType (resolver verifies)
    fixture: thermal_fixture_v2               # bind to a Fixture (CLI --fixture wins)
    runner:
      addopts: "-m thermal"
      markers:                                # ecosystem markers applied via the cascade
        - flaky:
            reruns: 2
    limits:                                   # session-wide limits
      output_voltage: {low: 3.2, high: 3.4, units: V}
    tests:                                    # recursive per-class / per-method overrides
      test_thermal:
        sweeps:
          - {temperature: [25, 85]}
```

`extends:` chains are walked parent-first; leaves carry only deltas. Parent profiles with no `facets:` are reachable only as extends targets (they cannot be selected directly). See [how-to/profiles.md](../how-to/profiles.md) for the workflow.

## Station — `stations/<id>.yaml` {#station-yaml}

Concrete station deployment. Validated by [`StationConfig`](models.md#model-stationconfig). Filename stem must equal `id:`.

```yaml
id: bench_1                       # required — matches filename stem
name: "Bench 1"                   # required — display name
station_type: thermal_bench       # optional — names a StationType template (resolver cross-checks)
hostname: bench-01.lab            # optional — auto-matches socket.gethostname() at session start
location: "Lab 3, Rack B"
description: "RF + thermal characterization bench"
supported_phases: [validation, production]

instruments:                      # dict[role, StationInstrumentConfig]
  dmm:
    type: dmm                     # required — instrument-type (canonical or alias)
    driver: pymeasure.instruments.keysight.KeysightDMM34465A
    resource: "TCPIP0::192.168.1.50::INSTR"
    catalog_ref: keysight_34465a  # optional — catalog entry id (resolves channels/capabilities)
    channels:                     # optional — dict[str, str]; resolved from catalog if omitted
      voltage: "1"
    mock: false                   # true = use Mock(driver, **mock_config) instead of real driver
    mock_config:                  # keys are driver METHOD NAMES (not signal names)
      measure_dc_voltage: 3.31
      measure_current: 0.105
    description: "Lab calibrated 2026-04-12"

  psu:
    type: psu
    driver: pymeasure.instruments.rigol.RigolDP832
    resource: "USB0::0x1AB1::0x0E11::DP8B240500001::INSTR"
```

- `instruments.<role>.channels` is `dict[str, str]`, not a list.
- `mock_config` keys are driver method names (`measure_dc_voltage`, `set_voltage`), not signal names. See [how-to/mock-mode.md](../how-to/mock-mode.md).
- For `type:` values: canonical names live on [`InstrumentType`](models.md#enum-instrumenttype). Short aliases (e.g. `fgen` → `function_generator`) are accepted via `_INSTRUMENT_TYPE_ALIASES` in `litmus.store`. Unknown values trigger a warning, not an error.
- Validator: real-hardware instruments (`mock: false`) require at least one of `resource:` or `driver:`. Mock-only instruments don't.

## Station type — `stations/types/<id>.yaml` {#station-type-yaml}

Abstract station-type template. Concrete stations declare compatibility via `station_type:`. Validated by [`StationType`](models.md#model-stationtype).

```yaml
id: thermal_bench
description: "Thermal characterization bench — chamber + 2× DMM + PSU"
instruments:                      # dict[role, InstrumentConfig] — required roles
  chamber:
    type: chamber
    driver: drivers.cincinnati.cs_900
  dmm_main:
    type: dmm
    driver: pymeasure.instruments.keysight.KeysightDMM34465A
  dmm_ref:
    type: dmm
    driver: pymeasure.instruments.keysight.KeysightDMM34465A
  psu:
    type: psu
    driver: pymeasure.instruments.rigol.RigolDP832
capabilities: [thermal_soak, dual_dmm_compare]
```

`validate_station_against_type(station, station_type)` enforces role coverage at session start. A station declaring `station_type: thermal_bench` must define instruments under every role the type names, with matching `type:` values.

## Fixture — `fixtures/<id>.yaml` {#fixture-yaml}

DUT-pin ↔ instrument-channel routing. Validated by [`FixtureConfig`](models.md#model-fixtureconfig).

Single-DUT — top-level `connections:`:

```yaml
id: power_board_fix
name: "Power Board Test Fixture"
product_id: power_board                # specific product (preferred)
product_family: power_boards           # OR product family for shared fixtures
product_revision: rev_a                # optional — refinement
station_types: [thermal_bench, rf_bench]  # which StationType templates this can wire against
dut_resource: "/dev/ttyUSB0"           # optional — DUT control connection
description: "Standard 4-rail board fixture"

connections:                           # dict[name, FixtureConnection]
  vout_measure:
    name: vout_measure                 # REQUIRED — must match the key
    instrument: dmm                    # role name on the station
    instrument_channel: "1"
    instrument_terminal: hi            # optional — hi / lo / sense_hi / sense_lo / signal / …
    dut_pin: VOUT                      # reference into Product.pins
    net: VOUT_3V3                      # optional — schematic net name
    function: dc_voltage               # optional — per-function disambiguation (DMM for DC, scope for AC)
    description: "Direct-wired DMM probe on VOUT"

  vout_switched:
    name: vout_switched
    instrument: dmm
    instrument_channel: "1"
    dut_pin: VOUT
    route:                             # optional — switch routing (SwitchRoute)
      switch: matrix                   # role name of the switch instrument
      channels: ["r0c0"]
      settling_ms: 10
```

Multi-DUT — top-level `slots:` instead of `connections:`:

```yaml
id: multi_slot_fix
name: "Quad Power Board Fixture"
product_id: power_board
station_types: [bench_4ch]
slots:                                 # dict[slot_name, FixtureSlot]
  slot_1:
    dut_resource: "/dev/ttyUSB0"       # per-slot DUT connection
    description: "Bottom-left slot"
    connections:
      vout_measure:
        name: vout_measure
        instrument: dmm
        instrument_channel: "1"
        dut_pin: VOUT
  slot_2:
    dut_resource: "/dev/ttyUSB1"
    connections:
      vout_measure:
        name: vout_measure
        instrument: dmm
        instrument_channel: "2"
        dut_pin: VOUT
```

- `FixtureConnection.name` is required — there is no key-as-name auto-fill. Declare `name:` matching the dict key on every connection.
- `connections:` and `slots:` are mutually exclusive on a single `FixtureConfig` — validator rejects both being set.

See [concepts/fixtures.md](../concepts/fixtures.md) for the design rationale, [how-to/multi-dut-testing.md](../how-to/multi-dut-testing.md) for slot workflow.

## Product — `products/<id>.yaml` {#product-yaml}

Product specification. Validated by [`Product`](models.md#model-product). Filename stem must equal `id:`.

```yaml
id: power_board                       # required — matches filename stem
name: "DC-DC Power Board"             # required
part_number: PWR-CONV-001             # optional — operator-facing dut_part_number
base: power_board_base                # optional — inherits from another product (see Variants)
revision: rev_a
description: "5 V → 3.3 V buck converter"
datasheet: "docs/DS-power-board-001.pdf"
schematic: "docs/SCH-power-board-001.pdf"
driver: drivers.power_board.PowerBoard   # optional — dotted import path for DUT driver

pins:                                 # dict[key, Pin] — physical connection points
  VIN:
    name: "J1.1"                      # physical designator
    net: VIN_5V                       # schematic net name
    role: power                       # signal | power | ground | reference (default: signal)
    description: "5 V input"
  VOUT:
    name: "J1.3"
    net: VOUT_3V3
    role: power
  GND:
    name: "J1.2"
    role: ground

signal_groups:                        # dict[name, SignalGroup] — bus interfaces
  i2c_control:
    protocol: i2c                     # i2c | spi | uart | parallel | custom
    signals:
      - pin: SDA
        role: data
      - pin: SCL
        role: clock
    parameters:
      frequency: 100000

characteristics:                      # dict[name, ProductCharacteristic]
  rail_3v3_output:
    function: dc_voltage              # MeasurementFunction enum
    direction: output                 # input | output | bidir | transform
    units: V
    pin: VOUT                         # at least one of: pin, pins, net, signal_group
    datasheet_ref: "Table 4.2"
    bands:                            # list[SpecBand]
      - when: {}                      # empty when: = unconditional default
        value: 3.3
        accuracy: {pct_reading: 3.0}
      - when:
          temperature: {min: 0, max: 70, units: degC}
        value: 3.3
        accuracy: {pct_reading: 2.0}
```

- `bands:` lives inside each characteristic. There is no top-level `bands:` on `Product`.
- `ProductCharacteristic` fields: `function`, `direction`, `units`, `pin`, `pins`, `net`, `signal_group`, `datasheet_ref`, plus the inherited `signals`/`conditions`/`controls`/`attributes`/`bands` from `Capability`. There is no `channel:` / `channels:` / `schematic_ref:` on characteristics — `extra="forbid"` rejects them.
- `base:` lets a product inherit from another. Sibling-then-products-root search; circular and missing-base raise `ValueError`.

See [tutorial/06-specifications.md](../tutorial/06-specifications.md) for the workflow and [how-to/spec-driven-testing.md](../how-to/spec-driven-testing.md) for spec-driven verify.

## Sidecar — `tests/test_<name>.yaml` {#sidecar-yaml}

Co-located with each test module. Validated by [`SidecarConfig`](models.md#model-sidecarconfig). Top-level shape is the same as a `TestEntry`, plus a recursive `tests:` tree for per-class / per-method overrides.

```yaml
# tests/test_power.yaml — sibling to tests/test_power.py
limits:                               # dict[measurement_name, MeasurementLimitConfig]
  output_voltage: {low: 3.2, high: 3.4, units: V}
  ripple_mv:    {high: 50, units: mV, characteristic: ripple_spec}

sweeps:                               # list[SweepEntry] — vector cross-products
  - {vin: [4.5, 5.0, 5.5], load: [0.1, 0.5, 1.0]}

mocks:                                # list[MockEntry] — installed via patch.object
  - target: psu.set_voltage
    return_value: null
  - target: dmm.measure_dc_voltage
    return_value: 3.31

characteristics: [rail_3v3_output]    # bind tests to product characteristics

connections: ["vout_measure"]         # constrain to a subset of fixture connections

retry:                                # RetryConfig
  max_retries: 2                      # not "max_attempts"
  delay: 1.0                          # seconds; not "delay_seconds"
  on: [AssertionError, TimeoutError]  # exception class names; None = retry on any

prompts:                              # dict[id, PromptConfig]
  confirm_dut_seated:
    message: "Confirm DUT is seated correctly"
    prompt_type: confirm

runner: {}                            # opaque per-runner config

tests:                                # recursive — keyed by pytest node-id segment
  TestRails:                          # class-level entry — overrides apply to its methods
    limits:
      output_voltage: {low: 3.25, high: 3.35, units: V}
    test_rail_under_load:             # per-method entry — most specific
      sweeps:
        - {load: [0.1, 1.0, 2.0]}
```

- `limits:` value shape: see [`MeasurementLimitConfig`](models.md#model-measurementlimitconfig). Supports direct `{low, high, nominal, units}`, characteristic-driven `{characteristic, tolerance_pct}`, conditional `{bands: [...]}`, callable, lookup tables, and stepped — see [how-to/limits.md](../how-to/limits.md).
- `sweeps:` value shape is a list of dicts; each dict maps param name → list of values. Multiple dicts in the list compose as axes (cross-product).
- `retry:` field names are `max_retries` and `delay`, not `max_attempts` / `delay_seconds`.

Resolution order for any field (least → most specific):

1. Sidecar file-level (top-level entry, applies to every test in the module)
2. Sidecar class-branch (`tests.<ClassName>`)
3. Sidecar per-test leaf (`tests.<ClassName>.<test_name>`)
4. Inline `@pytest.mark.<name>(...)` decorators on the method or class
5. Profile chain (parent-first, last-wins) injected as markers at collection time

CLI flags compose with this chain rather than overriding it wholesale. For example `--mock-instruments` overrides `ProjectConfig.mock_instruments`; `-k` / `-m` compose with `runner.keyword` / `runner.markexpr`.

See [pytest-native.md](pytest-native.md) for pytest node IDs and [reference/litmus-markers.md](litmus-markers.md) for the full marker surface.

## Catalog — `catalog/<vendor>/<model>.yaml` {#catalog-yaml}

Instrument capability catalog. Validated by [`InstrumentCatalogEntry`](models.md#model-instrumentcatalogentry). Full reference: [catalog-schema.md](catalog-schema.md); worked recipes: [catalog-cookbook.md](catalog-cookbook.md).

In brief — fields sit at the root, *not* under a `catalog_entry:` wrapper:

```yaml
id: keysight_34465a
manufacturer: Keysight
model: "34465A"
type: dmm
interfaces: [usb, lan, gpib]
channels:
  "1": {terminals: [hi, lo, sense_hi, sense_lo], connector: binding_post, ground: shared}
capabilities:
  - function: dc_voltage
    direction: input
    signals:
      voltage:
        range: {min: 0.0001, max: 1000, units: V}
        accuracy: {pct_reading: 0.0024, pct_range: 0.0005}
```

Variant SKUs use a separate file with `base:` pointing at the parent — merge happens at load time per `_merge_capabilities` (capabilities merged by `(function, direction)` key, signals/conditions/controls/attributes deep-merged inside matching capabilities). See [catalog-schema.md#variants-option-codes](catalog-schema.md#variants-option-codes).

## Loading a YAML file

```python
from pathlib import Path
from litmus.store import (
    load_project, load_station, load_station_type,
    load_fixture, load_product, load_catalog_entry, load_sidecar,
)

project = load_project(Path("litmus.yaml"))
station = load_station(Path("stations/bench_1.yaml"))
```

Every loader raises `pydantic.ValidationError` with the offending field path on type / shape errors and `ValueError` from the model's own validators on semantic problems (unknown SpecBand `when:` keys, namespace overlap, mutually-exclusive fields). See [models.md](models.md) for the full model surface, [api.md](api.md) for the JSON / MCP entry points.

## See also

- [Models](models.md) — every Pydantic model with field tables
- [Catalog schema](catalog-schema.md) — full `InstrumentCatalogEntry` reference
- [Catalog cookbook](catalog-cookbook.md) — recipes per datasheet shape
- [Profiles (how-to)](../how-to/profiles.md) — workflow for the `profiles:` block
- [Limits (how-to)](../how-to/limits.md) — `MeasurementLimitConfig` shapes
- [Spec-driven testing (how-to)](../how-to/spec-driven-testing.md) — characteristic-driven limits
- [Multi-DUT testing (how-to)](../how-to/multi-dut-testing.md) — fixture `slots:` workflow
- [Mock mode (how-to)](../how-to/mock-mode.md) — station `mock_config:` and sidecar `mocks:`
- [Pytest-native (reference)](pytest-native.md) — node IDs, marker surface
- [Litmus markers (reference)](litmus-markers.md) — every marker with payload shape
- [Fixtures (concept)](../concepts/fixtures.md) — design rationale for fixtures
