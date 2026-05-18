# Mock mode

Run tests without hardware. Litmus mocks instruments at the driver layer so your test code is identical to the real-hardware path.

## Quick start

Pass `--mock-instruments` to substitute mock instruments for every real driver the active station declares:

```bash
pytest tests/ --station=bench_1 --mock-instruments --dut-serial=SIM001
```

Or set the env var:

```bash
export LITMUS_MOCK_INSTRUMENTS=1
pytest tests/ --station=bench_1 --dut-serial=SIM001
```

Or set `mock_instruments: true` in your project's `litmus.yaml` so every run mocks by default; override per-run with `--no-mock-instruments`.

Take the [`mock_instruments`](../reference/litmus-fixtures.md#mock_instruments--session) fixture inside a test if you need to branch:

```python
@pytest.fixture
def my_setup(mock_instruments):
    if mock_instruments:
        yield {"mode": "mock"}
    else:
        yield {"mode": "hardware"}
```

All four sources resolve through `mocks_active()` (`src/litmus/pytest_plugin/helpers.py:296-316`). Priority — highest first:

1. `--mock-instruments` / `--no-mock-instruments` CLI flag (either explicit flag wins).
2. `LITMUS_MOCK_INSTRUMENTS=1` env var.
3. `litmus.yaml: mock_instruments:` project default.
4. `False` if nothing else set.

## What mock mode actually does

For each instrument in the active station YAML, the [`InstrumentPool`](../concepts/fixtures.md#shared-instruments) substitutes a mock for the real driver. The substitution is built in `load_and_connect` at `src/litmus/instruments/lifecycle.py:136`:

```python
inst: Any = Mock(object, **(mock_config or {}))
```

That call returns a `MockClass(object)` instance from `src/litmus/instruments/mocks.py:98-220`. Key properties of the substituted object:

- **`isinstance(dmm, MyDMM)` is `False`** in this path. The mock is a subclass of `object`, not of your driver class. Tests that rely on isinstance against the real driver class will fail; either don't do that, or construct `Mock(MyDMM, …)` yourself in a conftest fixture (the user-direct path is described in [custom-drivers.md](custom-drivers.md#conftestpy-bringup-tier--no-station-yaml-yet)).
- **Every method called on the mock is a silent no-op returning `None`** unless you've put a value for it in `mock_config:`. Because the base is `object`, the mock has no typed surface to validate against — there is no `AttributeError` for missing methods.
- **Methods you do list in `mock_config:` return the configured value** — a scalar (returned on every call), a dict (first positional arg is the lookup key), or a callable (invoked with the call args).
- `connect()` / `disconnect()` are wired automatically; the mock works as a context manager.
- The instance has `manufacturer` / `model` / `serial` / `firmware` copied from the station YAML's optional `info:` block if present (`lifecycle.py:137-141`).

What the platform skips for mocked instruments:

- **`*IDN?` identity verification** (`lifecycle.py:183-193`) — only runs for `record.mocked == False`.
- **Resource locking** (`pool.py:96`) — mocks don't take a real `resource:` lock.

What still runs for mocks:

- **Calibration check** (`lifecycle.py:195`, `check_calibration`) — runs unconditionally. If your station YAML lists a `calibration:` block with an expired or near-due date, you'll see the warning whether the instrument is real or mocked.
- **`test_phase` auto-demotion to `"development"`** — `--mock-instruments` (or any per-instrument `mock: true`) demotes the run's `test_phase` regardless of what `--test-phase=` requested. Dashboards and queries can filter mock data out of production yield by `WHERE test_phase = 'production'`.

## The three independent mock layers

Litmus has three places mock values get into a running test, applied in distinct passes. They are NOT a single priority chain — each layer adds or overrides values on top of the previous one.

```
session start    │ ① Station mock_config  →  Mock(object, **mock_config)
                 │                              (the base mock instance)
                 │
test setup       │ ② Sidecar / marker mocks:  →  patch.object(instr, attr, **kwargs)
                 │                              (overlay applied per-test, torn down after)
                 │
test body        │ ③ mocker.patch.object(...) →  patch.object(instr, attr, ...)
                 │                              (overlay applied per-call, torn down at test end)
```

Each layer's source of truth:

| Layer | Where it's configured | What installs it | What it can set |
|---|---|---|---|
| ① Station defaults | `station.yaml` → `instruments.<role>.mock_config` | `load_and_connect` (`lifecycle.py:136`) at session start | Default method return values for the role |
| ② Sidecar / marker | `tests/test_*.yaml` → `mocks:` or `tests.<name>.mocks:`, or `@pytest.mark.litmus_mocks([...])` inline | `_litmus_apply_mocks` autouse fixture (`pytest_plugin/autouse.py:300-345`), via `install_mocks` (`execution/mocks.py:30-63`) which calls `patch.object` | Any per-test override; full `patch.object` kwargs (`return_value`, `side_effect`, `wraps`, `spec`, etc.) |
| ③ Test-body patch | Inside the test body | `pytest-mock`'s `mocker.patch.object(...)` — **add `pytest-mock` to your project's dev dependencies; Litmus does not pull it in.** | Any per-vector / per-row override |

Layer ② cascade walks the marker chain root-to-leaf (file → class → test → profile). Later entries with the same `target` overwrite earlier ones in a `by_target` dict (`autouse.py:321-334`). **A profile with `mocks: []` does NOT clear earlier entries** — it simply contributes nothing. To remove a specific target, re-declare it with the value you want.

The `TestHarness` (non-pytest entry point) has its own fourth path with a different precedence (`vector._mocks` → test-level → limit nominal, with a hardcoded mapping of `*voltage*` → `dmm.measure_voltage` and `*current*` → `psu.measure_current`). The pytest path does NOT consult this fallback.

## Verify mocks are actually firing

Three signals to check before you trust a mock-mode result:

1. **The `mock_instruments` fixture is `True`**:
   ```python
   def test_check_mock_active(mock_instruments):
       assert mock_instruments
   ```

2. **The run record's `test_phase` is `"development"`** — `--mock-instruments` (or `mock: true` on any instrument) auto-demotes the phase. Read it from the parquet row, or via:
   ```python
   from litmus.analysis.runs_query import RunsQuery
   with RunsQuery() as q:
       row = q.get(run_id)
       assert row.test_phase == "development"
   ```

3. **`fixture.instrument_connected` events carry `mocked: true`** — each instrument logs whether it came up real or mocked (`pool.py:108`):
   ```python
   from litmus.data.event_store import EventStore
   with EventStore() as store:
       for ev in store.events(session_id=session_id, event_type="fixture.instrument_connected"):
           print(ev["role"], ev["mocked"])
   ```

If a measurement comes back as `None`, the next `float(...)` cast or `verify(...)` will surface it as an `ERRORED` row — the most likely cause is that the method the test called (e.g. `dmm.measure_dc_voltage()`) wasn't listed in `mock_config:`. Add it, or layer a `mocks:` entry over it.

## Layer ① — Station `mock_config`

Default values that apply whenever the role is mocked (whether by `--mock-instruments`, env var, project default, or per-instrument `mock: true`). Keys are **method names on the driver class**, not signal names.

```yaml
# stations/bench_1.yaml
id: bench_1
name: "Production Bench 1"

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      measure_dc_voltage: 3.31
      measure_current: 0.1
      measure_resistance: 1000

  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
    mock_config:
      measure_voltage: 5.0
      measure_current: 0.5
```

For [per-instrument `mock: true`](#per-instrument-mock-on-real-stations) (mocking one instrument while keeping others on real hardware), `mock_config:` works the same way.

## Layer ② — Sidecar `mocks:` (the `litmus_mocks` marker)

Per-test overrides written in the sidecar YAML next to the test module, or inline via `@pytest.mark.litmus_mocks([...])`. The sidecar form is the YAML serialization of the marker; both feed the same [`litmus_mocks`](../reference/litmus-markers.md#litmus_mocks) pipeline.

Each entry is a `MockEntry` — a `target:` plus any kwargs `unittest.mock.patch.object` accepts. `MockEntry` is `extra="allow"`, so unknown kwargs pass through verbatim. The kwargs `patch.object` actually does something useful with:

| Field | Effect |
|---|---|
| `target` | `<fixture_name>.<attr>` — the per-role auto-fixture (e.g. `dmm`) plus the method/property to patch. Required. |
| `return_value` | Constant return value for every call. |
| `side_effect` | A callable, an iterable (yields one value per call), or — **only when constructed in Python code** — an exception class to raise. |
| `wraps` | Pass-through to the underlying object (record calls without overriding return value). |
| `spec` / `spec_set` / `autospec` / `new_callable` | Forwarded verbatim to `patch.object`. |

File-level `mocks:` applies to every test in the file. Per-test override goes under `tests.<test_name>.mocks:`:

```yaml
# tests/test_power.yaml

# File-level: every test in test_power.py uses these unless overridden
mocks:
  - target: dmm.measure_dc_voltage
    return_value: 3.31

# Per-test override
tests:
  test_output_voltage:
    mocks:
      - target: dmm.measure_dc_voltage
        return_value: 3.32         # overrides the file-level value
      - target: psu.measure_current
        return_value: 0.5
```

The cascade order is file → class → test → profile, by `target`. Later wins; non-overlapping passes through. **`mocks: []` in a profile does not strip earlier entries.** To remove a specific target, re-declare it with the value you want.

### `side_effect`: sequence of values

Yields one value per call:

```yaml
tests:
  test_settling:
    mocks:
      - target: dmm.measure_dc_voltage
        side_effect: [3.1, 3.2, 3.28, 3.3, 3.3]   # one value per call
```

### `side_effect`: raise an exception

YAML cannot carry a Python exception class — a string like `"pyvisa.errors.VisaIOError"` would be passed verbatim to `patch.object` and used as a return value, not raised. To raise an exception, install the mock from test-body Python instead (layer ③):

```python
import pyvisa

def test_handle_timeout(dmm, mocker):
    mocker.patch.object(dmm, "measure_dc_voltage", side_effect=pyvisa.errors.VisaIOError)
    # ... exercise retry / error-handling code path ...
```

## Layer ③ — Test-body patches via `mocker`

For per-vector / per-row decisions, or for exception-raising side effects, patch inside the test body using `pytest-mock`'s `mocker` fixture. **`pytest-mock` is not bundled with Litmus** — add it to your project's dev dependencies (`uv add --dev pytest-mock`) before using `mocker`.

```python
import pytest

def test_load_regulation(load, dmm, verify, mocker):
    # decide the value based on the active vector
    expected = {0.1: 3.32, 0.5: 3.30, 0.8: 3.28}[load]
    mocker.patch.object(dmm, "measure_dc_voltage", return_value=expected)
    verify("output_voltage", dmm.measure_dc_voltage())
```

Pair with a sidecar sweep so pytest parametrizes the test:

```yaml
# tests/test_power.yaml
tests:
  test_load_regulation:
    sweeps:
      - {load: [0.1, 0.5, 0.8]}
```

`mocker.patch.object` runs after the autouse `_litmus_apply_mocks` fixture, so it layers on top of any sidecar / marker mocks for the same test.

This is also the path for **dict-keyed and callable values** — useful for SCPI-style mocks that need to respond differently to different commands:

```python
def test_idn_and_measure(dmm, mocker):
    # Callable: dict-style lookup by first positional arg
    mocker.patch.object(dmm, "query", new=lambda cmd: {
        "*IDN?":           "Vendor,Model,SN001,1.0",
        "MEAS:VOLT?":      "3.31",
        "MEAS:CURR?":      "0.10",
    }.get(cmd, ""))
```

The `Mock` factory in layer ① also accepts dict-form and callable-form values in `mock_config` — see `src/litmus/instruments/mocks.py:71-95`.

## Per-instrument mock on real stations

Mock one instrument while keeping others on real hardware. Set `mock: true` on the instrument's station entry. With `mock: true`, the entry doesn't need `driver:` or `resource:` (`StationInstrumentConfig.resource_required_for_real_hardware` skips the check when `mock=True`):

```yaml
# stations/mixed_bench.yaml
id: mixed_bench
name: "Mixed Mode Bench"

instruments:
  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
    # Real hardware

  dmm:
    type: dmm
    mock: true                     # Always mock this instrument
    mock_config:
      measure_dc_voltage: 3.3

  eload:
    type: eload
    driver: drivers.eload.MyELoad
    resource: "TCPIP::192.168.1.101::INSTR"
    # Real hardware
```

Run **without** `--mock-instruments`:

```bash
pytest tests/ --station=mixed_bench --dut-serial=SN001
```

`psu` and `eload` connect to real hardware; `dmm` is mocked. With `--mock-instruments` (or the env var, or `mock_instruments: true` in `litmus.yaml`), every instrument is mocked regardless of per-instrument `mock:` flags — the per-instrument flag is OR'd with the session-wide flag (`pytest_plugin/__init__.py:749`).

Common scenarios:

- One instrument is in cal lab — set `mock: true` on it, leave the rest real.
- Hardware-in-the-loop CI where one expensive instrument isn't available.
- Testing instrument-specific edge cases without disturbing the rest of the bench.

## CI

Mock-only CI is the canonical path for the green/red check on every PR:

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: |
    pytest tests/ \
      --station=ci_station \
      --mock-instruments \
      --dut-serial=CI-TEST \
      --test-phase=development
```

Pair with a `stations/ci_station.yaml` where every instrument's `mock_config:` covers every method the tests call. If a method isn't in `mock_config`, the mock returns `None`, and any downstream `float(...)` or arithmetic will fail loudly.

## Best practices

### Match limit nominals

When a test has a limit, set the mock's return value to the limit's nominal. The test passes in mock mode and any real-hardware failure is a real failure, not a mock-config mismatch:

```yaml
# stations/bench_1.yaml
instruments:
  dmm:
    mock_config:
      measure_dc_voltage: 3.3        # matches the nominal below
```

```yaml
# tests/test_power.yaml
limits:
  output_voltage:
    low: 3.135
    high: 3.465
    nominal: 3.3
    units: V
```

### Use realistic values

```yaml
# Good — values you'd see on real hardware
mock_config:
  measure_dc_voltage: 3.31
  measure_current: 0.102

# Bad — obvious sentinels make every test pass even when limits are wrong
mock_config:
  measure_dc_voltage: 1234
```

### Don't write per-vector `_mocks` in the sidecar

Sidecar YAML doesn't support per-vector mocks (only file-level / class-level / per-test). For per-vector values, drive them from the test body via `mocker.patch.object(...)` (layer ③ above).

### Method names, not signal names

`mock_config:` keys must match the **method names on the driver class**. `voltage:` doesn't work because the real driver doesn't have a `voltage()` method — it has `measure_dc_voltage()` (DMM), `measure_voltage()` (PSU/ELoad), `set_voltage()`, etc. If you're not sure what the driver class exposes, read its source.

## See also

- [Litmus fixtures → `mock_instruments`](../reference/litmus-fixtures.md#mock_instruments--session) — the boolean fixture this page demonstrates
- [Litmus markers → `litmus_mocks`](../reference/litmus-markers.md#litmus_mocks) — the marker that sidecar `mocks:` blocks compile to
- [Custom drivers](custom-drivers.md) — driver authoring, including the bringup-tier conftest pattern that uses `Mock(MyDMM, …)` directly
- [Configuration reference → Station YAML](../reference/configuration.md#station-yaml) — `mock_config:`, `mock:` field shapes
- [Limits](limits.md) — limit resolution chain (what the nominal you matched feeds into)
- [Profiles](profiles.md) — sidecar / marker cascade rules including how `mocks:` lists merge
- [Configuring stations](configuring-stations.md) — full station YAML reference
- [Writing tests](writing-tests.md) — pytest-test authoring patterns
