# Page audit: docs/how-to/managing-sessions.md

**Quadrant:** How-to (session management — list, query, retention)
**Audited:** 2026-05-17

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 1 |
| Audience | 0 | 3 | 2 |
| Accuracy | 2 | 3 | 1 |
| Gaps | 2 | 4 | 2 |
| Cross-links | 0 | 3 | 4 |
| **Total** | **4** | **16** | **12** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ⚠️ WARNING | L5–L22 ("Starting a Session") | A how-to should lead with prerequisites + the task. The page jumps straight into "Starting a Session" with no statement of what the reader already needs (a `litmus.yaml`, a station YAML named "cell-7", a project root) before `connect("cell-7")` will work. Readers running the snippet cold will get errors. |
| ⚠️ WARNING | L19–L22 ("With pytest") | The pytest sub-section is a single sentence with no example and no link to the pytest workflow. It interrupts the flow between "starting via `connect()`" and "querying" without delivering enough value to justify its position. Consider deleting or moving to a See Also bullet. |
| 💡 SUGGESTION | L23–L31 ("Session Metadata") | The Metadata section sits between Starting and Querying. Since the reader is told *how* to query (next section) before they're shown an example of a metadata field being read, the section reads as a digression. Either move Metadata after Querying (so the example queries are concrete first), or fold the fields into the Querying examples. |
| 💡 SUGGESTION | L82–L92 ("Data Retention") | Retention is the deepest topic on the page but is placed before "See Also" with no transition. For a how-to ordered by task, retention is plausibly its own how-to — at minimum, lead the section with the operator question ("How long is data kept?") before showing the CLI. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L86 | Hedging / forward-looking | "(planned CLI command)" — actually shipped; see `src/litmus/cli.py:2358`. Remove the parenthetical. |
| 💡 SUGGESTION | L92 | Marketing-flavoured reassurance | "No surprise data loss." — close to slogan voice. Either drop or replace with a factual statement ("Nothing is deleted unless you run `litmus data prune`."). |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L65 | Operator-facing identifier (`station_id`) | `print(f"{s['station_id']} - {s.get('operator_id')} - {s['occurred_at']}")` — for an operator-facing example, prefer `station_hostname` (per project rule: operator-facing identifiers use `station_hostname`, not `station_id`). `station_id` is admin/config. |
| ⚠️ WARNING | L78 | Operator-facing identifier (`dut_part_number` is fine, but `product_id` would also work in mixed examples) | `print(f"{r['dut_serial']} ({r.get('dut_part_number')})")` — this one is correct (`dut_part_number` is the operator-facing term). Flagging for consistency only: keep this and apply the same principle to the L65 example. |
| ⚠️ WARNING | L57 | Cold cross-page drop | `from litmus.data.event_store import EventStore` is shown without a link to `reference/connect.md` or any pointer telling the reader where `EventStore` is documented. The class is used as the central Python API of the page. |
| 💡 SUGGESTION | L21 | Programmer jargon ("session is created automatically by the Litmus pytest plugin") | "Litmus pytest plugin" — the project guidance says Litmus is a platform; "the Litmus pytest integration" or "pytest test runs" fits the user's vocabulary better. |
| 💡 SUGGESTION | L83 | Implementation detail leak | "date-partitioned directories under `results/events/`" — operator-facing prose; the directory structure is implementation detail. Either drop or move to a "Where data lives" callout. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L83 | Doc says session data is stored "under `results/events/`" | The data directory contains `events/` directly, not `results/events/`. `resolve_data_dir` returns `<data_dir>` (CWD's `litmus.yaml` `data_dir`, `LITMUS_HOME`, or `platformdirs.user_data_dir("litmus")/data`), and `EventStore` writes to `<data_dir>/events/`. There is no `results/` prefix. | `src/litmus/data/event_store.py:133`, `src/litmus/data/data_dir.py:32-61` |
| ❌ CRITICAL | L86 | "`litmus data prune --older-than 90d` (planned CLI command)" | The `litmus data prune` command is implemented and shipped — `@data.command("prune")` with `--older-than`, `--type`, `--data-dir`, `--dry-run`. Remove "(planned CLI command)". | `src/litmus/cli.py:2358-2396` |
| ⚠️ WARNING | L90 | "Data retention settings can be configured in the global config at `~/.config/litmus/config.yaml` or per-project in `litmus.yaml`." | `ProjectConfig` has NO `retention` field (it has `name`, `data_dir`, `default_station`, `default_fixture`, `default_profile`, `mock_instruments`, `profiles`, `runner`, `required_inputs`, `multi_slot`). `extra="forbid"` rejects unknown fields. No `~/.config/litmus/config.yaml` consumer exists in the codebase (only memory describes it as planned). | `src/litmus/models/project.py:69-88` |
| ⚠️ WARNING | L10 | `from litmus.connect import connect` | Works, but the canonical convention shown in `connect.py`'s own docstring and elsewhere is `import litmus` + `litmus.connect(...)`. The page should at minimum stay consistent with `reference/connect.md` and `concepts/sessions.md`, which both use `from litmus.connect import connect` — fine — but verify this is what new users will copy. Verified import path exists. | `src/litmus/connect.py:471` |
| ⚠️ WARNING | L62 | Comment says "All sessions (returns SessionStarted event dicts)" | True (`store.sessions()` returns `events(event_type="session.started")` which returns event dicts), but the comment on L63 says "SessionStarted carries session/station/operator fields only — DUT lives on RunStarted" — the operator field is `operator_id`/`operator_name`, both `Optional`. The comment is accurate but worth tightening since the next line on L65 prints `s['station_id']` (required) and `s.get('operator_id')` (correctly using `.get` for optional) — readers may not catch why one uses `[]` and the other `.get`. | `src/litmus/data/events.py:60-93` |
| 💡 SUGGESTION | L25 | "(see [reference/event-types](../reference/event-types.md))" | Could anchor directly to `#session-started--sessionstarted` (the heading on L19 of that file generates that slug). | `docs/reference/event-types.md:19` |
| ✅ VERIFIED | — | 14 claims verified against source (import paths, `EventStore` class, `sessions()` method, MCP tool names `litmus_sessions` / `litmus_events`, HTTP routes `GET /api/sessions` and `GET /api/sessions/{session_id}`, `SessionStarted` fields, `RunStarted` `dut_serial`/`dut_part_number` fields, `session.started`/`run.started` event types, `session_id` attribute on `StationConnection`, `connect()` signature, context-manager outcome semantics, `SessionEnded` auto-emission, `store.events(session_id=, event_type=)` signature, `litmus data prune` CLI presence) | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | L5–L17 ("Starting a Session" with `connect()`) | Prerequisites not stated: `connect("cell-7")` requires either a `stations/cell-7.yaml` (project-local) or `~/.local/share/litmus/stations/cell-7.yaml`. A reader copying the snippet cold gets an obscure error. Add a one-line prerequisite or link to "Configuring stations". |
| ❌ CRITICAL | L82–L92 ("Data Retention") | The page claims retention "Default: unlimited (keep everything)" but `ProjectConfig` has no retention field — so the reader cannot in fact configure retention in `litmus.yaml` today. This gap silently misleads operators about a critical data-management story. State the actual state of the world: "today, retention is operator-driven via `litmus data prune`; automatic retention policies are not yet supported." |
| ⚠️ WARNING | L19–L22 ("With pytest") | No example, no link to writing-tests or how the pytest-managed session lifecycle differs from `connect()`. A reader running both interactive and pytest sessions can't tell what's the same and what's different. |
| ⚠️ WARNING | L34–L52 (MCP + HTTP) | No "how do I know it worked" step — no example output, no expected JSON shape for `/api/sessions`, no hint that `litmus serve` must be running for the HTTP example. The reader curling localhost:8000 cold gets a connection refused with no explanation. |
| ⚠️ WARNING | L56–L71 (Python `EventStore`) | The example does not explain failure modes: what if no sessions exist (empty list)? What if the daemon is mid-restart? What about long-running notebooks that should use `EventStore.get_shared()` instead of `EventStore()` (per the class's own docstring at `event_store.py:118`)? |
| ⚠️ WARNING | L83 ("Session data is stored...") | Where actually is the data? "under `results/events/`" without saying the parent directory. The resolution chain (`data_dir` in `litmus.yaml` → `LITMUS_HOME` → platformdirs) is not stated and is the operator's first question. |
| 💡 SUGGESTION | L23–L31 ("Session Metadata") | The section tells the reader what `SessionStarted` captures but not how to *use* that data (filter sessions by station? by operator? by date?). Add one concrete example like "find all sessions on bench-7 last week". |
| 💡 SUGGESTION | L73–L79 ("To get DUT serials...") | Doesn't mention that a session can have *multiple* `RunStarted` events (multi-DUT, retests). The for-loop is correct but the reader may assume one run per session. State the cardinality. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ⚠️ WARNING | L25 | `[reference/event-types](../reference/event-types.md)` — file exists, but link should target the `#session.started--sessionstarted` anchor to land the reader on the exact field table. Currently lands at the top of a long reference page. |
| ⚠️ WARNING | L57 | First use of `EventStore` (the page's main Python API surface) has no link to its docs. `reference/connect.md` and `concepts/three-stores.md` both reference it; add an inline link. |
| ⚠️ WARNING | L7 / L19 | "With pytest" / "With `connect()`" subheadings — neither links to the relevant entry (`reference/connect.md` for `connect()`; `how-to/writing-tests.md` or `reference/litmus-fixtures.md` for the pytest path). On a how-to, first-use of the entry point should link to its reference. |
| 💡 SUGGESTION | L34 ("MCP Tool") | First use of `litmus_sessions()` and `litmus_events()` — could link to `how-to/mcp-integration.md` or `reference/cli.md#mcp-tools` for setup, since the reader needs `litmus mcp serve` running for these to work. |
| 💡 SUGGESTION | L44 ("HTTP API") | First mention of `localhost:8000` — could link to `reference/cli.md#litmus-serve` so readers know how to start the server. |
| 💡 SUGGESTION | L83 | "date-partitioned directories under `results/events/`" — when accuracy is fixed (no `results/` prefix), consider linking to `concepts/three-stores.md` or `concepts/event-log.md` for storage architecture context. |
| 💡 SUGGESTION | "See Also" (L94–L98) | Add link to [Querying historical events](querying-events.md) is present; consider also adding [Configuring stations](configuring-stations.md) (since `connect("cell-7")` requires station YAML) and [MCP integration](mcp-integration.md) (since the page shows MCP tool calls). |
