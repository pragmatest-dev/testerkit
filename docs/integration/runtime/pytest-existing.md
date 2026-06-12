# Adopting Litmus from an existing pytest project

You already have a pytest test suite. This page is the route to wiring Litmus in without rewriting it.

The short version: install Litmus, point a station YAML at your bench, and the [bundled pytest plugin](../../reference/pytest/fixtures.md) auto-loads. Existing tests keep running. New tests that take Litmus fixtures (`verify`, `logger`, `context`, your per-role instrument fixtures) get measurement logging, limit checking, parquet results, and the operator UI for free.

The longer version is the rest of this page: install, what auto-loads, what fixtures appear, how to keep an old test alongside a new one, and four entry points for mixing in Litmus features at different depths.

## Install

Add `litmus-test` to your project (PyPI release coming; for now install from a checkout):

```bash
git clone https://github.com/pragmatest-dev/litmus.git ~/src/litmus

# From inside your existing pytest project:
uv add ~/src/litmus
# or: uv pip install -e ~/src/litmus
```

That's it. Litmus's pytest plugin registers via its entry point in `pyproject.toml` — pytest discovers and loads it automatically. **You do not need to add `pytest_plugins = ["litmus"]` to your conftest.**

The plugin registers these CLI flags out of the box:

- `--uut-serial`, `--uut-serials`, `--uut-part-number`, `--uut-revision`, `--uut-lot-number`
- `--station`, `--slot`, `--fixture`, `--part`
- `--mock-instruments` / `--no-mock-instruments`, `--test-phase`, `--test-profile` / `--no-test-profile`, `--operator`
- `--data-dir`, `--guardband`, `--strict-traceability`

The full table with defaults and descriptions is in [reference/pytest-native.md](../../reference/overview/pytest-native.md). Dynamic flags for profile facets and `required_inputs:` keys are also registered — see that page.

**Do not re-register these in your own `pytest_addoption`** — pytest treats duplicate flag registration as a fatal `argparse.ArgumentError` at collection. The plugin already owns them.

## Verify it loaded

```bash
pytest --co -q
```

The plugin name appears in the loaded-plugins list at the top of the output. If your fixtures collection includes names like `context`, `verify`, `logger`, `pins`, `instruments`, `mock_instruments`, the plugin is live.

## What fixtures appear

