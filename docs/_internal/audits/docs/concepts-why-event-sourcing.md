# Page audit: docs/concepts/why-event-sourcing.md

**Quadrant:** Concepts / Explanation
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 2 | 3 |
| Audience | 0 | 2 | 2 |
| Accuracy | 1 | 3 | 2 |
| Gaps | 0 | 2 | 3 |
| Cross-links | 1 | 2 | 2 |
| **Total** | **2** | **12** | **14** |

---

## Ordering

The page follows a strong rhetorical arc for an Explanation page: problem (CRUD trap) → inversion → consequences → boundaries → trade-offs. The macro flow is sound. Issues are local.

### WARNING — "Materializers run in whatever process cares" sits awkwardly between "Principled split" and "Trade-offs"

Lines 47–58 introduce a concrete architectural mechanism (where materializers physically run, naming `ParquetSubscriber` / `LiveRunsSubscriber`, the runs daemon, orphan finalization). This is one level of implementation detail deeper than everything around it. The preceding section ("Principled split") is at the data-shape philosophy layer and the following section ("Trade-offs") is back at the conceptual layer. The materializer section breaks the altitude.

Two reasonable fixes:
1. Move the materializer section to `three-stores.md` or `event-log.md` (it explains *how* the projection layer is wired, which is a "what" question, not a "why" question).
2. Keep it but cut to the architectural claim only: "Each consumer runs its own materializer in its own process; nothing centralizes." Drop the named-class detail.

The current placement makes the page longer than its conceptual job requires.

### SUGGESTION — "Properties that fall out" bullet ordering could escalate impact

The seven bullets in lines 25–31 are not ordered by weight. Live visibility (operationally important) sits next to time-travel queries (analytically interesting but a corner case for most users). A reader who stops scanning at bullet 3 should have hit the three highest-leverage properties:

1. Live visibility during a run
2. Crash recovery is automatic
3. Composable consumers / cross-system correlation

Time-travel and audit log can drop to positions 5–6 without losing anything.

### SUGGESTION — The "principled split" table sits before its prose explanation

Lines 37–41 give the table, then lines 42–45 explain the underlying logic. Concepts pages usually read better when prose frames the table that follows it ("Litmus splits data along these lines, for these reasons: [table]"), especially since the prose explanation introduces the key insight (annotations are new facts, not edits) that the table can't express.

---

## Voice

The page is broadly in the right voice for the Concepts quadrant — explanatory, opinionated, oriented toward understanding rather than action. But it slides between several registers.

### WARNING — Mixed register: design-doc voice vs documentation voice

Several passages read like a design-decision memo to the team, not a concept page for a reader:

- Line 11: "Simple, but mutability brings race conditions on concurrent reads, audit-trail difficulties (when did the row become `passed`?), and a category of bugs where consumers see partially-updated state." — Three problems crammed into one sentence with parenthetical clarification.
- Line 21: "That single inversion is what dodges the CRUD trap entirely." — "dodges" is colloquial; "single inversion" reads like the author congratulating the design.
- Line 53: "It's defensible because the runner's job isn't done until its run's artifact is durable." — "defensible" is a design-review word; readers don't need to know the design is defensible, they need to know what it does.
- Line 64: "This page is part of paying that tax." — Self-reference breaks the fourth wall.

Concept pages should explain; they should not perform the act of designing for the reader.

### WARNING — Inconsistent treatment of jargon

The page introduces "CRUD," "WAL," "claim-check," and "materialized projection" with very different levels of care:

- "WAL" is unglossed at first use (line 31), then defined parenthetically in the table (line 41). Reverse the order.
- "Materialized projection" appears in line 3 with no gloss. Readers from a non-database background will not know what "materialized" means here.
- "CRUD" is used in the section heading (line 7) and throughout without a gloss. Test engineers may not have heard the acronym.
- "Claim-check URI" is not in this page at all but is the mechanism the cross-system correlation bullet relies on (line 30).

Pick one rule (gloss on first use, link to a glossary, or assume the audience). Apply it uniformly.

### SUGGESTION — "The CRUD trap" is a strong frame but unnecessarily combative

