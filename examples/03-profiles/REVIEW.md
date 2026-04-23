# Advanced Demo — Ergonomics Review

Notes gathered while building `examples/03-profiles/` (PMIC-A23, 14 tests across
power-on / multi-pin rails / regulation sweeps; default + production +
characterization profiles all green). Scope: UX of the pin / profile /
decorator decisions as a user lives with them for a non-trivial product.

## What worked

1. **No pins, IDs, or limits in test code.** Every test body reads as
   `setup → measure → verify("label", value)`. Writing `test_rails.py`
   took three lines and works for any product whose fixture routes the
   `rail_voltage_trio` pins to a DMM.
2. **`ctx.points` is invisible to the test.** Multi-pin iteration is
   one `for _ in ctx.points:` loop; no pin list, no channel switching,
   no routing code. The framework does all of it via
   `_active_point_var` + `FixtureManager.route`.
3. **Zero custom markers needed.** Full feature coverage with only
   `@pytest.mark.flaky` (pytest-rerunfailures) and the optional
   `@pytest.mark.litmus_limits` for per-test limit injection. Users
   stay in pytest muscle memory — nothing Litmus-specific to learn.
4. **Station `mock_config` as sensible defaults.** Letting the station
   YAML declare a plausible "nominal operation" reading meant most
   tests needed no per-test mock override — one less thing to think
   about when authoring.
5. **Profile vector collapse is a one-liner.** Production profile
   shrank the 5-point line-regulation sweep to a single nominal point
   by declaring one key. Same YAML format at profile and sidecar tier.
6. **Catalog `catalog_ref:` keeps station YAML thin.** Station just
   binds role → driver + catalog_ref; channels/terminals/connectors
   live once in `catalog/{vendor}/{model}.yaml`.

## Friction points

### 1. Spec-condition keys must also be vector keys — silent failure

The `quiescent_current` char has
`specs: [- when: {load: {min: 0, max: 0}}]`. My test for it had no
`load` in its vectors. Result: `ValueError: No spec band matches: .`

The failure is correct, but the contract is invisible from the test
file. Nothing in `test_quiescent_current` hints that it depends on a
`load` key existing in its vector params. A user reading the test
can't see why a `load: 0.0` vector entry is mandatory.

**Proposal.** On collection, when a test binds a `characteristic`
whose SpecBands are condition-gated, validate that every `when.*` key
is covered by the test's vector params. If not, raise
`pytest.UsageError` at collection time with the specific missing keys,
not `ValueError` at measure time. This turns a runtime mystery into a
config error before the test runs.

### 2. `mocks` is declared per-test in the model but only consumed at file scope

`TestConfig.mocks: dict[str, Any] | None` exists at
`config/test_config.py:620`. But the plugin autouse fixture
(`plugin.py:3496-3519`) only reads top-level `sidecar["mocks"]`. Nested
`tests.<method>.mocks` is silently ignored.

I hit this trying to mock different `dmm.measure_dc_voltage` returns
per test. Fell back to `pytest-mock`'s `mocker.patch.object` on each
method.

**Proposal.** Either (a) make `_litmus_apply_mocks` merge
`tests.<method>.mocks` on top of top-level `mocks:` (same precedence
rules as limits today), or (b) drop the field from `TestConfig` since
it's unused. Plan says mocks should follow the same tiered shape as
limits — today's behavior diverges silently.

### 3. Two sidecar shapes — top-level vs `tests:`

The sidecar has `vectors.methods.<name>`, top-level `limits:`,
top-level `mocks:`, AND `tests.<method>.{characteristic,limits,...}`.
A user has to know:

- `vectors` — use `vectors.methods.<name>` (or `vectors.class` for
  class-level).
- `limits` — use top-level for shared, or nest under
  `tests.<method>.limits` to override per test.
- `characteristic` — MUST live under `tests.<method>`; no top-level.
- `mocks` — top-level only today (see above).

Five keys, three different rules.

