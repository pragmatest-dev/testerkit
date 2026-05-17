# Page audit: docs/reference/event-types.md

**Quadrant:** Reference (every typed event payload — SessionStarted, RunStarted, StepStarted, MeasurementRecorded, etc.)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 1 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 2 | 2 |
| Accuracy | 9 | 5 | 2 |
| Gaps | 5 | 3 | 2 |
| Cross-links | 2 | 3 | 2 |
| **Total** | **16** | **16** | **11** |

---

## Ordering findings

**WARNING — Category order doesn't match the runtime timeline or the concept page.**
The page orders sections: Session, Run, Fixture, Test, Instrument, Diagnostic, Stream, Dialog. The concept page (`docs/concepts/event-log.md`) and the source file both group Slot, Sync, and Route events between Run/Fixture and Test/Instrument respectively. The reference omits them entirely (see Gaps), but even within what is documented, "Diagnostic" sits between Instrument and Stream in the reference while the source defines Diagnostic immediately after Test. For a reference page, the obvious choices are (a) source-of-truth file order or (b) timeline order; this page matches neither.

**WARNING — Within Fixture events, ordering doesn't match the source.**
Source order: `InstrumentConnected`, `IdentityVerified`, `CalibrationWarning`, `DutScanned`, `InstrumentDisconnected`. Doc order: same — OK. But the doc puts `IdentityVerified` and `CalibrationWarning` adjacent to `InstrumentConnected` (good), then `DutScanned` before `InstrumentDisconnected`. Runtime order would scan the DUT first, then connect instruments. If timeline ordering is the intent, `DutScanned` belongs above `InstrumentConnected`. Pick one principle and apply consistently.

**SUGGESTION — Base Fields section should call out the discriminator explicitly.**
Right after the base-fields table, add a one-liner: "Each subclass adds a `Literal['…']` `event_type` field used as the discriminator (see [Discriminated Union](#discriminated-union))." Currently the reader meets `event_type` only at the end of the page, after seeing 25+ tables that all silently include it.

---

## Voice findings

**WARNING — Inconsistent voice between sections.**
Most event subsections are bare tables with no prose. A handful (`session.started`, `run.started`, `fixture.instrument_connected`, `test.steps_discovered`, `test.measurement`, `instrument.read`) get a short narrative paragraph. This is fine — but the pattern should be predictable. Either (a) every event gets a one-sentence "Emitted when…" lead, or (b) only events with non-obvious semantics get prose. Currently it looks arbitrary.

**SUGGESTION — Replace "Emitted once at session start" with present-indicative-imperative form.**
The reference voice should describe *what the event is*, not narrate when the runtime emits it. Compare: "Session-wide metadata; emitted once per session." vs. "Emitted once at session start." The first reads as schema documentation; the second reads like a how-to. The how-to/when belongs in the event-log concept page.

**SUGGESTION — "Each item dict contains: …" is the only inline schema description.**
For `StepsDiscovered.items` the doc inlines the dict shape as a comma-separated list of keys. Every other dict field (`custom_metadata`, `expected`, `actual`, `inputs`, `outputs`, `custom`, `details`, `parameters`) is left as bare `dict`. Either describe all of them or move the items-dict description into a footnote. The current asymmetry implies the others are unstructured, which they are — but make it explicit.

---

## Audience findings

**WARNING — Page presumes the reader already knows what "session" vs "run" means.**
A reference page can presume the reader knows the vocabulary, but this page is the entry point for anyone deserializing events from disk, building an MCP tool, or writing an external consumer. A single sentence ("Sessions wrap one or more runs; see [Sessions](../concepts/sessions.md)") near the top would orient newcomers without bloating the page.

**WARNING — "claim-check URI" introduced without definition.**
The `instrument.read` section says "Array data is serialized as a `channel://` URI claim-check." A reader landing here from a search result has no anchor for "claim-check" or `channel://`. Link to wherever this is defined (likely `docs/concepts/three-stores.md` or a channel-store reference), or expand inline: "…serialized as a `channel://channel_id?session=…` URI; the raw array is written to the ChannelStore and the event payload carries only the reference."

**SUGGESTION — "discriminated union" is jargon for non-Pydantic readers.**
The closing section assumes Pydantic familiarity. For an LLM-friendly / cross-language reference, add a one-liner: "i.e., the `event_type` string tag selects which subclass Pydantic uses to validate the payload."

**SUGGESTION — `Any` type appearances need an explicit "see JSON serialization" note.**
`value: Any` appears in `RecordEvent`, `InstrumentRead`, `InstrumentSet`. For consumers reading JSON, `Any` means "could be anything that survives `json.dumps`." A footnote pointing at how `Any` is serialized (especially for arrays in `InstrumentRead`, which has a custom `model_serializer`) would prevent bug reports.

