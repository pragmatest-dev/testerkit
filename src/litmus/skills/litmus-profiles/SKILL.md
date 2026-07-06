---
name: litmus-profiles
description: Use when a user wants different limits, sweeps, mocks, or fixture/station wiring depending on test phase — e.g. looser limits in validation vs tight in production, or a per-part variant — selected with a CLI flag at run time.
---

# Phase-varying test behavior with profiles

A **profile** is a named set of session-level overrides, selected by a
facet flag at run time. Reach for one only when the request is a
**recurring lab condition** ("production", "characterization on
tps54302") — not a one-off knob (use a CLI flag) and not a permanent
change (edit the sidecar or the test).

## 1. Write the profile

`profiles/<name>.yaml` — same field vocabulary as a sidecar (`limits`,
`sweeps`, `mocks`, `specs`, `connections`, `retry`, `prompts`; see
`litmus-tests`), plus profile-only `description` / `facets` / `extends`:

```yaml
# profiles/production.yaml
description: "Production-floor limits, tighter than validation"
facets: {test_phase: production}

limits:                       # applies to every test in the session
  v_rail: {tolerance_pct: 5.0}

tests:                        # per-class / per-test overrides
  TestRails:
    sweeps:
      - {vin: [4.5, 5.0, 5.5]}
    tests:
      test_rail:
        limits:
          v_rail: {low: 3.25, high: 3.35}
```

`tests:` mirrors the pytest node tree (class → method); use the bare
method name unless two classes in the file share a method name, then
qualify with `TestClass.method`.

## 2. Select it

Every facet key any profile declares gets an auto-synthesized
`--<facet>` flag:

```bash
pytest --test-phase=production         # facet query
pytest --test-phase=characterization --part=tps54302   # two facets
pytest --test-profile=production       # name-based escape hatch
pytest                                  # no profile → baseline
```

Zero facet matches → `UsageError` listing the declared combinations.
More than one match → `UsageError` (tighten the query). `--test-profile`
bypasses facet matching entirely; if facet flags are also passed, they
must cross-check against that profile's declared facets.

## 3. Share a base with `extends`

```yaml
# profiles/power_family.yaml — parent; no facets, so unselectable directly
tests:
  TestRails.test_rail:
    limits:
      v_rail: {low: 3.2, high: 3.4}
```

```yaml
# profiles/production-tps54302.yaml — child
facets: {test_phase: production, part: tps54302}
extends: power_family
tests:
  TestRails.test_rail:
    limits:
      v_rail: {low: 3.25, high: 3.35}   # tightens the family
```

Single parent per profile, walked parent-first, child wins last on
overlap. A cycle or an unknown `extends:` target raises at project load.

## 4. Wire a station type + fixture (optional)

A profile can also bind the station layout and fixture a phase expects,
so one flag sets limits *and* the bench wiring:

```yaml
# profiles/production.yaml
facets: {test_phase: production}
station_type: production_bench    # active station must match this type
fixture: buck_3v3_production      # CLI --fixture wins on conflict
```

Four checks fire at session start (each a `UsageError` if it fails, no-op
if the fields are unset): station covers every role its type requires;
profile's `station_type` matches the active station's; profile's
`station_type` appears in the fixture's `station_types:`; a CLI
`--fixture` conflicting with the profile's `fixture:` wins with a warning.

## Cascade — where a profile sits

Merge order, least → most specific, last-wins per field:

```
sidecar (file → class → leaf) → profile (root → class → leaf) → CLI flags
```

A profile always overrides the sidecar for the same field — it's the
top of the stack, not a peer. See `litmus-mocks` for the same rule
applied to `mocks:`.

## Run it, then validate

```bash
pytest --test-phase=production
litmus validate                 # profiles/*.yaml against ProfileConfig
```

A profile listing a `tests:` key that matches no collected test is a
silent no-op on that key — Litmus warns loudly at session start rather
than failing, so watch the warning after adding or renaming a test.

## Best-practice defaults
- Create a profile for a **recurring facet** (phase, part variant),
  never for a single knob a user sets once — that's a CLI flag.
- Put shared limits in a parent via `extends:`; leaf profiles hold deltas.
- Bind `station_type`/`fixture` when a phase always runs on one bench
  layout — don't make the operator remember a matching `--fixture`.
- Prefer facet flags over `--test-profile=<name>` for anything an
  operator selects routinely — the name is an escape hatch.

## Deeper
Read the docs:
```bash
litmus docs show how-to/execution/profiles
```
Sibling skills: `litmus-tests` (sidecar layer, verb choice), `litmus-mocks`
(per-test mock cascade), `litmus-stations` (station types), `litmus-sites`
(multi-site execution).
