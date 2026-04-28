# Profiles

A **profile** is a named set of session-level overrides declared under
`profiles:` in `litmus.yaml` and/or one file per profile under
`profiles/*.yaml`. Selection is by **facet query** from the CLI; a
profile declares the facet combination it represents.

Profiles share the **same flat marker-scope shape as sidecars**. One
vocabulary across inline `@pytest.mark.litmus_*` decorators, sidecar
YAML, and profile YAML. Litmus marker fields (`limits`, `sweeps`,
`mocks`, `specs`, `connections`, `retry`, `prompts`) live at the
entry root alongside the reserved `runner:` and `tests:` keys.

## Shape

```yaml
# profiles/<name>.yaml — or inline under litmus.yaml: profiles: <name>:
description: string                 # optional; shown in litmus show
facets: {key: value, ...}           # exact-match keys for CLI selection
extends: <parent_profile_name>      # optional single parent

# Phase wiring — both optional; bind a station-type and/or fixture
station_type: <type_id>             # active station must comport (see Cross-checks)
fixture: <fixture_id>               # fixture id; CLI --fixture wins on conflict

runner:                             # opaque; the active runner plugin owns its schema
  addopts: string                   # appended to PYTEST_ADDOPTS pre-collection
  markexpr: string                  # like -m
  keyword: string                   # like -k
  markers:                          # ecosystem markers per scope (flaky, skip, parametrize, …)
    - flaky: {reruns: 2}

# Litmus marker fields apply to every test in the session
limits:
  v_rail: {tolerance_pct: 5.0}

tests:                              # recursive tree mirroring pytest node ids
  TestRails:                        # class branch
    sweeps:                         # applied to every method of TestRails
      - {vin: [4.5, 5.0, 5.5]}
    tests:
      test_rail:                    # nested method (leaf)
        limits:
          v_rail: {low: 3.25, high: 3.35}
  test_standalone:                  # module-level test (leaf)
    runner:
      markers:
        - skip: "bench required"
```

`runner:` and `tests:` are reserved at every level; everything else
is a Litmus marker name. `extra="forbid"` rejects typos at YAML load.

## Facet selection

Litmus auto-synthesizes a `--<facet>=<value>` CLI flag for every facet
key any profile declares:

```bash
pytest --test-phase=production --product=tps54302     # two facets
pytest --test-phase=characterization                   # one facet
pytest                                                 # no profile → baseline
pytest --litmus-profile=<name>                         # name-based escape hatch
```

Facet query must match exactly one profile:
- Zero matches → `UsageError` listing declared facet combinations.
- More than one match → `UsageError` (tighten the query).

`--litmus-profile=<name>` bypasses facet matching. Facet flags passed
alongside cross-check against the named profile's declared facets.

## `extends` chain

A child profile inherits from a single parent via `extends:`. The chain
walks parent-first; child fields last-wins on overlap (per measurement
name for `limits`, per target for `mocks`, etc).

```yaml
# profiles/power_family.yaml — parent; no facets → unselectable directly
description: "Shared base for all tps5430x power converters"
tests:
  TestRails.test_rail:
    limits:
      v_rail: {low: 3.2, high: 3.4}
```

```yaml
# profiles/production-tps54302.yaml — child
facets: {test_phase: production, product: tps54302}
extends: power_family
tests:
  TestRails.test_rail:
    limits:
      v_rail: {low: 3.25, high: 3.35}   # tightens family
```

Cycles and unknown parents → `UsageError` at project load.

## Filesystem layout

```
project/
├── litmus.yaml                 # project defaults; can also declare profiles inline
├── profiles/                   # one file per profile; stem = profile name
│   ├── power_family.yaml
│   ├── production-tps54302.yaml
│   └── characterization.yaml
└── tests/
```

Inline `litmus.yaml: profiles:` and `profiles/*.yaml` are both read.
Name conflict → `UsageError` at project load.

## Phase wiring (`station_type` + `fixture`)

A profile can bind both **which station-type layout it expects** and
**which fixture it uses**. Selecting `--test-phase=production` then
sets the limits, the required station type, and the fixture in one
flag — the operator doesn't have to remember a matching `--fixture=...`
each run.

```yaml
# profiles/production.yaml
facets: {test_phase: production}
station_type: production_bench    # active station must comport
fixture: buck_3v3_production      # fixture id; CLI --fixture wins on conflict
limits: { ... }
```

```yaml
# stations/types/production_bench.yaml — abstract type definition
id: production_bench
description: "Production bench layout"
instruments:
  dmm: {type: DMM, driver: ...}
  psu: {type: PSU, driver: ...}

# stations/bench_07.yaml — concrete instance, declares the type
id: bench_07
name: "Bench 7"
station_type: production_bench
hostname: bench07.lab.example     # optional; enables auto-match
instruments: { ... }              # actual driver/resource per role

# fixtures/buck_3v3_production.yaml — declares which types it works on
id: buck_3v3_production
product_id: buck_3v3
station_types: [production_bench] # cross-checked vs. profile.station_type
connections: { ... }
```

### Why types, not concrete stations

