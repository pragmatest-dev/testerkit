# CLI Reference

The `litmus` command-line interface provides tools for running the operator UI, inspecting test results, starting the MCP server, and configuring AI tool integrations.

## Installation

After installing Litmus, the `litmus` command is available:

```bash
uv sync            # Install dependencies
litmus --help      # Show available commands
litmus --version   # Show version (0.1.0)
```

## Commands

### litmus init

Initialize a new Litmus project with scaffolding for hardware tests.

```bash
litmus init [NAME] [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--no-git` | `False` | Skip git initialization |
| `--discover` | `False` | Auto-discover instruments and create station file |
| `--starter / --no-starter` | _prompts_ | Generate starter example files |
| `--tier` | _prompts_ | Scaffold tier: `bringup` (Tier 0/1 — MagicMock fixtures, one test, no station/product YAML), `bench` (Tier 2 starter), `factory` (Tier 3/4 — bench + profiles) |
| `--ai` | _none_ | Set up AI tool integration: `claude-code`, `claude-desktop`, or `copilot` |
| `--name` | _auto-detect_ | Project name (overrides auto-detect) |

**Examples:**

```bash
# Create new project directory
litmus init my_project                # Create my_project/ with scaffolding
litmus init my_project --discover     # + auto-detect instruments

# Scaffold current directory (like uv init)
cd my_project
litmus init                           # Add litmus files to CWD
litmus init --discover                # + auto-detect instruments
```

With `NAME`: creates a new directory and scaffolds inside it. Without `NAME`: scaffolds the current directory. All files are skip-if-exists, so it's safe to run on an existing project.

When `--discover` is used (or the user confirms discovery interactively), Litmus scans for VISA instruments, looks up each in the catalog to determine its type (dmm, smu, psu, etc.), and writes `stations/station.yaml` with auto-assigned roles. Duplicate types are numbered (dmm1, dmm2).

The station file is gitignored since it's bench-specific.

### litmus serve

Start the operator UI server (NiceGUI + FastAPI).

```bash
litmus serve [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `127.0.0.1` | Host address to bind to |
| `--port` | `8000` | Port to bind to |
| `--reload` | `false` | Enable auto-reload for development |

**Examples:**

```bash
# Default: http://localhost:8000
litmus serve

# Custom port
litmus serve --port 8080

# Bind to all interfaces (production)
litmus serve --host 0.0.0.0

