# Step 3: pytest-native tests

**Goal:** Adopt Litmus's per-test fixtures so measurements get recorded with full [traceability](../how-to/execution/traceability.md).

In step 2, your tests called driver methods and used `assert` for pass/fail. Litmus's `measure` and `verify` fixtures slot in alongside that, recording each measurement to the run record (see [three stores](../concepts/data/three-stores.md)) without changing how your test reads.

You don't need any new YAML for this step. Keep the `conftest.py` from step 2 — the `psu` / `dmm` fixtures still work.

## The fixtures you add

All three are available on every test run — no station, no sidecar, no sweep required. `measure` and `verify` record measurements; `context` exposes what's active for this test — the run, UUT, station, and any sweep values.

| Fixture  | What it gives the test                                 | Verbs                                            |
|----------|--------------------------------------------------------|--------------------------------------------------|
| `measure`| Records a measurement row — no pass/fail check          | `measure(name, value, limit=None, characteristic=None)` |
| `verify` | Records the row, resolves a limit, raises on FAIL      | `verify(name, value, limit=..., characteristic=...)` (`characteristic` = a named measurable property on the part spec — covered in step 6 / [concepts/capabilities](../concepts/configuration/capabilities.md)) |
| `context`| What's active — the run, UUT, station, and (if parametrized) sweep values | `get_param`, `changed`, `last`, `observe`, `.part`, `.station`, `.run` |

These are the three you'll reach for most. The Litmus plugin for pytest provides more — hardware accessors (`pins`, `instruments`, `uut`), config accessors (`part`, `station_config`), and special-purpose fixtures (`vectors`, `sync`) — see the [Litmus fixtures reference](../reference/pytest/fixtures.md) for the full set.

## From assert to measure

Take the test from step 2:

```python
def test_output_voltage(psu, dmm):
    psu.set_voltage(5.0)
    psu.enable_output()
    v = dmm.measure_dc_voltage()
    assert 3.2 <= v <= 3.4
```

Add `measure` and record the measurement explicitly:

```python
def test_output_voltage(psu, dmm, measure):
    psu.set_voltage(5.0)
    psu.enable_output()
    v = dmm.measure_dc_voltage()
    measure("output_voltage", v, limit={"low": 3.2, "high": 3.4, "unit": "V"})
    assert 3.2 <= v <= 3.4
```

Same control flow, but now there's a measurement recorded in the run record — value, units, limits, and outcome — visible to `litmus runs`, the operator UI, and any downstream analysis.

## Skip the assert with `verify`

`verify` is `measure` + `assert` in one call. Pass / fail is decided by the limit; an out-of-range value raises `AssertionError`:

```python
def test_output_voltage(psu, dmm, verify):
    psu.set_voltage(5.0)
    psu.enable_output()
    verify("output_voltage", dmm.measure_dc_voltage(),
           limit={"low": 3.2, "high": 3.4, "unit": "V"})
```

For one-off tests, passing `limit=` inline is fine. The cleaner home for limits is the part spec or the sidecar YAML — both arrive in later steps.

## Classes group related tests

Grouping related tests in a plain pytest class is the standard way to structure a Litmus test:

```python
class TestPowerUp:
    def test_input_voltage(self, psu, verify):
        psu.set_voltage(5.0)
        psu.enable_output()
        verify("input_voltage", psu.measure_voltage(),
               limit={"low": 4.5, "high": 5.5, "unit": "V"})

    def test_output_voltage(self, dmm, verify):
        verify("output_voltage", dmm.measure_dc_voltage(),
               limit={"low": 3.2, "high": 3.4, "unit": "V"})
```

Methods run in source order. Each emits its own [step](../concepts/execution/step-hierarchy.md) events; the class container's [outcome](../reference/data/models.md#enum-outcome) rolls up from the worst child outcome.

