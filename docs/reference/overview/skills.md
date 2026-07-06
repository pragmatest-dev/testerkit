# Skills reference

Litmus ships 11 **Agent Skills** — one directory per skill under
[`src/litmus/skills/`](https://github.com/pragmatest-dev/litmus/tree/main/src/litmus/skills),
each a `SKILL.md` written to the
[Agent Skills open standard](https://github.blog/changelog/2025-12-18-github-copilot-now-supports-agent-skills/):
YAML frontmatter (`name`, `description`) an agent matches against the user's
request, then a body the agent reads once matched. This page is the
inventory — what each skill covers, how the depth behind it is sourced, and
how `litmus setup <tool>` gets the files in front of your agent.

For motivation (why AI integration at all), see
[concepts/why-ai-integration](../../concepts/overview/ai-integration.md). For
registering the MCP server and instructions file per tool, see
[how-to/mcp-integration](../../how-to/overview/mcp-integration.md).

## The 11 skills

| Skill | Use when |
|---|---|
| `litmus-tests` | Testing, measuring, or logging any hardware value — the front door. Starts at zero config, grows only as the request demands. |
| `litmus-mocks` | Running tests without real hardware — dev-machine smoke tests, CI, a bench that's tied up, or pinning one instrument call's return value. |
| `litmus-stations` | Setting up the bench — instruments, roles, a bring-your-own driver, discovery, fixture pin routing. |
| `litmus-parts` | Specifying the DUT — documented characteristics, pin map, or a datasheet limit as reusable part YAML. |
| `litmus-profiles` | Different limits, sweeps, mocks, or wiring per test phase or part variant, selected with a CLI flag at run time. |
| `litmus-sites` | Testing multiple UUTs at once on one fixture — multi-site / multi-socket parallel production testing. |
| `litmus-capture` | Capturing or reading back non-tabular evidence — a waveform, a live sensor feed, a photo, a vendor capture file, a log. |
| `litmus-analysis` | Getting an answer out of existing runs — yield, Pareto, Ppk, a trend, a retest rate, an export or report. |
| `litmus-debug` | Triaging why a run failed, errored, looks wrong, or is missing. |
| `litmus-interactive` | Pausing a test for operator input, building a custom live operator screen, or driving a station interactively outside pytest. |
| `litmus-datasheets` | Importing an instrument or part datasheet PDF into catalog or part config. |

Each row above is a condensed reading of that skill's own `description`
frontmatter — the exact text an agent matches against. `litmus-tests` is
the deliberate front door: it starts at a bare `def test_x(verify): ...`
with no station, no YAML, and only routes to a sibling skill (mocks,
stations, parts, profiles, sites, capture, analysis, debug, interactive)
once the request needs that layer.

## Single-source design

A `SKILL.md` is not a standalone essay. It carries the **action
playbook** — the decision the agent has to make and the judgment calls
around it (which verb, which config rung, what to warn about) — in a body
short enough to read in full every time the skill matches. Anything that's
already documented at length lives in the shipped docs, and the skill
tells the agent to read it there instead of re-stating it:

```
## Deeper
Read the docs:
litmus docs show concepts/overview/tiers
litmus docs show how-to/execution/writing-tests
```

This keeps exactly one copy of any factual claim — the shipped `docs/`
tree — instead of a frozen paraphrase inside the skill that drifts the
next time the underlying behavior changes. `litmus docs show <path>`
streams that same tree; see [The `litmus docs` CLI](#the-litmus-docs-cli)
below.

Two skills carry a `references/` subdirectory for content that has no
shipped-doc home:

- **`litmus-datasheets`** — `references/catalog-pipeline.md`,
  `test-pipeline.md`, `scaffold.md`, `process-queue.md` are the full
  phase-by-phase orchestration contracts for its four import pipelines
  (too long and too procedural for a `docs/` page). It also carries
  `agents/` — five sub-agent prompts (`section-splitter`,
  `scaffold-writer`, `section-extractor`, `section-writer`,
  `section-reviewer`) the catalog pipeline spawns in sequence; each is
  single-responsibility and not invoked directly by a user.
- **`litmus-interactive`** — `references/live-ui-patterns.md` is the
  decision table + anti-pattern catalog for building a custom NiceGUI
  page against live channel data (subscribe-vs-raw-thread, when to
  stop mutating from a callback).

Every other skill's `SKILL.md` is self-contained plus links out to `litmus
docs show` — no `references/` directory.

## Install / cross-tool

`litmus setup <tool>` copies the packaged skill directories into the
tool's **native** Agent-Skills path — the same `SKILL.md` file, read
directly, no per-tool adapter or reformatting:

| Tool | Setup command | Skills copied to | Instructions file | MCP registration |
|---|---|---|---|---|
| Claude Code | `litmus setup claude-code` | `.claude/skills/` | `CLAUDE.md` | `claude mcp add litmus -- <bin> mcp serve` |
| OpenAI Codex | `litmus setup codex` | `.agents/skills/` | `AGENTS.md` | printed for `~/.codex/config.toml` (Codex's home config; Litmus doesn't write another tool's home config) |
| Cursor | `litmus setup cursor` | `.cursor/skills/` | `AGENTS.md` | `.cursor/mcp.json` |
| GitHub Copilot | `litmus setup copilot` | `.github/skills/` | `.github/copilot-instructions.md` + `AGENTS.md` | `.vscode/mcp.json` |

All four read `SKILL.md` natively — Claude Code and Codex from the start;
Cursor since 2.4 (shipped 2026-01-22); GitHub Copilot since its Agent
Skills support shipped 2025-12-18 (stable in VS Code early January 2026),
across VS Code/JetBrains agent mode, Copilot CLI, and the coding agent.
There is no Cursor `.mdc` rules file and no Copilot `.prompt.md` wrapper
generated — the single `SKILL.md` is the whole artifact.

The instructions file (`CLAUDE.md` / `AGENTS.md` /
`.github/copilot-instructions.md`) is marker-managed: `litmus setup`
writes the Litmus section between `<!-- litmus:start -->` /
`<!-- litmus:end -->` markers, creating the file if it doesn't exist or
replacing only that section if it does, leaving the rest of the file
alone.

`litmus setup claude-desktop` and `litmus setup cline` are MCP-only —
neither projects a native skills directory. `claude-desktop` bundles the
skill files inside the `.mcpb` extension as reference material rather
than a loose, natively-read directory; `cline` writes only
`cline_mcp_settings.json`.

Every `litmus setup <tool>` command accepts `--print-only` to preview all
of its side effects — skills copied, instructions file created/updated,
MCP config — without writing anything.

## The `litmus docs` CLI

```cli
$ litmus docs list reference
reference/catalog/schema
reference/cli
reference/overview/pytest-native
reference/overview/skills
...

$ litmus docs show concepts/data/three-verbs
# Three verbs: configure, observe, verify
...
```

`litmus docs list [section]` enumerates shipped doc pages (optionally
scoped to `concepts`, `how-to`, `reference`, `tutorial`, or `integration`);
`litmus docs show <path>` prints one page to stdout, resolved with or
without a trailing `.md`. Both read from the same `docs/` tree that ships
in the installed package (a bundled copy in a wheel install, the repo's
`docs/` directory in an editable/source checkout) — the environment-stable
way for an agent (or a human) to read Litmus documentation without baking
an absolute path into project config. This is the single source every
skill's "Deeper" section points at, and it replaces the removed `litmus
refs` command — there is no separate curated reference corpus to keep in
sync.

## MCP vs skills

Two different jobs, not overlapping ones:

- **MCP** is live execution and introspection — `litmus_project`,
  `litmus_run`, `litmus_match`, `litmus_discover`, `litmus_runs`,
  `litmus_metrics`, and the rest of the tool surface the `litmus mcp
  serve` server exposes. Calling one of these does something or reads
  something right now. Full per-tool detail: [API reference →
  MCP tools](../runtime/api.md#tools).
- **Skills** are judgment and procedure — which verb to reach for, which
  config rung a request actually needs, what to check before declaring a
  pipeline done. A skill matching doesn't execute anything by itself; it
  tells the agent which MCP tools or CLI commands to call and in what
  order, and where to read further before it does.

A skill's playbook routes into MCP tools and CLI commands as the steps
require — `litmus-tests` step 6 runs plain `pytest`; `litmus-datasheets`
saves through `litmus_project(action="save", ...)`; `litmus-debug` reads
back through `litmus_runs` / `litmus_steps`. Neither layer substitutes for
the other.

## See also

- [Concepts: why AI integration](../../concepts/overview/ai-integration.md) — motivation
- [How-to: MCP integration](../../how-to/overview/mcp-integration.md) — registering the server and instructions file with each AI tool
- [Reference: MCP tools](../runtime/api.md#tools) — per-tool parameter detail
- [Reference: CLI](../cli.md) — `litmus docs`, `litmus setup`, and every other command