# Development with auto-reload
litmus serve --reload
```

**What it starts:**

- **NiceGUI pages** at `/`, `/stations`, `/products`, `/fixtures`, `/instruments`, `/tests`, `/runs`
- **FastAPI routes** at `/api/*` for programmatic access
- **WebSocket** for live UI updates

### litmus runs

List recent test runs from the results directory.

```bash
litmus runs [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--data-dir` | `results` | Path to results directory |
| `--limit` | `20` | Number of runs to display |

**Example output:**

```
$ litmus runs
Run ID     DUT Serial      Station              Outcome
------------------------------------------------------------
a1b2c3d4   SN12345         bench_1              pass
e5f6g7h8   SN12346         bench_1              fail
i9j0k1l2   SN12347         bench_2              pass
```

### litmus show

Show details for a specific test run.

```bash
litmus show <RUN_ID> [OPTIONS]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `RUN_ID` | Test run ID (full or partial, e.g., `a1b2c3d4`) |

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--data-dir` | from `litmus.yaml` or `results` | Path to results directory |
| `-f`, `--format` | *(none)* | Generate report: `html`, `pdf`, `json`, `csv` |
| `-o`, `--output` | `.` (current dir) | Output file or directory |
| `-t`, `--template` | `default` | Jinja2 template name |

Without `-f`, prints a terminal summary. With `-f`, generates a report file.

**Terminal output:**

```
$ litmus show a1b2c3d4
Test Run: a1b2c3d4-5678-9abc-def0-1234567890ab
  DUT Serial: SN12345
  Station: bench_1
  Outcome: pass
  Started: 2025-01-15T10:30:00
  Ended: 2025-01-15T10:32:15
  Steps: 3
  Measurements: 27 (0 failed)

Measurements:
  output_voltage: 3.31 V [pass]
  input_current: 0.45 A [pass]
  efficiency: 87.2 % [pass]
```

**Report generation:**

```bash
# HTML report (self-contained, print-friendly)
litmus show a1b2c3d4 -f html

# PDF report (requires weasyprint: pip install 'litmus[pdf]')
litmus show a1b2c3d4 -f pdf -o reports/

# JSON report
litmus show a1b2c3d4 -f json -o result.json

# CSV report (one row per measurement)
litmus show a1b2c3d4 -f csv
```

**Template resolution:** project `reports/templates/{name}.html` → built-in `litmus/reports/templates/{name}.html`. Create custom templates to match your organization's report format.

## Yield / Manufacturing Metrics

### litmus metrics summary

Show yield summary (FPY = First-Pass Yield, RTY = Rolled Throughput Yield — manufacturing acronyms for the fraction of units that pass first try / pass every step).

```bash
litmus metrics summary [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--data-dir` | from `litmus.yaml` or `results` | Results directory |
| `--phase` | exclude `development` | Test phase filter (or `all`) |
| `--since` | *(none)* | Start date (ISO format) |
| `--until` | *(none)* | End date (ISO format) |
| `--product` | *(none)* | Product ID filter |
| `--station` | *(none)* | Station ID filter |
| `--period` | *(none)* | Period bucket: `day`, `week`, or `month` |
| `--json` | *(off)* | Emit JSON instead of a table |

**Example:**

```
$ litmus metrics summary --data-dir results
Runs: 150  |  Unique serials: 120
First-pass yield:  85.0%
Final yield:       95.8%
```

### litmus metrics pareto

Top failure modes (Pareto analysis).

```bash
litmus metrics pareto [--top N] [filter options...]
```

### litmus metrics cpk

Process capability (Cpk/Cp) across measurements.

```bash
litmus metrics cpk [--min-samples N] [filter options...]
```

### litmus metrics trend

Yield trend over time.

```bash
litmus metrics trend [--period day|week|month] [filter options...]
```

### litmus metrics time-loss

Time lost to failures and errors.

```bash
litmus metrics time-loss [--period day|week|month] [filter options...]
```

## Data management commands

Run-data lifecycle commands. Source: `src/litmus/cli.py` (`@main.group("data")`).

### litmus data prune

Delete run records older than a cutoff.

```bash
litmus data prune --older-than 30d [--dry-run]
```

### litmus data reindex

Rebuild the DuckDB index from parquet files (use after manually copying parquets between machines).

```bash
litmus data reindex
```

## Daemon commands

`litmus serve` and `pytest` both rely on background daemons (events, runs, channels). Manage them with `litmus daemon`.

### litmus daemon status

Show the state of every daemon Litmus owns on this machine.

```bash
litmus daemon status
```

### litmus daemon restart

Stop and restart all daemons (clears their in-memory state; on-disk parquet remains).

```bash
litmus daemon restart
```

### litmus daemon stop

Stop the daemons. They restart on next use.

```bash
litmus daemon stop
```

## MCP Commands

### litmus mcp serve

Start the MCP (Model Context Protocol) server for AI agents.

```bash
litmus mcp serve [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--transport` | `stdio` | Transport type (`stdio` or `sse`) |

**What it exposes:**

Twelve tools, all prefixed `litmus_`. Full reference in [HTTP & MCP API](api.md#mcp-tools).

| Tool | Purpose |
|------|---------|
| `litmus_project` | Read / list / save project files |
| `litmus_discover` | VISA discovery |
| `litmus_match` | Match a product against stations |
| `litmus_run` | Start a test run |
| `litmus_open` | Open a resource in the operator UI |
| `litmus_schema` | JSON schema for a YAML entity type |
| `litmus_events` | Query the event store |
| `litmus_sessions` | List sessions |
| `litmus_channels` | Query channel data |
| `litmus_metrics` | Compute yield / Pareto / Cpk / retest / time-loss |
| `litmus_runs` | Query the runs view |
| `litmus_steps` | Query the steps view |

**Example:**

```bash
# Start MCP server (used by Claude Code, etc.)
litmus mcp serve
```

## Setup Commands

Configure AI tool integrations automatically.

### litmus setup claude-code

Configure Litmus for Claude Code.

```bash
litmus setup claude-code [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--print-only` | Print config instead of installing |

**What it does:**

1. Registers the Litmus MCP server via `claude mcp add`
2. Copies skill command stubs to `.claude/commands/`
3. Generates `CLAUDE.md` project instructions (if not already present)

**Example:**

```bash
litmus setup claude-code
```

```
✓ Registered Litmus MCP server
✓ Copied commands to .claude/commands/ (2 files)
✓ Created CLAUDE.md (project instructions)
```

### litmus setup claude-desktop

Configure Litmus for Claude Desktop.

```bash
litmus setup claude-desktop [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--legacy` | Use legacy JSON config instead of .mcpb bundle |
| `--print-only` | Print config instead of installing |

**What it does:**

Builds a `.mcpb` Desktop Extension bundle that can be double-clicked to install in Claude Desktop. Includes the MCP server configuration and bundled skills.

Use `--legacy` for older Claude Desktop versions that don't support `.mcpb` — this writes directly to `claude_desktop_config.json`.

**Example:**

```bash
litmus setup claude-desktop
```

```
✓ Built litmus.mcpb (Desktop Extension)
  → /mnt/c/Users/ryan/Desktop/litmus.mcpb
  Double-click to install in Claude Desktop.
```

### litmus setup copilot

Configure Litmus for GitHub Copilot (VS Code and CLI).

```bash
litmus setup copilot [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--print-only` | Print config instead of installing |

**What it does:**

1. Creates/merges `.vscode/mcp.json` with litmus MCP server config
2. Copies prompt stubs to `.github/prompts/`
3. Generates `.github/copilot-instructions.md` (if not present)
4. Generates `AGENTS.md` (if not present) — read by Copilot CLI, Codex, Gemini CLI, and others

**Example:**

```bash
litmus setup copilot
```

```
✓ Wrote .vscode/mcp.json (litmus MCP server)
✓ Copied prompts to .github/prompts/ (2 files)
✓ Created .github/copilot-instructions.md
✓ Created AGENTS.md
```

### litmus setup cursor

Configure Litmus MCP server for Cursor IDE.

```bash
litmus setup cursor [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--print-only` | Print config instead of installing |

**What it does:**

Creates or updates `.cursor/mcp.json` in the current project directory.

**Example:**

```bash
# Install in current project
litmus setup cursor
```

### litmus setup cline

Configure Litmus MCP server for Cline (VS Code extension).

```bash
litmus setup cline [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--print-only` | Print config instead of installing |

**What it does:**

Creates or updates `cline_mcp_settings.json` in the VS Code user settings directory.

**Example:**

```bash
litmus setup cline
```

### litmus setup show

Display current MCP server configuration and available tools.

```bash
litmus setup show
```

**Example output:**

```
Litmus MCP Server
----------------------------------------
Command: /path/to/litmus mcp serve
Transport: stdio

Available tools (all prefixed ``litmus_``):
  - litmus_project   : Read / list / save project files
  - litmus_discover  : VISA discovery
  - litmus_match     : Match a product against stations
  - litmus_run       : Start a test run
  - litmus_open      : Open a resource in the operator UI
  - litmus_schema    : JSON schema for a YAML entity type
  - litmus_events    : Query the event store
  - litmus_sessions  : List sessions
  - litmus_channels  : Query channel data
  - litmus_metrics   : Compute yield / Pareto / Cpk / retest / time-loss
  - litmus_runs      : Query the runs view
  - litmus_steps     : Query the steps view
```

## Getting Started (recommended order)

**1. Create your project**
```bash
litmus init quick_start --starter && cd quick_start
pytest                              # verify everything works with mocks
```

**2. Define your product spec** — What are you testing?
Most engineers start here. Describe your DUT's characteristics and limits.
```bash
litmus serve                        # open UI → Products → New
# or create products/my_board.yaml manually
```

**3. Set up your bench** — What instruments do you have?
```bash
litmus discover                     # scan VISA bus to see what's connected
litmus station init                 # interactive: assign roles to discovered instruments
```
See [From Mocks to Hardware](../tutorial/from-mocks-to-hardware.md) for the full transition guide.

**4. Write your first real test**
```bash
litmus new-test output_voltage      # prompts for which instruments to use
# edit tests/test_output_voltage.py with your measurement logic
```

**5. Run with mocks, then real hardware**
```bash
pytest --mock-instruments           # verify test logic without hardware
pytest --station=my_bench           # run against real instruments
```

**6. Review results**
```bash
litmus runs                         # list recent runs
litmus show <run_id>                # terminal summary
litmus show <run_id> -f html        # generate HTML report
litmus serve                        # full UI at localhost:8000
```

**Common next steps:**
- Add a **sidecar** next to a test file to define vectors, limits, and mocks → see [Writing Tests](../how-to/writing-tests.md)
- Add a **fixture** to map DUT pins to instruments → `litmus serve` → Fixtures → New
- Set up **AI assistance** → `litmus setup claude-code`

## Common Workflows

### Development

```bash
# Start UI with auto-reload
litmus serve --reload

# In another terminal, watch test runs
litmus runs --limit 5
```

### CI/CD

```bash
# Run tests via pytest — the bundled plugin slots in automatically
pytest tests/

# Check results
litmus runs --data-dir results
litmus show <run_id>
```

### AI-Assisted Development

```bash
# One-time setup for Claude Code
litmus setup claude-code

# Start using Litmus tools in Claude Code
# Claude can now read specs, match stations, run tests
```

## Test phase

`test_phase` tags every run with the maturity tier it was produced for (`development`, `validation`, `characterization`, `production`). It lands on every parquet row so dashboards and queries can filter by phase.

### Setting the phase

Resolution order (first match wins):

1. **`pytest --test-phase=<phase>`** — explicit per-run.
2. **`LITMUS_TEST_PHASE` env var.**
3. **Profile YAML** — a profile can set `test_phase: <phase>` for every test it runs.
4. **Auto-detect** — `production` if the git tree is clean, `development` if dirty.

### Git-status enforcement

Non-development phases require a clean git repository. Uncommitted changes (or no git at all) force the phase down to `development` regardless of what was requested. This guarantees a production-tagged row is reproducible from a commit hash.

| Git status | Requested | Actual |
|---|---|---|
| Clean | `validation` | `validation` |
| Clean | `production` | `production` |
| Clean | (none) | `production` |
| Dirty | `validation` | `development` |
| Dirty | `production` | `development` |
| Dirty | (none) | `development` |
| No git | (any) | `development` |

### Query by phase

```python
import duckdb

duckdb.sql("""
    SELECT * FROM read_parquet('data/runs/**/*.parquet')
    WHERE test_phase = 'production'
""")

# Exclude dev work
duckdb.sql("""
    SELECT * FROM read_parquet('data/runs/**/*.parquet')
    WHERE test_phase != 'development'
""")
```

See [Profiles](../how-to/profiles.md) for the profile YAML shape.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `LITMUS_HOME` | Default data directory (resolution: `--data-dir` arg → project `litmus.yaml` `data_dir:` → `LITMUS_HOME` → `platformdirs.user_data_dir("litmus")`) |
| `LITMUS_TEST_PHASE` | Default `test_phase` for runs (see *Test phase* above). |
| `LITMUS_MOCK_INSTRUMENTS` | Set to `1` to enable mock mode without `--mock-instruments`. |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | General error (invalid options, missing files) |
| `2` | Command not found |

## See Also

- [Platform Architecture](../concepts/platform-architecture.md) — Multiple entry points
- [MCP Tools](../how-to/mcp-integration.md) — Full tool reference
