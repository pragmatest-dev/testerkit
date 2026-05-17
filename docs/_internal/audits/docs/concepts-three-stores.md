# Page audit: docs/concepts/three-stores.md

**Quadrant:** Concepts / Explanation
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 2 | 2 |
| Audience | 0 | 2 | 3 |
| Accuracy | 4 | 4 | 2 |
| Gaps | 1 | 3 | 2 |
| Cross-links | 0 | 3 | 2 |
| **Total** | **5** | **15** | **13** |

---

## Ordering

**WARNING — "Live streaming + crash safety" is wedged under ParquetBackend, but it cuts across all three stores.**
- Location: `docs/concepts/three-stores.md:43-45` (sub-section under `## ParquetBackend — Materialized View`).
- Issue: The section talks about "the materializer holds row state in-process," "the operator UI subscribes to the in-process event stream," and "close-time fallback." Crash safety is a system-level property (EventStore IPC segments + materializer fallback) — readers looking for the crash story will not find it in the per-store sections.
- Suggested move: promote to a sibling section between "How They Relate" and "Storage Layout," titled "Crash safety" or "Durability model," and rewrite to span EventStore (segment rotation), ChannelStore (segment rotation), and the materializer (close-time fallback → `aborted`). Alternatively, leave a short hook under ParquetBackend and put the cross-store discussion under "How They Relate."

**SUGGESTION — Put "How They Relate" before the per-store deep dives, not after.**
- Location: `docs/concepts/three-stores.md:47-60`.
- Issue: Diátaxis Explanation pages reward the "wide before deep" pattern — give the reader the relationship picture, *then* drill into each store. Right now a first-time reader hits three independent sections that all describe write/read/storage in similar shapes, then finally gets the diagram showing how they connect.
- Suggested order: Overview table → "How They Relate" (with the box-diagram) → EventStore → ChannelStore → ParquetBackend → Storage Layout → Crash safety → See Also.

**SUGGESTION — The Overview table and the box-diagram in "How They Relate" carry the same information twice; consider letting the diagram do the relational work.**
- Location: `docs/concepts/three-stores.md:7-11` vs `49-54`.
- Issue: The table is a static catalogue; the diagram is a flow. Together they're fine, but the table currently leads with "Query Layer" (an implementation detail) before the reader knows why three stores exist. Consider replacing the "Query Layer" column with "Role" (Source of truth / Time-series claim-check / Materialized view) so the table previews the conceptual story the diagram then makes concrete.

---

## Voice

**WARNING — The page mixes Explanation voice with Reference voice; pick one.**
- Locations: throughout `docs/concepts/three-stores.md`.
- Issue: A concept page should explain *why* three stores exist and *how* they relate. This page repeatedly drops into reference register — "Write path: `EventLog.emit()` → buffered Arrow IPC → Flight `do_put`...", "Read path: SQL via Flight `do_get`, or direct IPC file reads", "Storage: `results/events/{date}/{session_id}.arrow`". Those are reference contracts that belong on a `reference/data-stores.md` page (or the existing `reference/outputs.md`). Concept pages should focus on roles and trade-offs, with one or two illustrative path strings if any.
- Suggested fix: keep one prose paragraph per store explaining its job, and move the bulleted "Write path / Read path / Storage" triplets to a reference page (or render them as collapsed `<details>` blocks).

**WARNING — Bold-label bullets (`**Write path:** ...`) are a docs-as-spec pattern, not an Explanation pattern.**
- Locations: `docs/concepts/three-stores.md:17-19`, `27-29`, `37-39`.
- Issue: Three identical bullet lists in three sections read as a spec page. Concept voice prefers prose: "Events arrive at the EventStore via `EventLog.emit()`, which buffers them into Arrow IPC files on disk and ships record batches to the events DuckDB daemon over Flight `do_put`." The latter explains the *flow*; the former just lists ingredients.

**SUGGESTION — Mid-section the parenthetical at line 17 is doing too much.**
- Location: `docs/concepts/three-stores.md:17`.
- Issue: One bullet contains an inline cross-link explanation: "(see [flight-streaming](flight-streaming.md) for `do_put`/`do_get` / DuckDB daemon details; Arrow IPC is Apache Arrow's on-disk record-batch format)". This is doing three jobs at once (link, glossary, format definition) inside a bullet that's already dense. Split: hoist the Arrow IPC gloss into a one-line definition the first time IPC is mentioned, and demote the flight cross-link to See Also.

