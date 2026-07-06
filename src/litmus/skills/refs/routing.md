# Which tool? (routing)

Start with the **smallest thing** that answers the request; climb only when the request demands it.
This is the front door — each step links the reference that goes deeper.

## 0 — About existing data or state? → a CLI command, not test code

| The user wants… | Reach for |
|---|---|
| the last run / what ran | `litmus show <run_id>` / `litmus runs` |
| yield / top failures / Ppk | `litmus metrics summary \| pareto \| ppk` |
| to find instruments | `litmus discover` |
| how a verb/knob works | `litmus refs show verify \| observe \| mocks \| profiles \| tiers` |
| to write a full test / see the toolbox | `litmus refs show test-writing \| fixtures` |
| instruments, drivers, stations, catalog | `litmus refs show instruments` |
| per-DUT limits / part specs | `litmus refs show part-specs` |
| to open the operator UI | `litmus serve` |

## 1 — Writing a test: how much scaffold?

- **Just a passing test** → nothing to scaffold; write a bare test (plus an optional `<test>.yaml`
  sidecar).
- **Need mock `psu` / `dmm`** → `litmus init --tier bringup` — a `MagicMock` conftest + inline-limit
  smoke test, **no station or part YAML**. (`psu`/`dmm` are *not* built in; they come from a station's
  `instruments:` map or this scaffold. `--mock-instruments` only swaps drivers for roles a station
  already declares.)
- **They explicitly want the full bench/factory skeleton** → `litmus init --starter`. That's the
  *human* onramp; when you're the AI, prefer to right-size and add layers as needed.

## 2 — For each value the test produces: is it a **Measurement** at all?

This one question picks the verb (the TestStand "Measurement" distinction):

| The value is… | verb | it becomes |
|---|---|---|
| **not** a measurement — report text, "did X happen", a waveform/capture, an artifact, an incidental readout | `observe` | an **output** (outputs lane) |
| a measurement you'll **never** limit-check (characterization) | `measure` | a **measurement**, no judgment |
| a **spec parameter** — a limit exists now, or is meant to | `verify` | a **measurement**, judged |
| a stimulus you set | *see below* | an **input** (inputs lane) |
| a stream of samples over time | `stream` | a **channel** |

- `measure` and `verify` share a signature — start with `measure`, change the one word to `verify`
  when the spec lands. `observe` is a deliberate *lane* change (raw output, not a measurement).
- If "measurement vs output" or "judged vs not" isn't clear from the request, **ask** — a good
  clarifying question beats a confident wrong verb.

**Stimulus** (what you drive):
- **swept** → a declared axis (`@pytest.mark.parametrize` or sidecar `sweeps:`); recorded for you.
- **fixed / known** → set it imperatively: `psu.set_voltage(5.0)`.
- **execution-dynamic** (the *actual* readback, a runtime-computed setpoint) → `configure("name",
  value)` to record it on the inputs lane. (Rare — reach for it only when the value is decided at run
  time.)

Deeper: `litmus refs show observe` (record-only + evidence) and `litmus refs show verify` (judgment).

## 3 — If it's judged, where does the limit live?

The innermost layer that **owns** it, and stop at the lowest that works:

inline `limit={...}` (code-owned) → sidecar `<test>.yaml` (operator-tuned) → part spec
`{characteristic: <id>, tolerance_pct: <n>}` (a fact about the DUT) → profile (varies by phase).

Deeper: `litmus refs show verify` (limit resolution) · `litmus refs show tiers` (the ladder) ·
`litmus refs show profiles` (phase overrides).

## 4 — Sweeps

- **Outer is the default:** `@pytest.mark.parametrize` or sidecar `sweeps:` — one vector per pytest
  item. Teach this first.
- **Inner** (loop the `vectors` fixture inside the test body) is an *optimization* — use it to
  amortize expensive setup or collapse a sweep into one analytics row.
- `context.changed("name")` answers "did this input change since the last iteration?" so any test can
  skip redundant reconfiguration — it works across outer vectors **and** inner loops.

## The ladder

Adopt each rung only when you want it; every rung is a working test.

| Rung | You write | You need |
|---|---|---|
| 0 | `observe("v", x)` / `verify("v", x, limit={...})` | nothing (bare `pip install`) |
| 1 | `verify("v", x)` (limit in sidecar) | a `<test>.yaml` sidecar |
| 2 | `test(psu, dmm)` + `--mock-instruments` | a station (or `litmus init --tier bringup`) |
| 3 | `verify("v", x)` (limit from spec) | a part spec + `characteristic:` in the sidecar |
| 4 | `--test-profile` / `--test-phase` | profiles |

Full ladder detail: `litmus refs show tiers`. Working examples end-to-end: the `examples/` chapters
(`01-vanilla` → `12-parallel-sites`).
