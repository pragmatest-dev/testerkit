# Test Vectors & Range Expansion

Test vectors — the parametric sweeps that drive a test through N
combinations of conditions — are **runner-neutral** in Litmus. The
same concept reaches you through three surfaces:

- **Inline Python** — `@pytest.mark.litmus_vectors(...)`. IDE
  autocomplete, signature help, normal Python.
- **Sidecar YAML** — `config: - litmus_vectors: {...}` in
  `test_<module>.yaml` next to the test file.
- **Profile YAML** — same shape as sidecar, applies via the active
  profile (see the [profiles guide](profiles.md)).

All three produce identical parametrized test items. `context.get_param("name")`
reads the active row regardless of source.

Compatibility: native `@pytest.mark.parametrize` continues to work
unchanged — vanilla pytest projects keep running with no changes.
`litmus_vectors` is the runner-neutral name; `parametrize` is the
pytest-native name; both translate to the same `metafunc.parametrize`
call.

---

## Inline Python forms

Import `pytest` for the marker access; import the inline list-builders
from `litmus` when you want IDE-friendly numeric sweeps.

```python
import pytest
from litmus import linspace, arange, logspace, repeat, paired
```

### One axis (single argname)

```python
@pytest.mark.litmus_vectors(vin=[3.3, 5.0, 5.5])
def test_x(vin): ...
# 3 cases
```

### Cross-product (multiple independent axes)

Each kwarg = one loop axis. Multiple kwargs cross-product, same
mechanics as stacking parametrize decorators:

```python
@pytest.mark.litmus_vectors(vin=[3.3, 5.0], load=[0.1, 0.5, 0.9])
def test_x(vin, load): ...
# 2 × 3 = 6 cases
```

### Zip / paired axis

When values must advance together (input/expected pairs, corner-case
matrices), use the `paired` decorator:

```python
@paired(vin=[3.3, 5.0, 5.5], expected=[3.30, 3.30, 3.30])
def test_x(vin, expected): ...
# 3 paired cases — argvalue lists must have the same length
```

