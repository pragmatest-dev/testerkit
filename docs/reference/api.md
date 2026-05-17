# API Reference

Litmus exposes two equivalent surfaces over the same Python code:

1. **HTTP API** at `http://localhost:8000/api/*` when `litmus serve` is running. Use from any HTTP client.
2. **MCP server** for AI agents (Claude Code, Cursor, Cline, etc.). Use the [Model Context Protocol](https://modelcontextprotocol.io/) over stdio.

The MCP tools are thin wrappers around the same Python functions that back the HTTP routes; behavior is identical.

> **Live OpenAPI explorer.** When `litmus serve` is running, the OpenAPI schema lives at <http://localhost:8000/api/openapi.json>, with Swagger UI at <http://localhost:8000/api/docs> and ReDoc at <http://localhost:8000/api/redoc>. Use either for interactive request building, response previews, and codegen against the typed response models.

## Setup

### MCP server

```bash
litmus setup claude-code    # Claude Code
litmus setup cursor         # Cursor
litmus setup cline          # Cline (VS Code)
litmus mcp serve            # Manual stdio server
```

### HTTP server

```bash
litmus serve                # API at http://localhost:8000/api/
litmus serve --reload       # Dev mode with auto-reload
```

---

## MCP tools

Source of truth: `src/litmus/mcp/server.py`. Twelve tools, all prefixed `litmus_`:

### `litmus_project`

Read project files (`product`, `station`, `fixture`, `test`, `catalog`, etc.).

```
action: "read" | "list" | "save"
path:   relative path inside the project (for read/save)
type:   entity type (for list)
project: project root path
```

### `litmus_discover`

Discover instruments connected to the bench across all protocols.

```
protocols: list[str] | None — protocol names to scan
           (e.g. ["visa", "ni", "serial"]); omit to scan all
```

### `litmus_match`

Match a product against one or all stations for [capability](../concepts/capabilities.md) compatibility.

```
product_id: required
station_id: optional — single-station check if set, all-stations search if omitted
project:    project root path
```

### `litmus_run`

Execute tests and return results.

```
test:    str — test file or directory (e.g. "tests/test_x.py")
station: str — station id to run on
serial:  str — DUT serial number
project: str — project root path (from litmus action='init' response)
```

### `litmus_open`

Open a project resource (product, station, fixture, test) in the operator UI.

```
type:    entity type
id:      entity id
project: project root path
```

### `litmus_schema`

Return the JSON schema for a YAML entity type.

```
yaml_type: "product" | "station" | "fixture" | "catalog" | "instrument_asset" | "project"
```

### `litmus_events`

Query the event store.

```
session_id: filter by session UUID
event_type: filter by event type
role:       filter by instrument role
since:      ISO timestamp, only events after
limit:      max events (default 100)
project:    project root path
```

### `litmus_sessions`

List known sessions with metadata from `SessionStarted` events.

```
project: project root path
```

### `litmus_channels`

Query channel data from the streaming channel store.

```
channel_id: channel to query (e.g. "scope.ch1_waveform")
session_id: filter to a specific session
last_n:     return only the last N rows
max_points: downsample to at most N rows (LTTB)
project:    project root path
```

### `litmus_metrics`

Query manufacturing-test analytics (DuckDB SQL aggregated from parquet rows).

```
action:      str — one of: summary, pareto, cpk, trend, retest, time_loss
product:     filter by product / part number
station:     filter by station name
phase:       test phase (default excludes development; 'all' = no filter)
since:       ISO start date (inclusive)
until:       ISO end date (inclusive)
period:      time bucket — "day" (default), "week", "month"
top_n:       number of top failures for pareto (default 10)
min_samples: minimum sample count for cpk (default 10)
project:     project root path
```

### `litmus_runs`

Query the runs table — denormalized run-level summaries.

```
action:  "list" (most recent runs) or "get" (one run by id); default "list"
run_id:  required when action="get"; full UUID or 8-char prefix
limit:   max rows when action="list" (default 50)
project: project root path
```

### `litmus_steps`

Query the steps table for one run.

```
run_id:  required — full UUID or 8-char prefix of the run
action:  "list" (flat ordered rows) or "tree" (step_path hierarchy); default "list"
project: project root path
```

---

## HTTP endpoints

Base URL: `http://localhost:8000/api`. Source of truth: `src/litmus/api/app.py`. Response models are typed Pydantic — the OpenAPI explorer documents every shape.

### Runs

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/runs` | List recent runs (paginated) |
| GET | `/runs/{run_id}` | Get a run by id (`RunView`) |
| POST | `/runs` | Start a new run (`RunLaunchResponse`) |
| GET | `/runs/{run_id}/status` | Live status of a running test |
| GET | `/runs/{run_id}/ref` | Binary reference data attached to a vector (waveform, image, file) |
| GET | `/active` | Currently running tests |

```bash
curl http://localhost:8000/api/runs
curl http://localhost:8000/api/runs/abc12345
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"product_id": "power_board", "dut_serial": "SN001", "station_id": "bench_1", "test_path": "tests/"}'
```

### Products

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/products` | List all products |
| GET | `/products/{product_id}` | Get product by id |

