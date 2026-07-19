# TesterKit AI-skill evals

Measures whether the AI-facing surfaces (the 11 skills at
`src/testerkit/skills/<name>/SKILL.md`) actually lead a generative AI to produce
**correct, right-sized** TesterKit artifacts — not just plausible-looking ones.

This is **dev tooling**. It lives outside `src/testerkit` and calls a model itself;
the TesterKit platform never calls an LLM.

## How it works

Two pieces:

1. **`grader.py` — the trusted core (deterministic).** Given the files (and
   captured response) an AI produced for a task, it checks whichever
   dimensions the task needs:
   - **collects / passes** — real `pytest` run in an isolated project (with
     `--mock-instruments` / `--test-phase=...` / extra env where the task
     needs it) — most tasks (writing a test) are graded this way;
   - **sidecar valid** — any `<test>.yaml` validates against testerkit's own
     `SidecarConfig`, which also exercises `MeasurementLimitConfig` for any
     guardband-shaped (`{characteristic, guardband_pct}`) limit entry;
   - **station / part valid** — for scaffold tasks (`validate_yaml="station"`
     / `"part"`), the emitted `stations/*.yaml` / `parts/*.yaml` validates
     against the real `StationConfig` / `Part` model instead of running
     pytest at all;
   - **cli** — for CLI-answer tasks (`expect_cli=...`), a structural check
     that the candidate's response/files literally contain the expected
     `testerkit <subcommand>` invocation, not prose describing one;
   - **minimal** — no over-scaffolding (no station/part/profile YAML, no
     `psu`/`dmm` fixtures below the rung that needs them);
   - **negative control** — a paired out-of-band variant must *fail*, proving the
     test judges instead of rubber-stamping.

2. **`runner.py` — the model in the loop.** For each task it asks an AI (via
   `claude -p`, headless) to write the candidate into a throwaway dir, then
   grades it. Runs each task N times and reports a pass-rate, plus a
   **per-skill** rollup. Supports **vanilla vs skill-augmented** — with the
   skill, the augmentation context is that task's real
   `src/testerkit/skills/<skill>/SKILL.md` (run both to measure the lift the
   skill provides — the method Anthropic's skill guidance recommends). If a
   task's skill dir doesn't exist yet, the augmentation context is empty and
   the task simply runs vanilla instead of erroring.

`tasks.py` is the task set as plain data, one representative task per skill
(11 skills): the `testerkit-tests` set is the original start-simple ladder (Rung 0
record-only → Rung 1 sidecar → Rung 2 mock instruments) plus over-engineering
traps ("just log this" must *not* scaffold a station); every other skill
(`testerkit-mocks`, `testerkit-stations`, `testerkit-parts`, `testerkit-profiles`,
`testerkit-sites`, `testerkit-capture`, `testerkit-analysis`, `testerkit-debug`,
`testerkit-interactive`, `testerkit-datasheets`) gets one task spanning its trigger.
`testerkit-datasheets`' task is `manual=True` (needs a real datasheet PDF fixture)
and is skipped by the automated runner. Kept as data so a future optimizer
(DSPy/GEPA) can use it as a trainset.

## Run it

**Grader self-test (no model, no cost)** — proves the grader accepts good
candidates and rejects broken ones:

```bash
uv run python evals/check_grader.py
```

**Full eval (needs the `claude` CLI logged in — runs under your subscription,
not paid API):**

```bash
uv run python evals/runner.py                 # all tasks, skill-augmented, N=3
uv run python evals/runner.py --vanilla       # baseline (no skill in context)
uv run python evals/runner.py --task r0_verify --n 5
uv run python evals/runner.py --model claude-sonnet-5
```

`runner.py` uses `claude -p` under the hood, which draws against your Max/Pro
subscription rate limits — keep N modest. Swap `_generate` in `runner.py` to
point at any other model/provider; the tasks and grader are backend-agnostic.

## Design notes / limits

- The **grader is the trusted part** and is fully verifiable offline
  (`check_grader.py`). The runner is best-effort glue around whatever model you
  point it at.
- **Negative controls are best-effort** — they inject an out-of-band value by
  string replacement, so they're conclusive for typical scalar tests and
  `inconclusive` (`neg=?`) when they can't find a value to mutate.
- **CLI-answer grading is structural, not semantic** — `expect_cli` only greps
  for the literal `testerkit <subcommand>` substring in the candidate's response/
  files; it doesn't check the flags are right for the question asked. Good
  enough to catch "answered with prose instead of the tool," not to catch a
  subtly wrong filter.
- **Not yet graded:** an ambiguous prompt should yield a *question*, not code —
  no task currently exercises that; a future structural grader could check the
  candidate asked rather than guessed.
- **Next:** wrap the same grader as a DSPy/GEPA metric to *optimize* the skill
  text against these tasks, once the pass-rates justify it.
