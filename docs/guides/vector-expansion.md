# Test Vectors & Sweeps

Most hardware tests vary one or more inputs and measure the result at
each combination. In code, that's a nested loop:

```python
for temp in [-40, 25, 85]:               # outer — slow to change
    for vin in [3.3, 5.0, 5.5]:           # middle
        for load in arange(0.0, 1.0, 0.1):  # inner — fast to change
            measure()
```

`@pytest.mark.litmus_sweeps` declares that nested loop without you
writing it. Each loop becomes one **test vector** — pytest runs your
test once per combination, logs the values, and `context.changed("temp")`
tells you when an outer loop just rolled over so you can do the
expensive setup (chamber soak) only when needed.

Three places to declare vectors, all using the same shape:

- **Inline Python** — `@pytest.mark.litmus_sweeps(...)` on the test function
- **Sidecar YAML** — `config: - litmus_sweeps: [...]` next to the test file
- **Profile YAML** — same shape, applied via the active profile (see [profiles guide](profiles.md))

`litmus_sweeps` is the **recommended** marker for new tests.
Pytest's own `@pytest.mark.parametrize` keeps working unchanged for
existing tests (chapter 1 of the curriculum). Don't *mix* the two on
a single test — pick one.

---

## The basics

### One loop

Sweep one variable across some values. Test runs once per value:

```python
@pytest.mark.litmus_sweeps(vin=[3.3, 5.0, 5.5])
def test_x(vin): ...
# 3 cases
```

```yaml
config:
  - litmus_sweeps:
      - {vin: [3.3, 5.0, 5.5]}
```

### Paired values (one loop, two variables stepping together)

When you have a list of input/expected pairs (think: rows in a spec
table), put both as kwargs in one `litmus_sweeps`. The two lists
**must be the same length**; mismatched lists raise a clear error
right away, before pytest tries to run anything:

```python
@pytest.mark.litmus_sweeps(vin=[3.3, 5.0, 5.5], expected=[3.30, 3.31, 3.30])
def test_x(vin, expected): ...
# 3 cases — vin and expected step together
```

```yaml
config:
  - litmus_sweeps:
      - {vin: [3.3, 5.0, 5.5], expected: [3.30, 3.31, 3.30]}
```

If you write `vin=[3, 4]` and `expected=[5, 6, 7]` (two vs three),
pytest reports the mismatch at decoration time:

```
litmus_sweeps zip requires all argvalues to have the same length;
got {'vin': 2, 'expected': 3}
```

### Nested loops (cross-product)

To sweep two independent variables, **stack** decorators or use
multiple list items in YAML. The order reads top-to-bottom as
**outer-to-inner**:

```python
@pytest.mark.litmus_sweeps(temp=[-40, 25, 85])    # outer — changes 3 times
@pytest.mark.litmus_sweeps(vin=[3.3, 5.0, 5.5])    # inner — changes every test
def test_x(temp, vin): ...
# 3 × 3 = 9 cases
```

```yaml
config:
  - litmus_sweeps:
      - {temp: [-40, 25, 85]}     # outer
      - {vin: [3.3, 5.0, 5.5]}     # inner
```

### Outer simple, inner paired

Combine the patterns: outer loop is a single variable, inner loop
has paired values:

```python
@pytest.mark.litmus_sweeps(temp=[-40, 25, 85])
@pytest.mark.litmus_sweeps(vin=[3, 4], expected=[5, 6])  # paired
def test_x(temp, vin, expected): ...
# 3 outer × 2 paired = 6 cases
```

```yaml
config:
  - litmus_sweeps:
      - {temp: [-40, 25, 85]}
      - {vin: [3, 4], expected: [5, 6]}
```

---

## Loop ordering

The rule for both inline decorators and YAML lists: **top-to-bottom
reads as outer-to-inner**. Same as a nested `for` loop you'd write
by hand. Put the **slow / expensive** parameter at the top so it
changes least often:

```python
@pytest.mark.litmus_sweeps(temp=[-40, 25, 85])             # outer — 20-min soak per change, runs 3 times
@pytest.mark.litmus_sweeps(vin=[4.5, 5.0, 5.5])            # middle — 500-ms PSU settle, runs 9 times
@pytest.mark.litmus_sweeps(load=arange(0.0, 1.0, 0.2))     # inner — instant, runs 45 times
```

> **Note for pytest users:** this is **opposite** to
> `@pytest.mark.parametrize`'s stacking convention, which puts the
> bottom decorator at the outer loop. The pytest convention is a
> well-known footgun; `litmus_sweeps` flips it so your code reads
> the way you'd write the equivalent nested `for` loop. (One of the
> reasons `litmus_sweeps` is its own marker rather than a rename.)

### Skip expensive setup with `context.changed()`

`context.changed("temp")` returns `True` only when that parameter
differs from the previous test case. Pair this with the outer-to-inner
ordering to set up expensive things only when they actually change:

```python
@pytest.mark.litmus_sweeps(temp=[-40, 25, 85])
@pytest.mark.litmus_sweeps(vin=[4.5, 5.0, 5.5])
@pytest.mark.litmus_sweeps(load=arange(0.0, 1.0, 0.2))
def test_load_regulation(temp, vin, load, context, psu, eload, chamber, dmm, spec):
    if context.changed("temp"):                       # 3 times in 45 cases
        chamber.set_temperature(temp)
        chamber.wait_for_stable(timeout=300)
    if context.changed("vin"):                        # 9 times
        psu.set_voltage(vin)
        psu.enable_output()
    eload.set_current(load)                           # every case
    eload.enable()
    spec.check("output_voltage", dmm.measure_dc_voltage())
```