**Proposal.** Unify under `tests.<method>.*` as the single per-test
block, with a top-level block that's strictly "shared across all
methods in the file." Same sub-shape at both tiers. Today's
`vectors.methods.<name>` → `tests.<name>.vectors` would close the
gap. This matches the `TestConfig` model (flat, same shape at every
tier) already documented in the plan.

### 4. Profile vector overrides key on full pytest node IDs

In `litmus.yaml` today:

```yaml
profiles:
  production:
    vectors:
      "tests/test_regulation.py::TestRegulation::test_line_regulation":
        vin: [5.0]
```

Compare the sidecar:

```yaml
vectors:
  methods:
    test_line_regulation:
      list:
        - {vin: 5.0}
```

The profile form is stringly-typed and path-sensitive — rename a file,
move a method between classes, and the profile silently no-ops. The
sidecar is scoped to its own file so just `method` is enough.

**Proposal.** Accept `{file}::{method}` or bare `{method}` forms in
profile keys, falling back to the latter when the file is
unambiguous. Alternatively, validate every profile key against
collected node IDs and fail loudly if a key matches nothing. Silent
no-op on a typo here is worse than a skipped mock — it's a production
screen that just stopped running.

### 5. `pytest.ini` + `litmus.yaml` `addopts` interplay

`pytest.ini` has:

```ini
addopts = --operator=demo_operator --dut-part-number=PMIC-A23 --dut-serial=PMIC-A23-001
```

`litmus.yaml` profiles have their own `pytest.addopts`. Which wins?
Where do `--mock-instruments` vs `--tb=short` combine? No error, no
warning, and no visible merged value in the session output.

**Proposal.** On profile activation, log the composed `addopts`
string exactly once at session start so the user can see what pytest
actually got. Cheap, no behavior change, removes the "which file owns
this flag" guessing game.

### 6. `SpecBand.when` is verbose for single-point conditions

Writing `{load: {min: 0, max: 0}}` for "at no-load" is heavier than
`{load: 0}`. The RangeSpec is the right shape when the spec really is
a band, but point conditions are common.

**Proposal.** Accept a bare scalar in `when:` values and treat it as a
zero-width band at validation time. `{load: 0}` expands to
`{load: {min: 0, max: 0}}` in the Pydantic model. Purely cosmetic,
zero semantic change.

### 7. Driver package reuse needs `sys.path` boilerplate

`examples/03-profiles/conftest.py` only exists to prepend `examples/..` to
`sys.path` so `from examples.drivers import ...` works. Any user who
keeps their drivers in a sibling package to their tests hits the same
problem. A `pyproject.toml` with the project name solves it but
ceremonially — adds 10 lines and a `uv sync` before the first test
runs.

**Proposal.** Document the `conftest.py` trick prominently in the
"getting started" guide alongside the pyproject path. Both work; the
conftest is the fastest path from "I have a folder" to "I have green
tests." Today it's undocumented and users will either reinvent it or
conclude Litmus forces a package layout.

### 8. `verify` fixture has no public type

`def test_power_up(self, context, psu, verify):` — `verify` is a
callable but IDEs show it as `Any`. The `limits` fixture is in the
same boat.

**Proposal.** Export `VerifyFn`/`LimitsFn` Protocols (already implied
by the callable signatures) and annotate the fixtures. One-line
change per fixture; removes `Any` spam in every test file.

### 9. `logger.measure` vs `verify` — split is subtle

A new user sees two ways to log a measurement: one raises on limit
fail (`verify`), one doesn't (`logger.measure`). The difference is
entirely behavioral at fail time — both produce identical rows on
PASS. Documentation says "verify for screening, logger for
characterization"; easy to reach for the wrong one during a late-night
debug session.

**Proposal.** Nothing to change structurally — the two callables ARE
semantically distinct and collapsing them would lose the fail-raise
contract. But the ref docs should lead with a one-line decision
heuristic: "would a fail here stop the line? → `verify`. Else →
`logger.measure`." Put it at the top of `test-writing.md`.

