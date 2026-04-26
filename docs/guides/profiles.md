# Profiles — Named Config Sets

A **profile** is a named set of pytest overrides that applies across a
session. You select one per run: validation on Monday, production on
Tuesday, a quick debug profile for bench work. Profiles live as one file
per scenario under `profiles/*.yaml` (or inline under `litmus.yaml`) and
are selected by **facets** — `--test-phase=production --product=tps54302`
picks exactly one profile whose declared facets match.

Profiles speak the **same language as sidecars**: Litmus marker
fields (`limits`, `sweeps`, …) at the profile root and a recursive
`tests:` tree mirroring pytest's node-id structure (classes are
branches with their own marker fields plus nested `tests:`; functions
are leaves). If you already know how to write a sidecar, you already
know how to write a profile — same shape, session scope.

## Why profiles?

Hardware test suites run the same tree under many conditions:

- Validation: one voltage, one temperature, fail-fast, skip slow tests.
- Production: full vectors, corner temperatures, retries on flaky cases.
- Debug: single test, verbose, `-x -vv`.

Neither CLI flags nor the per-module sidecar fit this:

- CLI flags are ephemeral and can't declare per-test overrides.
- Sidecars are code-adjacent: one set per test module, versioned with
  the test.

Profiles sit between those two — versioned YAML, session-wide,
overlaid on top of sidecars.

## Selecting a profile

Profiles declare facets; CLI flags query facets.

```bash
pytest --test-phase=validation                   # one facet
pytest --test-phase=production --product=tps54302   # two facets
pytest --test-phase=production --mock-instruments   # facet + other flags
pytest                                           # no facets → baseline
pytest --litmus-profile=validation               # name-based escape hatch
```

Litmus auto-synthesizes a `--<facet>=<value>` CLI flag for every facet
key declared under any profile's `facets:` block. The facet query must
match exactly one profile:

- Zero matches → `UsageError` listing declared facet combinations.
- More than one match → `UsageError` (tighten the query).

`--litmus-profile=<name>` is an escape hatch: it selects by profile name
regardless of facets. Facet flags passed alongside cross-check against
the named profile's declared facets and error on mismatch.

## Profile shape

A profile is a `TestEntry` (same shape sidecars use) plus three
profile-only fields:

| Field         | Type                                 | Purpose                                     |
|---------------|--------------------------------------|---------------------------------------------|
| `facets`      | `dict[str, str]`                     | Exact-match keys for CLI selection          |
| `extends`     | `str \| None`                        | Parent profile name (single parent)         |
| `description` | `str \| None`                        | Shown in `litmus show <run_id>`             |
| `runner`      | `dict[str, Any]`                     | Opaque per-runner block (validated by plugin) |
| `limits` / `sweeps` / `mocks` / `specs` / `connections` / `retry` / `prompts` | (Litmus marker fields)               | Applied to **every** test in the session    |
| `tests`       | `dict[str, TestEntry]`               | Per-class / per-test overrides (recursive)  |

Litmus marker fields live directly on the profile. Ecosystem markers
(`flaky`, `skipif`, `parametrize`, …) live under `runner.markers:`;
the active runner plugin applies them.

```yaml
limits:                                    # applies to every test
  v_rail: {tolerance_pct: 5.0}
sweeps:                                    # nested loops
  - {vin: [4.5, 5.0, 5.5]}
runner:
  addopts: "--strict-markers"
  markers:
    - flaky: {reruns: 2, reruns_delay: 1}
    - skipif: "not os.getenv('HAS_BENCH')"
```

Per-test keys disambiguate by class when a file has two classes with
the same method name:

```yaml
tests:
  TestRails.test_rail:     # qualified — binds to TestRails.test_rail
    limits:
      v_rail: {tolerance_pct: 1.0}
  test_standalone:         # bare — binds to module-level test_standalone
    runner:
      markers:
        - skip: "bench required"
```

The qualified form wins over the bare shorthand when both are present.

## Scenario file per combination

One file per facet combination lives under `profiles/`. Each file's
**stem** becomes the profile name:

```
project/
├── litmus.yaml
├── profiles/
│   ├── power_family.yaml              # family base (no facets → unselectable)
│   ├── production-tps54302.yaml       # extends: power_family
│   ├── production-tps54303.yaml       # extends: power_family
│   └── characterization.yaml          # standalone
├── products/
├── stations/
└── tests/
```

The loader reads **both** `litmus.yaml`'s inline `profiles:` block and
every `profiles/*.yaml` file. A name conflict across the two sources
raises `UsageError` at project load.

Families are just parent profiles with no `facets:` block — reachable
only as an `extends:` target, never selectable from the CLI.

## `extends:` chain

A child profile inherits from a single parent via `extends:`. Chains
walk parent-first; child overrides last-wins on same marker name +
first key:

```yaml
# profiles/power_family.yaml — shared base, unselectable directly
description: "Shared base for all tps5430x power converters"
runner:
  addopts: "--strict-markers"
tests:
  TestRails.test_rail:
    limits: {v_rail: {low: 3.2, high: 3.4}}
  TestRails.test_output:
    sweeps:
      - {load: [0.1, 0.5, 0.9]}
```

