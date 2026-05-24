# AI integration

The platform exposes its operations as **MCP tools** an AI client can drive: discovery, capability matching, run launching, results query, schema validation, datasheet extraction, test scaffolding. It does not embed an LLM client itself — the user brings their own assistant (Claude Code, Cursor, Cline, Claude Desktop, Copilot, …), and Litmus is the typed surface on the other end of the wire.

The cost of any structured platform is the upfront encoding work — getting existing instruments and products into the schema. AI integration is the platform's answer to that cost. An agent reads a datasheet PDF, drafts the catalog YAML, lands it as a file you can diff and review. The structured approach becomes worth adopting because the encoding work shrinks from hours to minutes.

The rest of this page documents the integration's boundary: what changes for the user, why it's safe to lean on without losing visibility, what it deliberately doesn't do, and the adoption ramp.

## What changes for the user

| You're doing | Without AI | With AI |
|---|---|---|
| Onboarding a new instrument | Read datasheet, write capability YAML by hand from scratch (hours) | `/catalog-from-datasheet <pdf>` produces a reviewable draft; you correct (minutes) |
| Starting a new product | Read datasheet, write spec YAML, decide instruments, wire station, scaffold test | `datasheet-to-test` workflow drafts all of it with approval gates |
| Routine test authoring | Look up the spec in the YAML, write the `verify()` calls | (largely unchanged — you stay in pytest) |
| Run + analyze | (unchanged — pytest runs, results write to parquet) | (unchanged — pytest runs, results write to parquet) |

The agent's output is **a starting point you review**, not a finished artifact. Every YAML lands as a file in your repo. Every test is a `def test_*()` you can edit. If the agent gets a capability wrong, you spot it in the diff and push back. If you delete the AI from the loop tomorrow, the project keeps working — the files don't depend on the AI ever running again.

## What keeps the integration safe to lean on

Three properties of the platform make the AI surface safe to use without losing visibility:

1. **Everything is a file.** Products, stations, fixtures, profiles, sequences, results — all YAML or Parquet. An AI editing a product spec produces a diff you can review the same way you'd review a colleague's PR. There's no opaque database for the AI to mutate.

2. **MCP tools, not LLM calls.** Litmus does not embed an OpenAI / Anthropic / Google client. The AI tooling drives Litmus from outside. You bring your own AI client; Litmus exposes the operations.

3. **Operator-in-the-loop by design.** The shipped workflows (see [skills reference](../../reference/skills.md)) STOP at every approval gate. "Here is the product spec I extracted, ok to save?" — you say yes or you edit first.

This matters most when the AI gets it wrong. A misread accuracy spec or a wrongly assigned pin reads as a YAML diff or a `verify()` line — you spot it, push back, iterate. Compare to a workflow where the AI commits to a database: the same mistake disappears under "the system says so."

## Anti-goals

A few things AI integration in Litmus deliberately does **not** try to do:

- **Run the test.** Test execution is pytest. The AI can scaffold a test file, but the test runs the same way it would without the AI.
- **Decide pass/fail.** Limit checking, traceability, capability matching — all in code. The AI proposes; the deterministic platform decides.
- **Hide the prompt.** All shipped workflows ([skills reference](../../reference/skills.md)) ship as plain markdown files at `src/litmus/skills/`. You can read them, audit them, fork them, ignore them.
- **Lock you to one model.** Sub-agent prompts include a recommended **tier** (high-capability / mid-capability) but no hard-coded model name. Pick whichever your client supports.

## Adoption ramp

Three adoption levels — pick whichever matches the user's trust level today:

1. **Just MCP tools.** Register the server (`litmus setup claude-code` and friends), then drive operations conversationally without using the workflows. "Add a 3.3V output rail to the product spec" — the agent calls `litmus_project(action="save", ...)` and shows the diff.

2. **Workflows as a starting draft.** Invoke the `datasheet-to-test` workflow on a new product. Treat the YAML files it produces as a first draft, then hand-edit. Often faster than starting from a blank file.

3. **Full datasheet → tests pipeline.** Drive `/catalog-from-datasheet` (instrument catalog) and `datasheet-to-test` (product+tests) end-to-end. Operator approval gates at every phase. See [how-to/datasheet-to-test](../../how-to/catalog/datasheet-to-test.md) for the walkthrough.

## See also

- [How-to: AI-assisted test development via MCP](../../how-to/overview/mcp-integration.md) — registering the MCP server with each supported AI client
- [How-to: datasheet-to-test workflow](../../how-to/catalog/datasheet-to-test.md) — end-to-end walkthrough
- [Reference: skills](../../reference/skills.md) — full inventory of workflows, agents, slash commands, MCP tools and prompts
- [Reference: MCP server + HTTP API](../../reference/api.md) — the operations AI clients call