`paired(...)` validates dimensions match before pytest collection
(unlike the cross-product form, which doesn't care about lengths).
It returns a regular `litmus_vectors` marker under the hood; stacking
with another decorator cross-products independently:

```python
@pytest.mark.litmus_vectors(temp=[25, 85])
@paired(vin=[3.3, 5.5], expected=[3.30, 3.30])
def test_x(vin, expected, temp): ...
# 2 (paired) × 2 (temp) = 4 cases
```

### Numeric sweeps — `linspace` / `arange` / `logspace` / `geomspace` / `repeat`

Inline list-builders return a normal Python `list[float]` — IDE shows
the signature, mypy/pyright check the types. Each one is the Python
counterpart to a YAML range-expander dict, behaviorally identical:

```python
@pytest.mark.litmus_vectors(vin=linspace(3.3, 5.5, 11))     # 11 evenly-spaced
def test_x(vin): ...

@pytest.mark.litmus_vectors(freq=logspace(1, 6, 6))          # 10 Hz to 1 MHz
def test_x(freq): ...

@pytest.mark.litmus_vectors(load=arange(0.0, 1.0, 0.1))      # 0.0..0.9 step 0.1
def test_x(load): ...

@pytest.mark.litmus_vectors(soak=repeat(5.0, 100))           # 100 copies of 5.0
def test_x(soak): ...
```

For integer ranges, just use Python's built-in `range`:

```python
@pytest.mark.litmus_vectors(channel=list(range(1, 17)))      # channels 1..16
def test_x(channel): ...
```

### Stacking decorators

Stack any combination — each decorator adds an axis, all axes
cross-product:

```python
@pytest.mark.litmus_vectors(temp=[25, 85])
@pytest.mark.litmus_vectors(vin=linspace(3.3, 5.5, 5))
@paired(load_pct=[10, 50, 90], expected_eff=[0.91, 0.94, 0.92])
def test_x(temp, vin, load_pct, expected_eff): ...
# 2 × 5 × 3 = 30 cases
```

Pytest applies stacked decorators bottom-up: closest-to-function = inner
loop (varies fastest); furthest-from-function = outer loop (varies slowest).
Pair this with `context.changed("name")` to amortize expensive setup
on outer-loop changes (see below).

---

## YAML forms

YAML can't call functions, so the YAML surface uses literal lists and
dict-form expanders. Same semantics as the Python forms — just
expressed as data instead of expressions.

### One axis

```yaml
config:
  - litmus_vectors:
      vin: [3.3, 5.0, 5.5]
```

### Cross-product

Each top-level key in the `litmus_vectors` payload is one axis:

```yaml
config:
  - litmus_vectors:
      vin: [3.3, 5.0]
      load: [0.1, 0.5, 0.9]
# 2 × 3 = 6 cases
```

Equivalent to stacking two entries:

```yaml
config:
  - litmus_vectors: {vin: [3.3, 5.0]}        # outer
  - litmus_vectors: {load: [0.1, 0.5, 0.9]}  # inner (varies faster)
# Same 6 cases; explicit ordering
```

### Zip / paired axis (comma-joined argname key)

YAML keys can be any string, so the comma-joined argname form is
clean (no `**{...}` unpacking like inline Python):

```yaml
config:
  - litmus_vectors:
      "vin,expected":
        - [3.3, 3.30]
        - [5.0, 3.30]
        - [5.5, 3.30]
```

The quotes around `"vin,expected"` are optional but help readers see
the comma is data (one argname-string), not YAML structure. Block
style works too:

```yaml
config:
  - litmus_vectors:
      "vin,expected": [[3.3, 3.30], [5.0, 3.30], [5.5, 3.30]]   # flow style
```

### Range expanders

Any list position in any Litmus YAML file accepts a single-key dict
whose key names a generator. Expansion happens at YAML load, before
Pydantic validation — schema models and pytest only see plain lists.

| Key             | Semantics                                            | Backs to            |
|-----------------|------------------------------------------------------|---------------------|
| `linspace`      | `[start, stop, num]` — fixed count, exact endpoints  | `numpy.linspace`    |
| `arange`        | `[start, stop, step]` — floating step; stop exclusive| `numpy.arange`      |
| `logspace`      | `[start, stop, num]` — log-spaced (base 10)          | `numpy.logspace`    |
| `geomspace`     | `[start, stop, num]` — geometric                     | `numpy.geomspace`   |
| `repeat`        | `[value, n]` — N copies of value                     | `[value] * n`       |
| `range`         | `[start, stop]` or `[start, stop, step]` — integers  | built-in `range`    |

```yaml
config:
  - litmus_vectors:
      vin: {linspace: [3.3, 5.5, 11]}    # 11 evenly-spaced points
      freq: {logspace: [1, 6, 6]}        # 10 Hz..1 MHz, 6 points
      soak: {repeat: [5.0, 100]}         # 100 copies of 5.0
      channel: {range: [1, 17]}          # 1..16
```

Range expanders work anywhere a list is accepted — station channel
arrays, fixture pin arrays, product-spec `when:` bands with list
matchers, etc. Not just inside `litmus_vectors`.

---

## Inline ↔ YAML equivalences

| Inline Python | YAML |
|---|---|
| `litmus_vectors(vin=[3, 4])` | `litmus_vectors: {vin: [3, 4]}` |
| `litmus_vectors(vin=[3, 4], load=[0.1, 0.5])` | `litmus_vectors: {vin: [3, 4], load: [0.1, 0.5]}` |
| `paired(vin=[3, 4], vout=[5, 6])` | `litmus_vectors: {"vin,vout": [[3, 5], [4, 6]]}` |
| `litmus_vectors(vin=linspace(3, 5, 5))` | `litmus_vectors: {vin: {linspace: [3, 5, 5]}}` |
| `litmus_vectors(soak=repeat(5.0, 100))` | `litmus_vectors: {soak: {repeat: [5.0, 100]}}` |

Inline and YAML can also coexist on the same test — stack them by
putting one in the decorator and the other in the sidecar:

```python
# tests/test_x.py
@pytest.mark.litmus_vectors(temp=[25, 85])
def test_rail(vin, temp): ...
```

```yaml
# tests/test_x.yaml
tests:
  test_rail:
    config:
      - litmus_vectors: {vin: [3.3, 5.0, 5.5]}
# 2 (inline temp) × 3 (sidecar vin) = 6 cases
```

---

## `context.changed()` — skip expensive reconfig

Hardware reconfig dominates multi-parameter sweeps. `context.changed(key)`
returns `True` only when the parameter differs from the previous row:

```python
@pytest.mark.litmus_vectors(load=arange(0.0, 1.0, 0.2))   # inner, always set
@pytest.mark.litmus_vectors(vin=[4.5, 5.0, 5.5])           # middle, 500 ms PSU settle
@pytest.mark.litmus_vectors(temp=[-40, 25, 85])            # outer, 20 min soak per change
def test_load_regulation(temp, vin, load, context, psu, eload, chamber, dmm, spec):
    if context.changed("temp"):
        chamber.set_temperature(temp)
        chamber.wait_for_stable(timeout=300)
    if context.changed("vin"):
        psu.set_voltage(vin)
        psu.enable_output()
    eload.set_current(load)
    eload.enable()
    spec.check("output_voltage", dmm.measure_dc_voltage())
```

3 × 3 × 5 = 45 cases. Chamber sets 3 times, PSU 9 times, load 45 times.

How it works:
- Returns `True` on the first row of the sweep (no previous to compare)
- Returns `True` when the value differs from the previous row
- Returns `False` when the value matches the previous row
- Prior-row memory is per-method, scoped to the class/module

---

## Self-loop mode — `vectors` fixture

For tests that want to own the iteration (amortize expensive setup,
stream samples, conditional skip of interior rows), request the
`vectors` fixture. Litmus consolidates every source (inline + sidecar +
profile) into one matrix and collapses pytest expansion so the test
runs as a single case:

```python
@pytest.mark.litmus_vectors(vin=linspace(3.3, 5.5, 5))
def test_sweep(vectors, psu, dmm, verify):
    psu.enable_output()
    for v in vectors:
        psu.set_voltage(v["vin"])
        verify("output_voltage", dmm.measure_dc_voltage())
```

Each iteration pushes the active row's params so `context.changed`,
`verify`, and row stamping behave identically to parametrize mode.

---

## Choosing the right form

| Scenario | Use |
|---|---|
| Code-owned sweep, IDE-friendly | Inline `@pytest.mark.litmus_vectors(...)` with `linspace`/`paired` |
| Operator-edited sweep (no code deploy) | Sidecar `config: - litmus_vectors: {...}` |
| Scenario-conditional sweep | Profile YAML `config:` (selected by CLI facet) |
| Test iterates itself (setup amortization) | `vectors` fixture in the test signature |
| Related input/expected pairs (Python) | `@paired(in=[...], expected=[...])` |
| Related input/expected pairs (YAML) | `litmus_vectors: {"in,expected": [[...], ...]}` |
| All combinations | Multiple kwargs in one `litmus_vectors` (or stacked decorators) |
| Dense numeric sweep | `linspace(...)` inline / `{linspace: [...]}` in YAML |

---

## Performance tips

1. **Loop order matters.** Pytest applies stacked decorators bottom-up:
   the one closest to the function is the inner / fastest-changing
   loop. In one-decorator-multi-kwarg form, kwargs iterate in dict
   insertion order — first key = outer, last key = inner. Put
   expensive-to-change parameters on the outer loop so they stay slow.
2. **Use `context.changed()`** for every parameter that's expensive to
   reconfigure.
3. **Range expanders and `linspace` / `arange` etc. run at collection
   time** — no per-iteration cost.
