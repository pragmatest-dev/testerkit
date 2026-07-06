# AI integration

The platform exposes its operations as **typed tool calls** an AI client can drive — over an MCP server (the open protocol AI assistants use to call external tools) for clients that speak it, or via the CLI for any agent with a terminal: discovery, capability matching, run launching, results query, config validation, datasheet extraction, test scaffolding. It does not embed an LLM client itself — the user brings their own assistant (Claude Code, Cursor, Cline, Claude Desktop, Copilot CLI, …), and Litmus is the typed surface the assistant drives.

The cost of any structured platform is the upfront encoding work — getting existing instruments and parts encoded as catalog and spec YAML. AI integration is the platform's answer to that cost. An agent reads a datasheet PDF, drafts the catalog YAML, lands it as a file you can diff and review. The structured approach becomes worth adopting because the encoding work shrinks from hours to minutes.

The rest of this page documents the integration's boundary: what changes for the user, why it's safe to lean on without losing visibility, what it deliberately doesn't do, and the adoption ramp.

## What changes for the user

| You're doing | Without AI | With AI |
|---|---|---|
| Onboarding a new instrument | Read datasheet, write capability YAML by hand from scratch (hours) | The `litmus-datasheets` skill produces a reviewable draft; you correct (minutes) |
| Starting a new part | Read datasheet, write spec YAML, decide instruments, wire station, scaffold test | The `litmus-datasheets` skill drafts all of it, with approval gates at every phase |
| Routine test authoring | Look up the spec in the YAML, write the `verify()` calls | (largely unchanged — you stay in pytest) |
| Run + analyze | (unchanged — pytest runs, results write to parquet) | (unchanged — pytest runs, results write to parquet) |

The agent's output is **a starting point you review**, not a finished artifact. Every YAML lands as a file in your repo. Every test is a `def test_*()` you can edit. If the agent gets a capability wrong, you spot it in the diff and push back. If you delete the AI from the loop tomorrow, the project keeps working — the files don't depend on the AI ever running again.

## What keeps the integration safe to lean on

Three properties of the platform make the AI surface safe to use without losing visibility:

1. **Everything is a file.** Parts, stations, fixtures, profiles, sequences, results — all YAML or Parquet. An AI editing a part spec produces a diff you can review the same way you'd review a colleague's PR. There's no opaque database for the AI to mutate.

2. **Tool calls, not LLM calls.** Litmus does not embed an OpenAI / Anthropic / Google client. The AI tooling drives Litmus from outside — over MCP for clients that speak it, via the CLI for agents with a terminal. You bring your own AI client; Litmus exposes the operations.

3. **Operator-in-the-loop by design.** The `litmus-datasheets` skill (see [skills reference](../../reference/overview/skills.md)) is a gated, multi-phase pipeline that STOPS at an approval checkpoint after every phase — part spec, instrument match, station wiring, test scaffold. "Here is the part spec I extracted, ok to save?" — you say yes or you edit first.

This matters most when the AI gets it wrong. A misread accuracy spec or a wrongly assigned pin reads as a YAML diff or a `verify()` line — you spot it, push back, iterate. Compare to a workflow where the AI commits to a database: the same mistake disappears under "the system says so."

## Anti-goals

A few things AI integration in Litmus deliberately does **not** try to do:

- **Run the test.** Test execution is pytest. The AI can scaffold a test file, but the test runs the same way it would without the AI.
- **Decide pass/fail.** Limit checking, traceability, capability matching — all in code. The AI proposes; the deterministic platform decides.
- **Hide the prompt.** All shipped skills ([skills reference](../../reference/overview/skills.md)) are plain `SKILL.md` markdown files you can read, audit, fork, or ignore.
- **Lock you to one model.** The `litmus-datasheets` skill's sub-agent prompts each name a recommended **tier** (e.g. high-capability reasoning for schema-correct YAML emission) but no hard-coded model name. Pick whichever your client supports.

## Adoption ramp

Three adoption levels — pick whichever matches the user's trust level today:

1. **Just tool calls.** Either register the MCP server (`litmus setup claude-code` and friends) for clients that speak MCP, or have your agent invoke the CLI directly (`litmus runs`, `litmus show`, `litmus discover`, `litmus metrics`, ...). Drive operations conversationally without any skill matching. "Add a 3.3V output rail to the part spec" — the agent calls `litmus_project(action="save", ...)` (or edits the YAML file directly and runs `litmus validate`) and shows the diff.

2. **The `litmus-datasheets` skill as a starting draft.** Point it at a new part's datasheet PDF. Treat the YAML files it produces as a first draft, then hand-edit. Often faster than starting from a blank file.

3. **Full datasheet → tests pipeline.** Run the `litmus-datasheets` skill end-to-end — instrument catalog entries, part spec, station wiring, and test scaffold, in one gated pipeline. Operator approval gates at every phase. See [how-to/datasheet-to-test](../../how-to/catalog/datasheet-to-test.md) for the walkthrough.

## See also

- [How-to: AI-assisted test development via MCP](../../how-to/overview/mcp-integration.md) — registering the MCP server with each supported AI client
- [How-to: datasheet-to-test walkthrough](../../how-to/catalog/datasheet-to-test.md) — the `litmus-datasheets` skill end-to-end
- [Reference: skills](../../reference/overview/skills.md) — the 11 Agent Skills, MCP tools, and CLI
- [Reference: MCP server + HTTP API](../../reference/runtime/api.md) — the operations AI clients call over MCP
- [Reference: CLI](../../reference/cli.md) — the operations AI clients call via the terminal