**SUGGESTION — "Source of truth" / "Time-Series Data" / "Materialized View" section subtitles are uneven.**
- Location: `docs/concepts/three-stores.md:13, 23, 33`.
- Issue: First and third are *roles*; second is a *content type*. Either align all three to roles ("Source of truth," "Claim-check store," "Materialized view") or all three to content ("Typed events," "Time-series data," "Per-run results"). Mixed forms make scanning the page harder.

---

## Audience

**WARNING — Page assumes the reader already knows what Arrow IPC, DuckDB, Flight, LTTB, and "materialized view" mean.**
- Location: throughout `docs/concepts/three-stores.md`.
- Issue: All five terms appear in the first 30 lines with no gloss. The audience for this page is test engineers and architects evaluating Litmus — many will be familiar with Parquet but not with Arrow IPC vs Parquet, not with Flight (most will think Spark), and almost none will know LTTB. The page treats them as known.
- Suggested fix: add a one-line gloss on first use for each term, or a short "Terminology" callout at the top:
  - Arrow IPC — Apache Arrow's on-disk record-batch format (binary, schema-stamped, append-only).
  - Flight — Arrow's gRPC-based query/streaming protocol.
  - DuckDB — embedded analytical SQL engine; Litmus runs it in a daemon and queries via Flight.
  - LTTB — Largest-Triangle-Three-Buckets, a visualization-friendly time-series downsampler.
  - Materialized view — a pre-computed denormalized table derived from a source stream.

**WARNING — "claim-check URI" is jargon dropped without context.**
- Location: `docs/concepts/three-stores.md:25`.
- Issue: "claim-check pattern" is an enterprise integration term. The reader has to either know it or guess from context. A one-line gloss in parentheses ("the event stores a `channel://...` pointer; the actual array bytes live in the ChannelStore") would carry its weight.

**SUGGESTION — The intended reader is unclear.**
- Location: page-level.
- Issue: The page swings between "what these stores do for you" (audience: user) and "how the daemon dispatches RunEnded into the AccumulatorPool" (audience: internals contributor). Pick one. If it's user-facing, the AccumulatorPool / materializer mechanics in §"Live streaming + crash safety" are too internal. If it's contributor-facing, the page belongs under `docs/_internal/`.

**SUGGESTION — Storage layout tree is over-specified for a Concept page.**
- Location: `docs/concepts/three-stores.md:63-81`.
- Issue: An ASCII tree showing every subdirectory with example filenames is reference material. Concept readers benefit more from "three sibling directories under `results/`, each owning one store" with a link to the reference page. Right now the tree contains two factual errors (see Accuracy) which is exactly the failure mode you get when you over-specify in a concept page.

**SUGGESTION — Skip the "Subscribers denormalize at write time" sentence at line 21 for a user-facing audience.**
- Location: `docs/concepts/three-stores.md:21`.
- Issue: This is a contributor's framing of an internal invariant. Users mostly care that "your queries don't need joins"; the denormalization mechanism is incidental.

---

## Accuracy

**CRITICAL — `ParquetSubscriber` is referenced four times but does not exist as a class in the code.**
- Locations: `docs/concepts/three-stores.md:37` ("`ParquetSubscriber` listens to events, builds rows, writes on `RunEnded`"), `52` (diagram leaf), `57` (numbered list step 2), plus prior architecture.md and event-log.md echoes.
- Evidence: `grep -rn "^class .*Subscriber" /home/ryanf/repos/litmus/src/litmus/data/` returns `StdfSubscriber`, `TdmsSubscriber`, `Mdf4Subscriber`, `JsonSubscriber`, `CsvSubscriber`, `Hdf5Subscriber`, `AtmlSubscriber` — there is no `ParquetSubscriber`. The actual materializer is a *free function* in `src/litmus/data/backends/parquet.py:637` (`materialize_run_to_parquet`) called by the runs daemon's event-dispatch loop against an `EventAccumulator` from the daemon's `AccumulatorPool` (`src/litmus/data/_accumulator_pool.py:71`).
- File comment confirming the architecture changed: `parquet.py:553-562` — "Free-standing materializer — accumulator state → parquet file [...] Called by the runs daemon's event-dispatch loop when `RunEnded` lands [...] No subscriber class needed — projection lives on the accumulator, writing lives here."
- Fix: replace "`ParquetSubscriber`" everywhere with the actual mechanism — "the runs daemon's event-dispatch loop calls `materialize_run_to_parquet` against an `EventAccumulator` when `RunEnded` lands". This also has knock-on effects in §"Live streaming + crash safety" — see next finding.

