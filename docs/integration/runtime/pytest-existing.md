# Adopting TesterKit from an existing pytest project

You already have a pytest test suite. This page is the route to wiring TesterKit in without rewriting it.

The short version: install TesterKit, point a station YAML at your bench, and the [bundled pytest plugin](../../reference/pytest/fixtures.md) auto-loads. Existing tests keep running. New tests that take TesterKit fixtures (`verify`, `measure`, `context`, your per-role instrument fixtures) get measurement logging, limit checking, parquet results, and the operator UI for free.

The longer version is the rest of this page: install, what auto-loads, what fixtures appear, how to keep an old test alongside a new one, and four entry points for mixing in TesterKit features at different depths.

## Install

```bash
pip install testerkit
```

That's it. TesterKit's pytest plugin registers via its entry point in `pyproject.toml` — pytest discovers and loads it automatically. **You do not need to add `pytest_plugins = ["testerkit"]` to your conftest.**

The plugin registers these CLI flags out of the box:

- `--uut-serial`, `--uut-serials`, `--uut-part-number`, `--uut-revision`, `--uut-lot-number`
- `--station`, `--site`, `--fixture`, `--part`
- `--mock-instruments` / `--no-mock-instruments`, `--test-phase`, `--test-profile` / `--no-test-profile`, `--operator`
- `--data-dir`, `--guardband`, `--strict-traceability`

The full table with defaults and descriptions is in [reference/pytest-native.md](../../reference/overview/pytest-native.md). Dynamic flags for profile facets and `required_inputs:` keys are also registered — see that page.

Don't re-register these in your own `pytest_addoption` — pytest fails at collection if a flag is registered twice; the plugin already owns them.

## Verify it loaded

```bash
pytest --co -q
```

The plugin name appears in the loaded-plugins list at the top of the output. If your fixtures collection includes names like `context`, `verify`, `measure`, `pins`, `instruments`, `mock_instruments`, the plugin is live.

## What fixtures appear

The plugin provides a set of [plugin fixtures](../../reference/pytest/fixtures.md) (most-used: `verify`, `measure`, `context`, `pins`, `instruments`). It also creates one fixture per instrument in the active station YAML — a station with `instruments: { dmm: ..., psu: ..., scope: ... }` exposes `dmm`, `psu`, `scope` as fixtures automatically. No wrapper code needed.

```python
# tests/test_voltage.py — a new pytest test that uses TesterKit
def test_output_voltage(dmm, verify):
    verify("output_voltage", dmm.measure_dc_voltage())
```

The `dmm` fixture resolves to a connected DMM driver from your station YAML. The `verify` fixture resolves the limit (sidecar / marker / part spec / inline `limit=`), records the measurement to parquet, and raises `LimitFailure` if it's out of range.

Your existing tests keep running unmodified — pytest treats them as ordinary tests with no fixture dependencies on the TesterKit surface.

```python
# tests/test_existing.py — untouched
def test_calculate_something():
    assert calculate() == 42
```

Both run together:

```bash
pytest tests/ --station=bench_1 --uut-serial=SN001
```

## Configuration files

A complete TesterKit-aware project has up to four YAML files. None of them are required for plain pytest tests; each unlocks more of the platform.

| File | What it does | Required when |
|---|---|---|
| `testerkit.yaml` | Project-wide defaults (data dir, default station, etc.) | Always recommended — pin a `data_dir:` so results land somewhere predictable |
| `stations/<id>.yaml` | Declares instruments and their roles for one bench | Any test that takes an instrument fixture (`dmm`, `psu`, etc.) |
| `fixtures/<id>.yaml` | Maps UUT pins to instrument channels | Tests that use the `pins` fixture or need pin-level traceability |
| `parts/<id>.yaml` | Declares pins + characteristics + spec bands | Tests that use `verify` against a part spec |

For the full schemas, see [configuration reference](../../reference/configuration.md).

A minimal `testerkit.yaml`:

```yaml
# Project root
name: my-existing-project
data_dir: results              # writes to ./results/ instead of the global pool
default_station: bench_1
```

A minimal sidecar (per-test YAML, optional):

```yaml
# tests/test_voltage.yaml — colocated with the test module
limits:                        # applied to every test in the file
  voltage:
    low: 3.0
    high: 3.6
    unit: V

tests:
  test_power_rails:            # per-test overrides nested under tests:
    limits:
      vcc:
        low: 3.2
        high: 3.4
        unit: V
```

