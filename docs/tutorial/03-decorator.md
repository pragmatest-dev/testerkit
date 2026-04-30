# Step 3: pytest-native tests

**Goal:** Write hardware tests as plain pytest functions (or classes) that log measurements automatically.

## What You'll Build

A test that automatically logs measurements to Litmus results storage, with pass/fail against a spec.

## The Three Fixtures

Litmus tests are plain pytest tests. There is no base class to inherit and no
`@litmus_test` wrapper. Up to three Litmus-provided fixtures show up as
parameters, each with a single responsibility:

| Fixture  | What it holds                                  | Verbs                                            |
|----------|------------------------------------------------|--------------------------------------------------|
| `context`| Vector inputs + observations                   | `get_param`, `changed`, `observe`                |
| `verify` | Limit check + record + raise on FAIL           | `verify(name, value, limit=..., characteristic=...)` |
| `logger` | Event persistence                              | `measure(name, value, limit=...)`, `record`      |

Data-flow rule: **test → spec → logger**. The three objects never call each
other; `logger` reads ambient ContextVars at write time.

See the [pytest-native reference](../reference/pytest-native.md) for the
complete contract.

## The Simplest Test

```python
# tests/test_voltage.py
def test_output_voltage(dmm, logger):
    """Measure output voltage and log it."""
    logger.measure("output_voltage", dmm.measure_voltage())
```

No decorator. No base class. `dmm` is an auto-registered fixture from the
station config; `logger` is always present; the measurement is recorded with
full traceability.

If you also have a product spec configured, prefer `verify` — it resolves
the limit from the spec and raises `AssertionError` on failure:

```python
def test_output_voltage(dmm, verify):
    verify("output_voltage", dmm.measure_voltage())
```

## Classes Group Related Tests

Group related tests with a plain pytest class. Methods run in source order
and are independent by default:

```python
class TestPowerUp:
    def test_input_voltage(self, psu, verify):
        psu.set_voltage(5.0)
        psu.enable_output()
        verify("input_voltage", psu.measure_voltage())

    def test_output_voltage(self, dmm, verify):
        verify("output_voltage", dmm.measure_voltage())
```

If a downstream test should skip when an upstream test fails, use
`@pytest.mark.dependency(depends=["test_input_voltage"])` from the
`pytest-dependency` plugin.

## Accessing Vector Inputs

`@pytest.mark.parametrize` is first-class. Sidecar YAML `vectors:` is the
Litmus-native alternative; both land in `context.get_param(...)`:

```python
import pytest

@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
def test_output_voltage(vin, psu, dmm, verify):
    psu.set_voltage(vin)
    psu.enable_output()
    verify("output_voltage", dmm.measure_voltage())
```

Or via sidecar YAML (see Step 5).

## Multiple Measurements

Just call `verify` or `logger.measure` as many times as you need:

```python
def test_power_analysis(psu, dmm, verify):
    verify("input_voltage", psu.measure_voltage())
    verify("input_current", psu.measure_current())
    verify("output_voltage", dmm.measure_voltage())
```

Each call records one measurement with pass/fail.

## Streaming / Repeated Samples

`logger.measure` enforces unique names within a step. To record many samples
under one name, pass `allow_repeat=True`:

```python
import time

def test_stability(dmm, logger):
    for _ in range(10):
        logger.measure(
            "voltage_sample",
            dmm.measure_voltage(),
            allow_repeat=True,
        )
        time.sleep(1)
```

## Running the Test

```bash
# With mock instruments (no hardware)
pytest tests/test_voltage.py --station=stations/my_station.yaml --mock-instruments -v

# With real hardware
pytest tests/test_voltage.py --station=stations/my_station.yaml --dut-serial=SN001 -v
```

## What Gets Stored

Each measurement includes:

| Field | Description |
|-------|-------------|
| `name` | Measurement name passed to `verify` / `logger.measure` |
| `value` | The measured value |
| `units` | Unit of measure (from limits, when configured) |
| `outcome` | PASS, FAIL, or unchecked |
| `timestamp` | When it was recorded |
| `vector_index` | Which test vector (for parametrized tests) |

## Complete Example

**stations/my_station.yaml:**
```yaml
id: my_station
name: "My Test Bench"

instruments:
  dmm:
    type: dmm
    driver: pymeasure.instruments.keysight.Keysight34461A
    resource: "TCPIP::192.168.1.100::INSTR"
    mock_config:
      voltage: 3.31
  psu:
    type: psu
    driver: pymeasure.instruments.keysight.KeysightE36312A
    resource: "GPIB0::5::INSTR"
    mock_config:
      voltage: 5.0
```

**tests/test_power.py:**
```python
def test_input_voltage(psu, verify):
    """Measure input voltage."""
    psu.set_voltage(5.0)
    psu.enable_output()
    verify("input_voltage", psu.measure_voltage())


def test_output_voltage(dmm, verify):
    """Measure output voltage."""
    verify("output_voltage", dmm.measure_voltage())
```

**Run:**
```bash
pytest tests/test_power.py --station=stations/my_station.yaml --mock-instruments -v
```

## What You Learned

- Tests are plain pytest functions or classes — no `@litmus_test` wrapper
- Up to three Litmus fixtures: `context`, `verify`, `logger`
- `verify(name, value)` to check against product spec limits
- `logger.measure(name, value, ...)` when you need explicit limits
- Instrument role fixtures from station config (e.g. `dmm`, `psu`)

## Next Step

Right now, limits come from a product spec. Let's look at the `Limit` model
and how limits are wired in.

[Step 4: Add Limits →](04-limits.md)
