# Fixture toolbox

Every fixture Litmus's pytest plugin exposes to a test author. `psu`, `dmm`,
and any other instrument **role** name are *not* in this list — they are
generated per-session from the station's `instruments:` map (or
`litmus init --tier bringup`'s mock conftest); take them as plain arguments,
same as any pytest fixture.

Picking the right verb: `litmus refs show routing`. Sizing how much scaffold
a fixture needs: `litmus refs show tiers`.

## The four verbs (bare callables)

| Fixture | Signature | Routes to | Judges? |
|---|---|---|---|
| `verify` | `verify(name, value, limit=None, *, characteristic=None, namespace=None, unit=None)` | measurement row | yes — raises `LimitFailure` on FAIL |
| `measure` | same signature as `verify` | measurement row | no — `Outcome.DONE`, never raises on a missing limit |
| `observe` | `observe(name, value, *, namespace=None, unit=None)` | output lane (routes by value shape) | no |
| `stream` | `stream(name, sample, *, namespace=None, unit=None) -> str` | a channel | no |

`measure`/`verify` share a signature — write `measure` for characterization,
flip the one word to `verify` when a spec lands. `observe` is a lane change,
not a softer `verify`: scalars land inline, `Waveform`/arrays route to
ChannelStore, blobs route to FileStore, and `channel://`/`file://` URIs are
linked as-is. `stream` appends one sample to a channel and returns its
`channel://` URI; it never touches the measurement or output lane. Full
detail: `litmus refs show verify`, `litmus refs show observe`.

`context.configure(key, value, *, unit=None)` stamps an **input** — an
execution-dynamic stimulus value (an actual readback, a runtime-computed
setpoint). There is no bare `configure` fixture; reach for
`context.configure(...)` only when the value is decided at run time. For
fixed stimuli, set them imperatively (`psu.set_voltage(5.0)`); for swept
stimuli, declare the axis (`@pytest.mark.parametrize` / sidecar `sweeps:`)
and let it get recorded for you.

## `context` — the test's active context

Not a sweep reader — it provides what's active for the current run/step/
vector: DUT identity, station, part, limits, and prior-iteration values.
Bare fixture, always available:

```python
def test_output_voltage(context, dmm, verify):
    context.observe("ambient_c", 23.5)                  # output
    context.configure("psu.actual_voltage", 5.02)        # input, runtime value
    verify("vout", dmm.measure_dc_voltage())
```

| Method / property | Returns | Purpose |
|---|---|---|
| `configure(key, value, *, unit=None)` | `None` | stamp an input |
| `observe(key, value, *, namespace=None, unit=None)` | `None` | stamp an output (same routing as the `observe` fixture) |
| `measure(name, value, limit=None, ...)` | `Measurement` | same as the `measure` fixture, callable as a method |
| `stream(name, sample, ...)` | `str` | same as the `stream` fixture |
| `get_param(key, default=None)` | `Any` | read a param, walking the parent chain |
| `get_observation(key, default=None)` | `Any` | read a previously-observed value |
| `get_limit(name)` | `Limit \| None` | resolve a limit without judging |
| `changed(key)` | `bool` | did `key` change since the previous vector? |
| `last(key, default=None)` | `Any` | value of `key` on the previous vector (param, then observation) |
| `configure_all(values)` / `observe_all(values)` | `None` | bulk `configure`/`observe` over a dict |
| `params` / `observations` / `configured_params` | `dict[str, Any]` | current inputs / outputs / configure-only subset |
| `characteristics` | `tuple[str, ...]` | part characteristics bound to this test |
| `limits` | `LimitsView` | dict-like view over merged `litmus_limits` (`ctx.limits["vout"]`, `.for_characteristic(char_id)`) |
| `run` | `TestRun \| None` | active run record — `ctx.run.uut.serial` is canonical UUT identity |
| `station` | `StationConfig \| None` | active station, or `None` at bringup tier |
| `part` | `Part \| None` | active part definition |
| `connections` | `ConnectionIterator \| None` | see `connections` fixture below |

## Identity, part, and run-level fixtures

