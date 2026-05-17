# Page audit: docs/concepts/sessions.md

**Quadrant:** Concepts/Explanation (sessions — what they are, what they capture, SessionStarted vs RunStarted)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 0 | 2 |
| Audience | 0 | 1 | 2 |
| Accuracy | 0 | 1 | 2 |
| Gaps | 1 | 4 | 2 |
| Cross-links | 0 | 2 | 4 |
| **Total** | **1** | **9** | **12** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| WARNING | L5–L9 ("What is a Session?") | Concept page leads with a definition ("A session begins when a process calls `connect()`…") instead of the motivating problem. Diátaxis Concepts guidance: start with the *why* / the problem, then introduce the model. The "Why Sessions Exist" section (L37–L45) is the natural lead — it should precede "What is a Session?" or the two should be merged so motivation appears first. |
| SUGGESTION | L11–L35 ("Session Metadata") | The section title is "Session Metadata" but two-thirds of the table content is `RunStarted` fields. Either rename to "Session vs Run Metadata" or split into two sub-sections with their own headings so a reader scanning the TOC can find run metadata directly. |
| SUGGESTION | L47 ("The `connect()` API") | Code example appears before the bullet list explaining what `connect()` does (L66–L71). Reversing — short prose intro, then code — would help a reader skim. Alternatively, lead the bullet list with "When you call `connect()`, it…" rather than reciting the snippet's behavior after the fact. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| SUGGESTION | L3 | Hedging | "actively using" — drop "actively"; "using instruments" suffices. |
| SUGGESTION | L41 | Hedging | "during one sitting" — vague colloquialism on an otherwise crisp page; replace with "in one session" or drop. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L18 | Wrong vocabulary for operator-facing identifiers | Table lists `station_id`, `station_name` as Station fields. Per project convention (`feedback_operator_facing_identifiers.md`), operator-facing identifiers should be `station_hostname` / `dut_part_number`, not `*_id` / `*_name`. The table does include `station_hostname` last, but leading with `station_id` reinforces the wrong vocabulary. Note: these match the event schema field names verbatim (verified in `events.py`), so the fix is either to mark `station_id` / `station_name` as internal/admin-only, or to reorder so `station_hostname` leads. |
| SUGGESTION | L29 | Wrong vocabulary | `Product` row lists `product_id`, `product_name` — same operator-vocabulary issue. `dut_part_number` (on the DUT row) is the operator-facing identifier; product_id should be flagged as internal or reordered. |
| SUGGESTION | L45 | Programmer-ish phrasing | "Resource coordination — Sessions track which instruments are in use, enabling per-resource locking." Test engineers will read this; "per-resource locking" is borderline jargon. Concrete phrasing: "Two scripts can use different instruments on the same station at the same time — Litmus locks the resource (the VISA address), not the whole station." |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| WARNING | L49 | `from litmus.connect import connect` | Correct import path (the symbol lives in `litmus.connect`), but `litmus/__init__.py` does NOT re-export `connect`, so `import litmus; litmus.connect(...)` (as shown in `connect.py`'s own docstring) would fail. The doc's import is fine; flagging because the inconsistency between this page and `connect.py`'s docstring may confuse a reader who tries the shorter form. | `src/litmus/__init__.py:14`; `src/litmus/connect.py:1-22` |
| SUGGESTION | L66–L71 | "`connect()` creates a `StationConnection` that: Generates a new `session_id`; Creates an `EventLog` for this session; Emits `SessionStarted` with full context; Manages per-resource instrument locking; Emits `SessionEnded` on close" | Strictly speaking, `connect()` itself only constructs a `StationConnection` (`StationConnection(config, ...)`). The session_id is generated in `StationConnection.__init__`, and `EventLog` + `SessionStarted` are emitted by `start()` (called by context-manager `__enter__` or explicit `station.start()`). `SessionEnded` is emitted by `stop()`. The doc's "creates… that:" is acceptable shorthand but conflates `connect()` with `start()` — worth stating that `start()` does the EventLog + SessionStarted work. | `src/litmus/connect.py:471-498` (connect), `:71-114` (start), `:179-213` (stop) |
| SUGGESTION | L67–L70 | "Generates a new `session_id`" | Verified: `self._session_id = uuid4()` in `StationConnection.__init__`. Could clarify it's a `uuid4` to match other places in docs. | `src/litmus/connect.py:62` |
| VERIFIED | — | 14 claims verified against source: SessionStarted fields (session_type, station_id/name/type/location/hostname, pid, client, operator_id/name, fixture_id, slot_count), RunStarted fields (dut_serial/part_number/revision/lot_number, product_id/name/revision, slot_id/slot_index, fixture_id, test_phase, project_name, git_commit/branch/remote, environment_json, custom_metadata, channel_refs), SessionStarted/RunStarted event class existence, SessionEnded existence, `connect()` signature (`station: str | None`, `mock: bool = False`), `StationConnection.start()` / `.stop()` methods, per-resource locking in `InstrumentPool.acquire` (`acquire_resource` on `record.resource`), event class names emitted by `start()`/`stop()`. | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | "What is a Session?" (L5–L9) | The page never states what *ends* a session for the script form. It says "ends when the connection is released" but doesn't define "released." A reader needs to know: context-manager exit, explicit `station.stop()`, or process death (SIGTERM/atexit) all end the session — and the outcome differs (`passed` / `terminated` / `errored` / `aborted`). Without this, a reader writing a script can't reason about what happens on Ctrl-C. (Code at `connect.py:411-449` shows the four outcomes; none appear in the doc.) |
| WARNING | "Why Sessions Exist" (L37–L45) | "Sessions track which instruments are in use, enabling per-resource locking. Two scripts can use different instruments on the same station simultaneously." Does NOT answer: what happens to script B if it tries to acquire the same instrument script A holds? (Answer per code: `ResourceInUse` is raised, with `timeout=0` default.) A reader evaluating sessions for concurrency needs this. |
| WARNING | "Session Metadata" (L11) | The page promises "captures session-wide context — the *who/where/how* of the process holding the connection" but never explains *how* the values get populated. Are `operator_id` / `operator_name` read from env vars? From `litmus.yaml`? Passed to `connect()`? (Looking at `connect.py:497`, `connect()` doesn't accept operator args at all — they're populated for pytest via `logger.test_run`, not for `connect()`.) This gap matters for anyone wanting traceability from interactive scripts. |
| WARNING | "Session Metadata" (L23) | `RunStarted` is introduced as "emitted once per test run within a session" but the page never explains *who* emits it for non-pytest sessions. The pytest plugin emits it; a `connect()`-based script does NOT (verified — `connect.py` only emits `SessionStarted` + `SessionEnded`, never `RunStarted`). A reader will assume `RunStarted` accompanies every session and be wrong. |
| WARNING | "Session Metadata" (L29) | `slot_id` / `slot_index` appear in the RunStarted table without any mention of multi-DUT mode. A reader who isn't running multi-DUT will wonder what to put there. One-line context ("multi-DUT only; single-DUT runs leave these null") would close the gap. |
| SUGGESTION | "Session Metadata" (L35) | "Config files (station, fixture, product spec) are tracked via git — the `git_commit` field on each `RunStarted` identifies the exact code and config state." Implies the project IS a git repo. What populates `git_commit` if it isn't? (Stays `None`, per Pydantic default.) Worth one sentence. |
| SUGGESTION | "The `connect()` API" (L47–L71) | No mention of what happens when no station is given AND no `default_station` is set in `litmus.yaml`. Code raises `ValueError` (`connect.py:492`). Concept page can defer this to the reference, but linking it would help. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| WARNING | L13 | "`SessionStarted` (see [event-log](event-log.md) for the event-type taxonomy)" — link target `event-log.md` exists, but link should point at a specific anchor. `event-log.md` has `## Event Categories` (verified) and `### Session (2 events)` — link to `event-log.md#event-categories` or `event-log.md#session-2-events` would land the reader on the relevant section. |
| WARNING | "See Also" (L73–L78) | Missing link to **Outcomes** concept (`concepts/outcomes.md` exists) — the page mentions `SessionEnded` carrying outcome (implicit in L71) and the gap above shows session outcomes (`passed` / `terminated` / `errored` / `aborted`) are central to understanding session lifecycle. Outcomes page is the canonical reference. |
| SUGGESTION | L7 | First mention of `connect()` — could link to `reference/connect.md` here, not just at the bottom. The reference exists and is the canonical definition. |
| SUGGESTION | L7 | First mention of `session_id` — no link or definition. Could anchor to `reference/event-types.md` or `reference/parquet-schema.md` where the field is canonically described. |
| SUGGESTION | L31 | "`channel_refs` list" — first mention of channels in this page. Could link to `concepts/flight-streaming.md` or `how-to/querying-channels.md` for readers unfamiliar with the channel store. |
| SUGGESTION | L13 / L23 | `SessionStarted` and `RunStarted` are the page's central characters and could link to `reference/event-types.md` (which catalogs all event classes) for the full field list rather than only the curated subset shown in the tables. |