---

## Accuracy findings

Source of truth: `/home/ryanf/repos/litmus/src/litmus/data/events.py`.

**CRITICAL — `SessionStarted.station_id` is documented as `*required*` but the source has `station_id: str | None = None`.**
Source line 71. The doc says `station_id` is required; the source makes it optional (and a comment explicitly notes "id is None for bringup tier (no station YAML loaded)"). This is a contract-level falsehood — a consumer that asserts `station_id is not None` based on this doc will crash on bringup-tier events.

**CRITICAL — `RunStarted.station_id` is documented as `*required*` but the source has `station_id: str | None = None`.**
Source line 164. Same defect as above for run-level events.

**CRITICAL — `SessionEnded.outcome` is documented as `str` defaulting to `"passed"` but the source has `outcome: str | None = None`.**
Source line 140. Both the type and the default are wrong.

**CRITICAL — `RunEnded.outcome` is documented as `str` defaulting to `"passed"` but the source has `outcome: str | None = None`.**
Source line 213. Same defect.

**CRITICAL — `RunStarted.test_phase` is documented as `str` defaulting to `"production"` but the source has `test_phase: str | None = None`.**
Source line 193. Both the type and the default are wrong. The source removed the `"production"` default at some point and the reference wasn't updated.

**CRITICAL — `RunStarted` is missing `slot_index: int | None = None` (source line 170).**
Documented `slot_id` is correct, but `slot_index` is undocumented.

**CRITICAL — `RunStarted` is missing `project_name`, `git_branch`, `git_remote` (source lines 194, 196, 197).**
Three undocumented `str | None` fields that consumers ingesting parquet directly already see.

**CRITICAL — `StepEnded.outcome` description says it's `str | None` "default `None`" but the source default is implicit-None — the table column header says "Default" so the value should match the assignment form. Same for `vector_outcome` which has the long parenthetical instead of a default value.**
Both are technically `None`, but the inconsistency is jarring next to `inputs: dict | default {}`. Use `None` for both, move the prose into a paragraph above the table.

**CRITICAL — `InstrumentDisconnected` table omits the inherited base fields disclaimer used elsewhere.**
Not a wrong field, but every table in this page presents non-base fields. `InstrumentDisconnected` has only `role` and `instrument_id`, which is correct per source (lines 337-339), but worth confirming this minimal payload is intentional — readers will wonder why there's no `resource` to correlate with the matching `InstrumentConnected`.

**WARNING — `SessionStarted.client` and `RunStarted.client` defaults are documented as "auto-detected" but the actual default is the return value of `_detect_client()`, which inspects `sys.argv[0]` and returns one of `"pytest"`, `"jupyter"`, the basename of argv[0], or `"unknown"`.**
"auto-detected" is hand-wavy. Either enumerate the values or say "derived from `sys.argv[0]`; common values: `pytest`, `jupyter`, `unknown`."

**WARNING — `SessionStarted` is documented with no `session_type` enum values.**
The default is `"test_run"`, but readers don't know what other values are legal. Either constrain the field to a `Literal` in the source, or document the enumeration here.

**WARNING — `DialogOpened.dialog_type` and `DialogResponded.response_type` enums undocumented.**
The source code comments at lines 644 and 657 explicitly list valid values: `"confirm" | "choice" | "input" | "image"` and `"answered" | "cancelled" | "timed_out"`. These are critical for any consumer writing a UI/MCP integration, and they're already known — just copy them into the reference.

**WARNING — `SlotCompleted.outcome` enum undocumented.**
Source comment line 266: `"passed", "failed", "errored", etc — see Outcome`. The reference doesn't mention `SlotCompleted` at all (see Gaps), but when it's added, document the enum.

**WARNING — The "Source:" pointer at the top says `litmus/data/events.py` but the package layout has been `src/litmus/data/events.py` since the project switched to `src/` layout.**
The reader doing `find . -path '*/litmus/data/events.py'` will still find it, but be precise: `src/litmus/data/events.py`.

**SUGGESTION — `MeasurementRecorded.retry` shows the description inside the Default column.**
"`0` (0-based: 0 = first execution, N = Nth retry)". The 0-based semantics belong in a description column or in prose above the table, not stuffed into "Default".

