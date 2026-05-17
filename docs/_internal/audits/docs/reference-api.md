# Page audit: docs/reference/api.md

**Quadrant:** Reference (HTTP REST endpoints + MCP tools exposed by `litmus serve` / `litmus mcp serve`)
**Audited:** 2026-05-17

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 0 | 2 |
| Audience | 0 | 1 | 2 |
| Accuracy | 6 | 5 | 3 |
| Gaps | 2 | 5 | 3 |
| Cross-links | 0 | 2 | 5 |
| **Total** | **8** | **14** | **17** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ⚠️ WARNING | L32–166 vs L168–290 | MCP tools section precedes HTTP endpoints, but the page lead (L3–5) introduces HTTP first ("1. HTTP API ... 2. MCP server"). The body inverts the order presented in the at-a-glance list, forcing the reader to scroll past 130+ lines of MCP tools to reach the HTTP API they were primed for. |
| 💡 SUGGESTION | L34 | "Twelve tools" hard-codes a count that will drift as tools are added/removed; the count is not echoed by a foreshadowing table, so the reader has no way to verify they have seen all twelve without counting headings. |
| 💡 SUGGESTION | L168 | The HTTP section's introductory paragraph says response models are typed Pydantic and points to the OpenAPI explorer — but the Setup section (L12–28) already mentioned the explorer at L10. Repeating the pointer here is fine; placing the Authentication section (L303) and Response format section (L293) AFTER the per-endpoint tables is awkward — most readers will scroll past those once they have copied a `curl` command, never seeing them. Promote both to immediately under L170. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| 💡 SUGGESTION | L8 | Passive voice (clear actor) | "The MCP tools are thin wrappers around the same Python functions that back the HTTP routes; behavior is identical." — fine, but "behavior is identical" hides the actor; consider "both call the same Python functions, so they return identical results." |
| 💡 SUGGESTION | L305 | Hedging / vague advice | "For production deployments, place behind a reverse proxy." — passive, no actor; rewrite as "Place the server behind a reverse proxy for production deployments." |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L34 | Programmer jargon | "Source of truth: `src/litmus/mcp/server.py`." — pointing test engineers to source files in a reference page is acceptable for an open-source platform, but L170's "Source of truth: `src/litmus/api/app.py`" makes the same move; together they invite a reader to "read the source" instead of trusting the reference. A test engineer who needs the source has been failed by the reference. |
| 💡 SUGGESTION | L130 | Programmer jargon | "Query manufacturing-test analytics (DuckDB SQL aggregated from parquet rows)." — "DuckDB SQL aggregated from parquet rows" is an implementation detail; a test engineer cares that the metrics are computed at request time from saved run data. |
| 💡 SUGGESTION | L188 | Wrong vocabulary | The POST `/runs` example uses `"product_id": "power_board"` and `"station_id": "bench_1"`. Per CLAUDE.md / project memory, operator-facing identifiers should use `dut_part_number` and `station_hostname` rather than `product_id` / `station_id`. The API may still take `product_id`/`station_id` on the wire — but the curl example is the closest the reader gets to "operator-facing", and seeing the wrong names here normalizes them. At minimum, add a one-line note that `product_id` is the same value displayed as the product's part number. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L259 | Doc says `/channels/{channel_id}` supports `start`, `end` query params | Actual params are `since`, `until` (not `start`/`end`). `last_n`, `max_points`, `session_id` are correct. | `src/litmus/api/app.py:509-515` |
| ❌ CRITICAL | L41–45 | `litmus_project` signature: `action: "read"\|"list"\|"save"`, plus `path`, `type`, `project` | Actual `action` values: `init`, `list`, `get`, `save`, `read`, `lookup_enum`, `enum_reference`. Actual params also include `id`, `content`, `create`, `scaffold`. Doc is missing `init`/`get`/`lookup_enum`/`enum_reference` actions and `id`/`content`/`create`/`scaffold` params. | `src/litmus/mcp/server.py:282-335` |
| ❌ CRITICAL | L80–85 | `litmus_open` params: `type`, `id`, `project` | Actual signature: `type: str`, `id: str`, `base_url: str = "http://localhost:8000"`. There is NO `project` param; instead there is a `base_url` param. Doc names the wrong third parameter. | `src/litmus/mcp/server.py:434-448` |
| ❌ CRITICAL | L60–64 | `litmus_match` params: `product_id` (required), `station_id` (optional), `project` | Actual: `product_id: str \| None = None` (OPTIONAL), `station_id`, `fixture_id`, `requirements: list[dict] \| None`, `project`. Doc marks `product_id` required (it isn't), omits `fixture_id` and `requirements` (two whole usage patterns: fixture-to-stations and ad-hoc requirements). | `src/litmus/mcp/server.py:361-406` |
| ❌ CRITICAL | L174–181 | Runs HTTP table | Missing endpoints: `GET /runs/{run_id}/measurements`, `GET /runs/{run_id}/steps`, `GET /runs/{run_id}/steps/tree`. All three exist and have typed response models (`MeasurementsListResponse`, `StepsListResponse`, `StepsTreeResponse`). | `src/litmus/api/app.py:222-264` |
| ❌ CRITICAL | L256–259 | Channels HTTP table | Missing endpoint: `GET /channels/_recent` (channel registry + recent samples per channel, used by operator UI for sparklines). | `src/litmus/api/app.py:488-502` |
| ⚠️ WARNING | L92 | `litmus_schema` `yaml_type` enumeration: `"product"\|"station"\|"fixture"\|"catalog"\|"instrument_asset"\|"project"` | Actual docstring also lists `sequence`; `yaml_type` is `str \| None = None` (omittable to list types). Doc omits `sequence` and the "omit to list" affordance. | `src/litmus/mcp/server.py:454-468` |
| ⚠️ WARNING | L192–197 | Products HTTP table | Missing endpoint: `GET /products/{product_id}/requirements` (returns required capabilities for a product). | `src/litmus/api/app.py:552-575` |
| ⚠️ WARNING | L198–204 | Stations HTTP table | Missing endpoint: `GET /stations/{station_id}/capabilities` (returns capabilities provided by a station). | `src/litmus/api/app.py:595-623` |
| ⚠️ WARNING | L34 | "Twelve tools, all prefixed `litmus_`" | Count is correct (12 `@mcp.tool` decorators). Note however there is also a `@mcp.prompt(name="datasheet-to-test")` on line 634; "tools" excludes prompts, so the doc is technically correct but the page never mentions that MCP prompts exist at all. | `src/litmus/mcp/server.py:634` |
| ⚠️ WARNING | L70–74 | `litmus_run` parameters: `test`, `station`, `serial`, `project` — all marked positional with no defaults | Source signature `def run(test: str, station: str, serial: str, project: str)` — all required, no defaults. Doc is correct but the `project` description "from litmus action='init' response" is opaque without context (and the doc never describes the `init` action — see L41 critical above). | `src/litmus/mcp/server.py:412-428` |
| 💡 SUGGESTION | L266–275 | Metrics table descriptions | "Failure Pareto" (L271), "Process capability index per characteristic" (L272) — the underlying tool docstrings say "Top failure modes by count" and "Process capability (Cpk/Cp) per measurement" respectively. "per characteristic" is a step removed from what the endpoint actually groups by ("per measurement"); the docstring wording is more precise. | `src/litmus/mcp/server.py:553-558` |
| 💡 SUGGESTION | L277 | "All metrics endpoints return `MetricsResponse` and accept the same filter parameters" | True for response model, but parameters differ: `pareto` adds `top_n`, `cpk` adds `min_samples`, `summary/trend/retest/time-loss` add `period`. "the same filter parameters" understates the variation. | `src/litmus/api/app.py:708-827` |
| 💡 SUGGESTION | L300 | "Error responses follow FastAPI's convention: `{ \"detail\": \"Run 'abc12345' not found\" }`" | The example error string differs from any actual literal in the source. Actual: `HTTPException(status_code=404, detail="Run not found")` (no run id quoted). Either change the example to match an actual error literal or note that the example is illustrative. | `src/litmus/api/app.py:219` |
| ✅ VERIFIED | — | ~28 claims verified against source (12 MCP tool names; `/api` prefix; all 9 metrics + DuckDB SQL claim; HTTP setup commands `litmus setup claude-code/cursor/cline`, `litmus mcp serve`, `litmus serve`, `litmus serve --reload`; `litmus_setup` subcommands exist; OpenAPI/Swagger/ReDoc endpoint paths; POST `/runs` body shape; `LitmusClient` class exists; `concepts/capabilities.md` exists; all `See also` link targets resolve) | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | L41–45, L80–85, L60–64 | Three MCP tool signatures are partial (see Accuracy). For a REFERENCE page, omitting actions and parameters means an AI agent calling these tools from the documentation alone will fail. `litmus_project` is the entry point that returns `project_root` for every other tool — without documenting its `init` action, the rest of the page is unbootstrappable from the docs. |
| ❌ CRITICAL | L168–290 | No example of MCP tool invocation. The page has HTTP `curl` examples but zero examples of how an MCP client actually calls these tools (raw JSON-RPC, IDE config, or even pseudo-code). A reader who has never used MCP gets the tool list with no idea how to call it. |
| ⚠️ WARNING | L12–28 | "Setup" lists `litmus setup claude-code/cursor/cline` but never says what these commands DO. A reader who runs `litmus setup claude-code` should know whether it writes to `~/.claude/`, adds an entry to `.mcp.json` in cwd, asks for confirmation, etc. The doc just says "run this". |
| ⚠️ WARNING | L303–305 | "No authentication is required by default; the server binds to localhost." — does NOT mention what happens if a user passes `--host 0.0.0.0`. A test engineer in a shared bench network needs to know whether running `litmus serve --host 0.0.0.0` exposes an unauthenticated API. State the bind default explicitly and warn about non-localhost binding. |
| ⚠️ WARNING | L168 | "Base URL: `http://localhost:8000/api`" — the default port and host are stated, but not how to discover them when the user has run `litmus serve --port 9000`. No reference to the `--host`/`--port` flags of `litmus serve` (which exist per `src/litmus/cli.py:519-521`). |
| ⚠️ WARNING | L8 | "behavior is identical" between MCP tools and HTTP routes — but the parameter names differ (e.g. MCP `event_type` vs HTTP `type` for `/events`; MCP `litmus_open(base_url=)` vs HTTP `/open` takes the same `base_url`; MCP `litmus_project` has many actions, HTTP exposes them as separate routes `/save/`, `/read`, `/enum/`, `/enum-reference`). State this mapping so the reader is not misled by "identical". |
| ⚠️ WARNING | L226–234 | Dialogs section gives the endpoints but does NOT explain the dialog lifecycle: what triggers a dialog creation (test subprocess calls into the operator UI), what the operator sees, how the wait endpoint relates to the response endpoint, what a `timed_out: true` response means. Without this, the endpoint shapes are opaque. |
| 💡 SUGGESTION | L130, L266 | The metrics action enum (summary, pareto, cpk, trend, retest, time_loss) is repeated for the MCP tool but the HTTP table only lists the endpoints. Add a one-line cross-reference: "Each HTTP route corresponds to one `action` value on the MCP `litmus_metrics` tool." |
| 💡 SUGGESTION | L94–106 | `litmus_events` documents `event_type` as a filter but does not enumerate well-known event types (`session.started`, `instrument.read`, etc.). The MCP tool's source docstring gives examples; the doc page should too. |
| 💡 SUGGESTION | L293–301 | "Response format" says shapes are in the response model classes and links to Models — but does not state the response envelope convention. Are all responses wrapped in a top-level key (`{"runs": [...]}, {"steps": [...]}` per the source) or unwrapped (`RunView`)? Both shapes are present in the source. State the convention so the reader knows which to expect per endpoint family. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ⚠️ WARNING | L34, L170 | "Source of truth: `src/litmus/mcp/server.py`" and "`src/litmus/api/app.py`" appear as bare code-path mentions. For a reference page that points readers at source, consider linking to the file on GitHub (or wherever the source is browsable) — readers reading the doc on a website have no easy way to jump to those files. |
| ⚠️ WARNING | L226 | Dialogs section has no link to any concept page explaining the operator-dialog flow. There is no concept page for "operator dialogs" — but the endpoint table is the only place in docs that mentions them. Either link to a concept (if one exists / is planned) or add a brief inline blurb. |
| 💡 SUGGESTION | L58 | "[capability](../concepts/capabilities.md) compatibility" — good first-use link. But "compatibility" / "matching" is mentioned again at L206 (Matching HTTP table) with no link back. Add a one-line cross-reference. |
| 💡 SUGGESTION | L218 | "List the instrument types defined in the catalog" — first use of "catalog" on the page; consider linking to `reference/capability-schema.md` or the concept page that defines what the catalog is. |
| 💡 SUGGESTION | L266 | "Metrics" section first-uses "FPY", "Pareto", "Cpk" via the MCP description on L130 ("summary, pareto, cpk, trend, retest, time_loss"). These are T&M terms (do not explain them) but linking to whichever concept/how-to explains how Litmus computes them would help readers building dashboards. |
| 💡 SUGGESTION | L226 | "Dialogs (operator)" has no See-also link to the operator UI / NiceGUI pages that consume these endpoints. |
| 💡 SUGGESTION | L309–311 | "See also" lists 3 items but does not include `reference/models.md` even though that page is explicitly referenced in the body (L295: "see [Models](models.md)"). A reader skipping to See also will miss it. Also missing: `reference/capability-schema.md` (catalog), `reference/litmus-fixtures.md`, and any concept page on the event log / channel store, all of which are referenced in tool descriptions. |