**CRITICAL — "the materializer holds row state in-process and flushes to a single per-run parquet at run end" is wrong about *which* process.**
- Location: `docs/concepts/three-stores.md:45`.
- Evidence: per the parquet.py comments above and `_accumulator_pool.py:71`, the accumulator that holds row state lives in the **runs daemon** (the long-lived DuckDB daemon process), not in the runner process. The producer pushes events over Flight; the daemon owns the `AccumulatorPool`, dispatches events into it, and on `RunEnded` calls `materialize_run_to_parquet`. The runner does not hold the row state.
- The doc reading reinforces a stale "in-process subscriber" mental model that no longer matches the architecture. This matters because the page is also the explainer for *crash safety* — and crash safety only makes sense once you know the materializer lives in a separate, longer-lived process than the runner.
- Fix: rewrite §"Live streaming + crash safety" around the daemon. Mention `AccumulatorPool`, the daemon-side orphan sweep (`_runs_duckdb_daemon.py:1316, 1596, 1633`), and the synthetic `RunEnded(outcome="aborted")` path. Cross-reference `concepts/why-event-sourcing.md` (which describes the synchronous-tail trade-off — but note that page is also stale and says `ParquetSubscriber` runs in-process).

**CRITICAL — Storage layout tree shows `sessions/sessions.json` as a real directory; no such directory exists.**
- Location: `docs/concepts/three-stores.md:79-80`.
- Evidence: `find /home/ryanf/repos/litmus/data -maxdepth 2 -type d` returns `events/`, `channels/`, `runs/` — no `sessions/`. `grep -rn "sessions/\"\|sessions_dir" src/litmus/` returns no on-disk session index. Sessions are derived at query time from `SessionStarted` events via `EventStore.sessions()` (`event_store.py:348-349`). There is no `sessions.json` and no `sessions/` directory. The sibling page `docs/concepts/results-storage.md:12` repeats the same fictional directory — they have drifted together.
- Fix: delete the `sessions/` block from the tree and rewrite the sentence in `concepts/index.md` / `results-storage.md` accordingly. Add a short paragraph noting that session listings are derived from the event log at query time, with no separate index.

**CRITICAL — Events filename format in the storage tree is wrong.**
- Location: `docs/concepts/three-stores.md:19` ("`results/events/{date}/{session_id}.arrow`") and `67` (tree shows `{session_id}.arrow`).
- Evidence: `event_log.py:178` writes `date_dir / f"{session_id}-{os.getpid()}.arrow"`, and segments rotate to numbered suffixes via `_EventIPCWriter`. Inspecting `/home/ryanf/repos/litmus/data/events/2026-05-17/` confirms filenames like `0042d1db-91f0-4f80-b8d9-36d05c2fbc25-1573971.arrow` (UUID + PID). The page's own docstring header at `event_log.py:9` says `{session_id}-{pid}[_{segment}].arrow`.
- Fix: change to `{session_id}-{pid}.arrow` (and mention the optional `_NNN` segment suffix for large sessions), in both the Storage bullet at line 19 and the tree at line 67.

**WARNING — Run parquet filename in the tree is missing the trailing `Z`.**
- Location: `docs/concepts/three-stores.md:39` ("`{timestamp}_{serial}.parquet`") and `77` (tree shows `20260310T143022_SN001.parquet`).
- Evidence: `parquet.py:205` uses `test_run.started_at.strftime("%Y%m%dT%H%M%SZ")` — note the `Z` suffix marking UTC. Actual files on disk: `20260207T120000Z_SN-001.parquet` in `/home/ryanf/repos/litmus/data/runs/2026-02-07/`.
- Fix: update tree example to `20260310T143022Z_SN001.parquet` and note explicitly that the timestamp is UTC (matches the comment in `parquet.py:196`).

