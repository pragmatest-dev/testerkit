# Bench bringup demo (Tier 0/1)

Smallest possible Litmus project: one test file, one sidecar, one
`conftest.py` that stands up instrument fixtures locally. No station,
no product, no fixture YAML.

## Run

```bash
cd demo/bringup
uv run pytest -v
```

## What you get

* `meas_value`, `meas_limit_low/high`, `outcome` — populated on every row.
* `meas_dut_pin`, `meas_instrument_channel`, `meas_net`, `meas_spec_ref`
  — null. These light up when you graduate to Tier 2 (add a station +
  product + fixture). Test bodies don't change at the transition.

## Graduating to Tier 2

1. Copy one of the `catalog/<vendor>/*.yaml` entries from
   `demo/advanced/catalog/` (or author your own).
2. Add `stations/<id>.yaml` with a `catalog_ref:` pointing at it.
3. Delete the `dmm` / `psu` fixtures from this `conftest.py` — the
   plugin auto-registers them from the station.
4. Run with `--station=<id>` (or set a `default_station:` in
   `litmus.yaml`).

When you want product-backed spec derivation, add
`products/<id>.yaml` and swap `limits: {low/high}` for
`limits: {<name>: {characteristic: <char>, tolerance_pct: N}}`. Again
the test bodies are untouched.

`demo/pytest_native/` is the full Tier 2 example.
`demo/advanced/` is the full Tier 3/4 example with profiles,
multi-pin iteration, and production/characterization gating.
