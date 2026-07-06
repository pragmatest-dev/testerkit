# Litmus Agent Skills

Package data shipped inside `litmus`. Each `litmus-<domain>/` directory is a
self-contained [Agent Skill](https://agentskills.io/) — `SKILL.md` (name +
trigger-shaped description frontmatter, an action playbook under 500 lines)
that points out to the shipped docs (`litmus docs show <path>`) for deeper
detail instead of carrying its own frozen copy. `litmus-datasheets` is the
exception — its pipeline is a procedure, not a doc topic, so it keeps its own
`references/` and `agents/`.

## Layout

```
skills/
├── litmus-tests/        front door — test / measure / log a value, simple → advanced
├── litmus-mocks/        run without hardware
├── litmus-stations/     set up a bench / wire an instrument
├── litmus-parts/        spec a DUT's characteristics and limits
├── litmus-profiles/     different limits/behavior per test phase
├── litmus-sites/        test multiple units in parallel
├── litmus-capture/      capture/read back waveforms and files
├── litmus-analysis/     yield / Ppk / query existing runs
├── litmus-debug/        triage why a run failed
├── litmus-interactive/  guided/conversational test-writing on-ramp
├── litmus-datasheets/   import an instrument/part datasheet PDF (also has agents/ + references/)
├── templates/
│   └── project-instructions.md   thin always-on context (CLAUDE.md / AGENTS.md / copilot-instructions.md)
└── README.md             this file — not a skill (no frontmatter)
```

Each `litmus-<domain>` skill covers exactly one user-trigger and drives real
Litmus actions (CLI commands, MCP tools, generated YAML) rather than mirroring
`docs/` inline — it points at the shipped docs by path instead. `litmus-tests`
is the catch-all entry point; the rest have narrow, distinct triggers so an
assistant can pick the right one straight from its `description`.

## Installation

Skills aren't installed by users directly — `litmus setup <tool>` projects
every skill dir (any immediate child of `skills/` with a `SKILL.md`) into the
target tool's native skills location, and writes/updates the managed section
of that tool's always-on context file from `templates/project-instructions.md`:

| Tool | Skills copied to | Always-on file |
|---|---|---|
| `litmus setup claude-code` | `.claude/skills/` | `CLAUDE.md` |
| `litmus setup codex` | `.agents/skills/` | `AGENTS.md` |
| `litmus setup cursor` | `.cursor/skills/` | `AGENTS.md` |
| `litmus setup copilot` | `.github/skills/` | `.github/copilot-instructions.md` + `AGENTS.md` |

`litmus setup claude-desktop` bundles the whole `skills/` tree into the
`.mcpb` Desktop Extension instead of a per-tool copy. `litmus setup cline` only
registers the MCP server (Cline doesn't have a skills mechanism).

The projection is a straight copy — the packaged skill is the source of truth,
so project-local copies are always overwritten on the next `litmus setup` run
rather than hand-edited.
