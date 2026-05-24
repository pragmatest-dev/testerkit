# Litmus markers

The Litmus pytest plugin registers **seven markers** (`LITMUS_MARKER_NAMES` in `src/litmus/pytest_plugin/markers.py`). Each maps 1:1 to a field on `TestEntry` (the recursive node type in the sidecar YAML), so anything you can write inline as a marker, you can write in the sidecar — and vice versa.

Pytest's own markers (`@pytest.mark.parametrize`, `@pytest.mark.skip`, `@pytest.mark.flaky` from `pytest-rerunfailures`, etc.) work unchanged. Litmus's markers slot in alongside them.

## No-stacking rule

At most one inline `@pytest.mark.litmus_X` decorator of each kind per test. Multi-entry payloads (a list of dicts for sweeps/mocks, multiple kwargs for limits/prompts) consolidate onto one marker. `@pytest.mark.parametrize` is the explicit exception — pytest's native stacking convention stays. Stacking a Litmus marker raises `StackedMarkersError` at collection.

```python
# OK
@pytest.mark.litmus_sweeps([{"temperature": [-40, 25, 85], "load": [0.1, 0.5]}])
def test_x(...): ...

# Not OK — raises StackedMarkersError
@pytest.mark.litmus_sweeps([{"temperature": [-40, 25, 85]}])
@pytest.mark.litmus_sweeps([{"load": [0.1, 0.5]}])
def test_x(...): ...
```

---

## `litmus_limits`

