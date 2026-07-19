# TesterKit Agent Skills

Package data shipped inside `testerkit`. Each `testerkit-<domain>/` directory is a
self-contained [Agent Skill](https://agentskills.io/) — `SKILL.md` (name +
trigger-shaped description frontmatter, an action playbook under 500 lines)
that points out to the shipped docs (`testerkit docs show <path>`) for deeper
detail instead of carrying its own frozen copy. `testerkit-datasheets` is the
exception — its pipeline is a procedure, not a doc topic, so it keeps its own
`references/` and `agents/`.

## Layout

```
skills/
├── testerkit-tests/        front door — test / measure / log a value, simple → advanced
├── testerkit-mocks/        run without hardware
├── testerkit-stations/     set up a bench / wire an instrument
├── testerkit-parts/        spec a DUT's characteristics and limits
├── testerkit-profiles/     different limits/behavior per test phase
├── testerkit-sites/        test multiple units in parallel
├── testerkit-capture/      capture a waveform or file during a test
├── testerkit-data/         read/query/export runs, steps, measurements, channels, files
├── testerkit-analysis/     yield / Ppk / Pareto / trend metrics
├── testerkit-debug/        triage why a run failed
├── testerkit-interactive/  guided/conversational test-writing on-ramp
├── testerkit-datasheets/   import an instrument/part datasheet PDF (also has agents/ + references/)
├── templates/
│   └── project-instructions.md   thin always-on context (CLAUDE.md / AGENTS.md / copilot-instructions.md)
└── README.md             this file — not a skill (no frontmatter)
```

Each `testerkit-<domain>` skill covers exactly one user-trigger and drives real
TesterKit actions (CLI commands, MCP tools, generated YAML) rather than mirroring
`docs/` inline — it points at the shipped docs by path instead. `testerkit-tests`
is the catch-all entry point; the rest have narrow, distinct triggers so an
assistant can pick the right one straight from its `description`.

## Installation

Skills aren't installed by users directly — `testerkit setup <tool>` projects
every skill dir (any immediate child of `skills/` with a `SKILL.md`) into the
target tool's native skills location, and writes/updates the managed section
of that tool's always-on context file from `templates/project-instructions.md`:

| Tool | Skills copied to | Always-on file |
|---|---|---|
| `testerkit setup claude-code` | `.claude/skills/` | `CLAUDE.md` |
| `testerkit setup codex` | `.agents/skills/` | `AGENTS.md` |
| `testerkit setup cursor` | `.cursor/skills/` | `AGENTS.md` |
| `testerkit setup copilot` | `.github/skills/` | `.github/copilot-instructions.md` + `AGENTS.md` |

`testerkit setup claude-desktop` bundles the whole `skills/` tree into the
`.mcpb` Desktop Extension instead of a per-tool copy. `testerkit setup cline` only
registers the MCP server (Cline doesn't have a skills mechanism).

The projection is a straight copy — the packaged skill is the source of truth,
so project-local copies are always overwritten on the next `testerkit setup` run
rather than hand-edited.