**WARNING — Channel claim-check URI form is wrong.**
- Location: `docs/concepts/three-stores.md:25` ("`channel://scope.ch1/...`").
- Evidence: `ref.py:48` defines `make_channel_uri` as `f"channel://{quote(channel_id, safe='.')}?{urlencode({'session': session_id})}"` — i.e. `channel://scope.ch1?session=abc123` (channel id, then a `?session=` query string). There is no slash-separated path after the channel id. The docstring at `ref.py:6` confirms the correct shape.
- Fix: change the example to `channel://scope.ch1?session=...` and reference `ref.py` rather than inventing a shape.

**WARNING — "The operator UI subscribes to the in-process event stream for live updates — there is no separate JSONL journal on disk" is half right and half misleading.**
- Location: `docs/concepts/three-stores.md:45`.
- Evidence: "no separate JSONL journal on disk" is true (`grep -rn "jsonl" src/litmus/data/` returns nothing). But "the operator UI subscribes to the in-process event stream" is wrong: the operator UI uses the public Query API (`RunsQuery`, `StepsQuery`) and `EventStore` Flight queries (`src/litmus/ui/_asgi.py:145`), not an in-process subscription. There *is* an in-process subscription mechanism on `EventStore` (`event_store.py:153-154`), but the live UI mostly polls via the Query API per `CLAUDE.md`'s UI consistency rule ("pages read through the public Query API — never directly from parquet, ContextVars, or in-process dicts").
- Fix: replace with "The operator UI reads through the Query API, which is backed by the same Flight server the materializer feeds — so live updates appear as soon as the daemon ingests them."

**WARNING — "Subscribers denormalize at write time" is now misleading for the parquet path.**
- Location: `docs/concepts/three-stores.md:21`.
- Evidence: The parquet "subscriber" no longer exists; the materializer is invoked by the daemon's event-dispatch loop and projects via `EventAccumulator`. Denormalization happens in the accumulator, not in a subscriber. Other subscribers (`StdfSubscriber`, `AtmlSubscriber`, etc.) *do* denormalize at write time and remain `EventSubscriber` subclasses — so the sentence is true for exporters but false for the canonical parquet path.
- Fix: split the claim — "Exporters (`StdfSubscriber`, `AtmlSubscriber`, …) and the parquet materializer both denormalize at projection time; events stay normalized on disk."

**SUGGESTION — The diagram at lines 49-54 omits the daemon entirely.**
- Location: `docs/concepts/three-stores.md:49-54`.
- Issue: It shows Events → EventStore → ParquetSubscriber → ParquetBackend as if everything happens in one process. The actual flow is producer → Flight `do_put` → daemon → AccumulatorPool → materializer → ParquetBackend. The daemon is the missing actor that the page should make visible. Without it the live-streaming/crash-safety story has no anchor.

**SUGGESTION — "Each channel gets its own IPC file with a schema inferred from the first write" needs the rotation caveat next to it.**
- Location: `docs/concepts/three-stores.md:31`.
- Issue: The next sentence mentions segment rotation but it's worded as a separate fact. The reality is "one IPC file per channel per session, segmented when it grows" — saying "its own IPC file" without the rotation context contradicts the storage layout block (which doesn't show segments at all). Either show `{channel_id}_{session_short}.arrow` and `{channel_id}_{session_short}_001.arrow` in the tree, or rephrase.

---

## Gaps

**CRITICAL — The page does not explain *why* three stores instead of one.**
- Issue: This is an Explanation page and the headline question — "why are there three stores?" — is never answered. The Overview table tells the reader *that* there are three; the per-store sections describe each one's path; "How They Relate" sketches the data flow. But the page never says *what's load-bearing about the split*: the EventStore is append-only and crash-safe; the ChannelStore exists because putting megabyte waveforms inside event records would make the EventStore unqueryable; the ParquetBackend exists because analytics consumers (DuckDB, Polars, Spark, pandas) don't want to crawl Arrow IPC files. Without the "why," the page is just a directory listing.
- Fix: open the page with a "Why three stores?" section that names the three different access patterns and the trade-off each store optimizes for. Use the Overview table to *recap* what was just argued, rather than as the lede.

**WARNING — No discussion of the rebuild story (can ParquetBackend be regenerated from EventStore?).**
- Issue: Line 41 says "They can be regenerated from events if the schema changes," which is exactly the load-bearing property of treating Parquet as a materialized view — but it's one sentence buried in a bullet-flanked section, and there's no pointer to *how* (the `litmus export` replay path via the same materializers, per `docs/concepts/event-log.md:132` and `docs/reference/outputs.md:73`). A concept page about three stores should explain that EventStore is canonical and the other two are derivable.