Pin a `Limit` per measurement name. Both `verify(name, value)` and `logger.measure(name, value)` record the measurement row and resolve the limit against this marker (or the sidecar's `limits:` block, or the active product spec, in resolution order); the only difference is `verify` raises `AssertionError` on FAIL where `logger.measure` doesn't.

**Signature:** `@pytest.mark.litmus_limits(**by_name)` — one keyword per measurement name; each value is a dict matching `MeasurementLimitConfig`.

```python
@pytest.mark.litmus_limits(
    output_voltage={"low": 3.135, "high": 3.465, "units": "V"},
    output_current={"high": 0.5, "units": "A"},
)
def test_power_rail(dmm, psu, verify):
    verify("output_voltage", dmm.measure_dc_voltage())
    verify("output_current", psu.measure_current())
```

**Sidecar equivalent:**

```yaml
limits:
  output_voltage: {low: 3.135, high: 3.465, units: V}
  output_current: {high: 0.5, units: A}
```

**Full shape:** `low`, `high`, `nominal`, `units`, `comparator`, plus the alternative `characteristic:` / `tolerance_pct:` / `tolerance_abs:` derivation, and the `bands:` list for condition-indexed limits. See [`MeasurementLimitConfig`](models.md) for the full schema and [Test limits](../how-to/execution/limits.md#condition-indexed-bands) for the band semantics.

---

## `litmus_sweeps`

Litmus-native parametrize. Each entry in the list is one **axis-group dict** — single-key dicts run as one independent loop; multi-key dicts inside one entry zip together; stacked entries cross-product (top entry = outermost / slowest loop).

**Signature (inline):** `@pytest.mark.litmus_sweeps([entries])` — one positional list of axis-group dicts. Single-axis is `[{"name": [values]}]`; cross-product is multiple entries; zipped paired values are multiple keys in one entry.

```python
# Single axis
@pytest.mark.litmus_sweeps([{"vin": [3.3, 5.0, 12.0]}])
def test_rail(vin, psu, dmm, verify): ...

# Cross-product — outer first, inner last
@pytest.mark.litmus_sweeps([
    {"temperature": [-40, 25, 85]},  # outer
    {"vin": [3.3, 5.0, 12.0]},        # inner
])
def test_rail_temp_sweep(temperature, vin, psu, dmm, verify): ...

# Zipped axis — same-length lists, paired
@pytest.mark.litmus_sweeps([{"load_a": [0.1, 0.4], "load_b": [10, 20]}])
def test_zipped(load_a, load_b, ...): ...
```

**Sidecar equivalent:**

```yaml
sweeps:
  - {temperature: [-40, 25, 85]}
  - {vin: [3.3, 5.0, 12.0]}
```

**`litmus_sweeps` vs `@pytest.mark.parametrize`:** both work; both feed the same `context.get_param(name)` API and the same parquet `in_*` columns. Use `litmus_sweeps` when you want range expanders (`linspace`, `arange`, `logspace`, etc.) or sidecar parity; use `@pytest.mark.parametrize` when you want pytest's `pytest.param(..., id="...")` / `marks=[...]` per-row metadata. See [Test vectors & sweeps](../how-to/execution/vector-expansion.md) for full semantics including range expanders and the `vectors` self-loop fixture.

---

## `litmus_mocks`

Install one or more mocks at test entry, unwound at teardown. Each entry has a `target:` and arbitrary `unittest.mock.patch.object` kwargs (`return_value`, `side_effect`, `wraps`, `spec`, etc.).

**Signature:** `@pytest.mark.litmus_mocks([entries])` where each entry is a `MockEntry` dict.

```python
@pytest.mark.litmus_mocks([
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

The target is `"<fixture>.<attr>"` — the pytest fixture name plus the attribute on the resolved fixture value. See [Mock mode](../how-to/configuration/mock-mode.md) for the full priority resolution (per-test mocks > file-level mocks > station `mock_config` > zero).

---

## `litmus_characteristics`

Iterate the test body over a subset of the product spec's `characteristics`. Combined with `litmus_connections` to select which signal-path connections to bind. Used by [spec-driven testing](../how-to/execution/spec-driven-testing.md).

**Signature:** `@pytest.mark.litmus_characteristics([ids])` — list of characteristic IDs.

```python
@pytest.mark.litmus_characteristics(["output_voltage", "output_current"])
def test_rail(context, verify):
    for char_id in context.characteristics:
        verify(char_id, ...)
```

**Sidecar equivalent:**

```yaml
characteristics: [output_voltage, output_current]
```

---

## `litmus_connections`

Select which fixture connections the test iterates over. Pairs with `litmus_characteristics`. Two payload shapes:

- **List of names** — bind by fixture-connection name: `@pytest.mark.litmus_connections(["VOUT", "VIN"])`
- **Dict mapping instrument → channels** — bind by instrument and channel selector: `@pytest.mark.litmus_connections(dmm=["CH1", "CH2"])`

**Sidecar equivalents:**

```yaml
connections: [VOUT, VIN]              # list form
# OR
connections: {dmm: [CH1, CH2]}        # dict form
```

The pytest plugin's `connections` fixture exposes the resolved `FixtureConnection` iterator for the test body.

---

## `litmus_retry`

Per-test retry policy. Translates to `pytest-rerunfailures`' `flaky` under the hood.

**Signature:** `@pytest.mark.litmus_retry(max_retries=, delay=, on=)`

```python
@pytest.mark.litmus_retry(max_retries=2, delay=0.5, on=["AssertionError"])
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

Each retry produces measurement rows with the same `vector_index` and an incremented `vector_retry`. See [Parquet schema → Retries](parquet-schema.md#retries).

---

## `litmus_prompts`

Declare operator prompts the test can invoke via the `prompt` fixture. Keyword per prompt name.

**Signature:** `@pytest.mark.litmus_prompts(**by_name)` — each value matches `PromptConfig`.

```python
@pytest.mark.litmus_prompts(
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
| `timeout_seconds` | `int \| None` | `None` | Auto-fail after timeout |

**Sidecar equivalent:**

```yaml
prompts:
  inspect: {message: "Verify LED is GREEN", prompt_type: confirm}
```

---

## Where markers live

Same vocabulary, three delivery channels:

| Channel | Example |
|---|---|
| Inline decorator | `@pytest.mark.litmus_limits(output_voltage={"low": 3.135, "high": 3.465, "units": "V"})` |
| Sidecar YAML (`tests/test_<module>.yaml`) | `limits: { output_voltage: { low: 3.135, high: 3.465, units: V } }` |
| Profile YAML (`profiles/*.yaml`) | Same shape; applies session-wide via `--test-profile=<name>` |

Resolution order (least → most specific): inline marker (class then method) → sidecar file-level → sidecar class/method → profile chain. Sidecar overrides inline because sidecar markers are applied after inline decorators and the resolver is last-wins. CLI flags (`-k`, `-m`, `--mock-instruments`, etc.) compose with this chain rather than overriding it wholesale. See [Test configuration](configuration.md#sidecar-yaml) for the full merge semantics.

## See also

- [pytest-native reference](pytest-native.md) — how Litmus tests use pytest's own collection / fixtures / markers
- [Litmus fixtures reference](litmus-fixtures.md) — all 20 fixtures the plugin exposes
- [Models](models.md) — `MeasurementLimitConfig`, `MockEntry`, `SweepEntry`, `RetryConfig`, `PromptConfig` field shapes
- [Test vectors & sweeps](../how-to/execution/vector-expansion.md) — `litmus_sweeps` semantics + `vectors` self-loop fixture
