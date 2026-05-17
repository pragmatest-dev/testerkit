# Page audit: docs/tutorial/10-live-monitoring.md

**Quadrant:** Tutorial (step 10 of 10 — live monitoring with sessions and events)
**Audited:** 2026-05-17

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 2 | 1 |
| Voice | 0 | 0 | 0 |
| Audience | 0 | 2 | 1 |
| Accuracy | 1 | 2 | 1 |
| Gaps | 1 | 3 | 2 |
| Cross-links | 0 | 3 | 2 |
| **Total** | **3** | **12** | **7** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ❌ CRITICAL | L36–37 | "In another terminal: `litmus serve --reload`" appears under "Monitor in the UI" as an instruction to start the server, but the Prerequisites (L8) already list "`litmus serve` running" as a requirement. A reader who has the server running as required will start a second, conflicting instance. The two-terminal setup must be stated coherently in one place — either as a prerequisite explaining which terminal runs what, or as the first step of this page, not both. |
| ⚠️ WARNING | L95–101 | "What's Happening Under the Hood" introduces `EventStore`, `EventLog`, `ChannelStore`, and the DuckDB Flight daemon all at once before any of them have been explained on the page. A reader following top-to-bottom sees code using these classes in the first code block (L14–26) and the flow diagram (L54–55) long before the under-the-hood section tries to define them. The numbered explanation should either move earlier or each class needs a brief definition at first use. |
| ⚠️ WARNING | L59–75 | "Query Historical Data" section appears between "Run Tests While Monitoring" (live focus) and "Channel Data from Instrument Reads" (live focus). The historical query section breaks the live-monitoring narrative thread and then the page switches back to live data. Group historical queries after the live-monitoring sections or explicitly frame the page as covering both concerns. |
| 💡 SUGGESTION | L95 | "What's Happening Under the Hood" is the most technically dense section. For a Tutorial quadrant, this belongs either at the very end (current position is fine) or linked out to concept pages. Currently it tries to be a mini architecture explanation — consider whether it adds tutorial value or should simply be a sentence pointing to the concept docs. |

---

## Voice

No voice issues found.

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L38 | Cold cross-page drop | "the operator UI shows live session activity" — `session` is Litmus-specific jargon used without definition or link. `docs/concepts/sessions.md` exists and explains what a session is; this is the first use of the term on the page with no link. |
| ⚠️ WARNING | L101 | Programmer jargon / cold drop | "LTTB (Largest Triangle Three Buckets) decimation — a downsampling algorithm that preserves visual peaks" — the parenthetical explanation is fine, but "decimation" is a DSP term. The audience is test engineers, many of whom will recognise LTTB if they deal with waveforms, but the phrase "preserves visual peaks" is imprecise (LTTB preserves visual shape including troughs). The how-to page `querying-channels.md` has a correct explanation; this inline parenthetical could mislead. |
| 💡 SUGGESTION | L7 | Wrong vocabulary for tutorial context | "Completed Step 7: Real Instruments or using mock mode" — as the final tutorial step (step 10), this prerequisite skips steps 8 and 9. If a reader has done step 7 but not 9, they may lack station YAML or fixture context. Stating only step 7 as prerequisite implies steps 8 and 9 are optional for this page, which may or may not be true but is not explained. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L97 | doc says "`connect()` creates an `EventStore` and `EventLog` for the session" | `connect()` returns a `StationConnection`. It is `StationConnection.start()` (called by `__enter__`) that creates `EventStore` and `EventLog`. `connect()` alone creates neither. | `src/litmus/connect.py:471–498` (connect), `src/litmus/connect.py:71–114` (start) |
| ⚠️ WARNING | L85 | doc says event contains `{"value": {"_ref": "channel://scope.ch1/...", "length": 1000}}` | Actual serialized shape is `{"_ref": <uri>, "channel_id": <str>, "type": "array"/"struct", "length": <int>, "min": <float>, "max": <float>}` — the doc omits the `channel_id` and `type` fields that are always present, and implies `length: 1000` is fixed. | `src/litmus/data/events.py:562–582` |
| ⚠️ WARNING | L38–41 | UI bullet point "Session metadata (station, DUT, operator)" implies DUT is shown in session-level metadata | `SessionStarted` contains station and operator fields but NOT DUT fields. DUT fields (`dut_serial`, `dut_part_number`, etc.) live on `RunStarted`. Using `connect()` without running pytest (as this tutorial section does) produces no `RunStarted`, so there is no DUT visible in the UI. | `src/litmus/data/events.py:60–92` (SessionStarted), `src/litmus/data/events.py:154–246` (RunStarted) |
| 💡 SUGGESTION | L54–55 | Flow diagram says `pytest → EventLog.emit() → EventStore → UI subscription` | Accurate in spirit but imprecise: the arrow from `EventLog.emit()` to `EventStore` is mediated by the `on_emit` callback registered at `get_event_log()` time, and the arrow from `EventStore` to `UI subscription` goes through `_notify_subscribers`. Acceptable for a tutorial overview but the indirection may confuse readers who read the source. | `src/litmus/data/event_store.py:205–227` |
| ✅ VERIFIED | — | 9 claims verified against source | — | — |