| Fixture | Returns | Tier | Purpose |
|---|---|---|---|
| `run_context` | `RunContext` | 0 | session-scoped; `run_context.set(key, value)` attaches custom metadata to the whole run |
| `part` | `Part \| None` | 3 | resolves from `--part`, `--uut-part-number`, or the single `parts/*.yaml` file; `None` at bringup |
| `limits` | `LimitsFn` (`_LimitsMapping`) | 1 | read-only `name -> Limit`; `limits["vout"]` raises `KeyError` if unconfigured |
| `vectors` | `_VectorIterator` | 1+ | taking it switches the test to **self-loop mode**: all sweep sources pre-expand into one matrix and the body does `for v in vectors: ...` |
| `prompt` | `Callable[[str \| None], Any]` | 0 | resolves `litmus_prompts` markers by key; `prompt()` works when exactly one entry is in scope |

## Station / instrument / fixture-YAML fixtures (Tier 2+)

| Fixture | Returns | Purpose |
|---|---|---|
| `station_config` | `StationConfig \| None` | loaded from `--station` / `stations/*.yaml` |
| `fixture_config` | `FixtureConfig \| None` | loaded from `--fixture` / `fixtures/*.yaml`; flattens the resolved site's `connections` on multi-site |
| `mock_instruments` | `bool` | whether `--mock-instruments` / `LITMUS_MOCK_INSTRUMENTS=1` is active |
| `instrument_records` | `dict[str, InstrumentRecord]` | resolved instrument identity + calibration, keyed by role |
| `instruments` | `dict[str, Any]` | connected driver instances, keyed by role (session-scoped, auto-disconnected) |
| `instrument` | `InstrumentAccessor` | `instrument("dmm")` by role; `instrument.by_type("pymeasure.instruments.keithley.Keithley2000")` |
| `uut` | UUT driver instance \| `None` | instantiates `part.driver`, connects via `fixture_config.uut_resource` |
| `pins` | `PinAccessor` | `pins["VIN"].set_voltage(5.0)` — UUT-centric pin lookup from `fixture_config` |
| `routes` | `RouteManager \| None` | per-test switch routing: `with routes.for_pin("VOUT"): ...` |
| `fixture_manager` | `FixtureManager` | advanced pin/net lookups beyond `pins[...]` |
| `connections` | `ConnectionIterator \| None` | resolved from `litmus_characteristics` / `litmus_connections` markers; `for conn in connections: ...` |
| `sync` | `SyncPoint \| None` | multi-site coordination; `None` outside worker mode (`_LITMUS_SITE_INDEX` set) |

`pins`/`fixture_manager` raise `pytest.UsageError` without both a
`fixture_config` and `instruments` — a station and a fixture YAML, or
`litmus init --tier bench`.

## Markers Litmus registers

| Marker | Purpose |
|---|---|
| `litmus_sweeps([{argname: argvalues}, ...])` | declare nested parametric sweeps (each dict = one nesting level; multi-key dict = zipped axes) |
| `litmus_retry(max_retries=N, delay=S, on=[...])` | retry policy; translates to `pytest-rerunfailures`' `@pytest.mark.flaky` |
| `litmus_limits(**kwargs)` | inject limits by measurement name; merges with sidecar `limits:` |
| `litmus_characteristics([<characteristic_id>, ...])` | bind the test to part characteristics; derives fixture connections from their pins |
| `litmus_connections([<name>, ...] \| **{instrument: channels})` | bind to fixture-connection names, or raw instrument channels by kwarg |
| `litmus_prompts(**kwargs)` | declare named operator prompts, each a `PromptConfig`-shaped dict; read via the `prompt` fixture |
| `litmus_mocks([{target: "<fixture>.<attr>", **patch_kwargs}, ...])` | install mocks for the test's duration; see `litmus refs show mocks` |

## See also

`litmus refs show routing` — which verb, which limit layer, outer-vs-inner
sweep. `litmus refs show tiers` — which fixtures need a station/fixture/part
YAML vs. nothing. `litmus refs show verify` / `observe` / `mocks` — the
verb and marker deep dives.
