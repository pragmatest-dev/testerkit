# Why AI integration

The industry doesn't have a standard way to encode instrument capabilities or product specs in a reusable form. Every test framework reinvents it, and most just give up — accuracy specs end up as magic numbers buried in test code, instrument selection is "whatever was on the bench when we wrote this," product specs live in a spreadsheet someone owned three jobs ago. Capability matching across projects is mostly aspirational.

Litmus offers a structured alternative — a typed [Capability schema](../reference/catalog-schema.md) for instruments and products, served from YAML files that diff in git. Specs become reusable. Capability matching becomes a function call. Measurements become traceable to the spec that judged them.

The catch with any structured schema: getting your existing instruments and products into it. That's where AI integration earns its keep. Litmus exposes its operations as **MCP tools** an AI assistant can drive — read a datasheet, look up an instrument, save a YAML, check compatibility, scaffold a test. The agent reads the datasheet PDF and produces a reviewable first draft of the YAML; you correct and approve. That step is the difference between "encoding our 40 instruments is a months-long project" and having a working catalog by the end of the week.

## What it changes

| You're doing | Without AI | With AI |
|---|---|---|
| Onboarding a new instrument | Read datasheet, write capability YAML by hand from scratch (hours) | `/catalog-from-datasheet <pdf>` produces a reviewable draft; you correct (minutes) |
| Starting a new product | Read datasheet, write spec YAML, decide instruments, wire station, scaffold test | `datasheet-to-test` workflow drafts all of it with approval gates |
| Routine test authoring | Look up the spec in the YAML, write the `verify()` calls | (largely unchanged — you stay in pytest) |
| Run + analyze | (unchanged — pytest runs, results write to parquet) | (unchanged — pytest runs, results write to parquet) |

The agent's output is **a starting point you review**, not a finished artifact. Every YAML lands as a file in your repo. Every test is a `def test_*()` you can edit. If the agent gets a capability wrong, you spot it in the diff and push back. If you delete the AI from the loop tomorrow, the project keeps working — the files don't depend on the AI ever running again.

## Why this is supportive, not magic

Three properties of how Litmus is built make AI integration safe to lean on without losing visibility:

1. **Everything is a file.** Products, stations, fixtures, profiles, sequences, results — all YAML or Parquet. An AI editing a product spec produces a diff you can review the same way you'd review a colleague's PR. There's no opaque database for the AI to mutate.

2. **MCP tools, not LLM calls.** Litmus does not embed an OpenAI / Anthropic / Google client. The AI tooling drives Litmus from outside. You bring your own AI client; Litmus exposes the operations.

3. **Operator-in-the-loop by design.** The shipped workflows (see [skills reference](../reference/skills.md)) STOP at every approval gate. "Here is the product spec I extracted, ok to save?" — you say yes or you edit first.

This matters most when the AI gets it wrong. A misread accuracy spec or a wrongly assigned pin reads as a YAML diff or a `verify()` line — you spot it, push back, iterate. Compare to a workflow where the AI commits to a database: the same mistake disappears under "the system says so."

## Anti-goals

A few things AI integration in Litmus deliberately does **not** try to do:

- **Run the test.** Test execution is pytest. The AI can scaffold a test file, but the test runs the same way it would without the AI.
- **Decide pass/fail.** Limit checking, traceability, capability matching — all in code. The AI proposes; the deterministic platform decides.
- **Hide the prompt.** All shipped workflows ([skills reference](../reference/skills.md)) ship as plain markdown files at `src/litmus/skills/`. You can read them, audit them, fork them, ignore them.
- **Lock you to one model.** Sub-agent prompts include a recommended **tier** (high-capability / mid-capability) but no hard-coded model name. Pick whichever your client supports.

## Adopt at your own pace

Three reasonable adoption levels — pick whichever matches your trust level today:

1. **Just MCP tools.** Register the server (`litmus setup claude-code` and friends), then drive operations conversationally without using the workflows. "Add a 3.3V output rail to the product spec" — the agent calls `litmus_project(action="save", ...)` and shows the diff.

2. **Workflows as a starting draft.** Invoke the `datasheet-to-test` workflow on a new product. Treat the YAML files it produces as a first draft, then hand-edit. Often faster than starting from a blank file.

3. **Full datasheet → tests pipeline.** Drive `/catalog-from-datasheet` (instrument catalog) and `datasheet-to-test` (product+tests) end-to-end. Operator approval gates at every phase. See [how-to/datasheet-to-test](../how-to/datasheet-to-test.md) for the walkthrough.

## See also

- [How-to: AI-assisted test development via MCP](../how-to/mcp-integration.md) — registering the MCP server with each supported AI client
- [How-to: datasheet-to-test workflow](../how-to/datasheet-to-test.md) — end-to-end walkthrough
- [Reference: skills](../reference/skills.md) — full inventory of workflows, agents, slash commands, MCP tools and prompts
- [Reference: MCP server + HTTP API](../reference/api.md) — the operations AI clients call
