# Test Harness Integration

> **For new pytest projects, use the plugin: [Litmus fixtures](../reference/litmus-fixtures.md) (`context`, `verify`, `logger`, `pins`, … — 20 in total) and [Litmus markers](../reference/litmus-markers.md) (`litmus_limits`, `litmus_sweeps`, …) handle setup automatically.** `TestHarness` is the imperative entry point for non-pytest runners (Robot Framework, unittest, custom harnesses) or for situations where you need explicit lifecycle control.

`TestHarness` (in `litmus.execution.harness`) wraps the same machinery the pytest plugin uses: vector expansion, retry, limit resolution, measurement logging with full traceability.

## Required collaborators

`TestHarness` writes through a `TestRunLogger`. The logger only persists events to disk when it has an `EventLog` attached. The pytest plugin wires this up automatically; outside pytest you do it yourself:

```python
from litmus.data.event_store import EventStore
from litmus.execution.harness import TestHarness
from litmus.execution.logger import TestRunLogger

logger = TestRunLogger(
    dut_serial="SN12345",
    station_id="bench_1",
    test_phase="characterization",
    data_dir="data",
)

# Attach an EventLog so emitted events actually hit disk
store = EventStore(_data_dir="data")
logger.event_log = store.get_event_log(logger.test_run.session_id)

harness = TestHarness(logger=logger, step_name="test_output_voltage")

# … iterate vectors, measure, etc.

logger.finalize()   # emit RunEnded + flush; daemon materializes parquet
```

`TestRunLogger.__init__` takes the run-level metadata directly (`dut_serial`, `station_id`, `station_name`, `operator_id`, `test_phase`, `product_id`, `data_dir`, etc.). The logger constructs a `TestRun` and a `RunContext` (a plain class wrapping the run record, with a `.set(key, value)` method for custom metadata) for you; you don't construct either.

A harness whose logger has no `event_log` still runs, but **nothing is persisted** — every event the harness would emit silently no-ops. Useful for unit-testing the harness loop without writing to disk; not what you want for a real run. If your data dir stays empty, this is the first thing to check.

## Constructor signature

```python
TestHarness(
    config: Mapping[str, Any] | None = None,
    logger: TestRunLogger | None = None,
    step_name: str = "test",
    retry: RetryConfig | None = None,
    limits: dict[str, MeasurementLimitConfig | Limit] | None = None,
    product_context: ProductContext | None = None,
    instruments: dict[str, Any] | None = None,
    mock_instruments: bool = False,
    channel_store: Any | None = None,
)
```

| Argument | Purpose |
|---|---|
| `config` | Optional dict with `vectors:` / `limits:` / `mocks:` / `retry:` keys — same shape as the sidecar YAML |
| `logger` | `TestRunLogger` that owns the event log writes |
| `step_name` | Name attached to the step records this harness emits |
| `retry` | Explicit `RetryConfig` (overrides `config["retry"]`) |
| `limits` | Per-measurement limit map (overrides `config["limits"]`) |
| `product_context` | Active product spec — enables `verify(name, value)` style limit + traceability resolution |
| `instruments` | Dict of instrument instances; used by mock-configuration to patch return values |
| `mock_instruments` | Whether mocks are enabled |
| `channel_store` | Optional `ChannelStore` for direct time-series writes |

## Running vectors

`harness.vectors` is the expanded list of `Vector` instances; iterate them inside `run_vector` to scope context per vector:

```python
for vector in harness.vectors:
    with harness.run_vector(vector) as test_vector:
        # `harness.context` is now the vector-level context
        if vector.changed("temperature"):
            harness.prompt(f"Set chamber to {vector['temperature']}°C")
        psu.set_voltage(vector["vin"])
        harness.measure("output_voltage", float(dmm.measure_dc_voltage()))
```

`run_vector` is a context manager that opens / closes the vector boundary, runs the configured retry loop, and stamps every measurement inside with vector params and indices.