Profiles bind `station_type` (the layout *contract*), not a concrete
station id. Same `production` profile runs on `bench_07`, `bench_08`,
`bench_09` — they all comport with `production_bench`. Binding a
profile to a single bench would make it unportable.

### Hostname auto-match

When a station declares `hostname:`, the session-start resolver tries
`socket.gethostname()` against every station's hostname before
falling back to `ProjectConfig.default_station`. Operators on the
matching bench skip the `--station=<id>` boilerplate. Resolution
chain (first match wins):

1. `--station-config=<path>` (explicit)
2. `--station=<id>` (explicit)
3. Hostname auto-match
4. `ProjectConfig.default_station`
5. `None` — bringup tier without a station

### Cross-checks at session start

After station + fixture + profile resolve, four checks fire (each
raising `pytest.UsageError` on failure, no-op when fields aren't set):

1. **Compliance.** Active station's declared instruments must cover
   every role the matching `StationType` requires.
2. **Profile → station type.** Profile's `station_type` must equal
   active station's `station_type`.
3. **Profile → fixture compatibility.** Profile's `station_type`
   must appear in active fixture's `station_types: [...]`.
4. **CLI fixture override + profile fixture conflict.** CLI wins;
   warning emitted (explicit beats declarative).

### Run-record stamps

Each test run stamps `TestRun.station_type` and `TestRun.fixture_id`
along with the existing `station_id`, `station_hostname`, `profile`,
and `profile_facets` columns. Analytics like "first-pass yield across
all `production_bench` runs" don't need to join on the live station
YAMLs — the type label is snapshot per-run.

## Litmus marker fields

Litmus marker fields live at the root of any marker scope (sidecar
file, profile, class branch, per-test entry):

| Field           | Shape                              | Inline equivalent                                 |
|-----------------|------------------------------------|---------------------------------------------------|
| `limits`        | dict by measurement name           | `@pytest.mark.litmus_limits(v_rail={...})`        |
| `sweeps`        | list of axis-group dicts           | `@pytest.mark.litmus_sweeps([{vin: [...]}])`      |
| `mocks`         | list of patch-object dicts         | `@pytest.mark.litmus_mocks([{target: ...}])`      |
| `specs`         | list of characteristic IDs         | `@pytest.mark.litmus_characteristics(["rail_3v3"])`         |
| `connections`   | singleton dict                     | `@pytest.mark.litmus_connections(...)`            |
| `retry`         | singleton dict                     | `@pytest.mark.litmus_retry(max_attempts=3)`       |
| `prompts`       | dict by prompt name                | `@pytest.mark.litmus_prompts(setup={...})`        |

Ecosystem markers (`flaky`, `skipif`, `parametrize`, `dependency`, …)
go under `runner.markers:` per scope. Each entry is a single-key
dict; the active runner plugin applies them.

## Per-test keys: qualified vs bare

Use the qualified form `TestClass.method` when the file has two classes
sharing a method name; use the bare method name for unambiguous or
module-level tests. Qualified form wins over bare when both are present.

## Merge order (least → most specific)

```
file-level sidecar fields
  → class-branch sidecar fields (tests.<Cls>.<field>)
    → per-test sidecar fields (tests.<name>.<field> or nested)
      → per-test inline @decorators
        → selected profile chain (parent first, child last)
          → CLI flags
```

Same rule at every level: later value wins on overlap (per measurement
name, per mock target, etc.); non-overlapping passes through. CLI
always wins.

## `test_phase` convention

`test_phase` is the conventional facet key for deployment stage
(`validation` / `production` / `characterization`). The raw
`--test-phase=...` CLI value is used for profile selection. The
recorded `test_phase` **stamp** on the Parquet row is demoted to
`development` when the git tree is dirty or `--mock-instruments` is
active — the profile still applies fully, but the row is stamped
`development` so production dashboards never treat it as real. The
`profile_facets` column on the same row holds the raw CLI facet dict
for reproducibility.

## When to propose a profile

Create a profile when the user describes a **recurring lab condition**
distinguished by facet values ("validation", "production on tps54302",
"characterization sweep"). Prefer a profile over:

- Modifying the sidecar (affects all runs, not just this lab).
- Modifying test code (wrong layer).
- CLI aliases / shell scripts (profiles are versioned, discoverable,
  typed via `ProfileConfig`).

Do not create a profile to change a single knob a user sets once — use
a CLI flag or env var.

## Non-goals

- Wildcards / globs on facet values. Exact-match only.
- Multi-parent `extends:`. Single parent; chains are linear.
- Multi-match facet composition. Exactly one profile must match.
- Per-directory profile stacking. `profiles/*.yaml` is flat.
- Runtime profile switching. Session-scoped.
- Marker *removal* in child profiles. Override by replacement only.

## Cross-references

- `litmus/models/project.py` — `ProfileConfig`, `ProjectConfig`
- `litmus/models/test_config.py` — `TestEntry`, `SidecarConfig`
- `litmus/pytest_plugin/__init__.py` — marker injection
  (`_apply_cascade_to_items`, `_apply_entry_markers`)
- `litmus/execution/profiles.py` — `flatten_profile_chain`,
  `resolve_active_profile`, `PytestRunner`, `ProfileError`
- `docs/guides/profiles.md` — user-facing guide
