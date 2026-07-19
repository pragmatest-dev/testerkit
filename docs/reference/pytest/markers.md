# TesterKit markers

The TesterKit pytest plugin registers **seven markers**. Each maps 1:1 to a field on `TestEntry` (the recursive node type in the sidecar YAML), so anything you can write inline as a marker, you can write in the sidecar — and vice versa.

Pytest's own markers (`@pytest.mark.parametrize`, `@pytest.mark.skip`, `@pytest.mark.flaky` from `pytest-rerunfailures`, etc.) work unchanged. TesterKit's markers slot in alongside them.

## No-stacking rule

At most one inline `@pytest.mark.testerkit_X` decorator of each kind per test. Multi-entry payloads (a list of dicts for sweeps/mocks, multiple kwargs for limits/prompts) consolidate onto one marker. `@pytest.mark.parametrize` is the explicit exception — pytest's native stacking convention stays. Stacking a TesterKit marker raises `StackedMarkersError` at collection.

```python
# OK
@pytest.mark.testerkit_sweeps([{"temperature": [-40, 25, 85], "load": [0.1, 0.5]}])
def test_x(...): ...

# Not OK — raises StackedMarkersError
@pytest.mark.testerkit_sweeps([{"temperature": [-40, 25, 85]}])
@pytest.mark.testerkit_sweeps([{"load": [0.1, 0.5]}])
def test_x(...): ...
```

---

## `testerkit_limits`

