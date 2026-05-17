# Test Harness Integration

> **For new pytest projects, use the plugin: [Litmus fixtures](../reference/litmus-fixtures.md) (`context`, `verify`, `logger`, `pins`, … — 20 in total) and [Litmus markers](../reference/litmus-markers.md) (`litmus_limits`, `litmus_sweeps`, …) handle setup automatically.** `TestHarness` is the imperative entry point for non-pytest runners (Robot Framework, unittest, custom harnesses) or for situations where you need explicit lifecycle control.

`TestHarness` (in `litmus.execution.harness`) wraps the same machinery the pytest plugin uses: vector expansion, retry, limit resolution, measurement logging with full traceability. Source of truth: `src/litmus/execution/harness.py`.

## Required collaborators

`TestHarness` writes through a `TestRunLogger`, which writes through the event log. To run outside pytest you wire both up explicitly:

```python
from litmus.execution.harness import TestHarness
from litmus.execution.logger import TestRunLogger

logger = TestRunLogger(
    dut_serial="SN12345",
    station_id="bench_1",
    test_phase="characterization",
)

harness = TestHarness(logger=logger, step_name="test_output_voltage")
```

`TestRunLogger.__init__` takes the run-level metadata directly (`dut_serial`, `station_id`, `station_name`, `operator_id`, `test_phase`, `product_id`, `data_dir`, etc.) — see `src/litmus/execution/logger.py` for the full keyword list. The `RunContext` Pydantic model is created internally from `TestRun`; you don't construct it.

A harness without a logger still runs, but no events are recorded — useful only for tests-of-tests.

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

1. Per-measurement limit in `limits=` constructor arg
2. `config["limits"][name]` from the config dict
3. `product_context.get_limit(name)` from the active product spec
4. None — measurement recorded as unchecked

Pass a `Limit` object (`from litmus.models.test_config import Limit`) for explicit limits. The sidecar-style dict shape (`{"low": 3.0, "high": 3.6, "units": "V"}`) goes in `config["limits"]`, not as the `limit=` kwarg.

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

Step boundaries are required when you want measurements grouped under named work; otherwise everything attaches to the top-level `step_name` passed in the constructor.

## Operator prompts

```python
harness.prompt(
    message="Verify the chamber temperature is 25°C ± 1°C",
    prompt_type="confirm",
    timeout_seconds=30,
)
```

`prompt_type` matches the `PromptConfig` shapes from `litmus.models.test_config`: `confirm`, `choice`, `text`, etc.

## Hierarchical context

`harness.context` returns the active context (run → step → vector). Each level inherits from its parent and can override locally:

```python
harness.run_context.set("operator", "jane")           # run scope
with harness.step():
    harness.context.set("fixture.id", "FIX-01")       # step scope
    for vector in harness.vectors:
        with harness.run_vector(vector):
            harness.context.observe("temp_probe.temperature", 24.8)  # vector scope
            harness.measure("output_voltage", float(dmm.measure_dc_voltage()))
```

Run-scope fields appear as columns in every Parquet row this run produces. Step- and vector-scope fields appear only on the rows from that scope.

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
