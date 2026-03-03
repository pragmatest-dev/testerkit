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

- **NiceGUI pages** at `/`, `/stations`, `/products`, `/fixtures`, `/instruments`, `/sequences`, `/tests`, `/runs`
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
| `--results-dir` | `results` | Path to results directory |
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
| `--results-dir` | from `litmus.yaml` or `results` | Path to results directory |
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

### litmus yield summary

Show yield summary (FPY, final yield, RTY).

```bash
litmus yield summary [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--results-dir` | from `litmus.yaml` or `results` | Results directory |
| `--phase` | exclude `development` | Test phase filter (or `all`) |
| `--since` | *(none)* | Start date (ISO format) |
| `--until` | *(none)* | End date (ISO format) |
| `--product` | *(none)* | Product ID filter |
| `--station` | *(none)* | Station ID filter |
| `--lot` | *(none)* | Lot number filter |
| `--group-by` | *(none)* | Group by `product`, `station`, or `lot` |

**Example:**

```
$ litmus yield summary --results-dir results
Runs: 150  |  Unique serials: 120
First-pass yield:  85.0%
Final yield:       95.8%
```

### litmus yield pareto

Top failure modes (Pareto analysis).

```bash
litmus yield pareto [--top N] [filter options...]
```

### litmus yield cpk STEP_NAME

Process capability (Cpk/Cp) for a measurement step.

```bash
litmus yield cpk STEP_NAME [--measurement NAME] [--min-samples N] [filter options...]
```

### litmus yield trend

Yield trend over time.

```bash
litmus yield trend [--period day|week|month] [filter options...]
```

### litmus yield time

Test time analysis (run or step durations).

```bash
litmus yield time [--by run|step] [filter options...]
```

## Journal Commands

During test execution, measurements are streamed to JSONL journal files. On successful completion, journals are converted to Parquet and deleted. These commands help manage orphaned journals from crashed or interrupted runs.

### litmus journals

List orphaned journals (from crashed or interrupted runs).

```bash
litmus journals [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--results-dir` | `results` | Path to results directory |

**Example output:**

```
$ litmus journals
Orphaned journals (from crashed/interrupted runs):

  results/.journals/2026-02-03/20260203T143025Z_SN001/
    Run ID: a1b2c3d4-5678-9abc-def0-1234567890ab
    DUT: SN001
    Station: bench_1
    Started: 2026-02-03T14:30:25
    Measurements: 47

To recover: litmus recover <journal_dir>
To recover all: litmus recover --all
```

### litmus recover

Convert orphaned journal(s) to Parquet.

```bash
litmus recover [JOURNAL_DIR] [OPTIONS]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `JOURNAL_DIR` | Path to journal directory (optional if using --all) |

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--results-dir` | `results` | Path to results directory |
| `--all` | `false` | Recover all orphaned journals |

**Examples:**

```bash
# Recover specific journal
litmus recover results/.journals/2026-02-03/20260203T143025Z_SN001/

# Recover all orphaned journals
litmus recover --all
```

### litmus cleanup-journals

Delete journals that have corresponding Parquet files (already converted).

```bash
litmus cleanup-journals [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--results-dir` | `results` | Path to results directory |
| `--dry-run` | `false` | Show what would be deleted without deleting |

**Examples:**

```bash
# Preview what would be deleted
litmus cleanup-journals --dry-run

# Actually delete
litmus cleanup-journals
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

The MCP server provides tools for AI agents:

| Tool | Description |
|------|-------------|
| `litmus` | CRUD operations on products, stations, fixtures, instruments, sequences |
| `litmus_discover` | Discover instruments on VISA bus |
| `litmus_match` | Check if a station can test a product |
| `litmus_run` | Execute tests and get results |
| `litmus_open` | Open URLs in browser (UI pages) |

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

Available tools:
  - list_products: List all product specifications
  - get_product_spec: Get a product specification by ID
  - list_stations: List all test stations
  - get_station_config: Get a station configuration by ID
  - find_compatible_stations: Find stations for a product
  - check_station_compatibility: Check if station can test product
  - derive_required_capabilities: Get capability requirements
  - get_instrument_library: Get instrument definitions
  - list_sequences: List test sequences
  - save_product_spec: Save a new product specification
  - save_test_sequence: Save a new test sequence
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
- Add a **sequence** to define test order, vectors, and limits → `litmus serve` → Sequences → New
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
# Run tests via pytest (not CLI - Litmus is a pytest plugin)
pytest tests/

# Check results
litmus runs --results-dir results
litmus show <run_id>
```

### AI-Assisted Development

```bash
# One-time setup for Claude Code
litmus setup claude-code

# Start using Litmus tools in Claude Code
# Claude can now read specs, match stations, run tests
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `LITMUS_RESULTS_DIR` | Default results directory (fallback: `results/`) |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | General error (invalid options, missing files) |
| `2` | Command not found |

## See Also

- [Platform Architecture](../concepts/platform-architecture.md) — Multiple entry points
- [MCP Tools](../reference/mcp-tools.md) — Full tool reference
- [Operator UI](../guides/operator-ui.md) — UI pages and features