Verified claims: `from litmus.connect import connect` import path; `connect("bench_1", mock=True)` signature (station: str, mock: bool); `station.session_id` property returns `UUID`; `EventStore.on_event()` method exists; `EventLog.emit()` method exists; `litmus_events(session_id=...)` MCP tool name and parameter; `litmus_sessions()` MCP tool name; `litmus_channels(channel_id=...)` MCP tool name and required positional `channel_id`; `/api/channels/{channel_id}?max_points=...` HTTP endpoint; LTTB implemented in `ChannelStore`.

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | L14–26 | The code block calls `station.instrument("dmm")` and `dmm.measure_voltage()`, but the tutorial does not state that `bench_1` must exist as a station YAML, or that the station must define a `dmm` instrument role. In mock mode this still requires a station config to be loadable (see `connect.py:find_station_config`). A reader running this cold gets a `KeyError` or `FileNotFoundError` with no guidance. |
| ⚠️ WARNING | L5–8 | Prerequisites section lists "`litmus serve` running" but does not say whether this means running in a separate terminal before the Python snippet, or whether the reader starts it as part of this step. The page later says "In another terminal: `litmus serve --reload`" (L36), creating ambiguity about whether there are now two server instances. |
| ⚠️ WARNING | L64–74 | The MCP tool examples (`litmus_sessions()`, `litmus_events(...)`, `litmus_channels(...)`) appear as bare function calls with no explanation of how to invoke them — no mention that they require `litmus mcp serve` to be running and that a connected MCP client is needed. A test engineer who has never used MCP before will not know how to call these. |
| ⚠️ WARNING | L77–87 | "Channel Data from Instrument Reads" section explains the channel://URI mechanism and shows how scalar vs array values differ, but never tells the reader how they would know whether their data was stored as scalar (directly in event) or array (in ChannelStore). There is no guidance on which instrument methods trigger the array path vs the scalar path. |
| 💡 SUGGESTION | L95–101 | "What's Happening Under the Hood" says the EventStore acquires a "DuckDB Flight daemon" but never tells the reader what to do if daemon startup fails (e.g., port conflict, stale lock). As step 10 of a tutorial, this section is probably fine as-is, but a link to troubleshooting would help. |
| 💡 SUGGESTION | L104–108 | "Next Steps" section links to the event log architecture, three stores, and two how-to pages, but does not link to `docs/how-to/managing-sessions.md`, which is the most directly actionable follow-on for a reader who just completed this step and wants to manage sessions in their own project. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ⚠️ WARNING | L38 | First use of "session" (as a Litmus concept) — no link to `docs/concepts/sessions.md`, which defines the session model, `session_id` scope, and the difference between session and run. File confirmed to exist at `/home/ryanf/repos/litmus/docs/concepts/sessions.md`. |
| ⚠️ WARNING | L104–108 | "Next Steps" section omits `docs/how-to/managing-sessions.md` — the most directly relevant how-to for a reader completing this tutorial step who wants to manage sessions. File confirmed to exist. |
| ⚠️ WARNING | L104–108 | "Next Steps" section omits `docs/reference/connect.md` — the full API reference for `connect()` and `StationConnection`, which the tutorial relies on throughout. File confirmed to exist at `/home/ryanf/repos/litmus/docs/reference/connect.md`. |
| 💡 SUGGESTION | L15 | `from litmus.connect import connect` — `connect` is used without a link to `docs/reference/connect.md`. For a tutorial reader who wants to explore the full API surface (explicit `start()`/`stop()`, `on_event()`, `observe()`), a link here would bridge the gap. |
| 💡 SUGGESTION | L28 | `(see also [three-stores](../concepts/three-stores.md))` — link is redundant with the identical link on L79 and in the Next Steps (L106). Consider removing the L28 inline parenthetical and relying on the Next Steps entry instead to reduce noise. |
