# Mock mode

Run tests without hardware. Litmus mocks instruments at the driver layer; your test code is unchanged from the real-hardware path.

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

Or take the [`mock_instruments`](../reference/litmus-fixtures.md#mock_instruments--session) fixture in a custom fixture / test if you need to branch:

```python
@pytest.fixture
def my_setup(mock_instruments):
    if mock_instruments:
        yield {"mode": "mock"}
    else:
        yield {"mode": "hardware"}
```

All three resolve to the same flag (`src/litmus/pytest_plugin/__init__.py:559-568`).

## What mock mode actually does

For each instrument declared in the active station YAML, the [`InstrumentPool`](../concepts/fixtures.md#shared-instruments) calls `Mock(driver_class, **mock_config)` instead of instantiating the real driver (`src/litmus/instruments/pool.py:90-114`). The `Mock` factory (`src/litmus/instruments/mocks.py:98-220`) returns an object that:

- **Passes `isinstance(obj, driver_class)`** — typed test code is unaffected.
- **Returns the configured value** for any method named in `mock_config` (`measure_dc_voltage`, `set_voltage`, etc.).
- **Returns `None`** for any other method that the real driver class defines (no-op for setters, neutral for getters).
- **Raises `AttributeError`** for anything not on the driver class at all.

Mocks skip `*IDN?` identity verification, skip calibration checks, and skip resource locking. The data they produce gets the `test_phase` field auto-demoted to `"development"` in parquet (per the [`test_phase` resolution](../reference/cli.md#test-phase) rules) so dashboards and queries can filter mock data out of production yield.

## The three independent mock layers

Litmus has **three** ways mock values get into a running test, applied in distinct passes. They are NOT a single priority chain — each does a different thing.

```
session start    │ ① Station mock_config  →  Mock(driver_class, **mock_config)
                 │                              (the base mock instance)
                 │
test setup       │ ② Sidecar / marker mocks:  →  patch.object(instr, attr, ...)
                 │                              (layered on top of the base mock)
                 │
test body        │ ③ mocker.patch.object(...) →  patch.object(instr, attr, ...)
                 │                              (layered on top of (1) and (2);
                 │                               highest precedence within the test)
```

Each layer's source of truth:

| Layer | Where it's configured | What installs it | What it can set |
|---|---|---|---|
| ① Station defaults | `station.yaml` → `instruments.<role>.mock_config` | [`InstrumentPool.acquire`](../concepts/fixtures.md#shared-instruments) at session start | Default method return values for the role |
| ② Sidecar / marker | `tests/test_*.yaml` → `mocks:` or `tests.<name>.mocks:` (or `@pytest.mark.litmus_mocks([...])` inline) | [`_litmus_apply_mocks`](../reference/litmus-fixtures.md) autouse fixture, via `install_mocks` which uses `patch.object` | Any per-test override; full `patch.object` kwargs (`return_value`, `side_effect`, `wraps`, `spec`, etc.) |
| ③ Test-body patch | Inside the test body | `pytest-mock`'s `mocker.patch.object(...)` | Any per-vector / per-row override (read sweep param, decide value, patch) |

Layer ② cascade follows the sidecar's normal file → class → test → profile chain; later entries with the same `target` overwrite earlier ones in a `by_target` dict (`pytest_plugin/autouse.py:319-334`). Profile `mocks: []` does NOT clear earlier entries — to actually replace a specific target, re-declare it with the new value.

The `TestHarness` (non-pytest entry point) has its own fourth path with a different precedence (`vector._mocks` → test-level → limit nominal, with a hardcoded mapping of `*voltage*` → `dmm.measure_voltage` and `*current*` → `psu.measure_current`). The pytest path does NOT consult this fallback.

## Verify mocks are actually firing

Three signals to check before you trust a mock-mode result:

1. **The `mock_instruments` fixture is `True`**:
   ```python
   def test_check_mock_active(mock_instruments):
       assert mock_instruments
   ```

2. **The run record's `test_phase` is `"development"`** — `--mock-instruments` (or `mock: true` on any instrument) auto-demotes the phase regardless of what `--test-phase=` requested:
   ```bash
   litmus show <run_id>  # look for "phase: development"
   ```

3. **`InstrumentConnected` events carry `mocked: true`** — each instrument logs whether it came up real or mocked (`src/litmus/instruments/pool.py:108`):
   ```python
   from litmus.data.event_store import EventStore
   store = EventStore()
   for ev in store.events(session_id=session_id, event_type="instrument.connected"):
       print(ev["role"], ev["mocked"])
   ```

If a measurement comes back as `None`, the test will fail at the `verify(...)` step with `Measurement.outcome=ERRORED` — the most likely cause is that the method called (e.g. `dmm.measure_voltage()`) wasn't in `mock_config`. Either add it to `mock_config:` or layer a `mocks:` entry over it.

## Layer ① — Station `mock_config`

Default values that apply whenever the role is mocked (whether by `--mock-instruments` or per-instrument `mock: true`). Keys are **method names on the driver class**, not signal names.

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

Per-test overrides written in the sidecar YAML colocated with the test module, or inline via `@pytest.mark.litmus_mocks([...])`. The sidecar form is the YAML serialization of the marker; both feed into the same [`litmus_mocks`](../reference/litmus-markers.md#litmus_mocks) marker pipeline.

Each entry is a `MockEntry` dict: a `target:` plus any kwargs `unittest.mock.patch.object` accepts. The full list:

| Field | Effect |
|---|---|
| `target` | `<fixture_name>.<attr>` — the per-role auto-fixture (e.g. `dmm`) plus the method/property to patch. Required. |
| `return_value` | Constant return value for every call. |
| `side_effect` | A callable, an iterable (yields one value per call), or an exception class to raise. |
| `wraps` | Pass-through to the underlying object (record calls without overriding return value). |
| `spec` / `spec_set` / `autospec` / `new_callable` | All forwarded verbatim to `patch.object`. |

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
        return_value: 3.32         # overrides the file-level value above
      - target: psu.measure_current
        return_value: 0.5
```

The cascade order is file → class → test → profile, by `target`. Later wins; non-overlapping passes through. **`mocks: []` in a profile does not strip earlier entries** — it just adds nothing. To remove a specific target, re-declare it with the value you want.

### `side_effect`: raise an exception

Useful for testing retry/error paths:

```yaml
tests:
  test_handle_timeout:
    mocks:
      - target: dmm.measure_dc_voltage
        side_effect: pyvisa.errors.VisaIOError
```

### `side_effect`: sequence of values

Yields one value per call:

```yaml
tests:
  test_settling:
    mocks:
      - target: dmm.measure_dc_voltage
        side_effect: [3.1, 3.2, 3.28, 3.3, 3.3]   # one value per call
```

## Layer ③ — Test-body patches via `mocker`

For per-vector / per-row decisions, patch inside the test body using `pytest-mock`'s `mocker` fixture (Litmus pulls it in as a dependency).

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

This is also the path for **dict-keyed and callable mock values** — useful for SCPI-style mocks that need to respond differently to different commands:

```python
def test_idn_and_measure(dmm, mocker):
    # Dict: look up by first positional arg
    mocker.patch.object(dmm, "query", new=lambda cmd: {
        "*IDN?":           "Vendor,Model,SN001,1.0",
        "MEAS:VOLT?":      "3.31",
        "MEAS:CURR?":      "0.10",
    }.get(cmd, ""))

    # Callable: any logic
    mocker.patch.object(dmm, "compute", new=lambda *args: sum(args) / len(args))
```

The `Mock` factory itself (layer ①) also accepts dict-form and callable-form values in `mock_config` — see [`src/litmus/instruments/mocks.py:71-95`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/instruments/mocks.py).

## Per-instrument mock on real stations

Mock one instrument while keeping others on real hardware. Set `mock: true` on the instrument's station entry. With `mock: true`, the entry doesn't need `driver:` or `resource:` (the [station model](../reference/configuration.md#station-configuration) validator passes when `mock=True` regardless):

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

`psu` and `eload` connect to real hardware; `dmm` is mocked. With `--mock-instruments`, every instrument is mocked regardless of per-instrument `mock:` flags — the flag is an OR (`pytest_plugin/__init__.py:749`).

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
- [Custom drivers → the Mock factory](custom-drivers.md#the-mock-factory) — `Mock(cls, **values)` semantics, dict-form, callable-form
- [Configuration reference → Station configuration](../reference/configuration.md#station-configuration) — `mock_config:`, `mock:` field shapes
- [Limits](limits.md) — limit resolution chain (when `verify(...)` resolves to a limit, what the nominal feeds into)
- [Profiles](profiles.md) — sidecar / marker cascade rules including how `mocks:` lists merge
- [Configuring stations](configuring-stations.md) — full station YAML reference
- [Writing tests](writing-tests.md) — pytest-test authoring patterns