```yaml
# profiles/production-tps54302.yaml
facets: {test_phase: production, product: tps54302}
extends: power_family
tests:
  TestRails.test_rail:
    limits: {v_rail: {low: 3.25, high: 3.35}}     # tightens family
```

```yaml
# profiles/production-tps54303.yaml
facets: {test_phase: production, product: tps54303}
extends: power_family
# inherits family limits; no per-variant trim
```

```yaml
# profiles/characterization.yaml — wide sweep, no limits
facets: {test_phase: characterization}
tests:
  TestRails.test_rail:
    sweeps:
      - {vin: [3.0, 3.3, 3.6, 4.0, 4.5, 5.0, 5.5, 6.0]}
```

`pytest --test-phase=production --product=tps54302` resolves:

- Single match: `production-tps54302`.
- Chain walked parent-first: `power_family` → `production-tps54302`.
- `test_rail` limits: child's `{low: 3.25, high: 3.35}` wins over
  parent's `{low: 3.2, high: 3.4}`.
- `runner.addopts: "--strict-markers"` inherited from family.

Cycles and unknown parents raise `UsageError` at project load.

## Merge order (least → most specific)

```
project defaults (litmus.yaml)
    ↓
file-level sidecar marker fields (at sidecar root)
    ↓
class-branch sidecar marker fields (tests.<Cls>.<marker>)
    ↓
per-test sidecar marker fields (tests.<name>.<marker>
                                or tests.<Cls>.tests.<method>.<marker>)
    ↓
per-test inline @decorators
    ↓
selected profile chain (parent first, child last)
    ↓
CLI flags
```

Same rule at every level: later marker with the same name + key wins on
overlap; non-overlapping keys pass through. Exactly one profile is
selected per run; its `extends:` chain flattens before merging into the
cascade above. CLI always wins.

## Test phase and mocks

`test_phase` is the conventional facet key for deployment stage
(`validation` / `production` / `characterization`). Pass
`--test-phase=production` to select a profile whose
`facets: {test_phase: production}` matches. The raw CLI value is used
for profile selection regardless of the run environment.

**Run record stamp.** A dirty git tree or `--mock-instruments` demotes
the recorded `test_phase` stamp to `development` — the profile still
applies (limits, markers, fixtures all fire as production), but the
Parquet row is stamped `development` so production dashboards never
treat it as a real production run. The `profile_facets` column on the
same row holds the raw CLI facet dict for reproducibility.

## Worked example

```yaml
# litmus.yaml — inline profiles (small projects)
name: power_board_project
default_station: bench_1
```

```yaml
# profiles/validation.yaml — quick pre-merge sweep
description: "Quick sweep for pre-merge validation"
facets: {test_phase: validation}
runner:
  addopts: "-x -vv"
  markexpr: "not slow and not hardware"
tests:
  TestRails:
    tests:
      test_rails:
        sweeps:
          - {vin: [5.0]}
          - {temperature: [25]}
  TestSlow:
    tests:
      test_long_soak:
        runner:
          markers:
            - skip: "not run in validation"
```

```yaml
# profiles/production.yaml — full sweep, retries
description: "Full sweep, production-grade retries"
facets: {test_phase: production}
runner:
  addopts: "--reruns=2 --reruns-delay=1 -n=4"
tests:
  TestRails:                              # class branch
    runner:
      markers:
        - flaky: {reruns: 2, reruns_delay: 2}   # class-wide retries
    tests:
      test_rails:                         # nested method
        sweeps:
          - {vin: [4.5, 5.0, 5.5]}
          - {temperature: [25, 85]}
          - {load: [0.1, 0.4, 0.8]}
```

```yaml
# profiles/debug.yaml — single test, verbose, fail-fast
description: "Single test, verbose, fail-fast"
facets: {test_phase: debug}
runner:
  addopts: "-x -vv -s"
  keyword: "test_output_voltage"
```

## Provenance

The active profile name is recorded on the run as `profile=<name>` and
shows up in `litmus show <run_id>`. The `profile_facets` column on the
Parquet row holds the raw CLI facet dict. Combined with the git commit,
that's the minimum reproducibility payload: re-run at the same SHA with
the same facet flags and the same profile chain resolves.

## Non-goals (today)

- Wildcards / globs on facet values. Exact-match only. Family sharing
  goes through `extends:`, not `product: "tps5430*"`.
- Multi-parent `extends:`. Single parent per profile; chains are linear.
- Multi-match facet composition. Exactly one profile must match the
  query. Ambiguous = `UsageError`.
- Per-directory profile stacking. `profiles/*.yaml` is flat.
- Runtime profile switching mid-session. Session-scoped.
- Marker *removal* in child profiles. Child overrides by replacement;
  no negative markers.

## See also

- `docs/reference/configuration.md` — full `profiles:` schema
- `docs/guides/writing-tests.md` — sidecar and marker mechanics
- Pytest plugins commonly combined with profiles:
  - [pytest-rerunfailures](https://github.com/pytest-dev/pytest-rerunfailures)
  - [pytest-xdist](https://github.com/pytest-dev/pytest-xdist)
  - [pytest-timeout](https://github.com/pytest-dev/pytest-timeout)
