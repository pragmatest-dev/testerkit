# Page audit: docs/concepts/platform-architecture.md

**Quadrant:** Concepts / Explanation
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 1 | 2 | 4 |
| Audience | 1 | 3 | 3 |
| Accuracy | 0 | 2 | 3 |
| Gaps | 0 | 4 | 3 |
| Cross-links | 2 | 5 | 6 |
| **Total** | **4** | **18** | **21** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| WARNING | L5-14 | Page opens with a Platform-vs-Framework comparison table before establishing what problem Litmus solves. A concept page should lead with the "why" / problem, not a definitional contrast. The reader is asked to evaluate a distinction they have no stake in yet. |
| WARNING | L15-55 | Big ASCII service diagram appears immediately after the comparison table, before any concept it depicts has been explained (Configuration Service, Matching Service, Event Log Service, Dialogs Service, Channels Service are all named here for the first time and never defined elsewhere on the page). Reader hits opaque labels with no anchor. |
| SUGGESTION | L83-92 | "pytest Integration (Primary Path)" example uses `psu`, `dmm`, `context`, `logger` fixtures before the "Multiple Entry Points" table (which mentions them) has been read in any depth. Consider grouping the prose around fixtures together. |
| SUGGESTION | L212-237 | The "Architecture Summary" diagram at the end repeats material covered by the L20-55 diagram and the L130-158 MCP diagram. A concept page benefits more from one clean model than three overlapping ones. |

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| CRITICAL | L162 | Marketing language | "Benefits of Platform Architecture" — entire section reads as a sales pitch ("Separation of Concerns", "Flexibility", "Team Scalability") rather than explanation of how the platform actually works. |
| WARNING | L172 | Marketing language | "**Flexibility**" as a section header — the exact word called out in audience guidance ("They've been promised 'flexibility' before"). |
| WARNING | L237 | Marketing-adjacent prose | "Litmus is the infrastructure layer that connects your tests (top) to your data (bottom), regardless of how you choose to run them." — slogan-style closer rather than concept content. |
| SUGGESTION | L3 | Hedging/overclaim | "Understanding this distinction is **key** to using Litmus effectively." — meta-instruction telling the reader the content matters; either show it matters or cut. |
| SUGGESTION | L101-102 | Hedging | "Litmus **offers** an incremental migration path that **preserves** existing test logic" — soft verbs; the page could say what the adapter actually does. |
| SUGGESTION | L160 | Voice inconsistency | "**Important:** Litmus does NOT call LLMs." — the all-caps "NOT" is shouty for a concept page. Same factual point reads cleaner without emphasis tricks. |
| SUGGESTION | L186-191 | Marketing list | "Developers write test code... Engineers configure limits... Operators run tests..." — reads as a persona pitch slide. Either tie each role to a concrete platform service or cut. |

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| CRITICAL | L5-13, L193-200 | Anti-audience content | Two framework-comparison tables ("Platform vs Framework", "Comparison with Other Systems") are exactly the "framework comparison without engineering context" the audience guidance forbids. A test engineer with a deadline does not need a NI TestStand / Robot Framework / OpenHTF feature matrix. |
| WARNING | L162-191 | Anti-audience content | "Benefits of Platform Architecture" with sub-sections "Separation of Concerns", "Flexibility", "Incremental Adoption", "Team Scalability" is architecture-evaluator content, not test-engineer content. |
| WARNING | L17, L57, L65 | Programmer jargon | "infrastructure services", "test execution engine", "Multiple Entry Points" — software-architecture vocabulary instead of test-engineering vocabulary (what the operator types, what runs the test, where results land). |
| WARNING | L115, L351 in client | Wrong vocabulary | `station_id="bench_1"` in the Results API example — per project guidance, operator-facing prose should use `station_hostname`, not `station_id`. The doc's code is technically correct, but it propagates the very identifier the project ruled against in user-facing material. |
| SUGGESTION | L11 | Programmer jargon | "**Entry points**" — fine for software architects, less natural for test engineers; "How you reach it" is more direct. |
| SUGGESTION | L46-54 | Anti-audience diagram | The "platform feeds three runners" ASCII fan-out explains the software pattern (delegation) rather than a test flow (what happens when an operator runs a DUT). |
| SUGGESTION | L196 | Audience mismatch | "Could build integration" (for Robot Framework) is a roadmap teaser inside a concept page — invites questions the page can't answer. |

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| WARNING | L71 | "pytest-native: `context`, `verify`, `logger` fixtures" — listed as if these are *the* fixtures | The plugin defines ~20+ fixtures (logger, run_context, product_context, mock_instruments, station_config, fixture_config, instrument_records, instruments, instrument, dut, routes, pins, fixture_manager, sync, context, connections, verify, limits, vectors, prompt). Calling out only three understates the surface. | `src/litmus/pytest_plugin/__init__.py:369-1150` |
| WARNING | L88 | `def test_output_voltage(context, psu, dmm, logger)` example | `psu` / `dmm` are dynamic role-based fixtures from station config — not defined as built-in fixtures in the plugin. The example will only work after station YAML is configured. Page does not say so. | `src/litmus/pytest_plugin/__init__.py:598, 762-773` (only `instruments`/`instrument` are defined; per-role names come from station config) |
| SUGGESTION | L73 | "HTTP API: `POST /api/runs`, `GET /api/runs/{id}`" | Correct paths exist (`/api/runs` POST line 311, `/api/runs/{run_id}` GET line 214). The path param is `{run_id}` in source, not `{id}` — minor stylistic divergence. | `src/litmus/api/app.py:214, 311` |
| SUGGESTION | L40 | Channels Service bullets: "ChannelStore / Flight RPC / LTTB decim." | All three verified. ChannelStore class exists; Flight is the Arrow Flight transport; LTTB is the decimation algorithm. | `src/litmus/data/channels/store.py:50, 173`; `src/litmus/data/_ipc_writer.py:27` |
| SUGGESTION | L154 | "Tools (twelve, all `litmus_*`)" | Confirmed: exactly 12 `@mcp.tool(name="litmus_*")` decorators. All 12 names match the page's list. | `src/litmus/mcp/server.py:282, 341, 361, 412, 434, 454, 474, 499, 514, 537, 589, 612` |
| VERIFIED | — | 14 claims verified against source (12 MCP tool names; HTTP endpoint paths; client.start_run signature with `dut_serial`/`station_id`/`test_phase`; `RunBuilder.step`, `StepBuilder.measure`, `VectorBuilder` exist; `Context.get_param(key, default)`; `context`/`verify`/`logger` fixtures exist; EventStore class; ChannelStore class; Arrow IPC storage; Flight RPC; LTTB decimation; `litmus runs` / `litmus show` CLI commands; OpenHTF adapter doc target exists) | — | — |

