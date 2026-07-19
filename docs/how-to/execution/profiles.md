# Profiles — Named Config Sets

A **profile** is a named set of overrides that applies across a
session. You select one per run: validation on Monday, production on
Tuesday, a quick debug profile for bench work. Profiles live as one file
per scenario under `profiles/*.yaml` (or inline under `testerkit.yaml`) and
are selected by matching selection tags — `--test-phase=production --part=tps54302`
picks exactly one profile whose declared `facets:` tags match.

Profiles speak the **same language as sidecars**: TesterKit marker
fields (`limits`, `sweeps`, …) at the profile root and a recursive
`tests:` tree mirroring pytest's node-id structure (classes are
branches with their own marker fields plus nested `tests:`; functions
are leaves). If you already know how to write a sidecar, you already
know how to write a profile — same shape, session scope.

## Create and run a profile

1. Add `profiles/validation.yaml` with a `facets:` line and your
   overrides:

   ```yaml
   facets: {test_phase: validation}
   runner:
     addopts: "-x -vv"
   ```

2. Run `pytest --test-phase=validation`.

The file's stem (`validation`) is the profile name. The `facets:` block
declares the selection tags that `--test-phase=validation` must match.
Bare `pytest` with no flags runs on `default_profile` from `testerkit.yaml`
(or no profile if none is declared).

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

Profiles declare selection tags under `facets:`; CLI flags query those tags.

```bash
pytest --test-phase=validation                   # one tag
pytest --test-phase=production --part=tps54302   # two tags
pytest --test-phase=production --mock-instruments   # tag + other flags
pytest                                           # no tags → baseline
pytest --test-profile=validation               # select by name directly (bypassing facet matching)
```

TesterKit auto-synthesizes a `--<key>=<value>` CLI flag for every `facets:`
key declared under any profile. The query must match exactly one profile:

- Zero or multiple matches → the run errors and lists the declared facet combinations.

`--test-profile=<name>` selects by profile name directly, bypassing facet
matching. Facet flags passed alongside cross-check against the named
profile's declared facets and error on mismatch.

## Profile shape

A profile uses the same per-test config shape as a sidecar (`TestEntry`)
plus these profile-only fields:

| Field                | Type                                 | Purpose                                     |
|----------------------|--------------------------------------|---------------------------------------------|
| `facets`             | `dict[str, str]`                     | Exact-match selection tags for CLI          |
| `extends`            | `str \| None`                        | Parent profile name (single parent)         |
| `description`        | `str \| None`                        | Documentation in the YAML; not displayed by `testerkit show` |
| `station_type`       | `str \| None`                        | Require a matching station type at session start |
| `fixture`            | `str \| None`                        | Declare the fixture this profile targets    |
| `verify_requires_limit` | `bool \| None`                    | When `false`, `verify()` records without judging when no limit resolves |
| `runner`             | `dict[str, Any]`                     | Runner-specific options (e.g. pytest `addopts`, ecosystem markers) |
| `limits` / `sweeps` / `mocks` / `characteristics` / `connections` / `retry` / `prompts` | (TesterKit marker fields) | Applied to **every** test in the session |
| `tests`              | `dict[str, TestEntry]`               | Per-class / per-test overrides (recursive)  |

TesterKit marker fields live directly on the profile. Ecosystem markers
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
  TestRails.test_rail:     # qualified — matches TestRails.test_rail
    limits:
      v_rail: {tolerance_pct: 1.0}
  test_standalone:         # bare — matches module-level test_standalone
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
├── testerkit.yaml
├── profiles/
│   ├── power_family.yaml              # family base (no facets → unselectable)
│   ├── production-tps54302.yaml       # extends: power_family
│   ├── production-tps54303.yaml       # extends: power_family
│   └── characterization.yaml          # standalone
├── parts/
├── stations/
└── tests/
```

The loader reads **both** `testerkit.yaml`'s inline `profiles:` block and
every `profiles/*.yaml` file. A name conflict across the two sources
raises an error at project load.

Families are just parent profiles with no `facets:` block — reachable
only as an `extends:` target, never selectable from the CLI.

## `extends:` chain

A child profile inherits from a single parent via `extends:`. Child
values override the parent on the same key; everything else is inherited.
For the full merge order across sidecars, decorators, profiles, and CLI,
see [writing-tests.md](writing-tests.md).

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
facets: {test_phase: production, part: tps54302}
extends: power_family
tests:
  TestRails.test_rail:
    limits: {v_rail: {low: 3.25, high: 3.35}}     # tightens family
```

```yaml
# profiles/production-tps54303.yaml
facets: {test_phase: production, part: tps54303}
extends: power_family
# inherits family limits; no per-variant trim
```

```yaml
# profiles/characterization.yaml — wide sweep, record-only
facets: {test_phase: characterization}
verify_requires_limit: false   # verify() records without judging when no limit resolves
tests:
  TestRails.test_rail:
    sweeps:
      - {vin: [3.0, 3.3, 3.6, 4.0, 4.5, 5.0, 5.5, 6.0]}
```

`verify_requires_limit: false` flips `verify()` to record-only when no limit resolves from any source — the same test bodies that judge in `production` record values in `characterization` without failing. Default is to require a limit.

`pytest --test-phase=production --part=tps54302` resolves:

- Single match: `production-tps54302`.
- Chain walked parent-first: `power_family` → `production-tps54302`.
- `test_rail` limits: child's `{low: 3.25, high: 3.35}` wins over
  parent's `{low: 3.2, high: 3.4}`.
- `runner.addopts: "--strict-markers"` inherited from family.

Cycles and unknown parents raise an error at project load.

## Test phase and mocks

`test_phase` is the conventional `facets:` key for deployment stage
(`validation` / `production` / `characterization`). Pass
`--test-phase=production` to select a profile whose
`facets: {test_phase: production}` matches.

**Run record stamp.** A dirty git tree or `--mock-instruments` demotes
the recorded `test_phase` stamp to `development` — the profile still
applies (limits, markers, fixtures all fire as production), but the
run record is stamped `development` so production dashboards never
treat it as a real production run. The raw CLI facet dict is recorded
as run metadata (`profile_facets_json`) for reproducibility.

## Worked example

```yaml
# testerkit.yaml — inline profiles (small projects)
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

The active profile name is recorded on the run record (`TestRun.profile`).
The raw CLI facet dict is recorded as run metadata (`profile_facets_json`)
in the Parquet file. Combined with the git commit, that's the minimum
reproducibility payload: re-run at the same SHA with the same facet flags
and the same profile chain resolves.

## Limits of profile selection

- Wildcards / globs on facet values. Exact-match only. Family sharing
  goes through `extends:`, not `part: "tps5430*"`.
- Multi-parent `extends:`. Single parent per profile; chains are linear.
- Multi-match facet composition. Exactly one profile must match the
  query. Ambiguous queries error and list declared facet combinations.
- Per-directory profile stacking. `profiles/*.yaml` is flat.
- Runtime profile switching mid-session. Session-scoped.
- Marker *removal* in child profiles. Child overrides by replacement;
  no negative markers.

## See also

- `docs/reference/configuration.md` — full `profiles:` schema
- [writing-tests.md](writing-tests.md) — sidecar and marker mechanics
- Pytest plugins commonly combined with profiles:
  - [pytest-rerunfailures](https://github.com/pytest-dev/pytest-rerunfailures)
  - [pytest-xdist](https://github.com/pytest-dev/pytest-xdist)
  - [pytest-timeout](https://github.com/pytest-dev/pytest-timeout)