If a downstream test should skip when an upstream one fails, use `@pytest.mark.dependency(depends=["test_input_voltage"])` from the [`pytest-dependency`](https://pytest-dependency.readthedocs.io/) plugin — pytest's ecosystem, not a Litmus addition.

## Parametrize is first-class

`@pytest.mark.parametrize` works the way it always does. Add the `context` fixture if you want those parametrize values recorded with the measurement:

```python
import pytest
@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
def test_output_voltage(vin, psu, dmm, verify):
    psu.set_voltage(vin)
    psu.enable_output()
    verify("output_voltage", dmm.measure_dc_voltage(),
           limit={"low": 3.2, "high": 3.4, "unit": "V"})
```

Each parametrized `vin` value is recorded as an **input** named `vin` on the measurement (its role is `input` — see [traceability](../how-to/execution/traceability.md)), so you can later query "how did output_voltage track vin?" — inputs are addressable by name and role — without adding extra code to the test. Sweeping from YAML instead of inline arrives in step 5.

Litmus also adds a native sweep marker, `@pytest.mark.litmus_sweeps`, that records the same inputs and supports range expanders (`linspace`, `arange`, `logspace`):

```python
import pytest

@pytest.mark.litmus_sweeps([{"vin": [4.5, 5.0, 5.5]}])
def test_output_voltage(vin, psu, dmm, verify):
    ...
```

Use `@pytest.mark.parametrize` when you want pytest's per-row `pytest.param(..., id="...")` metadata; use `@pytest.mark.litmus_sweeps` when you want range expanders (`linspace` / `arange` / `logspace`) or want the sweep to match how you'll define it in YAML (step 5). See [`litmus_sweeps`](../reference/pytest/markers.md#litmus_sweeps) and the [Litmus markers reference](../reference/pytest/markers.md) for all seven `litmus_*` markers.

## Multiple measurements per test

Each `verify` or `measure` call records one measurement. Call them as many times as you need:

```python
def test_power_analysis(psu, dmm, verify):
    verify("input_voltage",  psu.measure_voltage(),
           limit={"low": 4.5, "high": 5.5, "unit": "V"})
    verify("input_current",  psu.measure_current(),
           limit={"high": 0.5, "unit": "A"})
    verify("output_voltage", dmm.measure_dc_voltage(),
           limit={"low": 3.2, "high": 3.4, "unit": "V"})
```

## Recording many samples

`measure` records one row per name within a step. For a stream of samples under a single name — a stability capture or a scope trace — use a channel (`stream`), covered in [Step 10](10-live-monitoring.md) and [Step 11](11-waveforms-and-evidence.md).

## Running the tests

Nothing new on the command line — same `pytest` invocation from step 2:

```bash
pytest tests/ --mock-instruments -v
```

If you want to see the recorded measurements, list runs from the CLI:

```bash
litmus runs
litmus show <run_id>
```

## What a measurement records

Read a run back and each measurement gives you:

| Field | Description |
|--------|-------------|
| `measurement_name` | name passed to `verify` / `measure` |
| `measurement_value` | the measured value |
| `measurement_unit` | unit (from `limit.unit`) |
| `measurement_outcome` | `passed` / `failed` / `done` / `skipped` / `errored` |
| `limit_low`, `limit_high`, `limit_nominal`, `limit_comparator` | the active limit |
| `measurement_timestamp` | when it was recorded |
| `vector_index` | which sweep variant (NULL for non-parametrized tests) |

Read these with `litmus runs` / the operator UI, or the [Query API](../reference/data/query-api.md).

## What you learned

- `measure(name, value, limit={"low": ..., "high": ..., "unit": "V"})` records a measurement explicitly
- `verify(name, value, limit=...)` does the same plus pass/fail + raise on FAIL
- Pytest classes group related tests; methods run in source order
- Parametrize works as it always does; values are recorded as inputs (role `input`)

## Continue

So far you've been passing `limit=` inline on every `verify` call. Step 4 separates the limit shape from the test code.

← [Step 2: Mock Instruments](02-mock-instruments.md)  |  [Step 4: Add Limits →](04-limits.md)
