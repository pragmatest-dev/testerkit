# Stage 5 — Station YAML + catalog

The conditional-mock conftest from chapters 1-4 disappears. The
bench is declared once in YAML; instrument fixtures (`psu`, `dmm`)
materialize automatically. The same `--mock-instruments` flag now
flips the whole rig — declared values come from each instrument's
`mock_config` block instead of a Python branch.

## Diff from stage 4

- Deleted `conftest.py` — no more hand-written `psu` / `dmm`
  fixtures. The plugin builds them from the station + catalog at
  session start.
- Added `catalog/` — generic instrument capability definitions
  (referenced from stations).
- Added `stations/bench_01.yaml` — a specific bench, wiring driver
  class → catalog entry → mock values.
- Added `litmus.yaml` — project defaults (`default_station: bench_01`,
  `mock_instruments: true`).

The `drivers/` folder is unchanged from chapter 1 — same `DMM` /
`PSU` driver classes; same PyVISA-shaped interface. Test bodies are
also unchanged: `psu.set_voltage(...)` / `dmm.measure_dc_voltage()`
on the same fixture names.

## Run it

```bash
cd examples/05-station-catalog
LITMUS_PROMPT_MODE=auto-confirm uv run pytest -v
```

The `LITMUS_PROMPT_MODE=auto-confirm` env var lets the operator-prompt
demo run without a tty (auto-confirms / picks the first choice). Drop
it for an interactive bench run.

## Why station + catalog

Three things gain leverage:

1. **One station, many tests.** Change the PSU model once in
   `stations/bench_01.yaml`; every test that uses `psu` follows.
2. **Bring your own driver.** Litmus doesn't ship instrument drivers.
   Point `driver:` at your PyVISA / PyMeasure / vendor class; the
   catalog describes *what* the instrument can do, the driver class
   describes *how* to talk to it.
3. **Mock declarations move from Python to YAML.** Chapters 1-4
   wrote `Mock(PSU, measure_voltage=5.0, ...)` in `conftest.py`.
   Now those values live in `stations/bench_01.yaml: mock_config:`.
   Same `--mock-instruments` flag, same `Mock(driver_class, **values)`
   factory under the hood — only the declaration site changes.
   Ops can edit values without touching Python; the same YAML file
   stays the truth when `--no-mock-instruments` points the rig at
   real hardware.

## `litmus_mocks` — per-test override

The station's `mock_config` declares a default return for each
instrument method. Right shape for the happy path; useless for
exercising fault paths. `litmus_mocks` patches one or more methods
on a fixture for one test:

```python
@pytest.mark.litmus_mocks([{"target": "dmm.measure_dc_voltage", "return_value": 4.5}])
def test_ovp_path_inline(verify, psu, dmm):
    psu.set_voltage(5.0)
    verify("v_overvoltage", dmm.measure_dc_voltage())   # 4.5, not 3.31
```

Sidecar form (same effect, on `test_ovp_path_sidecar`):

```yaml
test_ovp_path_sidecar:
  config:
    - litmus_mocks:
        - {target: dmm.measure_dc_voltage, return_value: 4.5}
```

The `v_overvoltage` band (`{low: 4.0, high: 5.0}`) is what makes
this demo *prove itself*: without the override the test sees the
bench default 3.31 V, fails the low limit, and you know the marker
didn't fire. The marker forwards every kwarg except `target`
straight to `unittest.mock.patch.object`, so `side_effect`,
`wraps`, `spec`, `autospec`, `new_callable`, etc. all work.

## `litmus_prompts` — operator in the loop

Hardware test routinely needs a human in the loop: confirm the DUT
is seated, pick a fixture variant, acknowledge a high-voltage step.
The `prompt` fixture resolves named entries declared by
`litmus_prompts` markers anywhere in scope:

```python
@pytest.mark.litmus_prompts(
    pick_fixture={
        "message": "Pick a fixture variant",
        "prompt_type": "choice",
        "choices": ["bench_01", "bench_02"],
    }
)
def test_operator_choice_inline(verify, prompt, psu, dmm):
    chosen = prompt("pick_fixture")
    assert chosen == "bench_01"          # auto-confirm returns first choice
    psu.set_voltage(5.0)
    verify("v_rail", dmm.measure_dc_voltage())
```

Sidecar form (same effect):

```yaml
test_operator_choice_sidecar:
  config:
    - litmus_prompts:
        pick_fixture:
          message: "Pick a fixture variant"
          prompt_type: choice
          choices: [bench_01, bench_02]
```

Each entry is a `PromptConfig`: `message`, optional `prompt_type`
(`confirm` / `choice` / `input`), `choices`, `timeout_seconds`. CI
runs use `LITMUS_PROMPT_MODE=auto-confirm`; bench operators see a
tty prompt; UI runners install their own handler.

## The gap this stage leaves

The limit is still a raw number (`low: 3.2, high: 3.4`). If you
manufacture three product variants with different spec limits, you
duplicate the number in three test sidecars. Stage 6 introduces a
**product** YAML so the spec lives once, and tests **reference** it
— `characteristic: rail_3v3` + `tolerance_pct: 2` derives the band
from the characteristic's declared value.
