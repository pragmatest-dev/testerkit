# Spec-Driven Testing

Derive test limits and traceability from the product specification. The `spec` fixture resolves the limit, DUT pin, and spec reference automatically — you just call `spec.check(name, value)`.

## The workflow

1. Define the product YAML with typed characteristics, pins, and operating conditions
2. Run with `--product=products/<name>.yaml` (or set `default_product:` in `litmus.yaml` / active profile)
3. Call `spec.check(name, value)` from the test body — everything else flows through

## Minimal example

```yaml
# products/power_board.yaml
id: power_board
name: "5V to 3.3V Converter"
pins:
  VOUT:
    name: "J1.3"
    net: "VOUT_3V3"
characteristics:
  output_voltage:
    direction: output
    function: dc_voltage
    units: V
    pins: [VOUT]
    datasheet_ref: "Section 7.2"
    bands:
      - conditions: {temperature: {min: 0, max: 50}, load: {min: 0.1, max: 0.5}}
        value: 3.3
        accuracy: {pct_reading: 5}
      - conditions: {temperature: {min: 50, max: 85}, load: {min: 0.5, max: 1.0}}
        value: 3.3
        accuracy: {pct_reading: 7}
```

```python
# tests/test_power.py
import pytest

class TestPowerBoard:
    @pytest.mark.parametrize("temperature", [25, 85])
    @pytest.mark.parametrize("load", [0.5, 1.0])
    def test_output_voltage(self, temperature, load, dmm, spec, chamber, eload):
        chamber.set_temperature(temperature)
        eload.set_current(load)
        spec.check("output_voltage", dmm.measure_dc_voltage())
```

`spec.check` picks the condition row that matches the current parametrize values, resolves limits from the accuracy spec, records `dut_pin="VOUT"` and `spec_ref="output_voltage @ temperature=25, load=0.5"`, and raises `AssertionError` on fail.

## Guardband

Apply a manufacturing-margin tightening at session level:

```bash
pytest --product=products/power_board.yaml --guardband-pct=10 ...
```

Or inline on the spec load:

```python
from litmus.products import ProductContext
spec = ProductContext.from_file("products/power_board.yaml", guardband_pct=10.0)
```

```
spec:                                  3.3 V ± 5 %      → 3.135 .. 3.465
with 10 % guardband (tighten by 10 %):                  → 3.152 .. 3.449
```

## Delegate a limit by name — `ref:`

When a test reports a value under a different name than the spec, delegate via `ref:`:

```python
@pytest.mark.litmus_limits(rail_3v3={"ref": "output_voltage"})
def test_output(context, dmm, logger):
    logger.measure("rail_3v3", dmm.measure_dc_voltage())
```

Same effect in sidecar:

```yaml
# tests/test_power.yaml
limits:
  rail_3v3: {ref: output_voltage}
```

## Condition matching

`spec.check("output_voltage", v)` uses the parametrize values on the current test to pick a matching condition row. If no row explicitly matches, the nearest row is used.

Pass conditions explicitly to override:

```python
spec.check("output_voltage", v, temperature=85, load=1.0)
```

## What ends up in the parquet row

Every `spec.check` records:

| Field            | Source                                                |
|------------------|-------------------------------------------------------|
| `measurement_name` | the `name` arg                                      |
| `value`          | the `value` arg                                       |
| `low` / `high` / `nominal` / `units` | spec characteristic + guardband    |
| `outcome`        | PASS / FAIL                                           |
| `spec_ref`       | e.g. `"output_voltage @ temperature=25, load=0.5"`    |
| `dut_pin`        | pins list on the characteristic                       |
| `fixture_connection`  | from the active fixture YAML                          |
| `instrument_*`   | ambient ContextVars from the driver layer             |

No manual threading of traceability fields — they're injected by the plugin.

## When to reach for `spec.check` vs `logger.measure`

| Scenario                                               | Use                                     |
|--------------------------------------------------------|-----------------------------------------|
| Measurement maps to a product-spec characteristic      | `spec.check("output_voltage", v)`       |
| Procedure-only measurement (no product characteristic) | `logger.measure("startup_time", t, ...)` |
| Dynamic limit from conditions                          | Callable limit via marker / sidecar     |
| No limits, data collection only                        | `logger.measure(...)` with no limits    |

## See also

- [Limits guide](limits.md) — `ref:`, callables, resolution order
- [pytest-native reference](../reference/pytest-native.md) — concise fixture card
- [Writing Tests](writing-tests.md) — end-to-end patterns
