# Vector Expansion

Vectors define the test conditions your tests run against. Litmus expands vectors into `pytest.mark.parametrize` calls and iterates — every vector produces one test invocation, the same as native pytest parametrize.

Vectors can come from:

- **`@pytest.mark.litmus_vectors(**kwargs)`** on a method or class — inline, code-owned
- **Sidecar YAML `test_<module>.yaml`** — operator-editable, lives next to tests
- **Sequence step `vectors:`** — operator-facing production runs (see the [sequence YAML reference](../reference/sequence-yaml.md))
- **Native `@pytest.mark.parametrize`** — first-class, fully compatible

All paths feed `context.get_param(...)` identically.

## The basics

A **vector** is a dict of parameters for one test iteration. `context.get_param("name")` reads `request.node.callspec.params["name"]` regardless of source:

```python
import pytest

@pytest.mark.litmus_vectors(vin=[4.5, 5.0, 5.5], load=[0.1, 0.5, 1.0])
def test_output_voltage(context, psu, dmm, spec):
    psu.set_voltage(context.get_param("vin"))
    psu.enable_output()
    spec.check("output_voltage", dmm.measure_dc_voltage())
```

Or in a sidecar YAML:

```yaml
# tests/test_power.yaml
vectors:
  vin: [4.5, 5.0, 5.5]
  load: [0.1, 0.5, 1.0]
```

Or natively:

```python
@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
@pytest.mark.parametrize("load", [0.1, 0.5, 1.0])
def test_output_voltage(context, psu, dmm, spec): ...
```

## Expansion modes (sidecar / sequence YAML)

### Explicit list

```yaml
vectors:
  - {vin: 5.0, load: 0.1}
  - {vin: 5.0, load: 0.5}
  - {vin: 12.0, load: 1.0}
```

### Product (cartesian)

```yaml
vectors:
  expand: product
  vin: [4.5, 5.0, 5.5]
  load: [0.1, 0.5, 1.0]
```

Generates 9 vectors. **First parameter is outermost** (slowest-changing); last is innermost.

### Zip (parallel)

```yaml
vectors:
  expand: zip
  vin: [4.5, 5.0, 5.5]
  expected: [4.4, 4.9, 5.4]
```

Generates 3 paired vectors.

### Nested (`vectors` sub-block)

```yaml
vectors:
  expand: product
  temperature: [-40, 25, 85]
  vectors:
    expand: zip
    voltage: [3.3, 5.0, 12.0]
    expected: [3.2, 4.9, 11.8]
```

9 vectors: 3 temperatures × 3 zipped pairs. Product-of-product collapses to flat product.

## Range strings (SCPI-style, inclusive)

| Syntax              | Example                                      |
|---------------------|----------------------------------------------|
| `"start:stop"`      | `"1:4"` → `[1, 2, 3, 4]`                     |
| `"start:stop:step"` | `"-40:85:25"` → `[-40, -15, 10, 35, 60, 85]` |
| `"a,b,c"`           | `"3.3,5.0,12.0"` → `[3.3, 5.0, 12.0]`        |
| `"a:b,c,d:e"`       | `"0,0.5:2:0.5,5"` → `[0, 0.5, 1.0, 1.5, 2.0, 5]` |

Ranges are **inclusive** of both endpoints (matches SCPI, Verilog, NI DAQmx). Range strings work anywhere a list is accepted — markers, sidecar, sequence steps.

## `context.changed()` — skip expensive reconfig

Hardware reconfig dominates multi-parameter sweeps. `context.changed(key)` returns `True` only when the parameter differs from the previous parametrize iteration:

```python
@pytest.mark.litmus_vectors(
    temperature=[-40, 25, 85],    # outer, 20-min soak per change
    vin="4.5:5.5:0.5",            # middle, 500-ms PSU settle
    load="0.1:1.0:0.1",           # inner, always set
)
def test_load_regulation(context, psu, eload, chamber, dmm, spec):
    if context.changed("temperature"):
        chamber.set_temperature(context.get_param("temperature"))
        chamber.wait_for_stable(timeout=300)   # 20 min — skipped when temp unchanged
    if context.changed("vin"):
        psu.set_voltage(context.get_param("vin"))
        psu.enable_output()
    eload.set_current(context.get_param("load"))
    eload.enable()
    spec.check("output_voltage", dmm.measure_dc_voltage())
```

With 3 × 3 × 10 = 90 vectors, the chamber changes 3 times, the PSU 9 times, the load 90 times.

### How `changed()` works

- Returns `True` on the first vector (no previous to compare)
- Returns `True` if the value differs from the previous vector
- Returns `False` if the value matches the previous vector
- Prior-context memory is per-method, scoped to the class/module — stored on `request.node.parent.stash`

## Native parametrize is first-class

`@pytest.mark.parametrize` works without wrapping:

```python
@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
def test_rails(context, psu, dmm, spec):
    psu.set_voltage(context.get_param("vin"))
    spec.check("output_voltage", dmm.measure_dc_voltage())
```

`litmus_vectors` compiles to `parametrize` internally and stacks with it — no conflict. When both sidecar and `parametrize` are present, `callspec.params` multiplies normally.

## Choosing the right form

| Scenario                                        | Use                                                           |
|-------------------------------------------------|---------------------------------------------------------------|
| Code-owned, fixed sweep                         | `@pytest.mark.litmus_vectors(...)` or native `parametrize`    |
| Operator-edited sweep (no code deploy)          | Sidecar YAML `test_<module>.yaml` `vectors:` block            |
| Production operator runs with dialogs/retries   | Sequence step `vectors:`                                      |
| Related input/expected pairs                    | `expand: zip`                                                 |
| All combinations of parameters                  | `expand: product`                                             |
| Dense numeric sweep                             | Range string `"4.5:5.5:0.1"`                                  |
| Multi-level with mixed product/zip              | Nested `vectors` sub-block                                    |

## Performance tips

1. **Loop order matters in product mode** — put expensive-to-change parameters (temperature, fixture setup) first so they stay in the outer loop
2. **Use `context.changed()`** for every parameter that's expensive to reconfigure
3. **Range strings expand at load time** — no runtime cost
