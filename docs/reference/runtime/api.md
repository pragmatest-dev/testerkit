# API reference

Litmus exposes two equivalent surfaces over the same Python code:

1. **HTTP API** at `http://localhost:8000/api/*` when `litmus serve` is running. Use from any HTTP client.
2. **MCP server** for AI agents (Claude Code, Cursor, Cline, etc.). Use the [Model Context Protocol](https://modelcontextprotocol.io/) over stdio.

The MCP tools are thin wrappers around the same Python functions that back the HTTP routes; behavior is identical.

## Live API explorer

When `litmus serve` is running, the HTTP API exposes three
introspection endpoints. They live under `/api/*` (not at FastAPI's
usual top-level paths — the top-level `/docs` route is the in-app
documentation viewer) and reflect the current `litmus` build on
the bench:

| URL | What it serves |
|---|---|
| <http://localhost:8000/api/openapi.json> | The raw OpenAPI 3 schema (JSON). Feed it to your favorite client generator (`openapi-typescript-codegen`, `openapi-python-client`, etc.) to produce typed clients. |
| <http://localhost:8000/api/docs> | Swagger UI — an interactive request-builder. Pick a route, fill in parameters, click Execute, see the actual response from the running server. Best for "what does this endpoint return on my bench". |
| <http://localhost:8000/api/redoc> | ReDoc — a single-page reference rendering of the same schema with nested response models expanded. Best for "I just want to read the whole API". |

The schema is generated from the same FastAPI route signatures and
Pydantic response models that the route tables below document — so
the Swagger / ReDoc views are always in sync with the actual
deployed code. No build step.

For setup details (how to start the server, how to register the
MCP equivalent), see the [`litmus serve`](cli.md#cli-serve) CLI
reference.

## Generated tables

The route and tool tables below are generated from source —
`src/litmus/api/app.py` for the HTTP routes and
`src/litmus/mcp/server.py` for the MCP tools. To regenerate after
touching either, run:

```bash
uv run python scripts/generate_reference_docs.py api
```

The pre-commit hook runs the same generator in `--check` mode, so source / docs drift fails the commit.

## MCP server

For AI agents (Claude Code, Cursor, Cline, etc.) over stdio. The MCP tools wrap the same Python functions that back the HTTP routes; behavior is identical.

### Setup

```bash
litmus setup claude-code     # Claude Code
litmus setup claude-desktop  # Claude Desktop
litmus setup copilot         # GitHub Copilot
litmus setup cursor          # Cursor
litmus setup cline           # Cline (VS Code)
litmus mcp serve             # Manual stdio server (auto-launched by the setup commands)
```

### Tools

Twelve tools, all prefixed `litmus_`. Each tool's parameter shape and full docstring is also available via the MCP `tools/list` protocol method; the table below summarizes.

<!-- GENERATED:api-mcp-tools:start -->
| Tool | Parameters | Summary |
|---|---|---|
| `litmus_channels` | `channel_id`, `session_id`, `last_n`, `max_points`, `project` | Query channel data from the streaming channel store. |
| `litmus_discover` | `protocols` | Scan for connected instruments across all protocols. |
| `litmus_events` | `session_id`, `event_type`, `role`, `since`, `limit`, `project` | Query events from the event store. |
| `litmus_files` | `uri`, `session_id`, `run_id`, `limit`, `project` | List FileStore artifacts (blobs, waveforms, streaming captures). |
| `litmus_match` | `part_id`, `station_id`, `fixture_id`, `requirements`, `project` | Check compatibility between parts, stations, and fixtures. |
| `litmus_metrics` | `action`, `part`, `station`, `phase`, `since`, `until`, `period`, `top_n`, `min_samples`, `project` | Query manufacturing-test analytics (DuckDB SQL aggregated from parquet rows). |
| `litmus_open` | `type`, `id`, `base_url` | Get URL to view/edit an entity in the browser UI. |
| `litmus_project` | `action`, `type`, `id`, `path`, `content`, `create`, `scaffold`, `project` | Unified Litmus operations: init, list, get, save, read. |
| `litmus_run` | `test`, `station`, `serial`, `project` | Execute tests and return results. |
| `litmus_runs` | `action`, `run_id`, `limit`, `project` | Query the runs table — denormalized run-level summaries. |
| `litmus_schema` | `yaml_type` | Get JSON Schema for a Litmus YAML file type. |
| `litmus_sessions` | `project` | List known sessions with metadata. |
| `litmus_steps` | `run_id`, `action`, `project` | Query the steps table for one run. |
<!-- GENERATED:api-mcp-tools:end -->

For per-tool parameter detail and worked examples, see [how-to/mcp-integration.md](../../how-to/overview/mcp-integration.md).

### Prompts

Prompts are reusable instruction templates an agent can fetch via the MCP `prompts/get` protocol method. Registered with `@mcp.prompt(name=...)` in `create_mcp_server()`.

<!-- GENERATED:api-mcp-prompts:start -->
| Prompt | Arguments | Summary |
|---|---|---|
| `datasheet-to-test` | — | Get the full datasheet-to-test workflow guide. |
<!-- GENERATED:api-mcp-prompts:end -->

## HTTP API

For any HTTP client.

### Setup

```bash
litmus serve                 # API at http://localhost:8000/api/
litmus serve --reload        # Dev mode with auto-reload
```

> **Running `litmus serve` locally?** The interactive OpenAPI explorer at <http://localhost:8000/api/docs> (Swagger UI) is richer than the table below — full request/response schemas, validation rules, and a "Try it out" button that executes calls from the browser. ReDoc at <http://localhost:8000/api/redoc> and raw spec at <http://localhost:8000/api/openapi.json> for codegen.

Every route is mounted under the `/api/` prefix. Field shapes for request / response models live in [models.md](../data/models.md); query parameter detail is in the per-handler source.

<!-- GENERATED:api-http-routes:start -->
### Runs

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/runs` | `RunsListResponse` | List recent test runs. |
| `GET` | `/api/runs/{run_id}` | `RunView` | Get a specific test run with steps, instruments, and measurements. |
| `GET` | `/api/runs/{run_id}/measurements` | `MeasurementsListResponse` | Get measurements for a test run. |
| `GET` | `/api/runs/{run_id}/steps` | `StepsListResponse` | List steps for a run, ordered by step_index. |
| `GET` | `/api/runs/{run_id}/steps/tree` | `StepsTreeResponse` | Hierarchical step tree built from ``step_path``. |
| `GET` | `/api/runs/{run_id}/ref` | — | Materialize a measurement-output ref URI to its underlying data. |
| `POST` | `/api/runs` | `RunLaunchResponse` | Start a new test run. |
| `GET` | `/api/runs/{run_id}/status` | `RunStatus` | Get status of a running test. |

### Active runs

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/active` | `ActiveRunsResponse` | List currently running tests. |

### Dialogs

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/dialogs` | `DialogsListResponse` | List pending dialogs. |
| `POST` | `/api/dialogs` | `DialogCreateResponse` | Create a pending dialog (from test subprocess). |
| `GET` | `/api/dialogs/{dialog_id}` | `Dialog` | Get a specific pending dialog. |
| `GET` | `/api/dialogs/{dialog_id}/wait` | `DialogResponse` | Long-poll waiting for dialog response. |
| `POST` | `/api/dialogs/{dialog_id}/respond` | `DialogRespondAck` | Respond to a pending dialog. |

### Events & sessions

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/events` | `GenericObjectResponse` | Query events from the event store. |
| `GET` | `/api/sessions` | `GenericObjectResponse` | List known sessions. |
| `GET` | `/api/sessions/{session_id}` | `GenericObjectResponse` | Get events for a specific session. |

### Channels

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/channels` | `GenericObjectResponse` | List known channels from the channel registry. |
| `GET` | `/api/channels/_recent` | `GenericObjectResponse` | Channel registry + recent samples per channel. |
| `GET` | `/api/channels/{channel_id}` | `GenericObjectResponse` | Query channel data. |

### Parts

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/parts` | `PartsListResponse` | List all available part specifications. |
| `GET` | `/api/parts/{part_id}` | `Part` | Get a part specification by ID. |
| `GET` | `/api/parts/{part_id}/requirements` | `PartRequirementsResponse` | Get required capabilities for a part. |

### Stations

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/stations` | `StationsListResponse` | List all available test stations. |
| `GET` | `/api/stations/{station_id}` | `StationConfig` | Get a station configuration by ID. |
| `GET` | `/api/stations/{station_id}/capabilities` | `StationCapabilitiesResponse` | Get capabilities provided by a station. |

### Capability matching

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/match` | `MatchSingleResponse` \| `MatchAllResponse` | Match part requirements to station capabilities. |

### Instruments

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/instruments/types` | `InstrumentTypesResponse` | List distinct instrument ``type`` values present in the catalog. |
| `GET` | `/api/instruments/catalog/{entry_id}` | `InstrumentCatalogEntry` | Get a catalog entry by type or ID. |
| `GET` | `/api/instruments/assets` | `InstrumentAssetsResponse` | List instrument asset files (physical devices you own). |
| `GET` | `/api/instruments/assets/{asset_id}` | `InstrumentAssetFile` | Get an instrument asset by ID. |

### Metrics

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/metrics/summary` | `MetricsResponse` | Yield summary — DuckDB SQL aggregated from parquet rows at request time. |
| `GET` | `/api/metrics/pareto` | `MetricsResponse` | Top failure modes (DuckDB SQL). |
| `GET` | `/api/metrics/cpk` | `MetricsResponse` | Process capability (DuckDB SQL). |
| `GET` | `/api/metrics/trend` | `MetricsResponse` | Yield trend (DuckDB SQL). |
| `GET` | `/api/metrics/retest` | `MetricsResponse` | Retest rates (DuckDB SQL). |
| `GET` | `/api/metrics/time-loss` | `MetricsResponse` | Time lost to failures/errors (DuckDB SQL). |

### MCP-parity tools

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/discover` | `GenericObjectResponse` | Scan for connected instruments across all protocols. |
| `GET` | `/api/open` | `GenericObjectResponse` | Get URL to view/edit an entity in the browser UI. |
| `GET` | `/api/schema/{yaml_type}` | `GenericObjectResponse` | Get JSON Schema for a Litmus YAML file type. |
| `POST` | `/api/save/{entity_type}/{entity_id}` | `GenericObjectResponse` | Create or update an entity (station, part, sequence, fixture, etc.). |
| `GET` | `/api/read` | `GenericObjectResponse` | Read a project file or template. |
| `GET` | `/api/enum/{abbrev}` | `GenericObjectResponse` | Resolve a datasheet abbreviation to its MeasurementFunction enum value(s). |
| `GET` | `/api/enum-reference` | `GenericObjectResponse` | Get the full abbreviation-to-enum reference table as markdown. |

### API discovery

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/openapi.json` | `dict` | OpenAPI 3.0 schema for the Litmus HTTP API. |
| `GET` | `/api/docs` | — | Swagger UI live API explorer (mounted under `/api/` to avoid colliding with NiceGUI's `/docs` Diátaxis browser). |
| `GET` | `/api/redoc` | — | ReDoc rendering of the OpenAPI schema. |

### Other

| Method | Path | Response model | Summary |
|---|---|---|---|
| `GET` | `/api/files/catalog` | `GenericObjectResponse` | List FileStore artifacts from the catalog (MCP-parity with ``litmus_files``). |
| `GET` | `/api/files` | — | Serve a FileStore artifact directly by ``file://`` URI. |
<!-- GENERATED:api-http-routes:end -->

### Response format

All JSON responses use camelCase for envelope fields (`runs`, `events`, `metrics`) and snake_case for record fields (which match the Pydantic model field names — see [models.md](../data/models.md)). Errors follow the FastAPI convention:

```json
{"detail": "Run not found"}
```

with the HTTP status code carrying the category (`404` not found, `422` validation, `500` server error).

### Authentication

No authentication for the local-only `litmus serve` deployment. If you expose the API beyond localhost, put it behind a reverse proxy that handles auth.

## See also

- [how-to/mcp-integration.md](../../how-to/overview/mcp-integration.md) — agent setup walkthrough + per-tool examples
- [reference/event-types.md](../data/event-types.md) — event payload shapes consumed by `/api/events` and `litmus_events`
- [reference/models.md](../data/models.md) — full Pydantic model surface (response_model targets)
- [reference/cli.md](cli.md) — `litmus serve`, `litmus setup`, `litmus mcp serve` CLI flags
- [concepts/three-stores.md](../../concepts/data/three-stores.md) — what `/api/events`, `/api/runs`, `/api/channels` each read from
