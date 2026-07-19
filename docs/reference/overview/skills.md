# Skills reference

TesterKit ships 12 **Agent Skills** — one directory per skill under
[`src/testerkit/skills/`](https://github.com/pragmatest-dev/testerkit/tree/main/src/testerkit/skills),
each a `SKILL.md` written to the
[Agent Skills open standard](https://github.blog/changelog/2025-12-18-github-copilot-now-supports-agent-skills/):
YAML frontmatter (`name`, `description`) an agent matches against the user's
request, then a body the agent reads once matched. This page is the
inventory — what each skill covers, how the depth behind it is sourced, and
how `testerkit setup <tool>` gets the files in front of your agent.

For motivation (why AI integration at all), see
[concepts/why-ai-integration](../../concepts/overview/ai-integration.md). For
registering the MCP server and instructions file per tool, see
[how-to/mcp-integration](../../how-to/overview/mcp-integration.md).

## The 12 skills

| Skill | Use when |
|---|---|
| `testerkit-tests` | Testing, measuring, or logging any hardware value — the front door. Starts at zero config, grows only as the request demands. |
| `testerkit-mocks` | Running tests without real hardware — dev-machine smoke tests, CI, a bench that's tied up, or pinning one instrument call's return value. |
| `testerkit-stations` | Setting up the bench — instruments, roles, a bring-your-own driver, discovery, fixture pin routing. |
| `testerkit-parts` | Specifying the DUT — documented characteristics, pin map, or a datasheet limit as reusable part YAML. |
| `testerkit-profiles` | Different limits, sweeps, mocks, or wiring per test phase or part variant, selected with a CLI flag at run time. |
| `testerkit-sites` | Testing multiple UUTs at once on one fixture — multi-site / multi-socket parallel production testing. |
| `testerkit-capture` | Capturing non-tabular evidence during a test — a waveform, a live sensor feed, a photo, a vendor capture file, a log. |
| `testerkit-data` | Reading, querying, or exporting existing test data — runs, steps, measurements, channels, files, events — via the models, CLI, Query API, or MCP. |
| `testerkit-analysis` | Computing a statistic across runs — yield, Pareto, Ppk, a trend, a retest rate, time-loss. |
| `testerkit-debug` | Triaging why a run failed, errored, looks wrong, or is missing. |
| `testerkit-interactive` | Pausing a test for operator input, building a custom live operator screen, or driving a station interactively outside pytest. |
| `testerkit-datasheets` | Importing an instrument or part datasheet PDF into catalog or part config. |

Each row above is a condensed reading of that skill's own `description`
frontmatter — the exact text an agent matches against. `testerkit-tests` is
the deliberate front door: it starts at a bare `def test_x(verify): ...`
with no station, no YAML, and only routes to a sibling skill (mocks,
stations, parts, profiles, sites, capture, data, analysis, debug,
interactive) once the request needs that layer.

(`testerkit-capture` writes non-tabular evidence, `testerkit-data` reads any store
back, `testerkit-analysis` computes statistics over the tabular data — three
distinct jobs on the data axis, no overlap.)

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
testerkit docs show concepts/overview/tiers
testerkit docs show how-to/execution/writing-tests
```

This keeps exactly one copy of any factual claim — the shipped `docs/`
tree — instead of a frozen paraphrase inside the skill that drifts the
next time the underlying behavior changes. `testerkit docs show <path>`
streams that same tree; see [The `testerkit docs` CLI](#the-testerkit-docs-cli)
below.

Two skills carry a `references/` subdirectory for content that has no
shipped-doc home:

- **`testerkit-datasheets`** — `references/catalog-pipeline.md`,
  `test-pipeline.md`, `scaffold.md`, `process-queue.md` are the full
  phase-by-phase orchestration contracts for its four import pipelines
  (too long and too procedural for a `docs/` page). It also carries
  `agents/` — five sub-agent prompts (`section-splitter`,
  `scaffold-writer`, `section-extractor`, `section-writer`,
  `section-reviewer`) the catalog pipeline spawns in sequence; each is
  single-responsibility and not invoked directly by a user.
- **`testerkit-interactive`** — `references/live-ui-patterns.md` is the
  decision table + anti-pattern catalog for building a custom NiceGUI
  page against live channel data (subscribe-vs-raw-thread, when to
  stop mutating from a callback).

Every other skill's `SKILL.md` is self-contained plus links out to `testerkit
docs show` — no `references/` directory.

## Install / cross-tool

`testerkit setup <tool>` copies the packaged skill directories into the
tool's **native** Agent-Skills path — the same `SKILL.md` file, read
directly, no per-tool adapter or reformatting:

| Tool | Setup command | Skills copied to | Instructions file | MCP registration |
|---|---|---|---|---|
| Claude Code | `testerkit setup claude-code` | `.claude/skills/` | `CLAUDE.md` | `claude mcp add testerkit -- <bin> mcp serve` |
| OpenAI Codex | `testerkit setup codex` | `.agents/skills/` | `AGENTS.md` | printed for `~/.codex/config.toml` (Codex's home config; TesterKit doesn't write another tool's home config) |
| Cursor | `testerkit setup cursor` | `.cursor/skills/` | `AGENTS.md` | `.cursor/mcp.json` |
| GitHub Copilot | `testerkit setup copilot` | `.github/skills/` | `.github/copilot-instructions.md` + `AGENTS.md` | `.vscode/mcp.json` |

All four read `SKILL.md` natively — Claude Code and Codex from the start;
Cursor since 2.4 (shipped 2026-01-22); GitHub Copilot since its Agent
Skills support shipped 2025-12-18 (stable in VS Code early January 2026),
across VS Code/JetBrains agent mode, Copilot CLI, and the coding agent.
There is no Cursor `.mdc` rules file and no Copilot `.prompt.md` wrapper
generated — the single `SKILL.md` is the whole artifact.

The instructions file (`CLAUDE.md` / `AGENTS.md` /
`.github/copilot-instructions.md`) is marker-managed: `testerkit setup`
writes the TesterKit section between `<!-- testerkit:start -->` /
`<!-- testerkit:end -->` markers, creating the file if it doesn't exist or
replacing only that section if it does, leaving the rest of the file
alone.

`testerkit setup claude-desktop` and `testerkit setup cline` are MCP-only —
neither projects a native skills directory. `claude-desktop` bundles the
skill files inside the `.mcpb` extension as reference material rather
than a loose, natively-read directory; `cline` writes only
`cline_mcp_settings.json`.

Every `testerkit setup <tool>` command accepts `--print-only` to preview all
of its side effects — skills copied, instructions file created/updated,
MCP config — without writing anything.

## The `testerkit docs` CLI

```cli
$ testerkit docs list reference
reference/catalog/schema
reference/cli
reference/overview/pytest-native
reference/overview/skills
...

$ testerkit docs show concepts/data/three-verbs
# Three verbs: configure, observe, verify
...
```

`testerkit docs list [section]` enumerates shipped doc pages (optionally
scoped to `concepts`, `how-to`, `reference`, `tutorial`, or `integration`);
`testerkit docs show <path>` prints one page to stdout, resolved with or
without a trailing `.md`. Both read from the same `docs/` tree that ships
in the installed package (a bundled copy in a wheel install, the repo's
`docs/` directory in an editable/source checkout) — the environment-stable
way for an agent (or a human) to read TesterKit documentation without baking
an absolute path into project config. This is the single source every
skill's "Deeper" section points at, and it replaces the removed `testerkit
refs` command — there is no separate curated reference corpus to keep in
sync.

## MCP vs skills

Two different jobs, not overlapping ones:

- **MCP** is live execution and introspection — `testerkit_project`,
  `testerkit_run`, `testerkit_match`, `testerkit_discover`, `testerkit_runs`,
  `testerkit_metrics`, and the rest of the tool surface the `testerkit mcp
  serve` server exposes. Calling one of these does something or reads
  something right now. Full per-tool detail: [API reference →
  MCP tools](../runtime/api.md#tools).
- **Skills** are judgment and procedure — which verb to reach for, which
  config rung a request actually needs, what to check before declaring a
  pipeline done. A skill matching doesn't execute anything by itself; it
  tells the agent which MCP tools or CLI commands to call and in what
  order, and where to read further before it does.

A skill's playbook routes into MCP tools and CLI commands as the steps
require — `testerkit-tests` step 6 runs plain `pytest`; `testerkit-datasheets`
saves through `testerkit_project(action="save", ...)`; `testerkit-debug` reads
back through `testerkit_runs` / `testerkit_steps`. Neither layer substitutes for
the other.

## See also

- [Concepts: why AI integration](../../concepts/overview/ai-integration.md) — motivation
- [How-to: MCP integration](../../how-to/overview/mcp-integration.md) — registering the server and instructions file with each AI tool
- [Reference: MCP tools](../runtime/api.md#tools) — per-tool parameter detail
- [Reference: CLI](../cli.md) — `testerkit docs`, `testerkit setup`, and every other command