Pin a `Limit` per measurement name. Both `verify(name, value)` and `measure(name, value)` record the measurement row and resolve the limit against this marker (or the sidecar's `limits:` block, or the active part spec, in resolution order); the only difference is `verify` raises `AssertionError` on FAIL where `measure` doesn't.

**Signature:** `@pytest.mark.testerkit_limits(**by_name)` — one keyword per measurement name; each value is a dict matching `MeasurementLimitConfig`.

```python
@pytest.mark.testerkit_limits(
    output_voltage={"low": 3.135, "high": 3.465, "unit": "V"},
    output_current={"high": 0.5, "unit": "A"},
)
def test_power_rail(dmm, psu, verify):
    verify("output_voltage", dmm.measure_dc_voltage())
    verify("output_current", psu.measure_current())
```

**Sidecar equivalent:**

```yaml
limits:
  output_voltage: {low: 3.135, high: 3.465, unit: V}
  output_current: {high: 0.5, unit: A}
```

**Full shape:** `low`, `high`, `nominal`, `unit`, `comparator`, plus the alternative `characteristic:` / `tolerance_pct:` / `tolerance_abs:` derivation, and the `bands:` list for condition-indexed limits. See [`MeasurementLimitConfig`](../data/models.md) for the full schema and [Test limits](../../how-to/execution/limits.md#condition-indexed-bands) for the band semantics.

---

## `testerkit_sweeps`

TesterKit-native parametrize. Each entry in the list is one **axis-group dict** — single-key dicts run as one independent loop; multi-key dicts inside one entry zip together; stacked entries cross-product (top entry = outermost / slowest loop).

**Signature (inline):** `@pytest.mark.testerkit_sweeps([entries])` — one positional list of axis-group dicts. Single-axis is `[{"name": [values]}]`; cross-product is multiple entries; zipped paired values are multiple keys in one entry.

```python
# Single axis
@pytest.mark.testerkit_sweeps([{"vin": [3.3, 5.0, 12.0]}])
def test_rail(vin, psu, dmm, verify): ...

# Cross-product — outer first, inner last
@pytest.mark.testerkit_sweeps([
    {"temperature": [-40, 25, 85]},  # outer
    {"vin": [3.3, 5.0, 12.0]},        # inner
])
def test_rail_temp_sweep(temperature, vin, psu, dmm, verify): ...

# Zipped axis — same-length lists, paired
@pytest.mark.testerkit_sweeps([{"load_a": [0.1, 0.4], "load_b": [10, 20]}])
def test_zipped(load_a, load_b, ...): ...
```

**Sidecar equivalent:**

```yaml
sweeps:
  - {temperature: [-40, 25, 85]}
  - {vin: [3.3, 5.0, 12.0]}
```

**`testerkit_sweeps` vs `@pytest.mark.parametrize`:** both work; both feed the same `inputs` lane on the vector row (query with `FieldRef.input(name)`). Use `testerkit_sweeps` when you want range expanders (`linspace`, `arange`, `logspace`, etc.) or sidecar parity; use `@pytest.mark.parametrize` when you want pytest's `pytest.param(..., id="...")` / `marks=[...]` per-row metadata. See [Test vectors & sweeps](../../how-to/execution/vector-expansion.md) for full semantics including range expanders and the `vectors` self-loop fixture.

---

## `testerkit_mocks`

Install one or more mocks at test entry, unwound at teardown. Each entry has a `target:` and arbitrary `unittest.mock.patch.object` kwargs (`return_value`, `side_effect`, `wraps`, `spec`, etc.).

**Signature:** `@pytest.mark.testerkit_mocks([entries])` where each entry is a `MockEntry` dict.

```python
@pytest.mark.testerkit_mocks([
    {"target": "dmm.measure_dc_voltage", "return_value": 3.31},
    {"target": "psu.measure_current", "side_effect": [0.1, 0.2, 0.3]},
])
def test_mocked(dmm, psu, verify): ...
```

**Sidecar equivalent:**

```yaml
mocks:
  - {target: dmm.measure_dc_voltage, return_value: 3.31}
  - {target: psu.measure_current, side_effect: [0.1, 0.2, 0.3]}
```

The target is `"<fixture>.<attr>"` — the pytest fixture name plus the attribute on the resolved fixture value. See [Mock mode](../../how-to/configuration/mock-mode.md) for the full priority resolution (per-test mocks > file-level mocks > station `mock_config` > zero).

---

## `testerkit_characteristics`

Iterate the test body over a subset of the part spec's `characteristics`. Combined with `testerkit_connections` to select which signal-path connections to bind. Used by [spec-driven testing](../../how-to/execution/spec-driven-testing.md).

**Signature:** `@pytest.mark.testerkit_characteristics([ids])` — list of characteristic IDs.

```python
@pytest.mark.testerkit_characteristics(["output_voltage", "output_current"])
def test_rail(context, verify):
    for char_id in context.characteristics:
        verify(char_id, ...)
```

**Sidecar equivalent:**

```yaml
characteristics: [output_voltage, output_current]
```

---

## `testerkit_connections`

Select which fixture connections the test iterates over. Pairs with `testerkit_characteristics`. Two payload shapes:

- **List of names** — bind by fixture-connection name: `@pytest.mark.testerkit_connections(["VOUT", "VIN"])`
- **Dict mapping instrument → channels** — bind by instrument and channel selector: `@pytest.mark.testerkit_connections(dmm=["CH1", "CH2"])`

**Sidecar equivalents:**

```yaml
connections: [VOUT, VIN]              # list form
# OR
connections: {dmm: [CH1, CH2]}        # dict form
```

The pytest plugin's `connections` fixture exposes the resolved `FixtureConnection` iterator for the test body.

---

## `testerkit_retry`

Per-test retry policy. Translates to `pytest-rerunfailures`' `flaky` under the hood.

**Signature:** `@pytest.mark.testerkit_retry(max_retries=, delay=, on=)`

```python
@pytest.mark.testerkit_retry(max_retries=2, delay=0.5, on=["AssertionError"])
def test_flaky_settling(dmm, verify): ...
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `max_retries` | `int >= 0` | `0` | 0 = single execution, 2 = up to 2 retries beyond original (3 total) |
| `delay` | `float >= 0` | `0.0` | Seconds between attempts |
| `on` | `list[str] \| None` | `None` (any) | Exception class names that trigger retry |

**Sidecar equivalent:**

```yaml
retry: {max_retries: 2, delay: 0.5, on: [AssertionError]}
```

Each retry produces measurement rows with the same `vector_index` and an incremented `vector_retry`. See [Parquet schema → Retries](../data/parquet-schema.md#retries).

---

## `testerkit_prompts`

Declare operator prompts the test can invoke via the `prompt` fixture. Keyword per prompt name.

**Signature:** `@pytest.mark.testerkit_prompts(**by_name)` — each value matches `PromptConfig`.

```python
@pytest.mark.testerkit_prompts(
    inspect={"message": "Verify LED is GREEN", "prompt_type": "confirm"},
)
def test_visual(prompt, verify):
    prompt("inspect")  # blocks until operator responds
    verify("led_state", read_led_color())
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `message` | `str` | (required) | Prompt text shown to the operator |
| `prompt_type` | `"confirm" \| "choice" \| "input"` | `"confirm"` | What the operator UI shows |
| `choices` | `list[str] \| None` | `None` | For `"choice"` type |
| `timeout_seconds` | `int \| None` | `None` | Stop waiting after N seconds (returns a timed-out response) |

**Sidecar equivalent:**

```yaml
prompts:
  inspect: {message: "Verify LED is GREEN", prompt_type: confirm}
```

---

## Composing `testerkit_characteristics` + `testerkit_connections`

These two markers are the two halves of selecting which connections a test iterates over `ctx.connections` (the `connections` fixture): `testerkit_characteristics` says *which characteristic* on the part, and `testerkit_connections` says *which fixture connections* to bind. They're independent, so every combination of present / absent / by-name / by-channel / fixture-loaded / fixture-absent has a defined behavior. Find the row that matches your test.

`testerkit_connections` takes one of two shapes (not both at once):

- **`[name, ...]`** — bind by fixture-connection name. Requires a fixture YAML.
- **`{instrument: [channels], ...}`** — bind by instrument → channel selector. Works without a fixture (synthesizes stubs) for early bringup.

| Case | `testerkit_characteristics` | `testerkit_connections` | Fixture loaded? | Result |
|------|---------------|----------------------|-----------------|--------|
| 1 | — | — | any | No markers → `ctx.connections` is `None`; the test runs once with no connection context. |
| 2 | `[X]` | — | yes | Iterate the fixture connections whose `uut_pin` (or `net`) matches a pin in `X`'s resolved pins. Order: characteristic order, then each characteristic's pin order (deduplicated). |
| 3 | `[X]` | — | no | Empty iterator — no fixture means no connections to bind. The test iterates `ctx.connections` zero times. |
| 4 | — | `[a, b]` (names) | yes | Iterate the named connections in the order listed. Unknown name → `pytest.UsageError`. |
| 5 | — | `[a, b]` (names) | no | `pytest.UsageError` — connection names are only meaningful against a fixture YAML. |
| 6 | — | `{inst: [ch]}` (channels) | yes | Match each `(instrument, channel)` against fixture connections, in listed order. No match → `pytest.UsageError`. `'all'` selects every connection on that instrument. |
| 7 | — | `{inst: [ch]}` (channels) | no | Synthesize connection stubs (`name="{inst}_ch{ch}"`, no `uut_pin`) for early bringup. `'all'` → `pytest.UsageError` (nothing to enumerate). |
| 8 | `[X]` | `[a, b]` (names) | yes | Resolve the names (case 4), then require every selected connection's `uut_pin` to fall in the union of the characteristics' pin sets. Out-of-set → `pytest.UsageError`. Listed order wins. |
| 9 | `[X]` | `[a, b]` (names) | no | `pytest.UsageError` (case 5 — a fixture is required for connection names). |
| 10 | `[X]` | `{inst: [ch]}` (channels) | yes | Resolve the channels (case 6), then require every match's `uut_pin` to fall in the union pin set. Out-of-set → `pytest.UsageError`. Listed order wins. |
| 11 | `[X]` | `{inst: [ch]}` (channels) | no | `pytest.UsageError` — fixtureless channel stubs have no `uut_pin` to cross-check against the characteristics. Drop `testerkit_characteristics` for pure bringup, or load a fixture. |

Invariants across the matrix:

- **No part loaded, or an unknown characteristic ID** → `pytest.UsageError`.
- **Iteration order** — when `testerkit_connections` is present it sets the order (user-listed); with characteristics alone, the order follows characteristic order then each characteristic's pin order.
- **Declared but un-iterated** — if connections resolve to a non-empty set and the test body never iterates `ctx.connections`, the test fails with `AssertionError`. An empty resolved set (case 3) is not an error; the body just runs zero rounds.

## Where markers live

Same vocabulary, three delivery channels:

| Channel | Example |
|---|---|
| Inline decorator | `@pytest.mark.testerkit_limits(output_voltage={"low": 3.135, "high": 3.465, "unit": "V"})` |
| Sidecar YAML (`tests/test_<module>.yaml`) | `limits: { output_voltage: { low: 3.135, high: 3.465, unit: V } }` |
| Profile YAML (`profiles/*.yaml`) | Same shape; applies session-wide via `--test-profile=<name>` |

Resolution order (least → most specific): inline marker (class then method) → sidecar file-level → sidecar class/method → profile chain. Sidecar overrides inline because sidecar markers are applied after inline decorators and the resolver is last-wins. CLI flags (`-k`, `-m`, `--mock-instruments`, etc.) compose with this chain rather than overriding it wholesale. See [Test configuration](../configuration.md#sidecar-yaml) for the full merge semantics.

## See also

- [pytest-native reference](../overview/pytest-native.md) — how TesterKit tests use pytest's own collection / fixtures / markers
- [TesterKit fixtures reference](fixtures.md) — all the fixtures the plugin exposes
- [Models](../data/models.md) — `MeasurementLimitConfig`, `MockEntry`, `SweepEntry`, `RetryConfig`, `PromptConfig` field shapes
- [Test vectors & sweeps](../../how-to/execution/vector-expansion.md) — `testerkit_sweeps` semantics + `vectors` self-loop fixture
