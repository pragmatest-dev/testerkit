# Spec-Driven Testing

Derive test limits and [traceability](traceability.md) from the [product specification](../concepts/products.md). The `verify` fixture resolves the limit, DUT pin, and spec reference automatically from the active `product_context` (a [`ProductContext`](../concepts/products.md) — the loaded-product container exposed to tests) — you just call `verify(name, value)`.

## The workflow

1. Define the product YAML with typed characteristics, pins, and operating conditions
2. Run with `--product=<id>` (looks up `products/<id>.yaml`) or `--product=<path>` (explicit path)
3. Call `verify(name, value)` from the test body — everything else flows through

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
      - when: {temperature: {min: 0, max: 50}, load: {min: 0.1, max: 0.5}}
        value: 3.3
        accuracy: {pct_reading: 5}
      - when: {temperature: {min: 50, max: 85}, load: {min: 0.5, max: 1.0}}
        value: 3.3
        accuracy: {pct_reading: 7}
```

```python
# tests/test_power.py
import pytest

class TestPowerBoard:
    @pytest.mark.parametrize("temperature", [25, 85])
    @pytest.mark.parametrize("load", [0.5, 1.0])
    def test_output_voltage(self, temperature, load, dmm, verify, chamber, eload):
        chamber.set_temperature(temperature)
        eload.set_current(load)
        verify("output_voltage", dmm.measure_dc_voltage())
```

`verify` picks the condition row that matches the current parametrize values, resolves limits from the accuracy spec, records `dut_pin="VOUT"` and `spec_ref="output_voltage @ temperature=25, load=0.5"`, and raises `AssertionError` on fail.

## Guardband

Apply a manufacturing-margin tightening at session level:

```bash
pytest --product=products/power_board.yaml --guardband=10 ...
```

Or inline on the spec load:

```python
from litmus.products.context import ProductContext
spec = ProductContext.from_file("products/power_board.yaml", guardband_pct=10.0)
```

```
spec:                                  3.3 V ± 5 %      → 3.135 .. 3.465
with 10 % guardband (tighten by 10 %):                  → 3.152 .. 3.449
```

## Delegate a limit by name — `characteristic:`

When a test reports a value under a different name than the spec, delegate via `characteristic:`:

```python
@pytest.mark.litmus_limits(rail_3v3={"characteristic": "output_voltage"})
def test_output(context, dmm, logger):
    logger.measure("rail_3v3", dmm.measure_dc_voltage())
```

Same effect in sidecar:

```yaml
# tests/test_power.yaml
limits:
  rail_3v3: {characteristic: output_voltage}
```

## Condition matching

`verify("output_voltage", v)` uses the current vector's active parameters to pick a matching condition row from the characteristic's `bands:`. The match runs against the row that `context.get_param(...)` would return — i.e. whatever the active `@pytest.mark.parametrize` / `@pytest.mark.litmus_sweeps` row sets. Drive different conditions by adding parametrize axes, not by passing condition kwargs to `verify`.

## What ends up in the parquet row

Every `verify` records:

| Field            | Source                                                |
|------------------|-------------------------------------------------------|
| `measurement_name` | the `name` arg                                      |
| `measurement_value` | the `value` arg                                    |
| `limit_low` / `limit_high` / `limit_nominal` / `measurement_units` | spec characteristic + tolerance |
| `measurement_outcome` | `passed` / `failed` (lowercase enum value)        |
| `spec_ref`       | e.g. `"output_voltage @ temperature=25, load=0.5"`    |
| `dut_pin`        | pins list on the characteristic                       |
| `fixture_connection`  | from the active fixture YAML                          |
| `instrument_*`   | ambient ContextVars from the driver layer             |

No manual threading of traceability fields — they're injected by the plugin.

## When to reach for `verify` vs `logger.measure`

| Scenario                                               | Use                                     |
|--------------------------------------------------------|-----------------------------------------|
| Measurement maps to a product-spec characteristic      | `verify("output_voltage", v)`       |
| Procedure-only measurement (no product characteristic) | `logger.measure("startup_time", t, ...)` |
| Dynamic limit from conditions                          | Callable limit via marker / sidecar     |
| No limits, data collection only                        | `logger.measure(...)` with no limits    |

## See also

- [Limits guide](limits.md) — `characteristic:`, callables, resolution order
- [Litmus fixtures](../reference/litmus-fixtures.md) — all 20 plugin fixtures with signatures
- [Writing Tests](writing-tests.md) — end-to-end patterns
