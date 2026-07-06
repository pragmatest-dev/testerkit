# The router — any request → the right Litmus tool

Start with the **smallest thing** that answers the request; climb only when the request
demands it. This is the front door to everything Litmus does — route first, then load
only the one ref the request needs (`litmus refs show <topic>`).

## 0 — Route the request

| The user wants… | Reach for | Goes deeper |
|---|---|---|
| a reading logged / a value checked / a test written | write a test — §2 picks the verb, §3 the limit's home | `test-writing`, `verify`, `observe`, `fixtures` |
| to build up a real test **solution** (bench → validation → production → factory) | the solution arc — find their stage, add ONE stage | `solutions` |
| to start / structure a project (`init`, `litmus.yaml`, folders) | `litmus init` only when needed — most requests need no setup | `project-setup` |
| their bench as config (instruments, roles, drivers) | a station YAML + bring-your-own driver | `instruments` |
| an instrument's capabilities captured from a datasheet | the catalog workflows (`/catalog-from-datasheet`, catalog-scaffold) | `workflow/datasheet-to-catalog.md` |
| per-DUT limits / characteristics shared across tests | a part spec | `part-specs` |
| mock hardware / run without instruments | `--mock-instruments` + per-test `mocks:` | `mocks` |
| limits/behavior that vary by lab phase | profiles (`--test-profile` / `--test-phase`) | `profiles` |
| waveforms / samples over time from a test | the `stream` verb → a channel | `streaming` |
| files attached to a run (captures, logs, reports) | artifacts (FileStore) | `artifacts` |
| N UUTs tested in parallel | sites (`--site`, `site_index`) | `multi-site` |
| the last run / what ran / why it failed | `litmus runs` → `litmus show <run_id>` — triage, don't guess | `debugging` |
| yield / top failures / Ppk / trend / retest cost | `litmus metrics …` (all take `--json`) | `analytics` |
| data out (query, export, Grafana, SBOM, reports) | Query API / `litmus show -f` / `litmus export` | `analytics` |
| to see it in a browser | `litmus serve` (operator UI) | — |
| to find instruments on the bus | `litmus discover` | `instruments` |
| YAML checked / a schema to generate against | `litmus validate` / `litmus schema` | `project-setup` |
| their AI tool wired up | `litmus setup claude-code\|copilot\|codex\|cursor` | — |

**Questions about existing data or state are CLI calls, never test code.** Prefer
`--json` for machine reading.

## 1 — Writing a test: how much scaffold?

- **Just a passing test** → write a bare test; nothing to scaffold (plus an optional
  `<test>.yaml` sidecar).
- **Need mock `psu` / `dmm`** → `litmus init --tier bringup` — a mock conftest + inline-limit
  smoke test, **no station or part YAML**. (`psu`/`dmm` are *not* built in; they come from a
  station's `instruments:` map or this scaffold. `--mock-instruments` only swaps drivers for
  roles a station already declares.)
- **They explicitly want the full bench/factory skeleton** → `litmus init --starter`. That's
  the *human* onramp; when you're the AI, right-size and add layers as needed.

## 2 — For each value the test produces: is it a **Measurement** at all?

This one question picks the verb (the TestStand "Measurement" distinction):

| The value is… | verb | it becomes |
|---|---|---|
| **not** a measurement — report text, "did X happen", a waveform/capture, an artifact, an incidental readout | `observe` | an **output** (outputs lane) |
| a measurement you'll **never** limit-check (characterization) | `measure` | a **measurement**, no judgment |
| a **spec parameter** — a limit exists now, or is meant to | `verify` | a **measurement**, judged |
| a stimulus you set | *see below* | an **input** (inputs lane) |
| a stream of samples over time | `stream` | a **channel** |

- `measure` and `verify` share a signature — start with `measure`, change the one word to
  `verify` when the spec lands. `observe` is a deliberate *lane* change (raw output, not a
  measurement).
- If "measurement vs output" or "judged vs not" isn't clear from the request, **ask** — a
  good clarifying question beats a confident wrong verb.

**Stimulus** (what you drive):
- **swept** → a declared axis (`@pytest.mark.parametrize` or sidecar `sweeps:`); recorded for you.
- **fixed / known** → set it imperatively: `psu.set_voltage(5.0)`.
- **execution-dynamic** (the *actual* readback, a runtime-computed setpoint) →
  `configure("name", value)` records it on the inputs lane. (Rare — only when the value is
  decided at run time.)

Deeper: `litmus refs show observe` (record-only + evidence) · `litmus refs show verify`
(judgment) · `litmus refs show fixtures` (the full toolbox).

## 3 — If it's judged, where does the limit live?

The innermost layer that **owns** it, and stop at the lowest that works:

inline `limit={...}` (code-owned) → sidecar `<test>.yaml` (operator-tuned) → part spec
`{characteristic: <id>, tolerance_pct: <n>}` (a fact about the DUT) → profile (varies by
phase).

Deeper: `litmus refs show verify` (limit resolution) · `litmus refs show part-specs` ·
`litmus refs show profiles`.

## 4 — Sweeps

- **Outer is the default:** `@pytest.mark.parametrize` or sidecar `sweeps:` — one vector per
  pytest item. Teach this first.
- **Inner** (loop the `vectors` fixture inside the test body) is an *optimization* — use it
  to amortize expensive setup or collapse a sweep into one analytics row.
- `context.changed("name")` answers "did this input change since the last iteration?" so any
  test can skip redundant reconfiguration — outer vectors **and** inner loops.

## 5 — The ladder (single-test view)

Adopt each rung only when you want it; every rung is a working test.

| Rung | You write | You need |
|---|---|---|
| 0 | `observe("v", x)` / `verify("v", x, limit={...})` | nothing (bare install) |
| 1 | `verify("v", x)` (limit in sidecar) | a `<test>.yaml` sidecar |
| 2 | `test(psu, dmm)` + `--mock-instruments` | a station (or `litmus init --tier bringup`) |
| 3 | `verify("v", x)` (limit from spec) | a part spec + `characteristic:` in the sidecar |
| 4 | `--test-profile` / `--test-phase` | profiles |

The whole-solution view — bench bringup through multi-site factory, keyed to the
`examples/` chapters (`01-vanilla` → `12-parallel-sites`): `litmus refs show solutions`.
Tier detail: `litmus refs show tiers`.