"Trap" implies anyone using the traditional model is naïve. The page would lose nothing by titling the section "Why the traditional schema struggles" or "The CRUD shape for test results." The actual argument that follows is fair; the heading overclaims.

### SUGGESTION — Em-dashes are doing a lot of work

The page has 18 em-dashes across 71 lines. Several do double duty as colon, parenthesis, and semicolon in the same paragraph. A copy pass converting some to colons or splitting sentences would improve readability.

### SUGGESTION — "for free" appears three times

Lines 3, 27, 30. "Free" is salesy in a concept page. Prefer "naturally," "by construction," or just "without extra work."

---

## Audience

The page targets a sophisticated reader and mostly serves that reader well. Two audience misalignments stand out.

### WARNING — Implicit reader is a database-architecture-fluent developer

The frame ("CRUD trap," "materialized projection," "WAL," "temporal-database extensions," "event-sourced") assumes the reader has at least passing familiarity with data architecture vocabulary. That is fine for the Concepts quadrant, but a Litmus user is a test engineer first and a database thinker second (or fifth). The reader who most needs this page — someone evaluating whether Litmus's storage model fits their data-retention obligations — may not have CRUD-vs-event-sourcing in their working vocabulary.

Two options:
1. Add a one-paragraph preamble that says, in test-engineer terms, what changes for them ("Your run data is permanent the moment it's written. You can rebuild any view of it without re-running tests. Live dashboards see partial runs as they happen."). Then keep the database-architecture frame below.
2. Replace the database vocabulary with test-engineering analogies (a strip-chart recorder vs a logbook). This is more invasive but would broaden the page.

### WARNING — "Audit log = primary data path" undersells the regulated-industries case

Line 28 is a single bullet, but for medical / aerospace / defense / automotive readers this is *the* reason they're evaluating Litmus. Burying it in a properties list when it's a procurement-grade feature is an audience mismatch. Either:

- Promote it to its own short subsection ("Regulatory readiness") with a sentence or two on what the immutable trail actually buys (FDA 21 CFR Part 11, ISO 26262 data integrity, AS9100 traceability — name the standards your buyers are looking at).
- Or pull it forward into the opening framing as one of the two or three things the inversion buys you.

### SUGGESTION — Reader's likely next question is unanswered

After reading this page, a thoughtful reader is going to ask: "OK, what happens when the event format itself needs to change?" The page asserts evolvability ("Replay is free... Replay the event log into a new projection") but doesn't acknowledge that the events themselves have a schema. The HARD-contract framing is in `event-log.md`, but this page is where the *why* lives, and the *why* of additive-only evolution belongs here. A two-sentence pointer would close the loop.

### SUGGESTION — Audience signal: who is this page *not* for?

Concept pages benefit from a quick "if you're here for X, go to Y" sentence near the top. Right now the second paragraph forwards to `three-stores.md` / `event-log.md` for "the what," which is good. But readers looking for "how do I query my data?" or "how do I configure retention?" have no signpost.

---

## Accuracy

I read `event-log.md`, `three-stores.md`, `results-storage.md`, `src/litmus/data/events.py`, `src/litmus/data/_accumulator_pool.py`, `src/litmus/data/backends/parquet.py`, and grepped for the named classes to verify claims.

### CRITICAL — "LiveRunsSubscriber" is not a class that exists in source code

Line 54: "the runs daemon cares about an always-on, queryable view of recent runs, so `LiveRunsSubscriber` runs in-daemon as a long-lived consumer indexing events as they arrive."

`grep -rn "class LiveRunsSubscriber" src/` returns nothing. The only references to `LiveRunsSubscriber` are in documentation: `docs/concepts/event-log.md`, `docs/reference/outputs.md`, `docs/_internal/audits/public-api.md`, and `docs/_internal/explorations/data-architecture.md`. The actual subscriber classes in the codebase are `EventSubscriber` (base), `TdmsSubscriber`, `StdfSubscriber`, `CsvSubscriber`, `Mdf4Subscriber`, `JsonSubscriber`, `AtmlSubscriber`, `Hdf5Subscriber`. The ingest-side daemon code lives in `_accumulator_pool.py` and uses an `AccumulatorPool` keyed by `run_id`, not a named `LiveRunsSubscriber` class.

