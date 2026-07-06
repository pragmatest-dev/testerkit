---
name: litmus-skills
description: AI-assistant guidance shipped inside the litmus package — always-on instructions, on-demand reference cards, step-by-step workflows, and per-tool command stubs for Claude Code, Copilot, Codex, and Cursor.
---

# Litmus AI guidance — what lives here and why

This tree is the **single canonical source** of AI-assistant guidance that ships
inside the `litmus-test` package. Users never edit it; `litmus setup <tool>`
projects it into their repo in whatever shape their AI tool reads, and the
`litmus refs` CLI streams pieces of it on demand. One source, thin per-tool
adapters.

## The four layers

| Layer | Directory | Loaded | Consumed via |
|---|---|---|---|
| **Always-on instructions** | `templates/project-instructions.md` | every conversation | `litmus setup <tool>` renders it into `CLAUDE.md`, `.github/copilot-instructions.md`, and `AGENTS.md` (Codex, Cursor, Copilot CLI) between `<!-- litmus:start/end -->` markers |
| **Reference cards** | `refs/*.md` | on demand, one topic at a time | `litmus refs list` / `litmus refs show <topic>` — env-stable, works from any AI tool that can run a shell command |
| **Workflows** | `workflow/*.md` + `catalog-scaffold.md` | when a multi-step procedure is invoked | referenced by command stubs; followed step-by-step, never summarized |
| **Command stubs** | `commands/<tool>/*.md` | user types the command | `litmus setup claude-code` → `.claude/commands/`; `litmus setup copilot` → `.github/prompts/*.prompt.md` |

`agents/*.md` are subagent prompt templates spawned *by* workflows (the
datasheet pipeline); they are internal to the workflows, never invoked directly.

## Design rules for this tree

1. **Instructions stay small; refs carry the depth.** The always-on template is
   an index — verbs, commands, and a table of `litmus refs show` topics. Detail
   lives in one ref per topic so an AI loads only what the request needs
   (progressive disclosure).
2. **Refs are tool-agnostic.** They are plain markdown streamed over stdout —
   no frontmatter magic, no tool-specific syntax. Any assistant on any tool can
   read them.
3. **One topic, one ref.** A ref answers one category of request. The router
   (`refs/routing.md`) is the front door that maps a request → verb + rung +
   the ref that goes deeper.
4. **Command stubs are pointers, not content.** A stub names the workflow file
   to follow and adapts invocation syntax to the tool. Never duplicate workflow
   steps into a stub.
5. **Everything here is guarded.** `tests/test_ai_surfaces_accuracy.py` runs
   the canonical snippets against the real plugin and models — a verb rename or
   schema change breaks CI instead of silently shipping stale advice.
6. **Right-size by default.** Every surface teaches the start-simple ladder:
   zero config → sidecar → mock instruments → part spec → profiles. An AI
   following this tree should never scaffold a station for a one-off check.

## Per-tool projection (`litmus setup …`)

| Tool | Instructions | On-demand | Commands | MCP |
|---|---|---|---|---|
| Claude Code | `CLAUDE.md` | `litmus refs` | `.claude/commands/` | `claude mcp add` |
| Copilot (VS Code) | `.github/copilot-instructions.md` + `AGENTS.md` | `litmus refs` | `.github/prompts/` | `.vscode/mcp.json` |
| Codex | `AGENTS.md` | `litmus refs` | — | `~/.codex/config.toml` (printed) |
| Cursor | `AGENTS.md` | `litmus refs` | — | `.cursor/mcp.json` |
| Claude Desktop | bundled in `.mcpb` | MCP tools | — | `.mcpb` extension |

## Adding guidance

- New topic → new `refs/<topic>.md` (auto-discovered by `litmus refs list`),
  then add its row to the reference table in `templates/project-instructions.md`
  and, if it routes requests, a pointer in `refs/routing.md`.
- New multi-step procedure → `workflow/<name>.md` + a stub per tool under
  `commands/`.
- Update the anti-drift test when a surface makes a new runnable claim.
