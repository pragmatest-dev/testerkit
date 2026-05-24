# Skills reference

Litmus ships a set of **AI workflow prompts** under `src/litmus/skills/` that drive Claude / Copilot / Cursor / Cline through hardware-test authoring tasks. This page is the inventory: what each prompt does, what it calls, and how it's installed.

For motivation (why AI integration at all), see [concepts/why-ai-integration](../concepts/overview/ai-integration.md). For setup commands (`litmus setup <client>`), see [how-to/mcp-integration](../how-to/overview/mcp-integration.md).

## Three layers

```
┌─────────────────────────────────────────────────────────────┐
│  Workflows         Multi-step prompts you invoke directly.  │
│  (3 files)         Drive the whole datasheet-to-tests flow. │
├─────────────────────────────────────────────────────────────┤
│  Sub-agent         Single-job prompts that workflows spawn  │
│  templates         via the Task tool. Not invoked directly. │
│  (5 files)                                                  │
├─────────────────────────────────────────────────────────────┤
│  Slash commands    Client-specific wrappers that invoke the │
│  (2 per client)    workflows from your editor's prompt UI.  │
└─────────────────────────────────────────────────────────────┘
```

All three layers ship as plain markdown — you can read every prompt in `src/litmus/skills/` of the installed wheel or the [source on GitHub](https://github.com/pragmatest-dev/litmus/tree/main/src/litmus/skills).

## Workflows

User-invocable. Multi-step. STOP at every approval gate.

### `datasheet-to-test`

| | |
|---|---|
| Source | [`workflow/datasheet-to-test.md`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/workflow/datasheet-to-test.md) |
| Input | A product datasheet PDF |
| Output | `products/<id>.yaml`, `stations/<id>.yaml`, `tests/test_<id>.py`, `tests/test_<id>.yaml` |
| Phases | Parse datasheet → save product spec → recommend instruments → create station config → generate tests → execute |
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

Single-responsibility prompts the workflows spawn via the Task tool. **Not invoked directly.** Each ships with a recommended model tier (see the source file for the per-agent justification).

| Template | Job | Tier |
|---|---|---|
| [`section-splitter`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/agents/section-splitter.md) | Read a datasheet PDF, divide into processing sections (page ranges). No YAML, no extraction. | mid-to-high |
| [`section-extractor`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/agents/section-extractor.md) | Read PDF pages, produce a complete structured inventory file. Extraction only, no schema knowledge. | high |
| [`section-writer`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/agents/section-writer.md) | Convert a pre-extracted inventory into catalog YAML capabilities. Does NOT re-read the PDF. | high |
| [`section-reviewer`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/agents/section-reviewer.md) | Review AND fix catalog YAML against the inventory. Semantic checks only, no PDF access. | high |
| [`scaffold-writer`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/agents/scaffold-writer.md) | Read targeted pages and write the device-level YAML (channels, interfaces, board attributes). Does NOT extract capabilities. | high |

The single-responsibility split is deliberate. Each agent does one job with a narrow context; the workflow chains them with a fix loop (extractor → writer → reviewer → fix → reviewer until clean). A single mega-agent doing all four jobs tends to confabulate; the chain catches errors at the boundary between agents.

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
| Cursor | `litmus setup cursor` | n/a — slash commands not supported; MCP only |
| Cline | `litmus setup cline` | n/a — slash commands not supported; MCP only |

Claude Desktop, Cursor, and Cline get the MCP server registration (so the agent can call `litmus_*` tools), but workflow invocation is conversational: "run the datasheet-to-test workflow on this PDF" instead of typing a slash command. The workflow prompt itself is the same.

## MCP tools the workflows call

The 12 MCP tools exposed by `litmus mcp serve`. Per-tool parameter detail in the [API reference](api.md#tools).

| Tool | Workflows that use it |
|---|---|
| `litmus_project` (init / save / read / lookup_enum / enum_reference) | All workflows |
| `litmus_match` | `datasheet-to-test` |
| `litmus_run` | `datasheet-to-test` |
| `litmus_open` | `datasheet-to-test` |
| `litmus_discover` | `datasheet-to-test` |
| `litmus_schema` | (available; rarely called by workflows directly) |
| `litmus_events`, `litmus_sessions`, `litmus_channels`, `litmus_runs`, `litmus_steps`, `litmus_metrics` | Post-run analysis (available to any agent) |

## MCP prompts

Prompts that AI clients can fetch via the MCP `prompts/get` protocol method (alternative to slash commands for clients that prefer prompt-list discovery).

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

Writes `.cursor/mcp.json` in the project directory.

### `litmus setup cline`

Writes `cline_mcp_settings.json` to VS Code user settings (`~/.config/Code/User/` on Linux, `~/Library/Application Support/Code/User/` on macOS, `~/AppData/Roaming/Code/User/` on Windows).

All `litmus setup` commands accept `--print-only` to show the config that would be written without modifying anything on disk.

## Reference material the workflows load

| File | Used as background context by |
|---|---|
| [`refs/profiles.md`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/refs/profiles.md) | Workflows that touch profile config — explains the facet-query selection model |
| [`templates/project-instructions.md`](https://github.com/pragmatest-dev/litmus/blob/main/src/litmus/skills/templates/project-instructions.md) | Installed as the project's `CLAUDE.md` / `copilot-instructions.md` so the agent has Litmus context in every conversation |

## See also

- [Concepts: why AI integration](../concepts/overview/ai-integration.md) — motivation
- [How-to: datasheet-to-test workflow](../how-to/catalog/datasheet-to-test.md) — end-to-end walkthrough
- [How-to: MCP integration](../how-to/overview/mcp-integration.md) — registering the server with each AI client
- [Reference: MCP tools](api.md#tools) — per-tool parameter detail