**WARNING — No coverage of retention / lifecycle differences across the three stores.**
- Issue: All three stores have separate retention semantics (`src/litmus/data/retention.py` references `_ref/` sidecar handling for ChannelStore data inlined into parquets). The page doesn't mention that retention can be per-store, that ChannelStore data can be promoted into the parquet's `_ref/` sidecar at retention time, or that the EventStore is the only one explicitly described as "canonical record." Readers thinking about disk usage will not find their answer.

**WARNING — `_ref/` sidecar directories are shown in the storage tree but never explained.**
- Location: `docs/concepts/three-stores.md:69, 78`.
- Evidence: `event_log.py:185` (`_ref/` per session for large data), `run_store.py:329-330` (`_ref/` sidecar per parquet). The tree displays both but the page never says what they are or when they're created. A reader looking at the tree will conclude they're some kind of duplicate.
- Fix: one sentence per occurrence: "Values too large to inline (e.g. blob attachments) spill into a `_ref/` sidecar alongside the owning file."

**SUGGESTION — No mention of the session_id join key, even though "Queries can join across stores using `session_id`" is listed as a numbered point.**
- Location: `docs/concepts/three-stores.md:59`.
- Issue: The page asserts the join works but never shows what a cross-store query looks like (e.g., "find all `channel://` URIs in a session's events, then pull the LTTB-decimated waveforms from the ChannelStore"). For a concept page, even a stylized SQL/pseudocode block would make the relationship concrete.

**SUGGESTION — No mention of what the EventStore Query Layer is *for*.**
- Location: `docs/concepts/three-stores.md:9`.
- Issue: The Overview table says EventStore's query layer is "DuckDB via Flight" — but the body of the page only describes events being ingested, never queried. A reader who knows nothing about `litmus events list` or the Flight-backed event index won't learn from this page that the EventStore is queryable at all. The `_event_reader.py` direct-file fallback (`event_store.py:223`) is also worth a sentence.

---

## Cross-links

**WARNING — No inbound link from `why-event-sourcing.md` despite heavy thematic overlap.**
- Evidence: `grep -l three-stores docs/concepts/why-event-sourcing.md` returns nothing. `why-event-sourcing.md:53` does mention `ParquetSubscriber` but does not link to three-stores. The two pages cover the same architectural ground from different angles; they should cross-reference.
- Fix: add See Also entries in both directions, and rewrite `why-event-sourcing.md` line 53 to point readers to the three-stores page for the concrete picture.

**WARNING — `architecture.md:347` and `event-log.md:191` link *to* this page but the See Also block doesn't reciprocate `architecture.md`.**
- Evidence: `docs/concepts/three-stores.md:83-87` See Also lists event-log, sessions, flight-streaming. No link to `architecture.md` (the parent overview) and no link to `results-storage.md` (the closest sibling, which contains the resolution-order, schema-evolution, and `_index.duckdb` material that this page implicitly assumes).
- Fix: add `architecture.md` (upward) and `results-storage.md` (sideways) to See Also.

**WARNING — `concepts/index.md:30-32` groups three-stores adjacent to results-storage and flight-streaming, but the body cross-links one and not the other.**
- Evidence: `docs/concepts/index.md:26-32` puts the three pages in the same "Data architecture" subsection. Within the body, `three-stores.md` links to `flight-streaming.md` but not to `results-storage.md`, even though `results-storage.md` is the page that holds the resolution-order discussion, schema-evolution rules, and `_index.duckdb` discussion this page would otherwise need to repeat.

**SUGGESTION — The first mention of LTTB at line 28 should link out (likely to `flight-streaming.md` or a dedicated reference) instead of dropping the acronym cold.**
- Location: `docs/concepts/three-stores.md:28`.
- Issue: LTTB has no inline gloss and no link target in the docs that defines it. Either define it in line or add a cross-link.

**SUGGESTION — `reference/outputs.md` mentions `ParquetSubscriber`, `LiveRunsSubscriber`, and the export-replay path. Three-stores should point readers there for the exporter list.**
- Evidence: `docs/reference/outputs.md:73` explicitly enumerates the format subscribers. Three-stores hints at multiple subscribers but doesn't enumerate them or point at the reference. Adding "for the full list of formats produced by event subscribers, see `reference/outputs.md`" would close the loop.

---
