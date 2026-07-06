# Skills reference

Litmus ships a set of **AI workflow prompts** that drive Claude / Copilot / Cursor / Cline through hardware-test authoring tasks. This page is the inventory: what each prompt does, what it calls, and how it's installed.

For motivation (why AI integration at all), see [concepts/why-ai-integration](../../concepts/overview/ai-integration.md). For setup commands (`litmus setup <client>`), see [how-to/mcp-integration](../../how-to/overview/mcp-integration.md).

> **Prerequisites.** These prompts run inside an AI client (Claude Code, Copilot, Cursor, or Cline) that you've connected to Litmus with `litmus setup <client>`. The client launches the `litmus mcp serve` server on demand over stdio — you don't start it yourself.

## Four layers

- **Project instructions** (1 template) — always-on context installed as `CLAUDE.md`, `.github/copilot-instructions.md`, or `AGENTS.md` (Codex, Cursor, Copilot CLI) by `litmus setup <client>`.
- **Reference cards** (`refs/`) — on-demand topic guides streamed by `litmus refs list` / `litmus refs show <topic>`; the instructions file indexes them so the agent loads only what a request needs.
- **Workflows** (3 prompts) — multi-step prompts you invoke directly; they drive the whole datasheet-to-tests flow. Plus **sub-agent templates** (5 prompts) the workflows spawn; not invoked directly.
- **Slash commands** (2 per client) — client-specific wrappers that invoke the workflows from your editor's prompt UI.

