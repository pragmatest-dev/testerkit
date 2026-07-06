# Litmus test-writing evals

Measures whether the AI-facing surfaces (the skills, refs, and generated
`CLAUDE.md`) actually lead a generative AI to produce **correct, right-sized**
Litmus tests — not just plausible-looking ones.

This is **dev tooling**. It lives outside `src/litmus` and calls a model itself;
the Litmus platform never calls an LLM.

## How it works

Two pieces:

1. **`grader.py` — the trusted core (deterministic).** Given the files an AI
   produced for a task, it *runs* them and checks:
   - **collects / passes** — real `pytest` run in an isolated project (with
     `--mock-instruments` where the rung needs it);
   - **sidecar valid** — any `<test>.yaml` validates against litmus's own
     `SidecarConfig` (so the grader can't drift from the real schema);
   - **minimal** — no over-scaffolding (no station/part/profile YAML, no
     `psu`/`dmm` fixtures below the rung that needs them);
   - **negative control** — a paired out-of-band variant must *fail*, proving the
     test judges instead of rubber-stamping.

2. **`runner.py` — the model in the loop.** For each task it asks an AI to write
   the test into a throwaway dir, then grades it. Runs each task N times and
   reports a pass-rate. Supports **vanilla vs skill-augmented** (run both to
   measure the lift the skills provide — the method Anthropic's skill guidance
   recommends).

`tasks.py` is the task set as plain data — the start-simple ladder (Rung 0
record-only → Rung 1 sidecar → Rung 2 mock instruments) plus over-engineering
traps ("just log this" must *not* scaffold a station). Kept as data so a future
optimizer (DSPy/GEPA) can use it as a trainset.

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
- **Not yet graded:** right-sizing for *non-test* requests (a "show me my last
  run" prompt should yield a CLI command, and an ambiguous prompt should yield a
  *question*, not code). Those need a structural grader — a documented next step.
- **Next:** wrap the same grader as a DSPy/GEPA metric to *optimize* the skill
  text against these tasks, once the pass-rates justify it.
