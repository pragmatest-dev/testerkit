# API Reference

Litmus exposes two APIs for AI agents and external tools:

1. **MCP Server** — For Claude Code, Cursor, Cline, and other MCP-compatible agents
2. **HTTP API** — REST endpoints for any HTTP client

Both APIs provide identical functionality.

## Setup

### MCP Server

```bash
# Claude Code
litmus setup claude-code

# Cursor
litmus setup cursor

# Cline (VS Code)
litmus setup cline

# Manual
litmus mcp serve
```

### HTTP Server

```bash
litmus serve
# API available at http://localhost:8000/api/
```

## MCP Tools

### Products

**list_products**
List all available product specifications.

```
Returns: [{id, name, description, revision, characteristics_count, specs}]
```

**get_product_spec**
Get full product specification by ID.

```
Params: product_id (string)
Returns: {product, characteristics, specs}
```

**derive_required_capabilities**
Get capability requirements derived from product characteristics.

```
Params: product_id (string)
Returns: [{direction, domain, signal_types, characteristic_name, range_max}]
```

### Stations

**list_stations**
List all available test stations.

```
Returns: [{id, name, location, description}]
```

**get_station_config**
Get station configuration by ID.

```
Params: station_id (string)
Returns: {station, instruments}
```

**get_station_capabilities**
Get capabilities provided by a station's instruments.

```
Params: station_id (string)
Returns: [{name, direction, domain, signal_types, instrument_type, instrument_name}]
```

### Capability Matching

**find_compatible_stations**
Find all stations that can test a product.

```
Params: product_id (string)
Returns: [{station_id, station_name, compatible, match_result}]
```

**check_station_compatibility**
Check if a specific station can test a specific product.

```
Params: product_id (string), station_id (string)
Returns: {product_id, station_id, compatible, requirements_count, satisfied_count, missing_count, missing[], matches[]}
```

### Instruments

**list_instrument_types**
List available instrument types in the library.

```
Returns: ["dmm", "scope", "psu", ...]
```

**get_instrument_library**
Get instrument definition from library.

```
Params: instrument_type (string)
Returns: {name, type, manufacturer, models, capabilities}
```

### Test Sequences

**list_sequences**
List available test sequences.

```
Returns: [{id, description, product_family, test_phase}]
```

### Write Operations

**save_product_spec**
Save a new product specification.

```
Params: product_id (string), spec (yaml string)
Returns: {saved: true, path: string}
```

**save_test_sequence**
Save a new test sequence.

```
Params: sequence_id (string), content (python string)
Returns: {saved: true, path: string}
```

## HTTP Endpoints

Base URL: `http://localhost:8000/api`

### Products

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/products` | List all products |
| GET | `/products/{id}` | Get product by ID |
| GET | `/products/{id}/requirements` | Get capability requirements |

**Example:**
```bash
curl http://localhost:8000/api/products
curl http://localhost:8000/api/products/power_board
curl http://localhost:8000/api/products/power_board/requirements
```

### Stations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/stations` | List all stations |
| GET | `/stations/{id}` | Get station by ID |
| GET | `/stations/{id}/capabilities` | Get station capabilities |

**Example:**
```bash
curl http://localhost:8000/api/stations
curl http://localhost:8000/api/stations/bench_1
curl http://localhost:8000/api/stations/bench_1/capabilities
```

### Matching

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/match?product_id=X` | Find compatible stations |
| GET | `/match?product_id=X&station_id=Y` | Check specific compatibility |

**Example:**
```bash
# Find all compatible stations
curl "http://localhost:8000/api/match?product_id=power_board"

# Check specific station
curl "http://localhost:8000/api/match?product_id=power_board&station_id=bench_1"
```

### Instruments

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/instruments` | List instrument types |
| GET | `/instruments/{type}` | Get instrument definition |

**Example:**
```bash
curl http://localhost:8000/api/instruments
curl http://localhost:8000/api/instruments/dmm
```

### Test Runs

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/runs` | List recent test runs |
| GET | `/runs/{id}` | Get run by ID |
| GET | `/runs/{id}/measurements` | Get measurements for run |
| POST | `/runs` | Start a new test run |
| GET | `/runs/{id}/status` | Get status of running test |
| GET | `/active` | List currently running tests |

**Example:**
```bash
# List runs
curl http://localhost:8000/api/runs

# Get specific run
curl http://localhost:8000/api/runs/abc12345

# Get measurements
curl http://localhost:8000/api/runs/abc12345/measurements

# Start a run
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"dut_serial": "SN001", "station_id": "bench_1", "test_path": "tests/"}'
```

### Test Sequences

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/sequences` | List test sequences |

### Dialogs (Operator UI)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dialogs` | List pending dialogs |
| POST | `/dialogs` | Create a dialog |
| GET | `/dialogs/{id}` | Get dialog by ID |
| GET | `/dialogs/{id}/wait` | Long-poll for response |
| POST | `/dialogs/{id}/respond` | Respond to dialog |

## Response Format

All endpoints return JSON. Successful responses:

```json
{
  "products": [...],
  "count": 5
}
```

Error responses:

```json
{
  "error": "Product 'xyz' not found"
}
```

## Authentication

Currently no authentication is required. The server binds to localhost by default.

For production deployments, place behind a reverse proxy with authentication.

## Next Steps

- [Quick Start](quickstart.md) — Getting started
- [Python Client](client.md) — Python API for result submission
