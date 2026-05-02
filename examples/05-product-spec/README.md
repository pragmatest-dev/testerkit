# Stage 5 — Product YAML drives spec-aware limits

The product is a first-class artifact: characteristics declare each
parameter's nominal value once (`rail_3v3 = 3.3 V`), and sidecar
limits reference the characteristic plus a tolerance. The resolver
reads the band from the product at measurement time.

The bench is still the conftest from stages 2-4. This chapter shows
the **spec layer in isolation** — change `rail_3v3` in the product
YAML and every test recomputes its limit band, no station YAML, no
fixture YAML, no connection iteration. Stage 6 introduces those.

## Diff from stage 4

- Added `products/buck_3v3.yaml` — characteristics (`rail_3v3`,
  `input_voltage`, `idle_current`) + the DUT pin map.
- Replaced raw `low: / high:` limits in the sidecar with
  `characteristic: rail_3v3, tolerance_pct: 2`.
- Same `conftest.py` from stages 2-4 — `psu` / `dmm` fixtures are
  still hand-written and mocked under `--mock-instruments`.

No station YAML, no fixture YAML, no connection iteration — those
arrive in stage 6.

## Run it

```bash
cd examples/05-product-spec
uv run pytest -v
```

## Why reference the product spec

Pointing each test at a product characteristic gives every measurement
row two things it couldn't have with absolute `low/high` numbers:

1. **Single source of truth for the value.** Change `rail_3v3`'s
   nominal in `products/buck_3v3.yaml` and every test recomputes its
   limit band. No hunting through test files to update numbers.
2. **Traceable rows.** Each Parquet row carries `characteristic_id`.
   Queries can aggregate by characteristic across many runs; reports
   can link back to the spec document.

## The gap this stage leaves

Instrument fixtures still come from `conftest.py`, and the test code
has to know whether to call `psu.measure_current` vs.
`dmm.measure_dc_voltage` per measurement. Stage 6 swaps the conftest
for a station YAML (instruments declared once), adds a fixture YAML
(pin↔channel routing), and introduces `ctx.connections` iteration so
the test body asks "what's the next thing to measure on this
characteristic" instead of naming the instrument explicitly.
