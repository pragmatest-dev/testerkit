# AI test-writing surfaces — 0.3.0 accuracy + start-simple compliance plan

**Status:** plan to follow (2026-07-05). Fix what a generative-AI user is *told* when they ask an AI
to write TesterKit tests, so the advice is accurate to shipped 0.3.0 and teaches "start simple, adopt
advanced as needed." Driven by a source-verified audit (findings below were each confirmed against
`verbs.py`, `pytest_plugin/`, `models/test_config.py`, `mcp/`, `data/data_dir.py`, `init.py`).

## North star

A user with a generative AI should get a test that **runs on a bare `pip install` with zero YAML**, and
should be led up the ladder — inline limits → station/mocks → part spec → profiles — only as needed.
Today the opposite happens: the AI's primary in-context example (the generated CLAUDE.md) **fails on a
fresh project** three different ways.

## The problem (confirmed against source)

The generated CLAUDE.md (`skills/templates/project-instructions.md`) is loaded into *every*
AI-assisted session, and its one "Writing Tests" example
`def test_output_voltage(verify, psu, dmm): verify("output_voltage", float(dmm.measure_dc_voltage()))`
breaks because:
1. **`psu`/`dmm` don't exist without a station.** `pytest_plugin/hooks.py:246` returns early with no
   station file → registers no role fixtures. `--mock-instruments` only swaps drivers for
   station-declared roles (`__init__.py:747`); it does **not** create `psu`/`dmm`.
2. **`verify` with no limit raises `MissingLimitError`** (`execution/verify.py:301`).
3. **`logger.measure` is a phantom** — there is no `logger`. Record-only verbs are `observe` /
   `measure` / `stream` (`verbs.py:142` `__all__`). The bare `measure` verb (the true analog of the
   intended `logger.measure`) is documented **nowhere** user-facing.

Plus drift in the other surfaces: MCP tool `testerkit` → is `testerkit_project`; MCP `TEST_TEMPLATE` uses
`mocks:` as a dict (must be a `list`), `ref:` (must be `characteristic:`), `vectors:` (must be
`sweeps:`) — all `extra="forbid"` → YAML load raises; `testerkit_schema(yaml_type="sequence")` — sequences
are **deleted** (`hooks.py:266`); the DuckDB SQL example points at `results/` — the dir is `data/`
(runs under `data/runs/`).

**Verified true minimum (this is what the surfaces should teach first):**
```python
def test_y(observe): observe("v", 3.3)                                    # record-only, zero config
def test_x(verify): verify("v", 3.3, limit={"low": 3.0, "high": 3.6, "unit": "V"})  # inline limit
```
Both pass with no YAML, no conftest, no station. `testerkit init --tier bringup` scaffolds the correct
MagicMock-`psu`/`dmm` conftest + inline-limit smoke test — but the AI is never pointed at it.

---

## Plan (sequenced; accuracy first — it's the highest ROI)

### Phase 1 — fix accuracy in the surfaces the AI already reads

**1a. Rewrite `skills/templates/project-instructions.md` "Writing Tests" (the single biggest fix).**
- Lead with the zero-config shape: `observe` (no limit) and `verify` with an **inline** `limit={...}`.
- Replace `logger.measure` → `measure` / `observe` (record-only) and state `verify` needs a limit
  (inline, or from a sidecar / part spec).
