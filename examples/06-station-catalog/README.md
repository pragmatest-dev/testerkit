# Stage 6 — Station YAML + catalog + fixture connections

The bench moves from code to config: instruments are declared once in
``stations/bench_01.yaml``, capability metadata lives under
``catalog/``, and pin↔channel routing is in
``fixtures/buck_3v3_bench.yaml``. Tests iterate ``ctx.connections``
instead of naming an instrument explicitly per measurement; spec
limits from stage 5 still apply.

## Diff from stage 5

- Deleted ``conftest.py`` — no more hand-written ``psu`` / ``dmm``
  fixtures. The plugin builds them from the station + catalog at
  session start.
- Added ``catalog/`` — generic instrument capability definitions
  (referenced from stations).
- Added ``stations/bench_01.yaml`` — a specific bench, wiring driver
  class → catalog entry → mock values.
- Added ``fixtures/buck_3v3_bench.yaml`` — connections that route DUT
  pins to station instrument channels.
- Tests gained ``for connection in ctx.connections:`` loops; sidecar
  added ``characteristics: [rail_3v3]`` per test so the resolver
  auto-derives the right connections.
- ``litmus.yaml`` adds ``default_station: bench_01`` and
  ``default_fixture: buck_3v3_bench``.

## Run it

```bash
cd examples/06-station-catalog
LITMUS_PROMPT_MODE=auto-confirm uv run pytest -v
```

The ``LITMUS_PROMPT_MODE=auto-confirm`` env var lets the
operator-prompt demo run without a tty (auto-confirms / picks the
first choice). Drop it for an interactive bench run.

## Why station + catalog + fixture together

Three things click into place in this single chapter:

1. **One station, many tests.** Change the PSU model once in
   ``stations/bench_01.yaml``; every test that uses ``psu`` follows.
2. **Bring your own driver.** Litmus doesn't ship instrument drivers.
   Point ``driver:`` at your PyVISA / PyMeasure / vendor class; the
   catalog describes *what* the instrument can do, the driver class
   describes *how* to talk to it.
3. **Connection iteration is config-driven.** ``ctx.connections``
   walks every fixture connection that matches the test's
   characteristic. Single-pin characteristics iterate once; multi-pin
   ones iterate per pin — same loop shape either way. The body never
   names an instrument channel; the routing comes from the fixture.

Fixture connections live where they belong (next to the bench), so
test code becomes portable across benches that share a station type.

## ``mocks`` — per-test override

The station's ``mock_config`` declares a default return for each
instrument method. Right shape for the happy path; useless for
exercising fault paths. The ``mocks`` field patches one or more
methods on a fixture for one test:

```python
@pytest.mark.litmus_mocks([{"target": "dmm.measure_dc_voltage", "return_value": 4.5}])
def test_ovp_path_inline(verify, psu, dmm):
    psu.set_voltage(5.0)
    verify("v_overvoltage", dmm.measure_dc_voltage())   # 4.5, not 3.31
```

Sidecar form (same effect, on ``test_ovp_path_sidecar``):

```yaml
test_ovp_path_sidecar:
  mocks:
    - {target: dmm.measure_dc_voltage, return_value: 4.5}
```

The ``v_overvoltage`` band (``{low: 4.0, high: 5.0}``) is what makes
this demo *prove itself*: without the override the test sees the
bench default 3.31 V, fails the low limit, and you know the marker
didn't fire. The marker forwards every kwarg except ``target``
straight to ``unittest.mock.patch.object``, so ``side_effect``,
``wraps``, ``spec``, ``autospec``, ``new_callable``, etc. all work.

## ``prompts`` — operator in the loop

Hardware test routinely needs a human in the loop: confirm the DUT
is seated, pick a fixture variant, acknowledge a high-voltage step.
The ``prompt`` fixture resolves named entries declared by ``prompts``
fields (sidecar) or ``@pytest.mark.litmus_prompts`` decorators
(inline) anywhere in scope:

```python
@pytest.mark.litmus_prompts(
    pick_fixture={
        "message": "Pick a fixture variant",
        "prompt_type": "choice",
        "choices": ["bench_01", "bench_02"],
    }
)
def test_operator_choice_inline(logger, prompt, psu, dmm):
    chosen = prompt("pick_fixture")
    assert chosen == "bench_01"          # auto-confirm returns first choice
    psu.set_voltage(5.0)
    logger.measure("v_rail", dmm.measure_dc_voltage())  # no limit → DONE
```

Sidecar form (same effect):

```yaml
test_operator_choice_sidecar:
  prompts:
    pick_fixture:
      message: "Pick a fixture variant"
      prompt_type: choice
      choices: [bench_01, bench_02]
```

Each entry is a ``PromptConfig``: ``message``, optional
``prompt_type`` (``confirm`` / ``choice`` / ``input``), ``choices``,
``timeout_seconds``. CI runs use ``LITMUS_PROMPT_MODE=auto-confirm``;
bench operators see a tty prompt; UI runners install their own
handler.

## The gap this stage leaves

One sidecar's worth of limits (the defaults) might fit dev bringup,
but real manufacturing separates dev / characterization / production
phases with different tolerances. Putting all three in one sidecar
with ``when:`` blocks works but forces every engineer editing a test
to read every scenario's bands. Stage 7 introduces **profiles** — one
file per scenario, with ``extends:`` chains for shared bases, plus
``station_type`` + ``fixture`` bindings so each phase loads the right
bench wiring along with its limits.
