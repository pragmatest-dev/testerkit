# Spec-Driven Testing

Derive test limits and [traceability](traceability.md) from the [product specification](../../concepts/configuration/products.md). The `verify` fixture resolves the limit, DUT pin, and spec reference automatically from the active `product_context` (a [`ProductContext`](../../concepts/configuration/products.md) — the loaded-product container exposed to tests) — you just call `verify(name, value)`.

> **Prerequisites.** A `products/<id>.yaml` file with at least one characteristic (see [tutorial step 6](../../tutorial/06-specifications.md)). The product context must be active — pass `--product=<id>` / `--product=<path>`, or `--dut-part-number=<pn>` to look it up by part number, or rely on single-file autodiscovery when there's exactly one product YAML in `products/`. Limits also flow from sidecar YAML / markers / profiles — this page focuses on the product-spec path.

## The workflow

1. Define the product YAML with typed characteristics, pins, and operating conditions
2. Run with `--product=<id>` (looks up `products/<id>.yaml`) or `--product=<path>` (explicit path)
3. Call `verify(name, value)` from the test body — everything else flows through

## Minimal example — unconditional characteristic

The simplest case: one band, no `when:` clauses. `verify("name", value)` picks up the limit straight from the product spec.

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
      - value: 3.3
        accuracy: {pct_reading: 5}
```

```python
# tests/test_power.py
def test_output_voltage(dmm, verify):
    verify("output_voltage", dmm.measure_dc_voltage())
```

`verify` resolves the limit (3.3 V ± 5 % → 3.135..3.465), records the row, and raises `LimitFailure` on fail. The recorded fields:

- `dut_pin = "J1.3"` — copied from the pin's `name:` field (the human designator), not from the dict key (`VOUT`) you reference it by.
- `spec_ref = "Section 7.2"` — built from the characteristic's `datasheet_ref:`. When `datasheet_ref:` is absent, the literal string `"spec"` is used instead.
- `characteristic_id = "output_voltage"` — the dict key under `characteristics:`.

## Condition-indexed example — when accuracy varies with operating point

When a characteristic's bands have `when:` clauses (different accuracy bands per temperature / load / etc.), `verify("name", value)` on its own won't pick the right band. The product-spec-only path inside `verify` doesn't forward your active sweep params to the band matcher, so condition-indexed lookups raise `ValueError` ("No spec band matches: …").

Bind through `@pytest.mark.litmus_limits` (or sidecar) using `characteristic:`. That route reads the active vector params, picks the matching band, and passes the limit back to `verify`:

```yaml
# products/power_board.yaml
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

@pytest.mark.litmus_limits(output_voltage={"characteristic": "output_voltage"})
@pytest.mark.parametrize("temperature,load", [(25, 0.5), (85, 1.0)])
def test_output_voltage(temperature, load, dmm, verify, chamber, eload):
    chamber.set_temperature(temperature)
    eload.set_current(load)
    verify("output_voltage", dmm.measure_dc_voltage())
```

(The two parametrize axes are zipped into one combined axis so every case hits a declared band — the cross-product `{25,85} × {0.5,1.0}` would produce the case `(25, 1.0)` that matches neither band. Through the marker path the no-match falls through to `None` and `verify` then raises `MissingLimitError` with the resolution chain in the message. Make your parametrize cover the bands your spec declares.)

`spec_ref` on the recorded row reflects the matched band's conditions in **alphabetical order by key**:

```
spec_ref = "Section 7.2 @ load=0.5, temperature=25"
```

(The base — `"Section 7.2"` — comes from the characteristic's `datasheet_ref:` and the conditions are appended after `@`, alphabetized.)

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

When the limit is bound through `@pytest.mark.litmus_limits(<name>={"characteristic": "<char_id>"})` (or sidecar), the resolver reads the active sweep params and selects the first `band` whose `when:` clauses all match. Drive different conditions by adding parametrize / `litmus_sweeps` axes, not by passing condition kwargs to `verify`.

If you call `verify("name", value)` without a `litmus_limits` binding and the characteristic has condition-indexed bands, the resolver can't match and raises `ValueError`. The unconditional-characteristic shortcut in [Minimal example](#minimal-example--unconditional-characteristic) only works because that characteristic has a single band whose empty `when:` matches anything.

## What ends up in the parquet row

Every `verify` records:

| Field            | Source                                                |
|------------------|-------------------------------------------------------|
| `measurement_name` | the `name` arg                                      |
| `measurement_value` | the `value` arg                                    |
| `limit_low` / `limit_high` / `limit_nominal` / `measurement_units` | spec characteristic + tolerance |
| `measurement_outcome` | `passed` / `failed` (lowercase enum value)        |
| `spec_ref`       | e.g. `"Section 7.2 @ load=0.5, temperature=25"` (`datasheet_ref` or `"spec"` + conditions sorted alphabetically) |
| `dut_pin`        | `Product.pins[primary_pin_id].name` (the human pin designator, e.g. `"J1.3"`) |
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

`verify` raises `MissingLimitError` (from `litmus.execution.verify`) when none of the resolution sources — markers, sidecar, profile, or product spec — produce a limit for the named measurement. This is intentional: a `verify` call with no spec is a config bug, not a silent "unchecked" recording. Use `logger.measure` for characterization sweeps where unchecked rows are the point.

## See also

- [Limits guide](limits.md) — `characteristic:`, callables, resolution order
- [Litmus fixtures](../../reference/litmus-fixtures.md) — all 20 plugin fixtures with signatures
- [Writing Tests](writing-tests.md) — end-to-end patterns
