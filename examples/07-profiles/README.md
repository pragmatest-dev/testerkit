# Stage 7 — Profiles with `extends:`

Separate the *scenario* (dev vs. production vs. characterization) from
the *bench fact* (which characteristic a test references). Each scenario
lives in its own file; shared behavior factors up through an
`extends:` chain.

## Diff from stage 6

- Added `profiles/rail_family.yaml` — a parent with no facets; shared
  parametrize sweep
- Added `profiles/development.yaml` — `extends: rail_family`, loose
  (±5 %) limits
- Added `profiles/production.yaml` — `extends: rail_family`, tight
  (±1 %) limits
- Added `profiles/characterization.yaml` — standalone, wide parametrize,
  no limits
- Stripped the per-test `parametrize` + `litmus_limits` out of the
  sidecar (`tests/test_rail.yaml`) for the rail tests — they come from
  the profile now. `TestIdle` still owns its limits in the sidecar
  because idle current doesn't vary by phase.

## Run it

Each scenario is selected by CLI facet:

```bash
cd examples/07-profiles

# Development: ±5 % limits, 3-row sweep
uv run pytest --test-phase=development -v

# Production: ±1 % limits, 3-row sweep inherited from rail_family
uv run pytest --test-phase=production -v

# Characterization: 8-row sweep, no limits (record-only)
uv run pytest --test-phase=characterization -v
```

## Why profiles

Scenarios diverge — dev is sloppy, production is strict,
characterization records everything without judgment — but the
**test code** is the same everywhere. A profile holds exactly the
bits that change per scenario. A scenario file is the diff reviewer's
unit: one file shows what "production" means differently from "dev."

`extends:` is single-parent inheritance. Walk parent-first, child
last-wins on conflicts. Parent-less profiles (like `rail_family`)
exist only as `extends:` targets — they don't match any CLI query.

## Facet resolution

- `--test-phase=X` is a CLI flag auto-synthesized from the
  `facets.test_phase` keys declared across the profiles.
- Exactly one profile must match the full facet query. Zero → error
  listing declared scenarios. More than one → error.
- CLI always wins. A profile sets scenario defaults; a CLI flag
  overrides them at run time.

## The road ahead

With all seven stages in place you've seen the full arc:

```
vanilla pytest
  → verify + Parquet
    → litmus_limits marker
      → sidecar markers
        → station + catalog
          → product + fixture connections
            → profiles + extends
```

Every layer you add is optional. A real project can sit at stage 4
forever if the DUT never grows; stage 7 unlocks scenario variation
when the product actually needs it.