- State plainly that `psu`/`dmm` come from **a station's `instruments:` map or `testerkit init --tier
  bringup`'s scaffolded conftest** — not from `--mock-instruments` alone.
- Fix the MCP tool list: `testerkit` → `testerkit_project`; add `testerkit_open`, `testerkit_schema`.
- Fix the SQL example: `data/runs/**/*.parquet` (or drop the raw-parquet example in favor of
  `testerkit runs`/`testerkit show`).
- Add one line pointing at `testerkit init --tier bringup` as the start-simple on-ramp.

**1b. Fix the MCP `TEST_TEMPLATE` + prompt (`mcp/tools.py` ~663-745, `mcp/server.py`).**
- `mocks:` → list-of-dicts with `target:`; `ref:` → `characteristic:`; `vectors:` → `sweeps:`.
- Remove all `sequence` references (`testerkit_schema(yaml_type="sequence")`, "per-step aliases in
  sequences") — deleted.
- Verify the template YAML actually loads through `TestEntry` (`extra="forbid"`) as a test.

**1c. Fix the refs.**
- `refs/tiers.md`: Tier-0 cell `verify + logger.measure` → `verify + observe/measure`.
- `refs/observe.md`: document the **`measure`** verb (currently only `observe`/`stream`).
- `refs/verify.md`: correct the signature — `verify(name, value, limit=None, *, characteristic=None,
  namespace=None, unit=None)`.

**1d. `workflow/datasheet-to-test.md`:** fix the `mocks:` dict shape; keep it as the config-first
(datasheet-driven) path but note it's the *advanced* road.

### Phase 2 — fill the start-simple gap

**2a. Add a from-scratch "your first test" progressive workflow** (the missing path). This is the
"test-gen skill" worth having — an **on-ramp**, not another config-first generator. It walks an AI:
just verbs (zero config) → add an inline limit → add a station + `--mock-instruments` → add a part
spec + sidecar → profiles. Each rung is a working test; each adds one concept. Home: a new
`skills/workflow/first-test.md` (+ optionally a `/first-test` slash-command stub alongside
`catalog-from-datasheet`).

**2b. (optional) a test-generation slash command** — only if 2a's workflow proves it earns a
dedicated command; don't build a heavy generator.

### Phase 3 — anti-drift (why this rotted, and how to stop it)

The root cause: **no check that the AI-facing examples actually run.** Add a test that executes the
canonical snippets from `project-instructions.md` / the refs / the MCP `TEST_TEMPLATE` against the real
plugin + models (a doctest-style or a small "these snippets import and pass" test), so a verb rename or
a schema change breaks CI instead of silently misleading users. This is what would have caught every
Phase-1 finding.

---

## The start-simple contract (make every surface teach this ladder)

| Rung | You write | You need |
|---|---|---|
| 0 | `observe("v", x)` | nothing (bare `pip install`) |
| 1 | `verify("v", x, limit={...})` | an inline limit |
| 2 | `test(psu, dmm)` + `--mock-instruments` | a **station** (or `testerkit init --tier bringup` conftest) |
| 3 | `verify("v", x)` (limit from spec) | a **part spec** + `<test>.yaml` sidecar |
| 4 | `--test-profile` / `--test-phase` | **profiles** |

The generated CLAUDE.md and `tiers.md` should both make Rung 0 the default and each higher rung
opt-in. No surface should imply a part spec or station is required to *start*.

## Verification (every fix)

- Each changed claim checked against source (the file:line citations above are the anchors).
- Phase-3 test proves the examples run — the durable guard.
- Re-run the audit (the surfaces-vs-source pass) after Phase 1 to confirm 0 remaining inaccuracies
  before adding Phase 2.

---

## Phase 4 — the request→toolbox router (design spine, locked 2026-07-05)

The unifying decision procedure behind Phases 1–3. Shaped + locked via discussion; grounded in
source + the progressive `examples/` series + the **TestStand mental model** (TesterKit courts
TestStand/LabVIEW migrants, so the router speaks their vocabulary).

**Governing rule:** the *smallest sufficient* intervention; climb only when the request demands it
(`tiers.md` "start low, stop when done", applied to the whole toolbox — CLI, MCP, verbs, rungs — not
just config).

**Verb model — "is it a Measurement at all?" is the primary fork** (all in CURRENT terms: role-tagged
`inputs`/`outputs`/`measurements` lanes queried by role+name; there are NO `in_*`/`out_*` columns):
- `observe` → **outputs** lane: raw data / evidence / non-measurement output (report text, "did X
  happen", a waveform/capture, an incidental readout). Not a measurement.
- `measure` → a **measurement**, never limit-checked (characterization).
- `verify` → a **measurement that is a spec parameter** (limits now, or meant to exist). A
  characterization profile's `verify_requires_limit: false` makes the *same* `verify` record instead
  of raise — the phase, not the code, decides.
- `measure`↔`verify` share an identical signature → start `measure`, change one word when the spec
  lands. `observe` is a deliberate *lane* change.
- `configure` → **inputs** lane; RARE — only for an **execution-dynamic** input (the *actual*
  readback, a runtime-computed setpoint). Fixed stimulus is imperative `driver.set_*()`; swept
  stimulus is a declared axis (`@parametrize` / sidecar `sweeps:`), recorded automatically.
- `stream` → channel (time-series). There is NO `logger`.
- Ambiguous intent (measurement-vs-output, judged-vs-not) → **ask one clarifying question** — a good
  question beats a confident wrong verb.

**Config-location law:** a value lives at the innermost layer that owns it — inline (code) → sidecar
(operator) → part spec (DUT fact) → profile (phase-varying); non-varying stays put. Test bodies stay
unchanged across tiers; only *where data lives and who edits it* changes.

**Sweeps:** outer is the default (one vector per pytest item, `@parametrize` / sidecar `sweeps:`);
inner (loop the `vectors` fixture in-body) is an optimization to amortize setup / collapse to one
analytics row. `context.changed("name")` is a *general* cross-iteration reconfig-skip — works across
outer vectors AND inner loops, not inner-only.

**Human vs AI scaffold:** `testerkit init --starter` (bench tier) is the fuller *human* onramp; the AI
**right-sizes** — bare test → `--tier bringup` only when instruments enter → fuller only on request.

**Home (progressive disclosure):** the router ships as a lean front-door
`skills/refs/routing.md` (reachable via `testerkit refs show routing`) that **links** the canonical refs
(`verify.md` / `observe.md` / `tiers.md`) + the `examples/` chapters — it does not duplicate them.

**Remaining under this phase:** (a) `routing.md` front-door [committed 2026-07-05]; (b) full 0.3.0
doc-reality sweep (task #1); (c) an eval harness that grades *right-sizing* (CLI-when-a-command-
suffices / ask-when-ambiguous / lowest-rung / no-over-scaffold) — dev tooling, its own branch, driven
by Claude Code headless under the subscription (NOT paid API; platform never calls LLMs); a DSPy/GEPA
optimizer over the same grader+tasks is a later follow-on.