### Convenience entry points

For the common case — one test function executed across every vector with retries handled for you — the harness exposes two higher-level entry points:

```python
def measure_rail(vector):
    psu.set_voltage(vector["vin"])
    return float(dmm.measure_dc_voltage())   # value goes to inferred measurement name

step = harness.run_all(measure_rail, step_name="output_voltage")
# step is a completed TestStep with one TestVector per harness.vectors entry,
# each carrying a Measurement with name inferred from limits.
```

| Method | Signature | What it does |
|---|---|---|
| `harness.run_all(test_fn, step_name=None)` | `Callable[[Vector], Any] → TestStep` | Opens a step, iterates `harness.vectors`, runs each through `run_with_retry`. Returns the completed step. |
| `harness.run_with_retry(vector, test_fn)` | `(Vector, Callable[[Vector], Any]) → TestVector` | Runs `test_fn(vector)` inside `run_vector`, retrying up to `retry_config.max_retries` times. Returns the final `TestVector`. |
| `harness.record(key, value)` | `(str, Any) → None` | Emits a `RecordEvent` with `(key, value)`. Use for non-measurement diagnostics — firmware version, calibration timestamp, raw register dump. JSON-serializable values only. |
| `harness.current_vector` (property) | → `Vector \| None` | The vector currently inside `run_vector`, or `None` when called outside a vector boundary. |
| `harness.retry_config` (property) | → `RetryConfig` | The active `RetryConfig` (constructor arg, sidecar `retry:`, or the default `max_retries=0, delay=0`). |

`test_fn` can return a single value (logged under the inferred measurement name) or yield `(name, value)` tuples for multiple measurements per vector. See `harness.measure(...)` below for the per-call form.

## Recording measurements

```python
harness.measure(
    name="output_voltage",
    value=3.31,
    units="V",                  # optional — defaults to limit.units
    limit=Limit(low=3.135, high=3.465, units="V"),  # optional — explicit override
    dut_pin="VOUT",             # optional — auto-resolved from product_context
    instrument_channel="CH1",   # optional
    fixture_connection="vout_dmm",  # optional
)
```

Limit resolution order (when `limit=` is not passed):

1. Per-vector limit, if the current vector was built with one
2. Test-level limits — the harness's `limits=` constructor kwarg, or the entries from `config["limits"]` (they're merged at construction time, not separate fallbacks)
3. The active product context's `get_limit(name, **vector_params)` — vector params are passed as condition kwargs so the right `SpecBand` is selected
4. `None` — measurement recorded as unchecked

Pass a `Limit` object (`from litmus import Limit`) for explicit limits. The sidecar-style dict shape (`{"low": 3.0, "high": 3.6, "units": "V"}`) goes in `config["limits"]`, not as the `limit=` kwarg.

## Steps

A harness writes step records via the `step` context manager:

```python
with harness.step(name="warmup", description="Drive PSU to nominal"):
    psu.set_voltage(5.0)
    psu.enable_output()
    time.sleep(2.0)

with harness.step(name="measure"):
    for vector in harness.vectors:
        with harness.run_vector(vector):
            harness.measure("output_voltage", float(dmm.measure_dc_voltage()))
```

A `step()` context manager is required around every measurement. Calling `harness.measure(...)` outside any `step()` block raises — there is no implicit step that gets created from the constructor's `step_name`. (`step_name` is the *default name* used by `harness.run_all(test_fn)`, which opens a step for you.)

## Operator prompts

```python
harness.prompt(
    message="Verify the chamber temperature is 25°C ± 1°C",
    prompt_type="confirm",
    timeout_seconds=30,
)
```

`prompt_type` is one of `"confirm"`, `"choice"`, or `"input"` (default `"confirm"`). No `"text"`; for free-form text input use `"input"`.

## Hierarchical context

`harness.context` returns the active `Context` (vector ▸ step ▸ run, most-specific-wins). `harness.run_context` returns the run-level `Context` directly. Each child context inherits from its parent and can override locally.