Top-level keys must be [SidecarConfig fields](../../reference/configuration.md#sidecar-yaml) (`limits`, `sweeps`, `mocks`, `prompts`, `retry`, `connections`, `characteristics`, `tests`, `runner`). A test name at the YAML root fails validation because the model rejects unknown keys.

## Four ways to mix TesterKit in

These are **independent entry points**, not a staircase. Pick the one that matches the project state. You can combine them.

### Path A — TesterKit fixtures from new tests (the canonical default)

The headline path. Add `--station=` and write new tests that take TesterKit fixtures.

```python
def test_output_voltage(dmm, verify):
    verify("output_voltage", dmm.measure_dc_voltage())
```

- Pros: smallest possible surface; standard pytest; uses everything the plugin offers.
- Trade-off: requires a station YAML to define `dmm`.

Use this for any test you're writing fresh. See [writing tests](../../how-to/execution/writing-tests.md) for end-to-end patterns and [TesterKit fixtures](../../reference/pytest/fixtures.md) for the full fixture surface.

### Path B — `TesterKitClient` for result tracking from any existing test

For tests where rewriting the assertion to use `verify` isn't worth it but you still want measurements landing in parquet:

```python
from testerkit import TesterKitClient

client = TesterKitClient()
run = client.start_run(uut_serial="SN001", station_id="bench_1", test_phase="production")

with run.step("voltage_check") as step:
    voltage = your_existing_measure_function()
    step.measure("voltage", voltage, unit="V", low=3.0, high=3.6)
    assert 3.0 <= voltage <= 3.6   # your existing assertion stays

run.finish()
```

`TesterKitClient` is a chained builder — `run.step()` and `step.vector()` are context managers; `run.finish()` finalizes and saves. Full API on [`reference/client.md`](../../reference/runtime/client.md).

- Pros: zero plugin dependency; works from any Python code (LabVIEW Python Node, TestStand Python adapter, standalone scripts).
- Trade-off: don't mix Path B with Path A in the same pytest session — the plugin and a manual `TesterKitClient` would each open a run, producing duplicate rows.

Use this when you've got an existing pytest suite you don't want to touch, or when you're driving TesterKit from non-pytest code. See also [submitting results from non-pytest sources](../data/results-api.md).

### Path C — non-pytest runners

**Driving TesterKit from a non-pytest runner** (Robot Framework, unittest, ad-hoc scripts)? Use `TestHarness` — see [test harness](harness.md). In a pytest project the plugin already does this for you; you don't need it here.

### Path D — `VisaInstrument` to replace ad-hoc driver code

Independent of how you track results, the `VisaInstrument` base class wraps PyVISA with `pyvisa-sim` simulation built in:

```python
# Before — raw pyvisa, no simulation, no TesterKit
def measure_voltage():
    import pyvisa
    rm = pyvisa.ResourceManager()
    dmm = rm.open_resource("TCPIP::192.168.1.100::INSTR")
    voltage = float(dmm.query("MEAS:VOLT?"))
    dmm.close()
    return voltage

# After — your driver class subclasses VisaInstrument
from testerkit.instruments.visa import VisaInstrument

class MyDMM(VisaInstrument):
    def measure_voltage(self) -> float:
        return float(self.query("MEAS:VOLT?"))

def measure_voltage(simulate=False):
    with MyDMM("TCPIP::192.168.1.100::INSTR", simulate=simulate) as dmm:
        return dmm.measure_voltage()
```

You can use this in isolation (no plugin, no station YAML) for the simulation contract alone, or wire it into a station YAML so the plugin handles instantiation. See [custom drivers](../../how-to/configuration/custom-drivers.md).

## Coexistence patterns

### Marking TesterKit vs non-TesterKit tests

Register a custom marker in your `pytest.ini` / `pyproject.toml` so `--strict-markers` doesn't warn:

```toml
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    "testerkit: requires TesterKit station + fixtures",
    "unit: pure-Python unit test, no TesterKit",
]
```

Then:

```python
@pytest.mark.testerkit
def test_with_testerkit(dmm, verify):
    verify("voltage", dmm.measure_dc_voltage())

@pytest.mark.unit
def test_without_testerkit():
    assert calculate() == 42
```

Run subsets:

```bash
pytest -m testerkit              # only TesterKit-flavored tests
pytest -m "not testerkit"        # only unit tests
```

### Separate directories

```
tests/
├── unit/           # plain pytest, no TesterKit surface
│   └── test_*.py
├── integration/    # uses TesterKit fixtures
│   └── test_*.py
└── conftest.py     # shared
```

### Loading station YAML from non-pytest code

For migration tooling or scripts that read your station YAML directly:

```python
from testerkit.store import get_station

# By id — looks up stations/<id>.yaml under the project root
station = get_station("bench_1")
if station is None:
    raise RuntimeError("bench_1 not found")

for role, cfg in station.instruments.items():
    print(role, cfg.driver, cfg.resource, cfg.mock)
```

`get_station(id)` looks up `stations/<id>.yaml`. `load_station(path)` (also exported from `testerkit.store`) takes an explicit `Path` for files outside the project's `stations/` directory.

## Running the tests

### Local development

```bash
pytest tests/                                  # auto-resolves default_station from testerkit.yaml
pytest tests/ --mock-instruments               # hardware-free run via mock instruments
pytest tests/ --station=bench_1 --uut-serial=SN001
```

### CI

```yaml
- name: Run tests
  run: |
    pytest tests/ \
      --mock-instruments \
      --uut-serial=CI \
      --station=ci_station \
      --test-phase=development
```

For CI, the simplest setup is a `stations/ci_station.yaml` whose every instrument has `mock: true`. With `--mock-instruments`, the platform substitutes a stand-in for each instrument that returns the values listed in `mock_config:`; your driver class is never instantiated, `connect()` is never called. See [mock mode](../../how-to/configuration/mock-mode.md) for the details.

### Production

```bash
pytest tests/ \
  --station=bench_1 \
  --uut-serial=$SERIAL \
  --operator=$OPERATOR \
  --test-phase=production
```

`--test-phase=production` requires a clean git tree; uncommitted changes silently demote the stamped phase to `development` (see [`cli.md`](../../reference/cli.md#test-phase)).

## How do I know it worked?

After the first test run with TesterKit active, verify the results landed:

```bash
testerkit runs                  # list of recent runs
testerkit show <run_id>         # detailed report for one run
testerkit serve                 # operator UI at http://localhost:8000
```

If `testerkit runs` is empty, the session likely didn't finish cleanly — a killed pytest process leaves the run stamped `aborted`. See [outcomes](../../concepts/execution/outcomes.md#aborted-process-died-before-cleanup).

## What this gets you vs what it costs

| You get | You spend |
|---|---|
| Every measurement persisted with full traceability (UUT serial, station, operator, timestamps, limits, outcomes) | Writing a `stations/<id>.yaml` for each bench |
| Mock-mode CI without changing test bodies | Per-test `mock_config` setpoints for the simulated bench |
| Operator UI, MCP tools, HTTP API on the same data | Nothing — they read the same parquet |
| Spec-driven limits (limits move from test code to part YAML) | Authoring `parts/<id>.yaml` |
| Capability matching (which station can run this part) | A `catalog/<vendor>/<model>.yaml` per instrument model |

Pick what you need. The plugin doesn't force any of it — without YAMLs, you still get plain pytest with no platform features active.

## See also

- [TesterKit fixtures](../../reference/pytest/fixtures.md) — the fixtures the plugin contributes (and the per-role auto-fixtures from station YAML)
- [TesterKit markers](../../reference/pytest/markers.md) — `testerkit_limits`, `testerkit_sweeps`, `testerkit_mocks`, `testerkit_characteristics`, `testerkit_connections`, `testerkit_retry`, `testerkit_prompts`
- [pytest-native reference](../../reference/overview/pytest-native.md) — how TesterKit tests use pytest's own collection / fixtures / markers / CLI flags
- [Writing tests](../../how-to/execution/writing-tests.md) — end-to-end patterns for new TesterKit-flavored tests
- [Configuration reference](../../reference/configuration.md) — full YAML schemas for `testerkit.yaml`, station, fixture, sidecar, profile
- [Mock mode](../../how-to/configuration/mock-mode.md) — `--mock-instruments`, `mock_config:`, the mock pipeline
- [Configuring stations](../../how-to/configuration/configuring-stations.md) — station YAML reference + the `driver:` field
- [Python client reference](../../reference/runtime/client.md) — full `TesterKitClient` / `RunBuilder` / `StepBuilder` API (Path B above)
- [Test harness](harness.md) — the imperative entry point for non-pytest runners (Path C above)
- [Custom drivers](../../how-to/configuration/custom-drivers.md) — writing your own driver class (Path D above)
- [Submitting results from non-pytest sources](../data/results-api.md) — LabVIEW, TestStand, CLI bridges via `TesterKitClient`
