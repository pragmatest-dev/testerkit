# Tiers

Litmus projects grow in **tiers** — each tier adds one layer of
configuration without changing the layers below. A project starts at
the lowest tier that runs and moves up only when the next layer
solves a real problem. Test bodies stay unchanged across tiers; only
where data lives and who edits it changes.

| Tier | Scaffold via | What it adds | What's still in code |
|------|--------------|--------------|----------------------|
| **0** | `litmus init --tier bringup` | `verify` + `measure`/`observe` flow + parquet log. `conftest.py` provides `MagicMock`-shaped instrument fixtures. No YAML. | Drivers, mocks, limits — all inline in the test or `conftest.py`. |
| **1** | same as Tier 0 | A sidecar `<test_file>.yaml` next to each test carries limits (and `sweeps:` / `mocks:` / `retry:` if needed). Limits leave the body. | Drivers, mock-return-values, station identity. |
| **2** | `litmus init --tier bench` (or `--starter`) | Station YAML + part YAML + fixture YAML. Real driver classes resolved via the catalog. `conftest.py` shrinks. | Optional profile selection. |
| **3** | `litmus init --tier factory` | Named profiles under `profiles/*.yaml` with `extends:` chains. Per-phase (dev / production / characterization) limit + mock + station-type binding. Pick a profile with `pytest --test-phase=<facet>`. | — |
| **4** | same as Tier 3 | Multi-site orchestration, retest gates, characterization profiles with `verify_requires_limit: false`, lakehouse export. | — |

## How to graduate

Each tier is additive. To go from Tier 1 → Tier 2:

1. `litmus init --tier bench` in a sibling directory and copy the
   `stations/`, `parts/`, `fixtures/` layout it scaffolds.
2. Swap the sidecar `limits: {low, high, unit}` shape for
   `limits: {characteristic: <id>, tolerance_pct: N}`. The `low/high`
   form keeps working, but `characteristic:` lets the part YAML
   own the spec value.
3. Drop the conftest mock-return-values. Real driver classes in the
   catalog do the work; `--mock-instruments` swaps them for
   `MagicMock(spec=DriverClass)` at session start.

The bundled Tier 0/1 `tests/test_smoke.py` includes both an inline
form and a sidecar form so the migration shape is visible in one
file.

## When to stop

A project at Tier 1 with five tests and one bench is **done** — there
is no requirement to graduate. Don't add a `parts/` directory until
the test code starts wanting `tolerance_pct` overrides. Don't add
profiles until you have a real phase split.
