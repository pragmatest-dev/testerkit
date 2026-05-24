# CLI reference

`litmus` is a Click application; every command, group, option, and argument below is enumerated from `litmus.cli:main`. To regenerate after touching any command:

```bash
uv run python scripts/generate_reference_docs.py cli
```

The pre-commit hook runs the same generator in `--check` mode, so source / docs drift fails the commit.

## Installation

```bash
uv pip install litmus-test       # PyPI name is litmus-test; the import is `litmus`
litmus --help
```

After install, `litmus` is on `$PATH`. The `litmus` entry point is registered by `pyproject.toml` (`litmus = "litmus.cli:main"`).

## Commands

<!-- GENERATED:cli-commands:start -->
### `litmus catalog` (group) {#cli-catalog}

Catalog commands.

#### `litmus catalog datasheet` {#cli-catalog-datasheet}

Generate a formatted datasheet from a catalog YAML file.

| Argument / option | Type | Description |
|---|---|---|
| `YAML_PATH` | `path` |  |
| `-f`/`--format` | `{html, pdf}` | Output format (default: html)  *(default: `html`)* |
| `-o`/`--output` | `path` | Output file path |

### `litmus daemon` (group) {#cli-daemon}

Manage Litmus background daemons (events / runs / channels).

#### `litmus daemon restart` {#cli-daemon-restart}

Restart selected daemons (SIGTERM the running process; respawn on next access).

| Argument / option | Type | Description |
|---|---|---|
| `TARGETS`... | `text` |  |
| `--all` | `flag` | Restart every daemon under the project |

#### `litmus daemon status` {#cli-daemon-status}

Show running daemons, their PIDs, refs, and locations.

*(no options or arguments.)*

#### `litmus daemon stop` {#cli-daemon-stop}

Stop selected daemons without respawning.

| Argument / option | Type | Description |
|---|---|---|
| `TARGETS`... | `text` |  |
| `--all` | `flag` | Stop every daemon under the project |

### `litmus data` (group) {#cli-data}

Data retention and management.

#### `litmus data promote` {#cli-data-promote}

Move a starter project's local runs to the global store.

| Argument / option | Type | Description |
|---|---|---|
| `--include-starter` | `flag` | Also promote runs that match starter sentinels (example_product / starter_station / STARTER001 / etc.). Default skips these as throwaway learning runs. |
| `--dry-run` | `flag` | Show what would be promoted; write nothing. |

#### `litmus data prune` {#cli-data-prune}

Delete date-partitioned data older than the specified period.

| Argument / option | Type | Description |
|---|---|---|
| `--older-than` | `text` | Retention period (e.g. 30d, 90d) |
| `--type` | `text` | Data types to prune (e.g. channels, events) |
| `--data-dir` | `text` | Results directory |
| `--dry-run` | `flag` | Show what would be deleted |

#### `litmus data reindex` {#cli-data-reindex}

Kill index daemons and rebuild on next access.

| Argument / option | Type | Description |
|---|---|---|
| `--data-dir` | `text` | Results directory |

### `litmus discover` {#cli-discover}

Scan for available instruments.

| Argument / option | Type | Description |
|---|---|---|
| `--visa` | `flag` | VISA instruments only |
| `--ni` | `flag` | NI devices only |
| `--serial` | `flag` | Serial ports only |
| `--lxi` | `flag` | LXI network instruments only |
| `--identify`/`--no-identify` | `flag` | Query *IDN? for each instrument |
| `--json` | `flag` | Output as JSON |

### `litmus export` {#cli-export}

Export a test run or session to a different format via event replay.

| Argument / option | Type | Description |
|---|---|---|
| `ID` | `text` |  |
| `-f`/`--format` | `text` | Target format (csv, json, stdf, hdf5, tdms, mdf4, atml) |
| `-o`/`--output-dir` | `text` | Output directory |
| `--data-dir` | `text` | Data directory |

### `litmus grafana` (group) {#cli-grafana}

