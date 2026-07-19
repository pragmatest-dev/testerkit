# AI-assisted test development via MCP

TesterKit exposes a [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server whose tools expose run/query/authoring actions to AI assistants. The platform does **not** call LLMs itself — it only exposes tools that an AI agent drives.

This page is the operational how-to: registering TesterKit with each supported AI client. For motivation see [concepts/why-ai-integration](../../concepts/overview/ai-integration.md); for the end-to-end workflow walkthrough — now the `testerkit-datasheets` skill's pipeline — see [datasheet-to-test](../catalog/datasheet-to-test.md); for the full inventory of the 11 Agent Skills see [reference/skills](../../reference/overview/skills.md). Per-tool MCP reference: [api.md → MCP tools](../../reference/runtime/api.md#tools).

> **CLI as a peer surface.** Any agent with a terminal — Claude Code with Bash, Cursor with terminal, the GitHub Copilot CLI — can drive TesterKit through `testerkit …` commands instead of (or alongside) MCP. The CLI surface mirrors most of the MCP tools (`testerkit runs`, `testerkit show`, `testerkit discover`, `testerkit metrics`, `testerkit schema`, `testerkit validate`, …). See [reference/cli](../../reference/cli.md). This page is for AI clients that speak MCP natively.

> **Prerequisites.** `testerkit` installed and on `$PATH` (`pip install testerkit` — distribution `testerkit`, import `testerkit`). One of the supported AI clients listed below — Claude Code, Claude Desktop, OpenAI Codex, GitHub Copilot, Cursor, or Cline. A working project directory (`testerkit init` to scaffold one). For `testerkit_run`, a station configured in `stations/` — note `testerkit_run` always executes in mock mode (see below).

## Setup

`testerkit setup <client>` writes the right MCP config file for each supported client. All `testerkit setup <client>` commands accept `--print-only` to show the config that would be written without modifying anything on disk.

| Client | Command | What gets written |
|---|---|---|
| Claude Code (CLI) | `testerkit setup claude-code` | Registers the MCP server via `claude mcp add testerkit`, projects the 11 Agent Skills into `.claude/skills/`, and creates / updates `./CLAUDE.md` |
| Claude Desktop | `testerkit setup claude-desktop` | Builds a `testerkit.mcpb` Desktop Extension bundle on the user's Desktop (zip) for double-click install — skills ship bundled inside the `.mcpb` as reference material, not a loose skills directory. Use `--legacy` to write `~/.config/Claude/claude_desktop_config.json` directly instead. |
| OpenAI Codex | `testerkit setup codex` | Projects the 11 Agent Skills into `.agents/skills/`, creates / updates `./AGENTS.md`, and prints the MCP server entry for `~/.codex/config.toml` (Codex's home config — TesterKit doesn't write another tool's home config) |
| GitHub Copilot Chat | `testerkit setup copilot` | Project-local `.vscode/mcp.json`, projects the 11 Agent Skills into `.github/skills/`, and creates / updates `.github/copilot-instructions.md` plus `./AGENTS.md` |
| Cursor | `testerkit setup cursor` | Project-local `.cursor/mcp.json`, projects the 11 Agent Skills into `.cursor/skills/`, and creates / updates `./AGENTS.md` |
| Cline (VS Code) | `testerkit setup cline` | `cline_mcp_settings.json` in VS Code User settings (`~/.config/Code/User/` on Linux, `~/Library/Application Support/Code/User/` on macOS, `~/AppData/Roaming/Code/User/` on Windows) — MCP-only, no skills directory |
| Anything else | `testerkit mcp serve` directly | You configure your AI client manually |

All four native-skills clients (Claude Code, Codex, Cursor, Copilot) read `SKILL.md` directly from their skills path — no per-tool adapter or reformatting. See [reference/skills](../../reference/overview/skills.md) for the full skill inventory and the single-source design behind `SKILL.md`.

After running any setup command, restart the client to pick up the new MCP server. To confirm it registered, ask the assistant to list its tools (or open the client's MCP panel) — the `testerkit_*` tools should appear. If they don't, the client didn't load the server: re-run the setup command, restart the client again, and confirm the config file from the table above was written.

If the `claude` CLI isn't on `$PATH`, `testerkit setup claude-code` prints the manual `claude mcp add …` command for you to run instead of registering automatically. For the VS Code clients, run with `--print-only` first to preview the exact `.vscode/mcp.json` / `.cursor/mcp.json` it will write.

To print the exact command TesterKit registers (for a manual setup):

```bash
testerkit setup show
```

For the manual path (any client that doesn't have a `testerkit setup` subcommand), start the server with:

```bash
testerkit mcp serve
# command: testerkit
# args: ["mcp", "serve"]
# transport: stdio
```

Add a server entry to your AI client's MCP config pointing at the above. See `testerkit setup claude-desktop --print-only` for a working example you can adapt.

## The MCP tools

| Tool | Purpose | Detail |
|---|---|---|
| `testerkit_project` | Unified CRUD: init, list, get, save, read | [details below](#testerkit_project) |
| `testerkit_discover` | Scan for connected instruments across all registered protocols (VISA, NI, serial, …) | Returns the list of resources reachable on this host |
| `testerkit_match` | Find compatible instruments and stations | Two modes: requirements (catalog recommendation) and station (compatibility check) |
| `testerkit_run` | Execute a test file via pytest, return exit summary | [details below](#testerkit_run) |
| `testerkit_open` | Get a browser URL for the operator UI | Allowed `type`: `part`, `station`, `run`, `fixture` |
| `testerkit_schema` | Get the JSON Schema for a YAML type | For AI clients that want to validate before saving |
| `testerkit_events` | Query the event store | Filter by session / event type |
| `testerkit_sessions` | List sessions with metadata | Each session = one `connect()` lifetime or pytest run |
| `testerkit_channels` | Query channel data from the streaming store | For waveform / time-series readouts referenced by events |
| `testerkit_files` | List FileStore artifacts (blobs, waveforms, streaming captures) | Each row carries its `file://` URI, name, format, session / run id, created_at |
| `testerkit_metrics` | Compute yield / Pareto / Ppk / retest / time-loss | Aggregations over a date range |
| `testerkit_runs` | Query the runs view (filtered, paginated) | Same data the operator-UI runs list reads |
| `testerkit_steps` | Query the steps view (one row per step execution) | Step-level rollup with outcome and timing |

For each tool's full parameter list and return shape, see [`api.md`](../../reference/runtime/api.md#tools).

### `testerkit_project`

The CRUD entry point. One tool with an `action:` argument; the rest of the workflow goes through it.

```python
# Initialize a project (call this first)
result = testerkit_project(action="init", path="~/my-project")
project = result["project_root"]

# List entities of a type
testerkit_project(action="list", type="part", project=project)

# Get one entity
testerkit_project(action="get", type="part", id="tps54302", project=project)

# Save an entity (validated against schema)
testerkit_project(action="save", type="part", id="tps54302",
               content={...}, project=project)

# Read a file or a template
testerkit_project(action="read", path="parts/tps54302.yaml", project=project)
testerkit_project(action="read", path="template:test", project=project)
```

**Entity types depend on the action:**

- `list` / `get` accept: `station`, `part`, `fixture`, `catalog`, `instrument_asset`, `run`
- `save` accepts: `station`, `part`, `fixture`, `catalog`, `instrument_asset`, `test`

`test` is save-only; `run` is read-only. `project` is not a type — it's the path argument every other call passes.

#### Saving test code with `action="save", type="test"`

When `type="test"`, the tool writes a **Python file** under `<project_root>/tests/`. The `id` is treated as the path, and if it doesn't end in `.py` the tool appends `.py`. So this tool cannot write the colocated sidecar (`tests/test_<module>.yaml`) — it would force a `.py` extension. Write the sidecar YAML directly to disk with your AI client's filesystem tool, not via `testerkit_project`.

### `testerkit_run`

Runs the test file with pytest in mock mode and returns the pass/fail summary. **It does not return structured measurement results** — those land in the parquet store and are queried separately via `testerkit_runs` / `testerkit_metrics` / `testerkit_steps`.

`testerkit_run` always runs with `--mock-instruments` — `station=` selects which station's `mock_config` to use, but no real hardware is touched. To run against a real bench, drive pytest directly: `pytest --station=<bench> --uut-serial=<sn>` (see [writing tests](../execution/writing-tests.md)).

```python
result = testerkit_run(
    test="tests/test_tps54302.py",
    station="bench_1",
    serial="SN001",
    project=project,
)
```

Return shape:

```python
{
    "run_id": "abc12345...",                # UUID of the run (or "unknown")
    "status": "passed",                     # one of: "passed" | "failed" | "error"
    "summary": "1 passed in 0.42s",         # pytest's bottom-line summary
    "test": "tests/test_tps54302.py",
    "station": "bench_1",
    "serial": "SN001",
    "started_at": "2026-05-17T...",
    "output": "<last 2000 chars of pytest stdout>",
}
```

`status` is a quick pass/fail/error from the pytest run, not the full outcome — fetch the stored run for the real `outcome` (next block).

For the full `Outcome` value (`passed`/`failed`/`errored`/`skipped`/`done`/`terminated`/`aborted`) that the runtime produces, fetch the run's stored row after it finishes:

```python
run = testerkit_runs(action="get", run_id=result["run_id"], project=project)["run"]
print(run["outcome"])               # one of the Outcome values
```

See [outcomes](../../concepts/execution/outcomes.md) for what each value means.

## What the agent does next

Once the server is registered, the agent drives the datasheet → test workflow through these tools: initialize a project, create a part spec from the datasheet, set up the station, generate tests, run, and inspect results.

Start the conversation by having the agent call:

```python
result = testerkit_project(action="init", path="~/my-hardware-tests")
project = result["project_root"]
```

Then hand the agent the datasheet. See [Datasheet → tests](../catalog/datasheet-to-test.md) for the full walkthrough.

## See also

- [api.md → MCP tools](../../reference/runtime/api.md#tools) — full per-tool reference: parameters, return shapes, every keyword
- [cli.md → testerkit setup](../../reference/cli.md#cli-setup) — `testerkit setup show` and the `--print-only` flag
- [TesterKit fixtures](../../reference/pytest/fixtures.md) — `context`, `verify`, `measure`, and every other fixture this page references
- [outcomes](../../concepts/execution/outcomes.md) — what each `run_outcome` / `step_outcome` / `measurement_outcome` value means
- [capabilities](../../concepts/configuration/capabilities.md) — characteristics, SpecBand, the matching model
- [limits](../execution/limits.md) — the full limit-resolution chain (sidecar / marker / part spec / inline)
- [vector-expansion](../execution/vector-expansion.md) — `sweeps:` shape (cross-product vs zipped), range expanders
- [spec-driven-testing](../execution/spec-driven-testing.md) — `testerkit_characteristics` + part-spec workflow
- [mock-mode](../configuration/mock-mode.md) — `--mock-instruments`, `mock_config:`, the substitution pipeline
- [writing-tests](../execution/writing-tests.md) — pytest-test authoring patterns (for tests written by hand, not by an AI agent)