To stamp stimulus values (→ parquet `in_*` columns), use `configure()`. For environmental readings (→ `out_*` columns), use `observe()`:

```python
harness.run_context.configure("operator", "jane")            # run scope
with harness.step(name="measure"):
    harness.context.configure("fixture.id", "FIX-01")        # step scope
    for vector in harness.vectors:
        with harness.run_vector(vector):
            harness.context.observe("temp_probe.temperature", 24.8)   # vector scope
            harness.measure("output_voltage", float(dmm.measure_dc_voltage()))
```

There is no `Context.set(name, value)` method — the verb pair is `configure` / `observe`. The pytest `run_context` fixture exposes a different object (a `RunContext`) which DOES have a `.set()` method for custom run-level metadata. Don't confuse the two: `harness.run_context` is a `Context`; the pytest `run_context` fixture is a `RunContext`.

Run-scope fields appear as columns in every parquet row this run produces. Step- and vector-scope fields appear only on the rows from that scope.

Bulk seeding (useful when you already hold the dict from somewhere else):

```python
harness.context.set_params({"vin": 5.0, "load": 0.5})
harness.context.set_observations({"temp_probe.temperature": 24.8})
```

`set_params` / `set_observations` are dict-update bulk helpers: equivalent to `configure(k, v)` / `observe(k, v)` for every key with one important asymmetry — `observe()` routes large numeric arrays to the channel store and stashes a `channel://` URI on the row, while `set_observations()` writes whatever you pass directly into `Context._observations` with no channel-store routing. Use `observe()` for waveforms / array readings; use `set_observations()` for plain scalar dicts you've already assembled.

`context.measure(name, value, ...)` is a third option for recording. It's a thin redirect to `harness.measure(...)`, so you can record without holding a harness reference — useful inside helper functions that already take a `Context`:

```python
def log_voltage(ctx, dmm):
    ctx.measure("output_voltage", float(dmm.measure_dc_voltage()))
```

`harness.measure(...)` and `context.measure(...)` produce identical events; pick whichever is in scope.

## Spec-driven limits

```python
from litmus.products.context import ProductContext

product_ctx = ProductContext.from_file("products/power_board.yaml", guardband_pct=10)

harness = TestHarness(
    logger=logger,
    step_name="characterize",
    product_context=product_ctx,
)

harness.measure("output_voltage", float(dmm.measure_dc_voltage()))
# Limit resolved from product YAML, guardband applied, traceability columns populated
```

## Comparison with pytest-native

| Concern | `TestHarness` | pytest-native |
|---|---|---|
| Lifecycle | Explicit (`step()`, `run_vector()`) | Implicit (pytest collection + hooks) |
| Vector expansion | Configure via `config["vectors"]` | `@pytest.mark.parametrize` / sidecar `sweeps:` |
| Limit resolution | Explicit `limits=` / `product_context=` | Fixture + marker chain (see [Litmus fixtures](../reference/litmus-fixtures.md) + [Litmus markers](../reference/litmus-markers.md)) |
| Trace context | `harness.context.*` | `context` fixture |
| Instrument access | Caller-managed | Auto-fixtures from station YAML |

If you can use pytest-native, prefer it — every feature works out of the box. Reach for `TestHarness` when the embedding environment leaves you no choice.

## See also

- [Litmus fixtures](../reference/litmus-fixtures.md) + [Litmus markers](../reference/litmus-markers.md) — preferred entry point for pytest projects
- [pytest-native reference](../reference/pytest-native.md) — how Litmus tests use pytest's own collection / fixtures / markers
- [Existing pytest projects](pytest-existing.md) — adopt Litmus from a working pytest suite
- [Results API](results-api.md) — post results from any external system without running a harness
- [Models](../reference/models.md) — `Limit`, `RetryConfig`, `MeasurementLimitConfig`, `PromptConfig` shapes
