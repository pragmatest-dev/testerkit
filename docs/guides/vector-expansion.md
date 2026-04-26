# Test Vectors & Range Expansion

Test vectors — the parametric sweeps that drive a test through N
combinations of conditions — are **runner-neutral** in Litmus.
`litmus_vectors` is the marker; the same concept reaches you through
three surfaces:

- **Inline Python** — `@pytest.mark.litmus_vectors(...)`. IDE
  autocomplete, signature help, normal Python.
- **Sidecar YAML** — `config: - litmus_vectors: [...]` in
  `test_<module>.yaml` next to the test file.
- **Profile YAML** — same shape as sidecar, applies via the active
  profile (see the [profiles guide](profiles.md)).

`litmus_vectors` is the **recommended** marker for new tests.
Pytest's own `@pytest.mark.parametrize` continues to work unchanged
for projects already using it (chapter 1 of the curriculum is
exactly this story). Don't *mix* the two stacked on a single test —
plugin-order semantics make the iteration order non-obvious in mixed
cases. Pick one.

`litmus_vectors` deliberately diverges from `parametrize` in four
ways — each one is a fix for a parametrize footgun and is the
reason the marker exists rather than being just a rename:

| Divergence | parametrize | litmus_vectors |
|---|---|---|
| Loop ordering | bottom decorator = outer | **top decorator = outer** |
| Multi-arg syntax | comma-string + tuples | **multi-kwarg = zip (auto)** |
| Dim coherence | silent shape error at runtime | **error at decoration time** |
| Inline shape | positional only | **kwargs only inline** |
| YAML shape | (no YAML form) | **always a list of axis-group dicts** |

---

## The shape

Every `litmus_vectors` declares one or more **axis-groups**. An axis-group
is a dict mapping argname(s) → values:

- **Single key** = one plain axis: `{vin: [3.3, 5.0, 5.5]}`
- **Multi-key** = one **zipped** axis (paired). All values lists must be
  the same length: `{vin: [3, 4], vout: [5, 6]}`. Dimensional coherence
  is checked at decoration / YAML-load time.

Multiple axis-groups stack as cross-product. Top-to-bottom reads as
outer-to-inner.

### Inline (kwargs only)

```python
import pytest
from litmus import linspace, arange, logspace, repeat

# Single axis
@pytest.mark.litmus_vectors(vin=[3.3, 5.0, 5.5])
def test_x(vin): ...

# Zip (paired axis) — multi-kwarg in one decorator, dims checked
@pytest.mark.litmus_vectors(vin=[3, 4], vout=[5, 6])
def test_x(vin, vout): ...

# Cross-product — stacked decorators (top = outer, bottom = inner)
@pytest.mark.litmus_vectors(temp=[-40, 25, 85])    # outer (slowest)
@pytest.mark.litmus_vectors(vin=[3.3, 5.0, 5.5])    # inner (fastest)
def test_x(temp, vin): ...

# Outer simple, inner zipped — combine the two
@pytest.mark.litmus_vectors(temp=[-40, 25, 85])
@pytest.mark.litmus_vectors(vin=[3, 4], vout=[5, 6])  # paired pair
def test_x(temp, vin, vout): ...
```

### YAML (always a list of axis-group dicts)

YAML uses list form everywhere — even single-axis. No dict-vs-list
polymorphism; Pydantic validates the shape end-to-end.

```yaml
config:
  # Single axis
  - litmus_vectors:
      - {vin: [3.3, 5.0, 5.5]}

  # Zipped axis — multi-key in one entry
  - litmus_vectors:
      - {vin: [3, 4], vout: [5, 6]}

  # Cross-product — multiple list items (top = outer)
  - litmus_vectors:
      - {temp: [-40, 25, 85]}     # outer
      - {vin: [3.3, 5.0, 5.5]}     # inner

  # Outer simple, inner zipped
  - litmus_vectors:
      - {temp: [-40, 25, 85]}     # outer
      - {vin: [3, 4], vout: [5, 6]}  # inner zipped pair
```

Stacking can also happen at the `config:` level — separate `litmus_vectors`
entries cross-product with each other. Use whichever reads cleaner; a
single `litmus_vectors` block keeps related axes together.

### Numeric sweeps — `linspace` / `arange` / `logspace` / `geomspace` / `repeat`

Inline list-builders return a normal `list[float]` — IDE shows the
signature, mypy/pyright check the types. Each is the Python counterpart
to a YAML range-expander dict, behaviorally identical:

```python
@pytest.mark.litmus_vectors(vin=linspace(3.3, 5.5, 11))
@pytest.mark.litmus_vectors(freq=logspace(1, 6, 6))
@pytest.mark.litmus_vectors(load=arange(0.0, 1.0, 0.1))
@pytest.mark.litmus_vectors(soak=repeat(5.0, 100))
@pytest.mark.litmus_vectors(channel=list(range(1, 17)))
```

In YAML, range expanders are dict-form generators that resolve at
YAML load (before Pydantic validation). They drop in anywhere a list
is expected:

```yaml
config:
  - litmus_vectors:
      - vin: {linspace: [3.3, 5.5, 11]}
  - litmus_vectors:
      - freq: {logspace: [1, 6, 6]}
```

