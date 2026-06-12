# Step 3: pytest-native tests

**Goal:** Adopt Litmus's per-test fixtures so measurements get recorded with full [traceability](../how-to/execution/traceability.md).

In step 2, your tests called driver methods and used `assert` for pass/fail. Litmus's `logger` and `verify` fixtures slot in alongside that, recording each measurement to the run record (the row Litmus writes per test in parquet — see [three stores](../concepts/data/three-stores.md)) without changing how your test reads.

You don't need any new YAML for this step. Keep the `conftest.py` from step 2 — the `psu` / `dmm` fixtures still work.

## The fixtures you add

All three are available on every test run — no station, no sidecar, no sweep required. `logger` and `verify` write measurement rows; `context` exposes the active run / UUT / station / vector state.

| Fixture  | What it gives the test                                 | Verbs                                            |
|----------|--------------------------------------------------------|--------------------------------------------------|
| `logger` | Per-measurement event-log writer                       | `measure(name, value, ...)`, `record`            |
| `verify` | Records the row, resolves a limit, raises on FAIL      | `verify(name, value, limit=..., characteristic=...)` (`characteristic` = a named measurable property on the part spec — covered in step 6 / [concepts/capabilities](../concepts/configuration/capabilities.md)) |
| `context`| Ambient run / UUT / station / vector state             | `get_param`, `changed`, `last`, `observe`, `.part`, `.station`, `.run` |

These are the common per-test entry points. The plugin exposes 17 others (hardware accessors like `pins` / `instruments` / `uut`, configuration accessors like `part` / `station_config`, special modes like `vectors` / `sync`) — see the [Litmus fixtures reference](../reference/pytest/fixtures.md) for the full set.

## From assert to logger.measure

Take the test from step 2:

```python
def test_output_voltage(psu, dmm):
    psu.set_voltage(5.0)
    psu.enable_output()
    v = dmm.measure_dc_voltage()
    assert 3.2 <= v <= 3.4
```

Add `logger` and record the measurement explicitly:

```python
def test_output_voltage(psu, dmm, logger):
    psu.set_voltage(5.0)
    psu.enable_output()
    v = dmm.measure_dc_voltage()
    logger.measure("output_voltage", v, limit={"low": 3.2, "high": 3.4, "units": "V"})
    assert 3.2 <= v <= 3.4
```

Same control flow, but now there's a row in the run record with the value, units, limits, and outcome — visible to `litmus runs`, the operator UI, and any downstream analysis.

## Skip the assert with `verify`

`verify` is `logger.measure` + `assert` in one call. Pass / fail is decided by the limit; an out-of-range value raises `AssertionError`:

```python
def test_output_voltage(psu, dmm, verify):
    psu.set_voltage(5.0)
    psu.enable_output()
    verify("output_voltage", dmm.measure_dc_voltage(),
           limit={"low": 3.2, "high": 3.4, "units": "V"})
```

For one-off tests, passing `limit=` inline is fine. The cleaner home for limits is the part spec or the sidecar YAML — both arrive in later steps.

## Classes group related tests

A plain pytest class with hardware-test-shaped methods is the canonical Litmus shape:

```python
class TestPowerUp:
    def test_input_voltage(self, psu, verify):
        psu.set_voltage(5.0)
        psu.enable_output()
        verify("input_voltage", psu.measure_voltage(),
               limit={"low": 4.5, "high": 5.5, "units": "V"})

    def test_output_voltage(self, dmm, verify):
        verify("output_voltage", dmm.measure_dc_voltage(),
               limit={"low": 3.2, "high": 3.4, "units": "V"})
```

Methods run in source order. Each emits its own [step](../concepts/execution/step-hierarchy.md) events; the class container's [outcome](../reference/data/models.md#enum-outcome) rolls up from the worst child outcome.