### Stations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/stations` | List all stations |
| GET | `/stations/{station_id}` | Get station config by id |

### Matching

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/match?product_id=X` | Find compatible stations |
| GET | `/match?product_id=X&station_id=Y` | Check specific station compatibility |

```bash
curl "http://localhost:8000/api/match?product_id=power_board"
curl "http://localhost:8000/api/match?product_id=power_board&station_id=bench_1"
```

### Instruments

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/instruments/types` | List the instrument types defined in the catalog |
| GET | `/instruments/catalog/{entry_id}` | Get a catalog entry by id |
| GET | `/instruments/assets` | List instrument assets (per-instrument calibration / inventory records) |
| GET | `/instruments/assets/{asset_id}` | Get one asset |

### Dialogs (operator)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dialogs` | List pending dialogs |
| POST | `/dialogs` | Create a dialog |
| GET | `/dialogs/{dialog_id}` | Get dialog by id |
| GET | `/dialogs/{dialog_id}/wait` | Long-poll for the operator response |
| POST | `/dialogs/{dialog_id}/respond` | Submit a response |

### Events

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/events` | Query the event store (filterable by `session_id`, `type`, `role`, `since`, `limit`) |

```bash
curl http://localhost:8000/api/events
curl "http://localhost:8000/api/events?session_id=abc12345&type=instrument.read"
```

### Sessions

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/sessions` | List sessions |
| GET | `/sessions/{session_id}` | Events for one session |

### Channels

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/channels` | List known channels |
| GET | `/channels/{channel_id}` | Query channel data (supports `session_id`, `last_n`, `max_points`, `start`, `end`) |

```bash
curl http://localhost:8000/api/channels
curl "http://localhost:8000/api/channels/dmm.voltage?max_points=500"
```

### Metrics

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/metrics/summary` | Pass/fail/yield summary |
| GET | `/metrics/pareto` | Failure Pareto |
| GET | `/metrics/cpk` | Process capability index per characteristic |
| GET | `/metrics/trend` | Yield/value trend over time |
| GET | `/metrics/retest` | Retest analysis |
| GET | `/metrics/time-loss` | Time spent in failed / retested vectors |

All metrics endpoints return `MetricsResponse` and accept the same filter parameters; consult the OpenAPI explorer for the exact filter shape.

### Discovery / catalog management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/discover` | VISA discovery scan |
| GET | `/open` | Open a project resource in the operator UI |
| GET | `/schema/{yaml_type}` | JSON schema for an entity YAML type |
| POST | `/save/{entity_type}/{entity_id}` | Save a YAML entity |
| GET | `/read` | Read a YAML entity |
| GET | `/enum/{abbrev}` | Resolve a measurement-function abbreviation to the enum value |
| GET | `/enum-reference` | Full enum reference for AI tools |

---

## Response format

All endpoints return JSON. The exact shape per route is in the response model classes (`RunsListResponse`, `RunView`, `MetricsResponse`, etc.) — see [Models](models.md) and the OpenAPI schema.

Error responses follow FastAPI's convention:

```json
{ "detail": "Run 'abc12345' not found" }
```

## Authentication

No authentication is required by default; the server binds to localhost. For production deployments, place behind a reverse proxy.

## See also

- [CLI reference](cli.md) — `litmus serve`, `litmus mcp serve`, `litmus setup`
- [Python client](client.md) — `LitmusClient` for programmatic result submission
- [`litmus.connect()`](connect.md) — single-call instrument-access helper