**SUGGESTION — `received_at` is documented as "When `EventLog.emit()` processed it" but the source has `received_at: datetime | None = None` (it's `None` until `emit()` stamps it).**
Add the `| None` to the type column so consumers don't assume it's always populated on the wire.

---

## Gaps findings

**CRITICAL — Slot events (`slot.started`, `slot.completed`) entirely missing.**
Source defines `SlotStarted` and `SlotCompleted` (lines 253-267) and includes them in the `Event` discriminated union (lines 702-703). The concept page `event-log.md` documents them. The reference does not. Any multi-DUT consumer will hit `KeyError` on `event_type` lookup against this reference.

**CRITICAL — Sync events (`sync.arrived`, `sync.release`) entirely missing.**
Source defines `SyncArrived` and `SyncRelease` (lines 270-282) and includes them in the union. Same problem as slot events for multi-DUT consumers.

**CRITICAL — Route events (`route.closed`, `route.opened`) entirely missing.**
Source defines `RouteClosed` and `RouteOpened` (lines 502-517) and includes them in the union. Any consumer building a switch-routing timeline will not find them in this reference.

**CRITICAL — `RunMaterialized` event entirely missing.**
Source defines it (lines 216-245) with extensive docstring describing the lifecycle handshake. The concept page documents it (with a note that it's not yet in the discriminated union). The reference omits it — but external consumers querying the EventStore *will* see these events, since they're emitted by the runs daemon. At minimum, document it with the caveat the concept page already uses.

**CRITICAL — `event_type` field itself is undocumented in Base Fields.**
The base-fields table lists `id`, `occurred_at`, `received_at`, `session_id`, `run_id` — but every concrete event also has `event_type: Literal["…"]`, which is the discriminator. The Discriminated Union section at the bottom mentions it, but it should appear in the base-fields table (with type `str` and a footnote pointing to the per-event constant string).

**WARNING — No section explains the relationship between event types and the parquet rows they materialize into.**
A reader landing here from `parquet-schema.md` (which cross-links to this page) needs to know which events feed which parquet tables. One small "See `parquet-schema.md` for how these payloads materialize into rows" callout would close the loop.

**WARNING — No JSON example anywhere.**
Reference pages benefit from one concrete payload example. A single fenced JSON block showing a `MeasurementRecorded` round-trip ("here's what hits the JSON column") would orient anyone writing a consumer.

**WARNING — No mention of category-grouping constants (`SESSION_EVENTS`, `TEST_EVENTS`, `ALL_EVENTS`, etc.).**
Source lines 667-694 export these sets, which are useful for consumers who want to filter by category. The reference doesn't expose them, but they're public.

**SUGGESTION — Class names vs type strings.**
Every section header uses the pattern `event-type-string — ClassName`. That's good. But there's no top-level mapping table. A small "Index" table near the top listing all 28+ events with class name, type string, and category would let readers Ctrl-F the page effectively. (The concept page has the per-category tables already; this reference page would benefit from one consolidated table.)

**SUGGESTION — `EventBase` docstring mentions `session_id` is `Field(default_factory=uuid4)` — i.e., events get a fresh session UUID by default if the caller doesn't supply one.**
That's almost never what consumers want; document it as a footgun.

---

## Cross-links findings

**CRITICAL — Page has zero outbound links.**
A reference page this dense should at minimum link to:
- `docs/concepts/event-log.md` (the conceptual companion — already cross-links *back* to this page)
- `docs/concepts/sessions.md` (defines what a session is)
- `docs/reference/parquet-schema.md` (the materialization target — cross-links back to this page)
- `docs/how-to/querying-events.md` (consumer entry point)
- `docs/concepts/three-stores.md` (for the `channel://` claim-check reference in `instrument.read`)

**CRITICAL — The "Source:" line links to a file path but isn't a clickable link.**
Either make it a relative link to the source file (mkdocs-style) or drop it entirely and use a "See Also" section with proper links. Bare-path "Source:" pointers rot silently.

**WARNING — No "See Also" section.**
The concept page (`event-log.md`) ends with one; this page should too. Mirror the same set plus the concept page itself.

**WARNING — Cross-page anchor consistency.**
Every section header is `### \`event.type\` — ClassName`. mkdocs-material will slugify these into hard-to-predict anchors (e.g., `#sessionstarted-sessionstarted`). Other pages that want to deep-link will break easily. Consider explicit anchors (`{#session-started}`) on each event subsection.

**WARNING — The concept page lists `RunMaterialized` and is the only doc that does.**
If the reference adds `RunMaterialized` (see Gaps), cross-link to the concept page's explanation of the materializer-pool eviction handshake — readers won't infer it from the field list.

**SUGGESTION — Add an outbound link from `EventBase`'s `session_id` row to `docs/concepts/sessions.md`.**
Inline-link the field name in the description column. This is the most-likely-to-be-followed link on the page.

**SUGGESTION — Add an outbound link from `event_type` (once added to base fields) to the Discriminated Union section at the bottom of the same page.**
Same-page anchor — keeps readers oriented when skimming.