### 10. No per-iteration mock control inside `ctx.points`

The multi-pin `test_rail_voltages` calls `dmm.measure_dc_voltage()`
three times (once per rail). The mock returns the same value each
call, so all three rails report `3.300 V` — fine for the demo but not
useful for exercising per-rail limits. Sidecar mocks are per-test,
not per-iteration.

**Proposal (non-blocking).** Let `mocks.<fixture>.<attr>` accept a
list; the Nth iteration of `ctx.points` returns `list[N % len]`. No
change to single-point tests. Makes multi-pin demos expressive
without a custom side_effect function.

## Highest-leverage fixes

Ordered by "pain per line of code to fix":

1. **(#1) Collection-time validation of spec-condition vs vector
   keys.** Turns a cryptic `ValueError` into a clean
   `pytest.UsageError` with the missing key named.
2. **(#2) Per-test `mocks:` — consume or delete the field.** The
   model already declares it; either wire it up or drop it.
3. **(#3) Unify the sidecar to one tiered shape.** Eliminates the
   "where does this key go?" lookup.
4. **(#8) Type the `verify` / `limits` fixtures.** Trivial change,
   removes `Any` everywhere.
5. **(#5) Log composed `addopts` at session start.** One print,
   removes config archaeology.

Items 4/6/7/9/10 are smaller polish / docs work — worth filing but
not blocking on the 0.2.0 cut.

## Status (landed 2026-04-23)

All ten findings addressed:

- **#1** — `_check_per_test_condition_coverage` in
  `litmus/execution/plugin.py` raises `pytest.UsageError` at
  collection time when a test binds a `characteristic` whose
  SpecBand `when:` keys aren't covered by any test vector. Guarded
  so it only fires when a policy-shaped limit entry will actually
  resolve against the char (`_any_limit_binds_to_char`).
- **#2** — `_litmus_apply_mocks` now merges
  `sidecar.tests.<method>.mocks` between top-level `mocks:` and
  method-level markers.
- **#3** — `pytest_generate_tests` now accepts
  `tests.<method>.vectors` as a first-class alias for
  `vectors.methods.<method>`.
- **#4** — `_warn_unmatched_profile_keys` warns when a profile
  `vectors`/`limits`/`markers` key matches no collected node-id
  (exact or fnmatch). No more silent typo no-ops.
- **#5** — `pytest_report_header` logs the composed `PYTEST_ADDOPTS`
  when a profile is active, so the user sees the merged flag set.
- **#6** — `SpecBand.when` accepts bare scalars (`{load: 0.1}`) via
  the existing Pydantic union type. `examples/03-profiles/products/pmic_a23.yaml`
  migrated to the scalar form throughout.
- **#7** — `docs/guides/writing-tests.md` now has a "Structuring
  drivers across multiple test folders" section documenting the
  `conftest.py` `sys.path` shim alongside the pyproject route.
- **#8** — `VerifyFn` Protocol and `LimitsFn` alias exported from
  `litmus.execution`; `verify` and `limits` fixtures annotated.
- **#9** — `docs/guides/writing-tests.md` leads with a one-line
  decision heuristic for `verify` vs `logger.measure`.
- **#10** — `_litmus_apply_mocks` accepts list values for
  `mocks.<fixture>.<attr>` and cycles them across calls via
  `itertools.cycle`, enabling per-rail mocked returns in multi-pin
  tests.

---

# Progressive Complexity — First Article → Production → Rev B

Second axis: how well does the platform match the arc an engineer
actually walks? Bench bring-up on a single unit → stable test flow
across a handful of engineering samples → factory deployment across
many stations → maintaining the test suite as the product evolves.

Methodology: probed each tier in an isolated `/tmp` directory, not
the demo, to measure what Litmus actually demands at each step.

## The four tiers, measured

### Tier 0 — "I have one board and one test"

Minimum viable:

```python
# tests/test_bringup.py
from litmus.models.config import Limit
def test_rail(verify):
    verify("v_rail", 3.31, limit=Limit(low=3.2, high=3.4, units="V"))
```

+ one-line `pytest.ini`. **Runs green.** No station, no fixture, no
product, no sidecar. The full `TestRun` lands in `~/.local/share/litmus`
with `meas_value`, `meas_limit_*`, `outcome`. Pin / channel / spec_ref
columns are null. **This is the layered promise working — layers you
haven't authored are no-ops, not errors.**

### Tier 1 — "Add a driver so I can read the real voltage"

Two paths:

**(a) User-defined fixture in `conftest.py`** — five lines, works
immediately with `--mock-instruments` or real hardware:

```python
@pytest.fixture
def dmm():
    return MyDmmDriver("GPIB::22")
```

**(b) Station YAML + `--station`** — full catalog/station framework.
More YAML, but buys auto-registration across all tests and real
instrument lifecycle.

Probed path (a): a test signature of `def test_rail(dmm, verify)`
**fails** with `fixture 'dmm' not found` unless the user defines it
OR authors a station. Path (a) is nowhere in the docs. Users who
don't know it exists jump directly to path (b) and conclude Litmus
"requires a station."

### Tier 2 — "Add a product so I can compare against a datasheet"

Move from `limits: {v_rail: {low: 3.2, high: 3.4}}` to
`limits: {v_rail: {characteristic: rail_3v3, tolerance_pct: 2}}`.
This requires: (a) a `products/<name>.yaml` exists, (b) vector params
cover every `when:` condition on the chosen SpecBand, (c) the test
signature picks up `context` for `get_param` calls.

The silent-failure mode from REVIEW #1 is the biggest cliff here:
a test that works at Tier 0/1 can fail at Tier 2 on move-in because
its vectors don't supply a condition key the SpecBand gates on.

### Tier 3 — "Stand up a production line"

Profiles in `litmus.yaml` declare vectors, limits, facets, and
pytest addopts per screening mode. Adding this tier requires zero
test-code changes — confirmed green in the advanced demo's
`production` profile (6/14 cases, 1-point sweeps) vs default (14/14
cases, full sweeps).

The only authoring tax at this tier is profile keys using full
pytest node IDs (REVIEW #4) — a real problem at factory scale where
a file rename silently no-ops a profile override.

### Tier 4 — "Rev B lands"

Two scenarios probed against the advanced demo:

**Same pinout, tighter spec** — just duplicate
`products/pmic_a23.yaml` → `pmic_a23_rev_b.yaml`, change SpecBand
values. **But** auto-discovery picks the first sorted file, so the
user MUST pass `--spec=products/pmic_a23_rev_b.yaml`. The more
natural selector — `--dut-part-number=PMIC-A23-RevB` matching the
product's `part_number:` field — is **not wired up**. The plugin
collects `--dut-part-number` for traceability but never uses it to
pick the active product.

**Different pinout** — duplicate fixture YAML too
(`fixtures/pmic_a23_bench_rev_b.yaml`). Tests still don't change.
This is the bright spot: the layer separation pays off exactly as
advertised on variant migration.

## Cliffs, in order of severity

| # | Tier | Cliff | Why it matters |
|---|------|-------|----------------|
| P1 | 0→1 | `dmm` fixture not found unless station YAML or conftest fixture exists | New users assume Litmus is heavyweight when it isn't |
| P2 | 1→2 | Adding a product can silently break passing tests (spec-condition ≠ vector key) | Duplicates REVIEW #1 — highest-leverage fix |
| P3 | 3→4 | Multi-product auto-discovery picks first sorted file | Factory flows run the wrong product without warning |
| P4 | 2 | `characteristic:` only under `tests.<method>:`, `limits:` works at both tiers | Asymmetric sidecar shape forces lookup on every edit (REVIEW #3) |
| P5 | 3 | Profile keys are full pytest node IDs | File rename silently drops the override |

## Proposals — progressive-complexity specific

**Status.** All five landed on `2026-04-23`:

- **PC1** — `docs/tutorial/01-first-test.md` now has a "Bench-bringup
  escape hatch" section plus a fixture-not-found troubleshooting hit.
- **PC2** — `_autodiscover_product` in `litmus/execution/plugin.py`
  matches `--dut-part-number` against product `part_number:` fields
  and raises `pytest.UsageError` on ambiguous / missing matches.
- **PC3** — `examples/01-bringup/` created as the Tier 0/1 reference. Three
  tests, one conftest, one sidecar, no station / product / fixture
  YAML. `examples/02-station/` and `examples/03-profiles/` remain the higher
  tiers.
- **PC4** — `litmus init --tier=bringup|bench|factory` wired up in
  `litmus/cli.py` + `litmus/init.py`. `bringup` writes a conftest
  with `MagicMock` fixtures, a smoke test, and a sidecar — no
  station/product scaffolding. `bench` maps to the existing
  `--starter`. `factory` tier is reserved for follow-up work.
- **PC5** — `_warn_uncovered_condition_keys` in plugin.py emits a
  `UserWarning` at collection time when product characteristics
  declare condition keys no test's vectors cover. Surfaces the P2
  cliff before the silent "No spec band matches" fires at measure
  time.

### PC1. Document the conftest escape hatch prominently

One paragraph in `getting-started.md`: "You don't need a station to
get instrument fixtures. Write a five-line conftest and graduate to a
station when you're ready." The fixture-not-found error should
mention this path too:

```
fixture 'dmm' not found. Define it in conftest.py, or author a
station YAML at stations/<id>.yaml (see docs/guides/stations.md).
```

Shifts the bar for Tier 1 from "learn the catalog/station system"
to "write one pytest fixture."

### PC2. Product selection via `--dut-part-number`

When multiple `products/*.yaml` files exist, match the `part_number:`
field against `--dut-part-number` before falling back to first-sorted.
Today's behavior becomes a deterministic error when two products
match and no CLI selector is given: "multiple products in products/;
pass --dut-part-number or --spec". Unblocks Rev B without the user
memorizing `--spec=products/pmic_a23_rev_b.yaml`.

### PC3. Three staged demo directories

`examples/` should model the arc directly:

- `examples/01-bringup/` — one test file, one conftest, no YAML. Shows
  Tier 0/1. Opens in a fresh IDE and just runs.
- `examples/02-station/` — add station + product + sidecar. Shows
  Tier 2.
- `examples/03-profiles/` (exists) — full production flow with profiles,
  catalog, multi-pin bindings. Shows Tier 3/4.

A user reads them in order and sees the platform progressively
reveal itself.

### PC4. `litmus init <tier>` scaffolding

`litmus init bringup` → drops `pytest.ini` + `tests/test_smoke.py` +
conftest with a mock `dmm`. `litmus init bench` → adds
`stations/<id>.yaml` + catalog entry. `litmus init factory` → adds
`litmus.yaml` with production/characterization profiles. Each tier
is additive and keyed off the previous.

Aligns the scaffolding command with the demo arc in PC3 and gives
users a zero-typing migration path between tiers.

### PC5. Warn when a loaded product has SpecBands whose condition keys are not covered by any test's vectors

At session end, emit a single warning per unused condition key. This
doesn't block anything (characterization runs might not cover every
condition), but it surfaces the silent-failure mode from P2 *before*
a user hits it at test time. Pairs well with the collection-time
validation from REVIEW #1 — that fix catches "test declares char X
but forgets condition key Y"; this one catches "product declares
condition key Y but no test drives it."

## Verdict

The layered architecture pays off exactly where the user most needs
it — Tier 4 variant swaps are near-zero test code churn — and Tier 0
is as light as a pure-pytest project. The real friction is the
bridging tier: Tier 1 → Tier 2, where "just add a product" reveals
the implicit coupling between vector keys and SpecBand conditions.
Fix P2 + P3 and the gradient from "one test file" to "production
line across Rev B" becomes one continuous onboarding ramp instead
of three.
