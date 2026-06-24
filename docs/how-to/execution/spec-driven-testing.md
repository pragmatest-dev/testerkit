# Spec-Driven Testing

Derive test limits and [traceability](traceability.md) from the [part specification](../../concepts/configuration/parts.md). The `verify` fixture resolves the limit, UUT pin, and spec reference automatically from the active part (loaded from `parts/*.yaml`; the `part` fixture exposes that [`Part`](../../concepts/configuration/parts.md) definition) — you just call `verify(name, value)`.

> **Prerequisites.** A `parts/<id>.yaml` file with at least one characteristic (see [tutorial step 6](../../tutorial/06-specifications.md)). The part context must be active — pass `--part=<id>` / `--part=<path>`, or `--uut-part-number=<pn>` to look it up by part number, or rely on single-file autodiscovery when there's exactly one part YAML in `parts/`. Limits also flow from sidecar YAML / markers / profiles — this page focuses on the part-spec path.

## The workflow

1. Define the part YAML with typed characteristics, pins, and operating conditions
2. Run with `--part=<id>` (looks up `parts/<id>.yaml`) or `--part=<path>` (explicit path)
3. Call `verify(name, value)` from the test body — everything else flows through

## Minimal example — unconditional characteristic

The simplest case: one band, no `when:` clauses. `verify("name", value)` picks up the limit straight from the part spec.

```yaml
# parts/power_board.yaml
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
    unit: V
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

- `uut_pin = "J1.3"` — copied from the pin's `name:` field (the human designator), not from the dict key (`VOUT`) you reference it by.
- `spec_ref = "Section 7.2"` — built from the characteristic's `datasheet_ref:`. When `datasheet_ref:` is absent, the literal string `"spec"` is used instead.
- `characteristic_id = "output_voltage"` — the dict key under `characteristics:`.

## Condition-indexed example — when accuracy varies with operating point

When a characteristic's bands have `when:` clauses (different accuracy bands per temperature / load / etc.), a bare `verify("name", value)` can't choose between them — it doesn't see your active conditions. To match on temperature, load, or any other condition, point the measurement at its spec characteristic with `@pytest.mark.litmus_limits` (or a sidecar) using `characteristic:` (see [Condition matching](#condition-matching)):

```yaml
# parts/power_board.yaml
characteristics:
  output_voltage:
    direction: output
    function: dc_voltage
    unit: V
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

The parametrize cases are paired `(25, 0.5)` and `(85, 1.0)`, not crossed. A crossed `{25,85} × {0.5,1.0}` would produce `(25, 1.0)`, which matches no declared band, and `verify` would raise `MissingLimitError`. Cover only the condition combinations your spec declares bands for.

`spec_ref` on the recorded row reflects the matched band's conditions in **alphabetical order by key**:

```
spec_ref = "Section 7.2 @ load=0.5, temperature=25"
```

`"Section 7.2"` comes from the characteristic's `datasheet_ref:`; conditions are appended after `@`, alphabetized.

## Guardband

Apply a manufacturing-margin tightening at session level:

```bash
pytest --part=parts/power_board.yaml --guardband=10 ...
```

Or inline on the spec load:

```python
from litmus.parts.context import PartContext
spec = PartContext.from_file("parts/power_board.yaml", guardband_pct=10.0)
```

```
spec:                                  3.3 V ± 5 %      → 3.135 .. 3.465
with 10 % guardband (tighten by 10 %):                  → 3.152 .. 3.449
```

## Map a test name to a spec characteristic

When a test reports a value under a different name than the spec, point the measurement at its spec characteristic with `characteristic:`:

```python
@pytest.mark.litmus_limits(rail_3v3={"characteristic": "output_voltage"})
def test_output(context, dmm, measure):
    measure("rail_3v3", dmm.measure_dc_voltage())
```

Same effect in sidecar:

```yaml
# tests/test_power.yaml
limits:
  rail_3v3: {characteristic: output_voltage}
```

## Condition matching

When the limit is pointed at a characteristic through `@pytest.mark.litmus_limits(<name>={"characteristic": "<char_id>"})` (or a sidecar), Litmus reads your active sweep conditions and uses the first `band` whose `when:` clauses all match. Drive different conditions by adding `parametrize` / `litmus_sweeps` axes, not by passing condition kwargs to `verify`.

A bare `verify` against a characteristic that has per-condition bands raises an error — point it at the characteristic through `litmus_limits` so the conditions are available. The minimal example above works without this only because its single band has no conditions.

## What ends up in the parquet row

Every `verify` records:

| Field            | Source                                                |
|------------------|-------------------------------------------------------|
| `measurement_name` | the `name` arg                                      |
| `measurement_value` | the `value` arg                                    |
| `limit_low` / `limit_high` / `limit_nominal` / `measurement_unit` | spec characteristic + tolerance |
| `measurement_outcome` | `passed` / `failed` (lowercase enum value)        |
| `spec_ref`       | e.g. `"Section 7.2 @ load=0.5, temperature=25"` — see [Condition-indexed example](#condition-indexed-example--when-accuracy-varies-with-operating-point) |
| `uut_pin`        | the pin's `name:` from the part YAML (e.g. `"J1.3"`) |
| `fixture_connection`  | from the active fixture YAML                          |
| `instrument_*`   | filled in automatically from the active instrument driver |

Your test body only names the measurement and supplies the reading. Pins, limits, spec references, and conditions all live in the part YAML — Litmus fills the traceability fields in for you. Change a limit by editing the spec, not the test.

## When to reach for `verify` vs `measure`

| Scenario                                               | Use                                     |
|--------------------------------------------------------|-----------------------------------------|
| Measurement maps to a part-spec characteristic      | `verify("output_voltage", v)`       |
| Procedure-only measurement (no part characteristic) | `measure("startup_time", t, ...)` |
| Dynamic limit from conditions                          | a function-valued limit — see [Limits guide](limits.md) |
| No limits, data collection only                        | `measure(...)` with no limits    |

`verify` raises `MissingLimitError` when none of the resolution sources — markers, sidecar, profile, or part spec — produce a limit for the named measurement. `verify` always expects a limit, so a missing one surfaces immediately rather than recording an unchecked value. Use `measure` when an unchecked row is what you want.

## See also

- [Limits guide](limits.md) — `characteristic:`, callables, resolution order
- [Litmus fixtures](../../reference/pytest/fixtures.md) — all the plugin fixtures with signatures
- [Writing Tests](writing-tests.md) — end-to-end patterns