All layers ship as plain markdown — read any prompt on [GitHub](https://github.com/pragmatest-dev/litmus/tree/main/src/litmus/skills), or in the `litmus/skills/` directory of your installed copy.

## Workflows

User-invocable. Multi-step. STOP at every approval gate.

### `datasheet-to-test`

| | |
|---|---|
| Source | [`workflow/datasheet-to-test.md`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/workflow/datasheet-to-test.md) |
| Input | A part datasheet PDF |
| Output | `parts/<id>.yaml`, `stations/<id>.yaml`, `tests/test_<id>.py`, `tests/test_<id>.yaml` |
| Phases | Parse datasheet → save part spec → recommend instruments → create station config → generate tests → execute |
| MCP tools used | `litmus_project` (init, save, read), `litmus_match`, `litmus_run`, `litmus_open`, `litmus_discover` |

Approval gates at every phase. The user reviews extracted specs, picked instruments, station wiring, and the generated test before the agent moves on.

### `datasheet-to-catalog`

| | |
|---|---|
| Source | [`workflow/datasheet-to-catalog.md`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/workflow/datasheet-to-catalog.md) |
| Input | An instrument datasheet PDF |
| Output | A `catalog/<instrument>.yaml` entry with channels, capabilities, accuracy specs |
| Approach | Section-by-section: split → extract → write → mechanical audit + semantic review → fix-loop until clean |
| Sub-agents spawned | `section-splitter`, `section-extractor`, `section-writer`, `section-reviewer`, `scaffold-writer` |

Thorough. Use when you need accuracy specs and condition-indexed bands captured correctly. Slower than `catalog-scaffold` but produces audit-clean catalog entries.

### `catalog-scaffold`

| | |
|---|---|
| Source | [`catalog-scaffold.md`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/catalog-scaffold.md) |
| Input | An instrument make + model (no datasheet PDF needed) |
| Output | A `catalog/<instrument>.yaml` entry from the model's prior knowledge |
| When to use | Well-known instruments (Keysight 34461A, Keithley 2400, etc.) where the model can recall specs without re-reading the datasheet |

Fast path. For instruments the model doesn't know well, use `datasheet-to-catalog` instead.

## Sub-agent templates

Single-responsibility prompts the workflows spawn as sub-agents. **Not invoked directly.** Each names a recommended model size for its job — the `Tier` column below.

| Template | Job | Tier |
|---|---|---|
| [`section-splitter`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/agents/section-splitter.md) | Read a datasheet PDF, divide into processing sections (page ranges). No YAML, no extraction. | mid-to-high |
| [`section-extractor`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/agents/section-extractor.md) | Read PDF pages, produce a complete structured inventory file. Extraction only, no schema knowledge. | high |
| [`section-writer`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/agents/section-writer.md) | Convert a pre-extracted inventory into catalog YAML capabilities. Does NOT re-read the PDF. | high |
| [`section-reviewer`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/agents/section-reviewer.md) | Review AND fix catalog YAML against the inventory. Semantic checks only, no PDF access. | high |
| [`scaffold-writer`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/agents/scaffold-writer.md) | Read targeted pages and write the device-level YAML (channels, interfaces, board attributes). Does NOT extract capabilities. | high |

The single-responsibility split is deliberate. Each agent does one job with a narrow context; the workflow chains them with a fix loop (extractor → writer → reviewer → fix → reviewer until clean). A single agent doing all four jobs is more error-prone; the chain catches mistakes at each boundary between agents.

## Slash commands

Per-client wrappers that invoke the workflows from your editor's slash-command UI.

| Command | Clients | Invokes |
|---|---|---|
| `/catalog-from-datasheet [pdf] [yaml]` | Claude Code, Copilot | `datasheet-to-catalog` workflow on a single PDF |
| `/process-catalog` | Claude Code, Copilot | Walks `catalog/QUEUE.md`, runs `/catalog-from-datasheet` on each pending entry |

Installed automatically by `litmus setup <client>`:

| Client | Setup command | Where commands install |
|---|---|---|
| Claude Code | `litmus setup claude-code` | `./.claude/commands/` (project-local) |
| GitHub Copilot | `litmus setup copilot` | `.github/prompts/` (project-local) |
| Claude Desktop | `litmus setup claude-desktop` | n/a — slash commands not supported; MCP only |
| Cursor | `litmus setup cursor` | n/a — instructions via `AGENTS.md`; MCP via `.cursor/mcp.json` |
| OpenAI Codex | `litmus setup codex` | n/a — instructions via `AGENTS.md`; MCP entry printed for `~/.codex/config.toml` |
| Cline | `litmus setup cline` | n/a — slash commands not supported; MCP only |

Claude Desktop, Cursor, Codex, and Cline get the MCP server registration (so the agent can call `litmus_*` tools), but workflow invocation is conversational: "run the datasheet-to-test workflow on this PDF" instead of typing a slash command. The workflow prompt itself is the same.

## MCP tools the workflows call

The 13 MCP tools exposed by the `litmus mcp serve` server (the AI client launches it on demand over stdio; you don't run it yourself). Per-tool parameter detail in the [API reference](../runtime/api.md#tools).

| Tool | Workflows that use it |
|---|---|
| `litmus_project` (init / save / read / lookup_enum / enum_reference) | All workflows |
| `litmus_match` | `datasheet-to-test` |
| `litmus_run` | `datasheet-to-test` |
| `litmus_open` | `datasheet-to-test` |
| `litmus_discover` | `datasheet-to-test` |
| `litmus_schema` | (available; rarely called by workflows directly) |
| `litmus_events`, `litmus_sessions`, `litmus_channels`, `litmus_files`, `litmus_runs`, `litmus_steps`, `litmus_metrics` | Post-run analysis (available to any agent) |

## MCP prompts

Workflows that MCP clients can fetch as a prompt — an alternative to slash commands for clients that surface a prompt list.

| Prompt | Returns | Equivalent to |
|---|---|---|
| `datasheet-to-test` | The full workflow text | The slash command, but discoverable via the MCP prompts list |

## What the setup commands install

For reference, the full per-client install scope of `litmus setup <client>`:

### `litmus setup claude-code`

1. Registers the MCP server: `claude mcp add litmus -- <litmus-bin> mcp serve`
2. Copies slash command stubs: `skills/commands/claude-code/*.md` → `./.claude/commands/`
3. Writes or merges `./CLAUDE.md` from `skills/templates/project-instructions.md` (Litmus context the agent always reads)

### `litmus setup copilot`

1. Writes `.vscode/mcp.json` (MCP server registration)
2. Writes `.github/copilot-instructions.md` (Litmus context for Copilot Chat)
3. Copies slash command stubs: `skills/commands/copilot/*.prompt.md` → project

### `litmus setup claude-desktop`

Builds a `litmus.mcpb` Desktop Extension bundle on the user's Desktop. Double-click to install. `--legacy` writes JSON config to `~/.config/Claude/claude_desktop_config.json` instead.

### `litmus setup cursor`

1. Writes `.cursor/mcp.json` in the project directory
2. Writes or merges `./AGENTS.md` from `skills/templates/project-instructions.md` (Cursor reads `AGENTS.md` natively)

### `litmus setup codex`

1. Writes or merges `./AGENTS.md` from `skills/templates/project-instructions.md` (Codex's native context file)
2. Prints the `[mcp_servers.litmus]` entry to add to `~/.codex/config.toml` (user-global config — Litmus prints it rather than editing another tool's home config)

### `litmus setup cline`

Writes `cline_mcp_settings.json` to VS Code user settings (`~/.config/Code/User/` on Linux, `~/Library/Application Support/Code/User/` on macOS, `~/AppData/Roaming/Code/User/` on Windows).

All `litmus setup` commands accept `--print-only` to show the config that would be written without modifying anything on disk.

## Reference cards (`litmus refs`)

On-demand topic guides under [`refs/`](https://github.com/pragmatest-dev/litmus/tree/main/src/litmus/skills/refs). Any agent (or human) streams them from the installed package — `litmus refs list` enumerates topics, `litmus refs show <topic>` prints one. The generated instructions file indexes them so the agent pulls a topic only when the request needs it. Topics: `routing` (any request → the right tool, start here), `solutions` (the simple→advanced arc keyed to `examples/01…12`), `test-writing`, `fixtures`, `tiers`, `verify`, `observe`, `streaming`, `artifacts`, `instruments`, `part-specs`, `mocks`, `profiles`, `multi-site`, `debugging`, `analytics`, `project-setup`.

[`templates/project-instructions.md`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/templates/project-instructions.md) is installed as the project's `CLAUDE.md` / `.github/copilot-instructions.md` / `AGENTS.md` so the agent has Litmus context in every conversation.

## See also

- [Concepts: why AI integration](../../concepts/overview/ai-integration.md) — motivation
- [How-to: datasheet-to-test workflow](../../how-to/catalog/datasheet-to-test.md) — end-to-end walkthrough
- [How-to: MCP integration](../../how-to/overview/mcp-integration.md) — registering the server with each AI client
- [Reference: MCP tools](../runtime/api.md#tools) — per-tool parameter detail
