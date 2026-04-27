# Stage 6 — Product YAML + fixture connections

The product is a first-class artifact now. Characteristics declare
the nominal spec once (`rail_3v3 = 3.3 V`); sidecar limits reference
the characteristic plus a tolerance. The fixture YAML wires DUT pins
through the bench.

## Diff from stage 5

- Added `products/buck_3v3.yaml` — characteristics (`rail_3v3`,
  `input_voltage`, `idle_current`) + the DUT pin map
- Added `fixtures/buck_3v3_bench.yaml` — connections that route DUT pins
  to station instrument channels
- Added `default_fixture: buck_3v3_bench` to `litmus.yaml`
- Replaced raw `low: / high:` limits in the sidecar with
  `characteristic: rail_3v3, tolerance_pct: 2`
- Added `characteristics: [rail_3v3]` to each test so the row carries
  spec-ref traceability

## Run it

```bash
cd examples/06-product-spec
uv run pytest -v
```

## Why reference the product spec

Pointing each test at a product characteristic gives every measurement
row two things it couldn't have otherwise:

1. **Single source of truth for the value.** Change `rail_3v3`'s
   nominal in `products/buck_3v3.yaml` and every test recomputes its
   limit band. No hunting through test files to update numbers.
2. **Traceable rows.** Each Parquet row carries `spec_id` /
   `spec_ref` / `dut_pin`. Queries can aggregate by characteristic
   across many runs; reports can link back to the spec document.

## The gap this stage leaves

The sidecar encodes **one set** of limits — the defaults. Real
manufacturing separates dev bringup (loose), characterization (no
limits, record-only), and production (tight). Putting all three in
one sidecar with `when:` blocks works but forces every engineer
editing a test to read every scenario's bands. Stage 7 introduces
**profiles** — one file per scenario, with `extends:` chains for
shared bases.