For zipped axes generated from helpers — just put each axis as its
own kwarg/key. Multi-kwarg auto-zips with dim check; if the
expanders produce different lengths, you get a clear error:

```yaml
- litmus_vectors:
    - vin:  {linspace: [3.3, 5.5, 5]}     # → 5 floats
      vout: {linspace: [3.30, 3.32, 5]}   # → 5 floats; zips cleanly
```

Inline equivalent:

```python
@pytest.mark.litmus_vectors(
    vin=linspace(3.3, 5.5, 5),
    vout=linspace(3.30, 3.32, 5),
)
```

---

## YAML range expanders

| Key             | Semantics                                            | Backs to            |
|-----------------|------------------------------------------------------|---------------------|
| `linspace`      | `[start, stop, num]` — fixed count, exact endpoints  | `numpy.linspace`    |
| `arange`        | `[start, stop, step]` — floating step; stop exclusive| `numpy.arange`      |
| `logspace`      | `[start, stop, num]` — log-spaced (base 10)          | `numpy.logspace`    |
| `geomspace`     | `[start, stop, num]` — geometric                     | `numpy.geomspace`   |
| `repeat`        | `[value, n]` — N copies of value                     | `[value] * n`       |
| `range`         | `[start, stop]` or `[start, stop, step]` — integers  | built-in `range`    |

Range expanders work anywhere a list is accepted — station channel
arrays, fixture pin arrays, product-spec `when:` bands, etc. Not
just inside `litmus_vectors`.

---

## Inline ↔ YAML equivalences

| Inline Python | YAML |
|---|---|
| `litmus_vectors(vin=[3, 4])` | `litmus_vectors:`<br>`  - {vin: [3, 4]}` |
| `litmus_vectors(vin=[3, 4], vout=[5, 6])` | `litmus_vectors:`<br>`  - {vin: [3, 4], vout: [5, 6]}` |
| Stacked decorators | `litmus_vectors:`<br>`  - {axis1}`<br>`  - {axis2}` |
| `litmus_vectors(vin=linspace(3, 5, 5))` | `litmus_vectors:`<br>`  - {vin: {linspace: [3, 5, 5]}}` |

---

## `context.changed()` — skip expensive reconfig

Hardware reconfig dominates multi-parameter sweeps. `context.changed(key)`
returns `True` only when the parameter differs from the previous row.
Pair this with the top-to-bottom outer-to-inner ordering for natural
"reconfigure outer when it rolls over" patterns:

```python
@pytest.mark.litmus_vectors(temp=[-40, 25, 85])    # outer (20-min soak per change)
@pytest.mark.litmus_vectors(vin=[4.5, 5.0, 5.5])    # middle (500-ms PSU settle)
@pytest.mark.litmus_vectors(load=arange(0.0, 1.0, 0.2))  # inner (always set)
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

---

## Self-loop mode — `vectors` fixture

For tests that want to own the iteration (amortize expensive setup,
stream samples, conditional skip of interior rows), request the
`vectors` fixture. Litmus consolidates every source into one matrix
and collapses pytest expansion so the test runs as a single case:

```python
@pytest.mark.litmus_vectors(vin=linspace(3.3, 5.5, 5))
def test_sweep(vectors, psu, dmm, verify):
    psu.enable_output()
    for v in vectors:
        psu.set_voltage(v["vin"])
        verify("output_voltage", dmm.measure_dc_voltage())
```

---

## Choosing the right form

| Scenario | Use |
|---|---|
| Code-owned sweep, IDE-friendly | Inline `@pytest.mark.litmus_vectors(vin=linspace(...))` |
| Operator-edited sweep (no code deploy) | Sidecar `config: - litmus_vectors: - {...}` |
| Scenario-conditional sweep | Profile YAML (selected by CLI facet) |
| Test iterates itself | `vectors` fixture in the test signature |
| Related input/expected pairs | Multi-kwarg in one decorator/entry (auto-zip with dim check) |
| All combinations | Multiple kwargs/entries → cross-product |
| Dense numeric sweep | `linspace(...)` inline / `{linspace: [...]}` in YAML |

---

## Performance tips

1. **Loop order reads top-to-bottom as outer-to-inner.** Both forms
   (stacked decorators, list of axis-groups in one block) follow the
   same rule. Put **expensive-to-change parameters at the top** so
   they change rarely.

   .. note::

      `litmus_vectors` **inverts pytest's** `@pytest.mark.parametrize`
      stacking convention. Pytest registers the bottom (closest-to-
      function) parametrize call first, making *bottom* the outer
      loop — counterintuitive and a regular footgun. Litmus reverses
      the iteration so top-to-bottom reads outer-to-inner. This is
      one of the reasons `litmus_vectors` exists as a separate
      marker rather than just being an alias. Don't mix stacked
      `@pytest.mark.parametrize` and `@pytest.mark.litmus_vectors`
      on the same test — pick one.

2. **Use `context.changed()`** for every parameter that's expensive
   to reconfigure.
3. **Range expanders and `linspace`/`arange`/etc. run at collection
   time** — no per-iteration cost.