The plugin provides a fixed set of [20 plugin fixtures](../../reference/pytest/fixtures.md) (most-used: `verify`, `logger`, `context`, `pins`, `instruments`). It also synthesizes one [per-role auto-fixture](../../reference/pytest/fixtures.md#per-role-auto-fixtures) per instrument in the active station YAML — so a station with `instruments: { dmm: ..., psu: ..., scope: ... }` exposes `dmm`, `psu`, `scope` as fixtures automatically. No wrapper code needed.

```python
# tests/test_voltage.py — a new pytest test that uses Litmus
def test_output_voltage(dmm, verify):
    verify("output_voltage", dmm.measure_dc_voltage())
```

The `dmm` fixture resolves to a connected DMM driver from your station YAML. The `verify` fixture resolves the limit (sidecar / marker / part spec / inline `limit=`), records the measurement to parquet, and raises `LimitFailure` if it's out of range.

Your existing tests keep running unmodified — pytest treats them as ordinary tests with no fixture dependencies on the Litmus surface.

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

A complete Litmus-aware project has up to four YAML files. None of them are required for plain pytest tests; each unlocks more of the platform.

| File | What it does | Required when |
|---|---|---|
| `litmus.yaml` | Project-wide defaults (data dir, default station, etc.) | Always recommended — pin a `data_dir:` so results land somewhere predictable |
| `stations/<id>.yaml` | Declares instruments and their roles for one bench | Any test that takes an instrument fixture (`dmm`, `psu`, etc.) |
| `fixtures/<id>.yaml` | Maps UUT pins to instrument channels | Tests that use the `pins` fixture or need pin-level traceability |
| `parts/<id>.yaml` | Declares pins + characteristics + spec bands | Tests that use `verify` against a part spec |

For the full schemas, see [configuration reference](../../reference/configuration.md).

A minimal `litmus.yaml`:

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
    units: V

tests:
  test_power_rails:            # per-test overrides nested under tests:
    limits:
      vcc:
        low: 3.2
        high: 3.4
        units: V
```

Top-level keys must be [SidecarConfig fields](../../reference/configuration.md#sidecar-yaml) (`limits`, `sweeps`, `mocks`, `prompts`, `retry`, `connections`, `characteristics`, `tests`, `runner`). A test name at the YAML root fails validation because the model rejects unknown keys.

## Four ways to mix Litmus in

These are **independent entry points**, not a staircase. Pick the one that matches the project state. You can combine them.

### Path A — Litmus fixtures from new tests (the canonical default)

The headline path. Add `--station=` and write new tests that take Litmus fixtures.

```python
def test_output_voltage(dmm, verify):
    verify("output_voltage", dmm.measure_dc_voltage())
```

- Pros: smallest possible surface; standard pytest; uses everything the plugin offers.
- Trade-off: requires a station YAML to define `dmm`.

Use this for any test you're writing fresh. See [writing tests](../../how-to/execution/writing-tests.md) for end-to-end patterns and [`reference/litmus-fixtures.md`](../../reference/pytest/fixtures.md) for the full 20-fixture surface.

### Path B — `LitmusClient` for result tracking from any existing test

For tests where rewriting the assertion to use `verify` isn't worth it but you still want measurements landing in parquet:

```python
from litmus import LitmusClient

client = LitmusClient()
run = client.start_run(uut_serial="SN001", station_id="bench_1", test_phase="production")

with run.step("voltage_check") as step:
    voltage = your_existing_measure_function()
    step.measure("voltage", voltage, units="V", low=3.0, high=3.6)
    assert 3.0 <= voltage <= 3.6   # your existing assertion stays

run.finish()
```

`LitmusClient` is a chained builder — `run.step()` and `step.vector()` are context managers; `run.finish()` finalizes and saves. Full API on [`reference/client.md`](../../reference/runtime/client.md).

- Pros: zero plugin dependency; works from any Python code (LabVIEW Python Node, TestStand Python adapter, standalone scripts).
- Trade-off: don't mix Path B with Path A in the same pytest session — the autouse `logger` fixture (plugin path) and a manually-constructed `LitmusClient` would each open their own run, producing duplicate parquet rows.

Use this when you've got an existing pytest suite you don't want to touch, or when you're driving Litmus from non-pytest code. See also [submitting results from non-pytest sources](../data/results-api.md).

### Path C — `TestHarness` for non-pytest runners

The lowest-level run-tracking primitive. Same machinery the pytest plugin sits on, but you own the lifecycle.

```python
from litmus import Limit
from litmus.execution.harness import TestHarness
from litmus.execution.logger import TestRunLogger

logger = TestRunLogger(uut_serial="SN001", station_id="bench_1")
harness = TestHarness(logger=logger)

with harness.step("test_power_rails"):
    vcc = measure_vcc()
    vdd = measure_vdd()
    harness.measure("vcc", vcc, limit=Limit(low=3.2, high=3.4, units="V"))
    harness.measure("vdd", vdd, limit=Limit(low=1.7, high=1.9, units="V"))
```

`TestHarness.measure()` takes `name`, `value`, optional `units`, `limit` (a `Limit` model — no `low=` / `high=` kwargs), `uut_pin`, `instrument_channel`, `fixture_connection`. When `limit=` is not passed, the harness resolves limits from its `limits=` / `config["limits"]` (whichever you provided at construction) and the active `part_context`; see [integration/harness.md → Recording measurements](harness.md#recording-measurements).

- Pros: the most direct way to drive Litmus from non-pytest Python (Robot Framework, unittest, ad-hoc scripts).
- Trade-off: don't construct `TestRunLogger` at module-import time — its `__init__` captures git state and the hostname for the `TestRun` record, and you'd rather that snapshot happen at session start, not module load. Open the event log explicitly afterward (`logger.event_log = store.get_event_log(...)`) so it lines up with the session boundary. That work belongs in a session-start hook or `pytest_sessionstart`, not at import.
- Trade-off: in a pytest project where the plugin is loaded, the autouse `logger` fixture already does this work for you. Path C is for the non-pytest case.

See [test harness](harness.md) for the imperative-runner integration guide.

### Path D — `VisaInstrument` to replace ad-hoc driver code

Independent of how you track results, the `VisaInstrument` base class wraps PyVISA with `pyvisa-sim` simulation built in:

```python
# Before — raw pyvisa, no simulation, no Litmus
def measure_voltage():
    import pyvisa
    rm = pyvisa.ResourceManager()
    dmm = rm.open_resource("TCPIP::192.168.1.100::INSTR")
    voltage = float(dmm.query("MEAS:VOLT?"))
    dmm.close()
    return voltage

# After — your driver class subclasses VisaInstrument
from litmus.instruments.visa import VisaInstrument

class MyDMM(VisaInstrument):
    def measure_voltage(self) -> float:
        return float(self.query("MEAS:VOLT?"))

def measure_voltage(simulate=False):
    with MyDMM("TCPIP::192.168.1.100::INSTR", simulate=simulate) as dmm:
        return dmm.measure_voltage()
```

You can use this in isolation (no plugin, no station YAML) for the simulation contract alone, or wire it into a station YAML so the plugin handles instantiation. See [custom drivers](../../how-to/configuration/custom-drivers.md).

## Coexistence patterns

### Marking Litmus vs non-Litmus tests

Register a custom marker in your `pytest.ini` / `pyproject.toml` so `--strict-markers` doesn't warn:

```toml
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    "litmus: requires Litmus station + fixtures",
    "unit: pure-Python unit test, no Litmus",
]
```

Then:

```python
@pytest.mark.litmus
def test_with_litmus(dmm, verify):
    verify("voltage", dmm.measure_dc_voltage())