Chamber sets 3 times. PSU sets 9 times. Load sets 45 times. The
20-minute soak only runs when temperature actually rolls over.

---

## Generating values — `linspace`, `arange`, etc.

Numeric sweeps usually want evenly-spaced or log-spaced points.
Litmus provides Python helpers for inline use (with IDE autocomplete
and type checking) and equivalent dict-form generators for YAML:

| Inline helper | YAML form | What it does |
|---|---|---|
| `linspace(start, stop, num)` | `{linspace: [start, stop, num]}` | N evenly-spaced points, exact endpoints |
| `arange(start, stop, step)` | `{arange: [start, stop, step]}` | Floating step; stop excluded |
| `logspace(start, stop, num)` | `{logspace: [start, stop, num]}` | Log-spaced (base 10) |
| `geomspace(start, stop, num)` | `{geomspace: [start, stop, num]}` | Geometrically-spaced |
| `repeat(value, n)` | `{repeat: [value, n]}` | N copies of `value` |
| `list(range(start, stop))` | `{range: [start, stop]}` | Integer range |

```python
from litmus import linspace, arange, logspace, repeat

@pytest.mark.litmus_sweeps(vin=linspace(3.3, 5.5, 11))     # 11 evenly-spaced
@pytest.mark.litmus_sweeps(freq=logspace(1, 6, 6))          # 10 Hz to 1 MHz
@pytest.mark.litmus_sweeps(load=arange(0.0, 1.0, 0.1))      # 0.0..0.9 step 0.1
@pytest.mark.litmus_sweeps(soak=repeat(5.0, 100))           # 100 copies of 5.0
@pytest.mark.litmus_sweeps(channel=list(range(1, 17)))      # channels 1..16
```

```yaml
config:
  - litmus_sweeps:
      - {vin: {linspace: [3.3, 5.5, 11]}}
  - litmus_sweeps:
      - {freq: {logspace: [1, 6, 6]}}
```

The dict-form generators work anywhere a list is expected — station
channel arrays, fixture pin arrays, product spec conditions — not
just inside `litmus_sweeps`.

### Generated paired values

When two paired variables both come from generators, just put each
as its own key. The list-length check catches mistakes:

```python
@pytest.mark.litmus_sweeps(
    vin=linspace(3.3, 5.5, 5),           # 5 points
    expected=linspace(3.30, 3.32, 5),    # 5 points — pairs cleanly
)
```

```yaml
config:
  - litmus_sweeps:
      - vin:      {linspace: [3.3, 5.5, 5]}
        expected: {linspace: [3.30, 3.32, 5]}
```

If the two generators produce different counts, you get a clear
error pointing at the mismatch.

---

## Inline ↔ YAML cheat sheet

| Inline Python | YAML |
|---|---|
| `litmus_sweeps(vin=[3, 4])` | `litmus_sweeps:`<br>`  - {vin: [3, 4]}` |
| `litmus_sweeps(vin=[3, 4], expected=[5, 6])` | `litmus_sweeps:`<br>`  - {vin: [3, 4], expected: [5, 6]}` |
| Two stacked decorators | `litmus_sweeps:`<br>`  - {outer: [...]}`<br>`  - {inner: [...]}` |
| `litmus_sweeps(vin=linspace(3, 5, 5))` | `litmus_sweeps:`<br>`  - {vin: {linspace: [3, 5, 5]}}` |

---

## Self-loop mode — `vectors` fixture

Sometimes you want the test body to own the loop — to amortize
expensive setup, stream samples, or skip rows conditionally. Request
the `vectors` fixture; Litmus pre-builds the full table and your
test iterates it as one pytest case:

```python
@pytest.mark.litmus_sweeps(vin=linspace(3.3, 5.5, 5))
def test_sweep(vectors, psu, dmm, verify):
    psu.enable_output()
    for v in vectors:
        psu.set_voltage(v["vin"])
        verify("output_voltage", dmm.measure_dc_voltage())
```

`context.changed`, `verify`, and the run record all behave the same
as in normal parametrized mode.

---

## Choosing where to declare your vectors

| Scenario | Use |
|---|---|
| Code-owned sweep, IDE-friendly | Inline `@pytest.mark.litmus_sweeps(...)` with `linspace` etc. |
| Operator-edited sweep (no code deploy) | Sidecar `config: - litmus_sweeps: ...` |
| Different sweeps per scenario | Profile YAML (selected by CLI facet) |
| Test owns the loop (amortize setup) | `vectors` fixture in the signature |
| Input / expected pairs from a spec table | Paired values (multi-kwarg or multi-key) |
| All combinations of N parameters | Stacked decorators / multiple list items |
| Dense numeric range | `linspace`/`arange`/etc. |

---

## Performance tips

1. **Top = outer = slowest.** Put expensive-to-change parameters at
   the top decorator (or first list item). They'll change least often
   in the nested loop. Pair with `context.changed("name")` to skip
   redundant setup.
2. **Generators run at collection time** — `linspace(3.3, 5.5, 1000)`
   doesn't allocate until pytest collects, and there's no per-iteration
   cost. If you're generating thousands of points, the overhead is in
   pytest's collection, not the loop.
3. **Use `vectors` fixture** when per-test pytest setup/teardown is
   the bottleneck — one pytest case with N internal iterations is
   often dramatically faster than N pytest cases.
