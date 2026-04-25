# Parametrize & Range Expansion

Litmus reuses `pytest.mark.parametrize` for every sweep — inline
decorators, sidecar YAML, and profile YAML all speak the same language.
A sweep can come from:

- **Native `@pytest.mark.parametrize`** — first-class, code-owned
- **Sidecar YAML** `markers:` list with `- parametrize: [...]` entries
- **Profile YAML** `markers:` list — applies to tests matching the
  profile's facet query (see the [profiles guide](profiles.md))
- **Sequence step `vectors:`** — operator-facing production runs (see
  the [sequence YAML reference](../reference/sequence-yaml.md))

All paths produce identical parametrized test items. `context.get_param(...)`
reads the active row regardless of source.

## The basics

```python
import pytest

@pytest.mark.parametrize("load", [0.1, 0.5, 1.0])
@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
def test_output_voltage(vin, load, context, psu, dmm, spec):
    psu.set_voltage(vin)
    psu.enable_output()
    spec.check("output_voltage", dmm.measure_dc_voltage())
```

The same sweep in YAML as sidecar markers:

```yaml
# tests/test_power.yaml
tests:
  test_output_voltage:
    markers:
      - parametrize: ["vin", [4.5, 5.0, 5.5]]
      - parametrize: ["load", [0.1, 0.5, 1.0]]
```

Two parametrize entries with distinct argnames **cross-product** — same
rule as stacked `@pytest.mark.parametrize` decorators. Pytest does not
allow stacking two parametrize calls on the same argname; Litmus raises
a collection-time `UsageError` if a sidecar tries to.

## Multi-argname parametrize (zipped pairs)

For related input/expected pairs, use a single marker with a comma-joined
argname string and a list-of-tuples:

```yaml
tests:
  test_converted:
    markers:
      - parametrize:
          - "vin,expected"
          - [[4.5, 4.4], [5.0, 4.9], [5.5, 5.4]]
```

Equivalent to `@pytest.mark.parametrize("vin,expected", [(4.5, 4.4), (5.0, 4.9), (5.5, 5.4)])`.

## Dict form — per-case `id` / `marks` / kwargs

When you need pytest's `ids=` / `scope=` or per-case `pytest.param(...)`
features, use the dict form:

```yaml
- parametrize:
    argnames: "vin"
    argvalues:
      - 5.0
      - {value: 5.5, id: "high"}
      - {value: 6.0, marks: [skip]}
    ids: ["nominal", "high", "over"]   # optional
    scope: session                      # optional
```

Scalar entries become `pytest.param(value)`; dict entries become
`pytest.param(value, id=..., marks=...)`.

## Range expanders

Any list position in any Litmus YAML file accepts a single-key dict
whose key names a generator. Expansion happens at YAML load, before
Pydantic validation — schema models and pytest only see plain lists.

| Key             | Semantics                                    | Backs to            |
|-----------------|----------------------------------------------|---------------------|
| `linspace`      | `[start, stop, num]` — fixed count, exact endpoints | `numpy.linspace`    |
| `arange`        | `[start, stop, step]` — floating step; stop exclusive | `numpy.arange`      |
| `logspace`      | `[start, stop, num]` — log-spaced (base 10)  | `numpy.logspace`    |
| `geomspace`     | `[start, stop, num]` — geometric             | `numpy.geomspace`   |
| `repeat`        | `[value, n]` — N copies of value             | `[value] * n`       |
| `range`         | `[start, stop]` or `[start, stop, step]` — integers | built-in `range`    |

```yaml
- parametrize: ["vin", {linspace: [4.5, 5.5, 11]}]
- parametrize: ["freq", {logspace: [1, 6, 6]}]
- parametrize: ["soak", {repeat: [5.0, 100]}]
```

Expanders work anywhere a list is accepted — station channel arrays,
fixture pin arrays, product-spec `when:` bands with list matchers, etc.

## `context.changed()` — skip expensive reconfig

Hardware reconfig dominates multi-parameter sweeps. `context.changed(key)`
returns `True` only when the parameter differs from the previous
parametrize iteration:

```python
@pytest.mark.parametrize("load", [0.1, 0.25, 0.5, 0.75, 1.0])    # inner, always set
@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])                 # middle, 500-ms PSU settle
@pytest.mark.parametrize("temperature", [-40, 25, 85])           # outer, 20-min soak per change
def test_load_regulation(temperature, vin, load, context, psu, eload, chamber, dmm, spec):
    if context.changed("temperature"):
        chamber.set_temperature(temperature)
        chamber.wait_for_stable(timeout=300)   # 20 min — skipped when temp unchanged
    if context.changed("vin"):
        psu.set_voltage(vin)
        psu.enable_output()
    eload.set_current(load)
    eload.enable()
    spec.check("output_voltage", dmm.measure_dc_voltage())
```

With 3 × 3 × 5 = 45 vectors, the chamber changes 3 times, the PSU 9
times, the load 45 times.

### How `changed()` works

- Returns `True` on the first vector (no previous to compare)
- Returns `True` if the value differs from the previous vector
- Returns `False` if the value matches the previous vector
- Prior-context memory is per-method, scoped to the class/module —
  stored on `request.node.parent.stash`

## Self-loop mode — `vectors` fixture

For tests that want to own the iteration (amortize expensive setup,
stream samples, conditional skip of interior rows), request the
`vectors` fixture. Litmus consolidates **every** source (native
parametrize, sidecar `parametrize:` markers, profile markers) into one
matrix and collapses pytest expansion so the test runs as a single case:

```python
@pytest.mark.parametrize("vin", [4.5, 5.0, 5.5])
def test_sweep(vectors, psu, dmm, verify):
    psu.enable_output()
    for v in vectors:
        psu.set_voltage(v["vin"])
        verify("output_voltage", dmm.measure_dc_voltage())
```

Each iteration pushes the active row's params so `context.changed`,
`verify`, and row stamping behave identically to parametrize mode.

## Choosing the right form

| Scenario                                        | Use                                                              |
|-------------------------------------------------|------------------------------------------------------------------|
| Code-owned, fixed sweep                         | Native `@pytest.mark.parametrize(...)`                           |
| Operator-edited sweep (no code deploy)          | Sidecar `markers: - parametrize: [...]`                          |
| Scenario-conditional sweep                      | Profile YAML `markers:` (selected by CLI facet)                  |
| Production operator runs with dialogs           | Sequence step `vectors:`                                         |
| Test iterates itself (setup amortization)       | `vectors` fixture in the test signature                          |
| Related input/expected pairs                    | Multi-argname parametrize (`"vin,expected"`)                     |
| All combinations of parameters                  | Two stacked `parametrize:` entries with distinct argnames        |
| Dense numeric sweep                             | Range expander (`{linspace: [...]}`, `{arange: [...]}`)          |

## Performance tips

1. **Loop order matters** — pytest applies stacked `parametrize`
   decorators **bottom-up**: the one closest to the function is the
   innermost/fastest-changing loop. Put expensive-to-change parameters
   (temperature, fixture setup) on the **outermost** decorator so they
   stay in the slowest loop.
2. **Use `context.changed()`** for every parameter that's expensive to reconfigure
3. **Range expanders run at YAML load** — no runtime cost