@pytest.mark.unit
def test_without_litmus():
    assert calculate() == 42
```

Run subsets:

```bash
pytest -m litmus              # only Litmus-flavored tests
pytest -m "not litmus"        # only unit tests
```

### Separate directories

```
tests/
├── unit/           # plain pytest, no Litmus surface
│   └── test_*.py
├── integration/    # uses Litmus fixtures
│   └── test_*.py
└── conftest.py     # shared
```

### Loading station YAML from non-pytest code

For migration tooling or scripts that read your station YAML directly:

```python
from litmus.store import get_station

# By id — looks up stations/<id>.yaml under the project root
station = get_station("bench_1")
if station is None:
    raise RuntimeError("bench_1 not found")

for role, cfg in station.instruments.items():
    print(role, cfg.driver, cfg.resource, cfg.mock)
```

`get_station(id)` looks up `stations/<id>.yaml`. `load_station(path)` (also exported from `litmus.store`) takes an explicit `Path` for files outside the project's `stations/` directory.

## Running the tests

### Local development

```bash
pytest tests/                                  # auto-resolves default_station from litmus.yaml
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

After the first test run with Litmus active, verify the results landed:

```bash
litmus runs                  # list of recent runs
litmus show <run_id>         # detailed report for one run
litmus serve                 # operator UI at http://localhost:8000
```

If `litmus runs` is empty, check that the test session reached `RunEnded` (the plugin's autouse `logger` finalizes the run at session end). A killed pytest process produces a parquet stamped `aborted` — see [outcomes](../../concepts/execution/outcomes.md#aborted-process-died-before-cleanup).

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

- [Litmus fixtures](../../reference/pytest/fixtures.md) — the 20 fixtures the plugin contributes (and the per-role auto-fixtures from station YAML)
- [Litmus markers](../../reference/pytest/markers.md) — `litmus_limits`, `litmus_sweeps`, `litmus_mocks`, `litmus_characteristics`, `litmus_connections`, `litmus_retry`, `litmus_prompts`
- [pytest-native reference](../../reference/overview/pytest-native.md) — how Litmus tests use pytest's own collection / fixtures / markers / CLI flags
- [Writing tests](../../how-to/execution/writing-tests.md) — end-to-end patterns for new Litmus-flavored tests
- [Configuration reference](../../reference/configuration.md) — full YAML schemas for `litmus.yaml`, station, fixture, sidecar, profile
- [Mock mode](../../how-to/configuration/mock-mode.md) — `--mock-instruments`, `mock_config:`, the mock pipeline
- [Configuring stations](../../how-to/configuration/configuring-stations.md) — station YAML reference + the `driver:` field
- [Python client reference](../../reference/runtime/client.md) — full `LitmusClient` / `RunBuilder` / `StepBuilder` API (Path B above)
- [Test harness](harness.md) — the imperative entry point for non-pytest runners (Path C above)
- [Custom drivers](../../how-to/configuration/custom-drivers.md) — writing your own driver class (Path D above)
- [Submitting results from non-pytest sources](../data/results-api.md) — LabVIEW, TestStand, CLI bridges via `LitmusClient`