If a downstream test should skip when an upstream one fails, use `@pytest.mark.dependency(depends=["test_input_voltage"])` from the [`pytest-dependency`](https://pytest-dependency.readthedocs.io/) plugin — pytest's ecosystem, not a Litmus addition.

## Parametrize is first-class

`@pytest.mark.parametrize` works the way it always does. Add the `context` fixture if you want the test to read its current parametrize values through Litmus's traceability path:

```python
import pytest
@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
def test_output_voltage(vin, psu, dmm, verify):
    psu.set_voltage(vin)
    psu.enable_output()
    verify("output_voltage", dmm.measure_dc_voltage(),
           limit={"low": 3.2, "high": 3.4, "units": "V"})
```

The `vin` value lands in each measurement row's `in_vin` column (an example of the `in_*` [traceability](../how-to/execution/traceability.md) columns — every parametrized input lands in its own `in_<name>` column), so you can later query "how did output_voltage track vin?" without re-instrumenting the test. Sweeping from YAML instead of inline arrives in step 5.

Litmus also adds a native sweep marker, `@pytest.mark.litmus_sweeps`, that feeds the same `in_*` columns and supports range expanders (`linspace`, `arange`, `logspace`):

```python
import pytest

@pytest.mark.litmus_sweeps([{"vin": [4.5, 5.0, 5.5]}])
def test_output_voltage(vin, psu, dmm, verify):
    ...
```

Use `@pytest.mark.parametrize` when you want pytest's per-row `pytest.param(..., id="...")` metadata; use `@pytest.mark.litmus_sweeps` when you want range expanders or sidecar parity. See [`litmus_sweeps`](../reference/pytest/markers.md#litmus_sweeps) and the [Litmus markers reference](../reference/pytest/markers.md) for all seven `litmus_*` markers.

## Multiple measurements per test

Each `verify` or `logger.measure` call records one measurement. Call them as many times as you need:

```python
def test_power_analysis(psu, dmm, verify):
    verify("input_voltage",  psu.measure_voltage(),
           limit={"low": 4.5, "high": 5.5, "units": "V"})
    verify("input_current",  psu.measure_current(),
           limit={"high": 0.5, "units": "A"})
    verify("output_voltage", dmm.measure_dc_voltage(),
           limit={"low": 3.2, "high": 3.4, "units": "V"})
```

## Streaming samples under one name

`logger.measure` enforces unique names within a step. To record many samples under one name (e.g. a stability sweep), pass `allow_repeat=True`:

```python
import time
def test_stability(dmm, logger):
    for _ in range(10):
        logger.measure(
            "voltage_sample",
            dmm.measure_dc_voltage(),
            limit={"low": 3.2, "high": 3.4, "units": "V"},
            allow_repeat=True,
        )
        time.sleep(1)
```

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

## What gets stored

Each measurement row carries:

| Column | Description |
|--------|-------------|
| `measurement_name` | name passed to `verify` / `logger.measure` |
| `measurement_value` | the measured value |
| `measurement_units` | units (from `limit.units` or the explicit `units=` kwarg) |
| `measurement_outcome` | `passed` / `failed` / `skipped` / `errored` |
| `limit_low`, `limit_high`, `limit_nominal`, `limit_comparator` | the active limit |
| `measurement_timestamp` | when it was recorded |
| `vector_index` | which sweep variant (NULL for non-parametrized tests) |

Full schema in [Parquet storage schema](../reference/data/parquet-schema.md).

## What you learned

- `logger.measure(name, value, limit={"low": ..., "high": ..., "units": "V"})` records a measurement explicitly
- `verify(name, value, limit=...)` does the same plus pass/fail + raise on FAIL
- Pytest classes group related tests; methods run in source order
- Parametrize works as it always does; values land in `in_*` columns

## Continue

So far you've been passing `limit=` inline on every `verify` call. Step 4 separates the limit shape from the test code.

← [Step 2: Mock Instruments](02-mock-instruments.md)  |  [Step 4: Add Limits →](04-limits.md)