Grafana dashboard provisioning and data server.

#### `litmus grafana export` {#cli-grafana-export}

Export dashboards and provisioning templates for manual setup.

| Argument / option | Type | Description |
|---|---|---|
| `--output-dir`/`-o` | `path` | Output directory  *(default: `grafana-export`)* |

#### `litmus grafana serve` {#cli-grafana-serve}

Start the pgwire server for Grafana.

| Argument / option | Type | Description |
|---|---|---|
| `--host` | `text` | Bind address  *(default: `0.0.0.0`)* |
| `--port` | `integer` | PostgreSQL wire protocol port  *(default: `5433`)* |
| `--data-dir` | `path` |  |
| `--refresh-seconds` | `integer` | Seconds between IPC table refreshes (events, channels)  *(default: `30`)* |

#### `litmus grafana setup` {#cli-grafana-setup}

Install provisioning config and dashboards into Grafana.

| Argument / option | Type | Description |
|---|---|---|
| `--grafana-home` | `directory` | Grafana installation directory (default: auto-detect) |
| `--grafana-url` | `text` | Grafana URL for API setup (e.g. http://localhost:3000) |
| `--grafana-token` | `text` | Grafana API token or service account token |
| `--grafana-user` | `text` | Grafana username for basic auth |
| `--grafana-password` | `text` | Grafana password for basic auth |
| `--host` | `text` | pgwire host for datasource config  *(default: `127.0.0.1`)* |
| `--port` | `integer` | pgwire port for datasource config  *(default: `5433`)* |
| `--folder` | `text` | Grafana folder for dashboards  *(default: `Litmus`)* |

### `litmus init` {#cli-init}

Initialize a new Litmus project.

| Argument / option | Type | Description |
|---|---|---|
| `NAME` | `text` |  |
| `--no-git` | `flag` | Skip git initialization |
| `--discover` | `flag` | Auto-discover instruments and create station file |
| `--starter`/`--no-starter` | `flag` | Generate starter example files (prompts if not specified) |
| `--tier` | `{bringup, bench, factory}` | Scaffold tier. 'bringup' = Tier 0/1 (MagicMock fixtures, one test, one sidecar, no station/product YAML). 'bench' = Tier 2 starter (equivalent to --starter). 'factory' = Tier 3/4 (bench + profiles). |
| `--ai` | `{claude-code, claude-desktop, copilot}` | Set up AI tool integration (MCP server + project instructions) |
| `--name` | `text` | Project name (overrides auto-detect) |

### `litmus instrument` (group) {#cli-instrument}

Instrument management commands.

#### `litmus instrument cal` {#cli-instrument-cal}

Update calibration information for an instrument.

| Argument / option | Type | Description |
|---|---|---|
| `INSTRUMENT_ID` | `text` |  |
| `--due` | `text` | Calibration due date (YYYY-MM-DD) |
| `--last` | `text` | Last calibration date (YYYY-MM-DD) |
| `--cert` | `text` | Certificate number |
| `--lab` | `text` | Calibration lab name |

#### `litmus instrument list` {#cli-instrument-list}

List all instrument configuration files.

| Argument / option | Type | Description |
|---|---|---|
| `--json` | `flag` | Output as JSON |

#### `litmus instrument show` {#cli-instrument-show}

Show details for a specific instrument.

| Argument / option | Type | Description |
|---|---|---|
| `INSTRUMENT_ID` | `text` |  |
| `--json` | `flag` | Output as JSON |

### `litmus mcp` (group) {#cli-mcp}

MCP server commands for AI-assisted workflows.

#### `litmus mcp serve` {#cli-mcp-serve}

Start the MCP server for AI agents.

| Argument / option | Type | Description |
|---|---|---|
| `--transport` | `text` | Transport type (stdio, sse)  *(default: `stdio`)* |

### `litmus metrics` (group) {#cli-metrics}

Manufacturing-test analytics (yield, pareto, cpk, trend, retest, time-loss).

#### `litmus metrics cpk` {#cli-metrics-cpk}

Process capability (Cpk/Cp) per measurement.

| Argument / option | Type | Description |
|---|---|---|
| `--json` | `flag` | Output as JSON |
| `--station` | `text` | Station ID |
| `--product` | `text` | Product ID |
| `--until` | `text` | End date (ISO format) |
| `--since` | `text` | Start date (ISO format) |
| `--phase` | `text` | Test phase (or 'all') |
| `--data-dir` | `text` | Results directory |
| `--min-samples` | `integer` | Minimum sample count  *(default: `10`)* |

#### `litmus metrics pareto` {#cli-metrics-pareto}

Top failures (Pareto). Group by product / step / measurement.

| Argument / option | Type | Description |
|---|---|---|
| `--json` | `flag` | Output as JSON |
| `--station` | `text` | Station ID |
| `--product` | `text` | Product ID |
| `--until` | `text` | End date (ISO format) |
| `--since` | `text` | Start date (ISO format) |
| `--phase` | `text` | Test phase (or 'all') |
| `--data-dir` | `text` | Results directory |
| `--top` | `integer` | Number of top failures  *(default: `10`)* |
| `--group-by` | `{product, step, measurement}` | Lens for the pareto: ``product`` groups runs by ``dut_part_number`` (most-failing SKUs); ``step`` groups steps by ``step_path`` (most-failing tests); ``measurement`` groups limit-bearing measurements by name (the historical default).  *(default: `product`)* |

#### `litmus metrics retest` {#cli-metrics-retest}

Retest rates: how often DUTs are retried.

| Argument / option | Type | Description |
|---|---|---|
| `--json` | `flag` | Output as JSON |
| `--station` | `text` | Station ID |
| `--product` | `text` | Product ID |
| `--until` | `text` | End date (ISO format) |
| `--since` | `text` | Start date (ISO format) |
| `--phase` | `text` | Test phase (or 'all') |
| `--data-dir` | `text` | Results directory |
| `--period` | `{day, week, month}` | *(default: `day`)* |

#### `litmus metrics summary` {#cli-metrics-summary}

Yield summary: FPY, final yield, run counts, duration stats.

| Argument / option | Type | Description |
|---|---|---|
| `--json` | `flag` | Output as JSON |
| `--station` | `text` | Station ID |
| `--product` | `text` | Product ID |
| `--until` | `text` | End date (ISO format) |
| `--since` | `text` | Start date (ISO format) |
| `--phase` | `text` | Test phase (or 'all') |
| `--data-dir` | `text` | Results directory |
| `--period` | `{day, week, month}` | *(default: `day`)* |

#### `litmus metrics time-loss` {#cli-metrics-time-loss}

Time lost to failures and errors.

| Argument / option | Type | Description |
|---|---|---|
| `--json` | `flag` | Output as JSON |
| `--station` | `text` | Station ID |
| `--product` | `text` | Product ID |
| `--until` | `text` | End date (ISO format) |
| `--since` | `text` | Start date (ISO format) |
| `--phase` | `text` | Test phase (or 'all') |
| `--data-dir` | `text` | Results directory |
| `--period` | `{day, week, month}` | *(default: `day`)* |

#### `litmus metrics trend` {#cli-metrics-trend}

Yield trend over time.

| Argument / option | Type | Description |
|---|---|---|
| `--json` | `flag` | Output as JSON |
| `--station` | `text` | Station ID |
| `--product` | `text` | Product ID |
| `--until` | `text` | End date (ISO format) |
| `--since` | `text` | Start date (ISO format) |
| `--phase` | `text` | Test phase (or 'all') |
| `--data-dir` | `text` | Results directory |
| `--period` | `{day, week, month}` | *(default: `day`)* |

### `litmus new-test` {#cli-new-test}

Scaffold a new test file.

| Argument / option | Type | Description |
|---|---|---|
| `NAME` | `text` |  |

### `litmus refs` (group) {#cli-refs}

Stream curated reference docs to stdout.

#### `litmus refs list` {#cli-refs-list}

List available reference topics.

*(no options or arguments.)*

#### `litmus refs show` {#cli-refs-show}

Print the named reference doc to stdout.

| Argument / option | Type | Description |
|---|---|---|
| `TOPIC` | `text` |  |

### `litmus runs` {#cli-runs}

List recent test runs.

| Argument / option | Type | Description |
|---|---|---|
| `--data-dir` | `text` | Results directory |
| `--limit` | `integer` | Number of runs to show  *(default: `20`)* |
| `--json` | `flag` | Output as JSON |

### `litmus sbom` {#cli-sbom}

Export CycloneDX SBOM for a test run's software environment.

| Argument / option | Type | Description |
|---|---|---|
| `RUN_ID` | `text` |  |
| `--data-dir` | `text` | Results directory |
| `-o`/`--output` | `text` | Output file (default: stdout) |

### `litmus schema` (group) {#cli-schema}

JSON Schema generation for YAML validation.

#### `litmus schema export` {#cli-schema-export}

Export JSON Schema files for all Litmus YAML types.

| Argument / option | Type | Description |
|---|---|---|
| `--output-dir`/`-o` | `text` | Directory for .schema.json files  *(default: `schemas`)* |

#### `litmus schema refresh` {#cli-schema-refresh}

Refresh .vscode/schemas/ and .vscode/settings.json after a Litmus upgrade.

| Argument / option | Type | Description |
|---|---|---|
| `--project-dir` | `text` | Project root (defaults to current directory).  *(default: `.`)* |

### `litmus serve` {#cli-serve}

Start the operator UI server.

| Argument / option | Type | Description |
|---|---|---|
| `--host` | `text` | Host to bind to  *(default: `127.0.0.1`)* |
| `--port` | `integer` | Port to bind to  *(default: `8000`)* |
| `--reload` | `flag` | Enable auto-reload for development |

### `litmus setup` (group) {#cli-setup}

Configure AI tool integrations.

#### `litmus setup claude-code` {#cli-setup-claude-code}

Configure Litmus MCP server for Claude Code.

| Argument / option | Type | Description |
|---|---|---|
| `--print-only` | `flag` | Print config instead of installing |

#### `litmus setup claude-desktop` {#cli-setup-claude-desktop}

Configure Litmus for Claude Desktop.

| Argument / option | Type | Description |
|---|---|---|
| `--legacy` | `flag` | Use legacy JSON config instead of .mcpb bundle |
| `--print-only` | `flag` | Print config instead of installing |

#### `litmus setup cline` {#cli-setup-cline}

Configure Litmus MCP server for Cline (VS Code extension).

| Argument / option | Type | Description |
|---|---|---|
| `--print-only` | `flag` | Print config instead of installing |

#### `litmus setup copilot` {#cli-setup-copilot}

Configure Litmus for GitHub Copilot (VS Code + CLI).

| Argument / option | Type | Description |
|---|---|---|
| `--print-only` | `flag` | Print config instead of installing |

#### `litmus setup cursor` {#cli-setup-cursor}

Configure Litmus MCP server for Cursor.

| Argument / option | Type | Description |
|---|---|---|
| `--print-only` | `flag` | Print config instead of installing |

#### `litmus setup show` {#cli-setup-show}

Show current MCP server configuration.

*(no options or arguments.)*

### `litmus show` {#cli-show}

Show details for a specific test run.

| Argument / option | Type | Description |
|---|---|---|
| `RUN_ID` | `text` |  |
| `--data-dir` | `text` | Results directory |
| `-f`/`--format` | `{html, pdf, json, csv}` | Generate report in format |
| `-o`/`--output` | `text` | Output file or directory |
| `-t`/`--template` | `text` | Report template name  *(default: `default`)* |
| `--env` | `flag` | Show environment snapshot |

### `litmus station` (group) {#cli-station}

Station management commands.

#### `litmus station init` {#cli-station-init}

Initialize a new station configuration.

| Argument / option | Type | Description |
|---|---|---|
| `--station-id` | `text` | Unique station identifier |
| `--name` | `text` | Human-readable station name |
| `--location` | `text` | Physical location |

#### `litmus station update` {#cli-station-update}

Re-discover and update instrument identity in configuration.

| Argument / option | Type | Description |
|---|---|---|
| `STATION_ID` | `text` |  |

#### `litmus station validate` {#cli-station-validate}

Validate station instruments against configuration.

| Argument / option | Type | Description |
|---|---|---|
| `STATION_ID` | `text` |  |
| `--strict` | `flag` | Fail on any mismatch |

### `litmus validate` {#cli-validate}

Validate YAML configuration files.

| Argument / option | Type | Description |
|---|---|---|
| `PATHS`... | `path` |  |
| `--type`/`-t` | `{catalog, product, station, sequence, fixture, instrument_asset, project}` | Explicit file type (skips auto-detection). |
| `--json` | `flag` | Output as JSON |
<!-- GENERATED:cli-commands:end -->

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

See [Profiles](../how-to/execution/profiles.md) for the profile YAML shape.

## Environment variables

| Variable | Description |
|---|---|
| `LITMUS_HOME` | Default data directory. Resolution: `--data-dir` arg → project `litmus.yaml` `data_dir:` → `LITMUS_HOME` → `platformdirs.user_data_dir("litmus")`. |
| `LITMUS_TEST_PHASE` | Default `test_phase` for runs (see *Test phase* above). |
| `LITMUS_TEST_PROFILE` | Default profile name; equivalent to `--test-profile`. |
| `LITMUS_MOCK_INSTRUMENTS` | Set to `1` to enable mock mode without passing `--mock-instruments`. |
| `LITMUS_AUTO_CONFIRM` | Truthy → auto-resolve operator prompts and dialogs in non-tty contexts (CI, subprocess runs). Set to `"confirm"` to auto-confirm, `"cancel"` to auto-cancel; any other truthy value defaults to confirm. |
| `LITMUS_SERVER_URL` | Server URL the dialog bridge uses to POST operator prompts from subprocess test runs back to the UI host (default: `http://localhost:8000`). |
| `LITMUS_DUT_SERIAL` | Default DUT serial (shared across slots). For per-slot serials, use `LITMUS_DUT_SERIAL_<SLOT_ID>` (e.g. `LITMUS_DUT_SERIAL_SLOT_1`). |
| `LITMUS_DUT_PART_NUMBER` | Default DUT part number (`dut_part_number` on every run). |
| `LITMUS_DUT_REVISION` | Default DUT hardware revision. |
| `LITMUS_DUT_LOT_NUMBER` | Default DUT lot / batch number. |
| `LITMUS_FIXTURE_SLOT` | JSON-serialized `ResolvedSlot` injected into per-slot child processes by the multi-DUT orchestrator. Operator-set values are ignored. |
| `LITMUS_DAEMON_IDLE_TIMEOUT` | Seconds a background daemon (events, runs, channels) waits idle before self-shutting-down (default: `300`). |
| `LITMUS_DAEMON_SPAWN_TIMEOUT` | Seconds to wait for a daemon to report ready after spawning (default: `30`). |
| `LITMUS_SKIP_DAEMON_NOTIFY` | Suppresses the daemon-notify gRPC hop when constructing `ParquetBackend` — useful in tooling scripts that read backends without serving runs. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | General error (invalid options, missing files, validation failures) |
| `2` | Command not found / usage error (Click standard) |

## See also

- [Platform architecture](../concepts/overview/platform-vs-framework.md) — what each entry point owns
- [MCP tools](../how-to/overview/mcp-integration.md) — the agent-side parallel to most `litmus` subcommands
- [Configuration](configuration.md) — YAML files the CLI reads
- [API reference](api.md) — HTTP routes the `litmus serve` UI mounts
