# Profiles

A **profile** is a named set of session-level overrides declared under
`profiles:` in `litmus.yaml` and/or one file per profile under
`profiles/*.yaml`. Selection is by **facet query** from the CLI; a
profile declares the facet combination it represents.

Profiles speak the **same markers/classes/tests language as sidecars**.
One vocabulary across inline `@pytest.mark.*` decorators, sidecar YAML,
and profile YAML.

## Shape

```yaml
# profiles/<name>.yaml — or inline under litmus.yaml: profiles: <name>:
description: string                 # optional; shown in litmus show
facets: {key: value, ...}           # exact-match keys for CLI selection
extends: <parent_profile_name>      # optional single parent

pytest:
  addopts: string                   # appended to PYTEST_ADDOPTS pre-collection
  markexpr: string                  # like -m
  keyword: string                   # like -k

markers:                            # applied to every test in the session
  - litmus_limits: {v_rail: {tolerance_pct: 5.0}}

classes:
  TestRails:
    markers:                        # applied to every method of TestRails
      - parametrize: ["vin", [4.5, 5.0, 5.5]]

tests:
  TestRails.test_rail:              # qualified — disambiguates across classes
    markers:
      - litmus_limits: {v_rail: {low: 3.25, high: 3.35}}
  test_standalone:                  # bare — module-level test
    markers:
      - skip: "bench required"
```

Every entry in a `markers:` list is one pytest marker — identical shape
whether it appears inline, in a sidecar, or in a profile.

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
walks parent-first; child markers last-wins on same marker name + first
key (measurement name for `litmus_limits`, target for `litmus_mock`, etc).

```yaml
# profiles/power_family.yaml — parent; no facets → unselectable directly
description: "Shared base for all tps5430x power converters"
tests:
  TestRails.test_rail:
    markers:
      - litmus_limits: {v_rail: {low: 3.2, high: 3.4}}
```

```yaml
# profiles/production-tps54302.yaml — child
facets: {test_phase: production, product: tps54302}
extends: power_family
tests:
  TestRails.test_rail:
    markers:
      - litmus_limits: {v_rail: {low: 3.25, high: 3.35}}   # tightens family
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

## Marker spec shapes

One entry per line; mirrors `@pytest.mark.<name>(...)`:

| YAML                                 | Marker                                    |
|--------------------------------------|-------------------------------------------|
| `- flaky`                            | `@pytest.mark.flaky`                      |
| `- skip: "reason"`                   | `@pytest.mark.skip("reason")`             |
| `- skipif: "condition expr"`         | `@pytest.mark.skipif(...)`                |
| `- parametrize: ["vin", [4.5, 5.0]]` | `@pytest.mark.parametrize("vin", [...])`  |
| `- flaky: {reruns: 2, delay: 1}`     | `@pytest.mark.flaky(reruns=2, delay=1)`   |
| `- litmus_limits: {v_rail: {...}}`   | `@pytest.mark.litmus_limits(v_rail={...})`|

Ecosystem markers (`flaky`, `skipif`, `parametrize`, `dependency`, …)
work out of the box — each plugin's native handler fires because Litmus
attaches the marker to the item.

## Per-test keys: qualified vs bare

Use the qualified form `TestClass.method` when the file has two classes
sharing a method name; use the bare method name for unambiguous or
module-level tests. Qualified form wins over bare when both are present.

## Merge order (least → most specific)

```
file-level sidecar markers
  → class-scoped sidecar markers (classes.<Cls>.markers)
    → per-test sidecar markers (tests.<name>.markers)
      → per-test inline @decorators
        → selected profile chain (parent first, child last)
          → CLI flags
```

Same rule at every level: later marker with the same name + first key
wins on overlap; non-overlapping passes through. CLI always wins.

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

- `litmus/models/project.py` — `ProfileConfig`, `ProfilePytest`,
  `ProjectConfig`
- `litmus/config/test_config.py` — `MarkerSpec`, `ClassMarkers`,
  `TestMarkers`, `SidecarConfig`
- `litmus/execution/plugin.py` — `_resolve_active_profile`,
  `_flatten_profile_chain`, `_profile_markers_for_item`,
  `_collect_profile_facet_keys`, `_collect_facet_flags_from_config`
- `docs/guides/profiles.md` — user-facing guide