## Gaps

| Severity | Location | Gap |
|---|---|---|
| WARNING | L17-55 | The "What Litmus Provides" diagram names six services (Configuration, Instruments, Matching, Event Log, Dialogs, Channels) but never explains what any of them *do* or links to a page that does. A reader who needs to understand "what is the Matching Service" has nowhere to go from this page. |
| WARNING | L107-128 | Results API example shows `client.start_run` / `run.step` / `step.measure` / `run.finish` but does not say: where do results land by default? What does `data_dir` default to? When does failure happen? What format are they stored in? The link to client reference covers it, but a concept page should at least gesture at the answer. |
| WARNING | L130-160 | MCP section says "exposes its platform services via MCP" but does not connect specific MCP tools back to the six services in the L20-55 diagram. Reader cannot answer "which service does `litmus_match` belong to?" |
| WARNING | L178-184 | "Incremental Adoption" lists four phases but does not state what's actually required to start (does Phase 1 need a station YAML? a product? a `litmus.yaml`?). Concrete on-ramps belong on a concept page that promises incremental adoption. |
| SUGGESTION | L65-82 | "Multiple Entry Points" table promises "All entry points share the same configuration files, instrument drivers, result storage, data models" but does not explain what that means in practice (e.g., a measurement logged via pytest is queryable via HTTP and MCP without re-config). |
| SUGGESTION | L99-105 | OpenHTF section is one paragraph and a link. A concept page should at least name the three migration strategies before sending the reader elsewhere. |
| SUGGESTION | L173-175 | Storage line "Event log (Arrow IPC) + Parquet (materialized views) + Channels (time-series)" surfaces the three-stores model in passing. A concept page benefits from explaining *why* there are three rather than one — or linking explicitly to `concepts/three-stores.md`. |

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| CRITICAL | L17-55 (services diagram) | First use of "Configuration Service", "Matching Service", "Event Log Service", "Dialogs Service", "Channels Service" — none link to their concept page. Most have one: `concepts/three-stores.md`, `concepts/event-log.md`, `concepts/capabilities.md`, `concepts/flight-streaming.md`. Reader has no path to deeper material. |
| CRITICAL | L88, L71 | First use of `context`, `verify`, `logger`, `psu`, `dmm` fixtures — no link to `reference/litmus-fixtures.md`. Page lists them as the primary fixture surface but never points at the reference that defines them. |
| WARNING | L107-128 | First use of `LitmusClient`, `RunBuilder`, `StepBuilder`, `VectorBuilder` — only one trailing link to `reference/client.md`. Should link on first mention (the import line) for readers who jump to the code block. |
| WARNING | L130-158 | First use of "MCP", "Model Context Protocol" and the 12 tool names — no link to `how-to/mcp-integration.md` or `reference/api.md#mcp-tools`. The 12-tool list is duplicated effort with `how-to/mcp-integration.md` which says "## The 12 MCP Tools". |
| WARNING | L72 | "CLI" uses `litmus runs`, `litmus show` without linking to `reference/cli.md`. |
| WARNING | L73 | "HTTP API: `POST /api/runs`, `GET /api/runs/{id}`" — no link to `reference/api.md#http-endpoints`. |
| WARNING | End of page | No "See also" section. Concept pages may weave links in prose, but this one does neither — only one link in the body (the OpenHTF adapter at L103). For a hub-style overview page, this is a structural gap. |
| SUGGESTION | L13 | "Robot Framework" and "NI TestStand" — external context. No link expected, but if comparison is kept, an aside on why Litmus is comparable would help. |
| SUGGESTION | L62 | "OpenHTF" first use — link to `integration/openhtf-adapter.md` here rather than only at L103. |
| SUGGESTION | L74 | "Operator UI" first use — link to wherever the operator UI is documented (currently no dedicated concept page exists, but a how-to or reference would do). |
| SUGGESTION | L173 | "Event log (Arrow IPC) + Parquet (materialized views) + Channels (time-series)" — three concept names, each with a dedicated page (`concepts/event-log.md`, `concepts/results-storage.md`, `concepts/flight-streaming.md`); link them. |
| SUGGESTION | L181 | "Use Results API to store test data" — link to `integration/results-api.md` (file exists). |
| SUGGESTION | L208 | "Use OpenHTF adapter" in the "When to Use What" table — link to `integration/openhtf-adapter.md`. |