This is a recurring doc-vs-code drift across multiple pages, but it is load-bearing here because the materializer section names the class specifically. Either:
- The class needs to be created/renamed in code, or
- The references need to be replaced with the actual mechanism (`AccumulatorPool` driven by the runs daemon's event watcher).

### WARNING — "ParquetSubscriber runs in-process and finalizes synchronously at RunEnded" is half-true

Line 53 claims the runner's `ParquetSubscriber` "runs in-process and finalizes synchronously at `RunEnded`." That matches the normal-close path. But per `src/litmus/data/_accumulator_pool.py` and `docs/_internal/explorations/data-architecture.md`, a *fresh* `ParquetSubscriber` instance also runs **inside the runs daemon** when the orphan sweep synthesizes a `RunEnded(aborted)` for a crashed producer. So `ParquetSubscriber` is not exclusively in-process to the runner. The page later says (line 55) "Different trigger, different timing, same materializer pattern, same process boundary" — but the "same process boundary" claim contradicts the orphan path. The orphan finalization writes the parquet from the *runs daemon* process, not the runner process. The page's own example shows two writers, not one.

Fix: explicitly note that orphan finalization runs the materializer in the daemon, *not* the runner. That actually strengthens the "materializers run wherever cares" thesis.

### WARNING — The crash-recovery property overstates the automation

Line 26: "The orphan-finalization path emits a synthetic `RunEnded(aborted)` and the projection materializes normally."

Per `_accumulator_pool.py:17` and the data-architecture exploration, the orphan sweep uses `os.kill(pid, 0)` liveness checks on a 30-second cadence with a 1-hour wall-clock fallback. So "automatic" is true but not instantaneous, and the recovery requires the runs daemon to be running. A test machine that crashes the runs daemon along with the runner will not self-heal until the daemon is restarted. The page should not promise "automatic" without acknowledging the 30s–1h latency and the dependency on a live daemon.

### WARNING — "Replay is free" is correct in principle but ignores a real constraint

Line 27: "Want a new analytical view three years from now — different schema, new format, additional aggregations? Replay the event log into a new projection."

This is true *as long as* the event WAL remains intact and the event schema has only evolved additively. `event-log.md` lines 161–187 spell out the HARD contract that makes this true. This page asserts the property without pointing at the contract that backs it, which is a meaningful gap. A reader who believes "replay is free" without understanding the additive-only constraint will be surprised the first time someone proposes a breaking event change.

### SUGGESTION — `RunFlagged` and `MeasurementAnnotated` are presented as concrete examples

Line 45: "emit a new event type for the new fact (e.g. a future `RunFlagged` or `MeasurementAnnotated`)"

Neither type exists in code today. The "(future)" qualifier is honest but invites the reader to grep for them and find nothing. Either drop the specific names (just say "a new annotation event type") or list event types that *do* exist as examples of post-hoc fact-adding.

### SUGGESTION — "kHz–MHz rates" is a sample-rate claim with no source

Line 43 claims channel sample streams operate at "kHz–MHz rates" and "can't fit through the event WAL." That may be operationally true but the page does not back the number. A more honest version: "Channel sample streams can produce thousands or millions of samples per second, far more than the event WAL is sized for." Drops the false precision.

### CRITICAL → kept as one accurate severity-1 finding above. Note: the `LiveRunsSubscriber` issue is the only CRITICAL accuracy finding; everything else degrades but does not invert a claim.

---

## Gaps

### WARNING — No mention of what happens to events older than a retention window

The page asserts the event log is the source of truth and emphasizes "replay is free." But Litmus's `results-storage.md` describes retention as configurable (and per `MEMORY.md`, defaulting to unlimited / opt-in pruning). A reader of this page will reasonably wonder: "If retention is finite, is replay still free? What gets lost?" The page should at least gesture at this — even one sentence ("Retention policy determines how far back replay can go; see [Results Storage]") would close the gap.

### WARNING — No mention of how this affects test-runner choice

Line 56 says "Any future consumer — a Grafana exporter, a Snowflake pipeline, an analytics view." But the page misses the user-facing question that motivates much of Litmus's positioning: *the same architecture makes OpenHTF, pytest, and a results-API ingest all peers*. Each runner emits the same events; the same projections work for all of them. That is one of the strongest payoffs of the inversion and the page never says it. Given that "platform, not pytest plugin" is a project-level positioning rule, this gap is doubly visible.

### SUGGESTION — No diagram

The page is dense and conceptual. A simple diagram showing "events on the left, projections fanning out on the right, with a dotted line to 'your future consumer'" would carry a lot of weight and reduce the prose load. The sibling pages (`three-stores.md`, `event-log.md`) have ASCII diagrams; this page has none.

### SUGGESTION — "The principled split" leaves test-config and per-run input data unaddressed

The table covers config, test execution data, and channel data. But what about per-run inputs — DUT serial, station snapshot, profile snapshot? These are technically captured *inside* the event log (as fields on `RunStarted`), but a reader of the table might wonder where they live. A short sentence ("Per-run context is captured as fields on `RunStarted` — see Event Log Architecture") would close it.

### SUGGESTION — No "when to use the projection vs the event log" guidance

The page tells the reader events are primary and projections are derived but does not advise on which to query. For 95% of users, the projection (parquet, DuckDB index) is the right answer. For a small number — auditors, debugging odd cases, building a new view — the events are the right answer. A short closing note would orient the reader to the practical default before they get lost in the philosophy.

---

## Cross-links

### CRITICAL — Outbound link to `event-log.md` HARD-contract section is missing

The page makes architectural claims (replay is free, additive evolution, immutability) that depend on the event WAL's HARD-contract additive-only rule. That contract is documented at `docs/concepts/event-log.md` lines 161–187. This page should link directly to that anchor when it asserts those properties — most directly on the "Replay is free" bullet (line 27) and the "Annotations don't break this" paragraph (line 45). Without that link, the reader has no way to know how the promise is backed.

### WARNING — Sibling-page link block at line 5 is incomplete

Line 5: "The companion pages cover the *what*: see [Three Stores Architecture](three-stores.md) and [Event Log Architecture](event-log.md)."

`results-storage.md` and `flight-streaming.md` are also "what" pages in this group (per the `concepts/index.md` "Data architecture" section) and would be reasonable additions to the companion list, particularly `flight-streaming.md` which is referenced implicitly by the multi-consumer / cross-process argument late in the page.

### WARNING — "See also" footer skips related concept pages

The footer (lines 66–70) lists three data-architecture pages. It should also point to:
- `outcomes.md` — the page mentions `aborted` and `RunEnded(aborted)`; outcomes is where that vocabulary is defined.
- `sessions.md` — line 30's "cross-system correlation" rests on session-keyed joins.
- `platform-architecture.md` — the "platform, not framework" framing is part of the *why* this page argues for; the link would situate the data-architecture argument inside the broader platform thesis.

### SUGGESTION — Inbound link surface is thin

`grep -rn "why-event-sourcing\|event sourcing"` against `docs/` finds only two references: the index entry in `concepts/index.md` and a mention in `integration/index.md` that doesn't link the term. A page making a foundational architectural argument should be linked from:

- `architecture.md` (when storage is mentioned)
- `outcomes.md` (when `aborted` is introduced — the inversion is *why* that outcome exists)
- The OpenHTF and pytest-plugin integration overviews (the unification argument is core to why both runners feel the same)
- Any reference page that mentions "event log" or "WAL" without prior context

This is a documentation-graph problem more than a problem in this page, but it's a missed opportunity that the page does nothing to fix.

### SUGGESTION — Internal cross-references could be richer

The page uses the phrase "event log" 14 times and "projection" 11 times. Only one of these (line 5) is hyperlinked. A reader who first lands here through search and doesn't know the term should be able to click on either word the first time it appears and land on the matching reference. Linking the first occurrence of each key term in each section (not every occurrence) is the usual sweet spot.
