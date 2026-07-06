# Project setup

`pip install litmus-test` (PyPI name) installs the `litmus` package (import name). Nothing else
runs, no server or account, and `pytest` passes on a bare test with no scaffold:

```python
def test_smoke():
    assert True
```

Don't scaffold ahead of need — see the gate at the bottom before reaching for `litmus init`.

## `litmus init [NAME]`

Creates `NAME/` and scaffolds inside it, or scaffolds the current directory if `NAME` is omitted
(like `uv init`). Every file is skip-if-exists — safe to re-run on an existing project.

| Flag | Effect |
|---|---|
| `--tier bringup` | Tier 0/1: `tests/`, `reports/` only. `conftest.py` defines `psu`/`dmm` as `MagicMock` fixtures. `tests/test_smoke.py` + `test_smoke.yaml` sidecar (inline-limit test + sidecar-limit test). No station/part/fixture YAML. |
| `--tier bench` | Same as `--starter` (below). |
| `--tier factory` | Creates the Tier-2 directory set (`parts/ stations/ fixtures/ instruments/ tests/ reports/`) and `litmus.yaml`, **but currently does not generate the starter test files, station, or a `profiles/` skeleton** — those still need `--starter` or manual authoring. Verify before promising factory-tier content to a user. |
| `--starter` / `--no-starter` | With no `--tier`: prompts interactively unless one of these is passed. `--starter` writes the Tier-2 example set: `stations/starter_station.yaml`, `parts/example_part.yaml`, `fixtures/example_fixture.yaml`, `instruments/generic_psu_001.yaml` + `generic_dmm_001.yaml`, `tests/test_example.py` + sidecar. `pyproject.toml` gets `addopts = "-v --station=starter_station --mock-instruments --uut-serial=STARTER001"` so bare `pytest` runs clean. |
| `--discover` | Runs instrument discovery and writes `stations/station.yaml` from what it finds, skipping the starter prompt. |
| `--no-git` | Skips `git init`. |
| `--ai claude-code\|claude-desktop\|copilot` | Runs the matching `litmus setup <tool>` after scaffolding. Without the flag, `init` detects an installed tool and prompts (TTY only). |
| `--name` | Overrides the auto-detected project name (git remote leaf → git root folder → directory name). |

Every mode also writes `litmus.yaml`, `.gitignore`, `README.md`, and `.vscode/settings.json` +
`.vscode/schemas/*.schema.json` (one schema per YAML type, for editor autocomplete). Bringup and
bare (`--no-starter`) modes write a bare `litmus.yaml` with just `name:` — runs land in the shared
platformdirs data directory. `--starter`/`--tier bench` additionally sets `data_dir: data`,
`default_station: starter_station`, `default_fixture: example_fixture`, `mock_instruments: true` —
runs stay project-local until `litmus data promote` migrates them to the shared store.

## `litmus new-test NAME`

Scaffolds `tests/test_<name>.py` (strips a `test_` prefix from `NAME` if given, then re-adds it).
Prompts for instrument roles to include — offers roles from the first station under `stations/` as
a hint, but accepts any comma-separated list or none. Generates a function signature
`(context, <roles>, verify)` and a commented 3-step skeleton (get conditions → drive stimulus →
`verify(...)`). With no roles, writes a bare `pass` body. Does not write a sidecar — add one by
hand if the test needs limits pulled out of the body (`litmus refs show tiers`).

## `litmus.yaml` (project config)

Root fields from `ProjectConfig` (`src/litmus/models/project.py`), all at the top level, `extra:
forbid`:

| Field | Purpose |
|---|---|
| `name` | Required. Project name. |
| `data_dir` | Override for where parquet/events land. Unset → shared platformdirs directory; a relative path (e.g. `data`) keeps runs inside the repo. |
| `default_station` / `default_fixture` / `default_profile` | Fallbacks used when `--station` / `--fixture` / `--test-profile` isn't passed on the CLI. |
| `mock_instruments` | Default for `--mock-instruments` — swap real driver classes for a `Mock` built from each instrument's `mock_config:`. |
| `profiles` | Named `ProfileConfig` entries (same shape as a sidecar, plus `description`, `facets`, `extends`, `station_type`, `fixture`, `verify_requires_limit`) — see `litmus refs show profiles`. |
| `channels` / `files` / `stream` / `session` | Producer-local tuning for channel/file streaming and session liveness — defaults are fine until you're chasing a throughput or timeout issue. |
| `required_inputs` | Named prompts (`PromptConfig`) the session asks for at start if not supplied on the CLI. |
| `multi_site` | `child_grace_seconds` — per-child shutdown grace for multi-site orchestration. |
| `runner` | Opaque block validated by the active runner's plugin (pytest fields: `addopts`, `markexpr`, `keyword`, `markers`). |

## Folder convention

| Kind | Dirs | Notes |
|---|---|---|
| YAML config | `catalog/` `instruments/` `stations/` `parts/` `fixtures/` `profiles/` | Entity-aligned — one file per instrument/station/part/fixture/profile. `catalog/` isn't created by `init`; it's commonly a separate shared source (git submodule) referenced via `catalog_ref:`, not regenerated per project. |
| Code | `tests/` `drivers/` | `tests/` holds pytest files + sidecars; `drivers/` (not scaffolded) is where you put PyVISA/PyMeasure/vendor driver classes if you factor them out of `stations/*.yaml` instrument entries. |
| Generated | `data/` `reports/` | `data/` (parquet + index) only exists if `data_dir` is project-local; `reports/` is the target for `litmus show <run_id> -f html|pdf`. |

## Correctness and access

| Command | Does |
|---|---|
| `litmus validate [PATHS...] [--type TYPE] [--json]` | Validates YAML against Pydantic schemas (`catalog`, `part`, `station`, `fixture`, `instrument_asset`, `project`). No paths → auto-scans `catalog/ parts/ stations/ fixtures/ instruments/` plus `litmus.yaml`. Exits non-zero on any failure. |
| `litmus schema export [-o DIR]` | Writes `.schema.json` for every YAML type (sidecar, profile, project, station, fixture, part, catalog, instrument_asset). |
| `litmus schema refresh [--project-dir DIR]` | Regenerates `.vscode/schemas/*` and the `yaml.schemas` map in `.vscode/settings.json` after a `litmus-test` upgrade — preserves other settings keys. |
| `litmus serve [--host] [--port] [--reload]` | Starts the operator UI (NiceGUI/FastAPI) at `127.0.0.1:8000` by default. |
| `litmus setup <tool>` | AI integration: `claude-code`, `claude-desktop`, `copilot`, `cursor`, `codex`, `cline` each register the MCP server (`litmus mcp serve`) and write that tool's native instructions file (`CLAUDE.md`, `AGENTS.md`, etc.); `litmus setup show` prints the MCP command and tool list without writing anything. |

## Before scaffolding anything

Most requests need none of this. If the task is "write a test" or "explain a verb," go to
`litmus refs show routing` first — it routes to the smallest tool for the job before `init` or
`new-test` ever come up. `litmus refs show tiers` covers what each tier is *for*; this page covers
what the CLI actually writes to disk.
