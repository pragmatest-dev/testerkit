# Page audit: docs/how-to/querying-events.md

**Quadrant:** How-to (querying historical events — MCP, HTTP API, Python)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 2 | 2 |
| Accuracy | 1 | 2 | 1 |
| Gaps | 0 | 4 | 2 |
| Cross-links | 1 | 2 | 2 |
| **Total** | **2** | **12** | **11** |

---

## Ordering

**WARNING — Section ordering buries the most-used surface (HTTP) and orders by interface, not by task**

The page is structured as three interface chunks (MCP → HTTP → Python) followed by a shared filter reference. For a how-to in the "Query results" group, readers arrive with a *task* in mind ("get events for session X", "tail measurements for the DMM"), not an interface. The current shape forces them to read three near-duplicate code blocks before they reach the `Filtering Options` table that actually explains what the parameters do.

Two reasonable rearrangements:

- **Task-first**, with each section ("All events for a session", "Filter by event type", "Filter by role", "Events since a time") showing all three interfaces side by side or in tabs.
- **Filter-reference first**: lead with the `Filtering Options` table so all three interface chunks reduce to "here is how this surface spells those filters."

Either is a stronger how-to shape than "three nearly-identical recipe lists in series."

**SUGGESTION — `Filtering Options` belongs above the interface examples, not below**

For a reader scanning to learn what `role` actually matches, the filter table is the single most informative element on the page and currently sits at the bottom. Move it above the three interface sections, or duplicate it under each (cheap and friendly).

**SUGGESTION — `See Also` ordering**

The three See-Also links go: reference → concepts → how-to. Convention across other how-to pages (e.g. `querying-channels.md`, `managing-sessions.md`) is concept → reference → adjacent how-to. Reorder to: Event Log Architecture (concepts) → Event Types Reference (reference) → adjacent how-tos.

---

## Voice

**WARNING — Section voice drifts between imperative-recipe and label-mode**

A how-to page should read as a sequence of actions the user takes ("Filter events by session", "Query measurements since a timestamp"). Most of the page is fine, but headings and comment-style framing slip between voice modes:

- `## MCP Tool: \`litmus_events\`` — label voice ("here is a thing").
- `## HTTP API` — bare noun, no verb.
- `## Python: \`EventStore\`` — label voice.
- `## Filtering Options` — noun.

A how-to is stronger with action-oriented headings: "Query from an AI agent", "Query over HTTP", "Query from Python", "Filter the results". The current headings read like a reference index, which conflicts with the quadrant.

**SUGGESTION — Opening sentence is functional but flat**

> "Three ways to query events: MCP tool (AI agents), HTTP API (any client), or Python (in-process)."

This is a table-of-contents sentence dressed as prose. A how-to lede usually sets up the *why* ("After a test runs, every measurement, instrument read, and step boundary is in the event log. Pull it back by session, type, role, or time — from an agent, a script, or the CLI."). The current version skips the reason a reader would want any of this.

**SUGGESTION — Comments in code blocks vary between sentence fragments and bare labels**

- `# All events for a session` (fragment)
- `# Only measurement events` (fragment)
- `# Combine filters` (verbless phrase)
- `# Events since a timestamp` (fragment)

These are consistent with each other so the inconsistency is small, but compared with the imperative comments on `querying-channels.md` ("Get channel data", "Last 100 readings") the gap is visible. Settle on one style; the project leans imperative.

---

## Audience

**WARNING — Page assumes the reader already knows what an "event" is and which event types exist**

The how-to opens with `litmus_events(event_type="test.measurement")` with no link or pointer to the catalog of event types. A reader landing here from search ("how do I query test results") has no way to discover that `test.measurement`, `instrument.read`, `session.started`, `test.step_ended` and the rest are valid values, nor what each carries. The Event Types Reference exists at `docs/reference/event-types.md` — link to it from the **opening paragraph**, not just from the See Also tail.

**WARNING — Page silently assumes the operator UI / API server is running**

Every HTTP example is `curl http://localhost:8000/api/events`. There is no sentence telling the reader they need `litmus serve` running first, no hint that the port is configurable, and no note that MCP requires `litmus mcp serve` (or a host IDE wiring). The MCP integration how-to mentions this, but a reader arriving here cold will copy the curl and get connection refused. Add a one-liner prerequisite or a "Prerequisites" callout.

**SUGGESTION — Audience for the Python section is unclear**

The Python section uses `EventStore()` directly and `try/finally` with `store.close()`. That implies:

1. The reader is running inside a Litmus project (the data dir is auto-resolved).
2. The reader is OK managing the gRPC daemon lifecycle each call.

For UI pages and most application code, `EventStore.get_shared()` is the documented entry point ("UI page handlers should use this instead of `EventStore(...)` so the thread count stays flat" — `event_store.py:120`). Spell out which audience this snippet is for (one-off script vs. long-running consumer) and point the latter at `get_shared()`.

**SUGGESTION — UUID handling differs across the three surfaces with no explanation**

- MCP: `session_id="abc12345-..."` — string with ellipsis.
- HTTP: `?session_id=abc12345-...` — string with ellipsis.
- Python: `UUID("abc12345-...")` — wrapped UUID constructor, will raise on the literal `"abc12345-..."`.

Newcomers will copy the Python literal verbatim and get `ValueError: badly formed hexadecimal UUID string`. Either show a real-looking UUID, or annotate explicitly: `# Replace with a real session UUID from \`litmus runs\``.

---

## Accuracy

**CRITICAL — Self-referential See Also link**

```
- [Subscribing to Events](querying-events.md) — Real-time monitoring
```

This link points back at the page you are currently reading. There is no `docs/how-to/subscribing-to-events.md` (verified by `find /home/ryanf/repos/litmus/docs -name "subscrib*.md"` returning nothing). The "real-time monitoring" surface (`EventStore.on_event`) is documented in `docs/reference/connect.md` and discussed in `docs/tutorial/10-live-monitoring.md`. Either:

- delete the link, or
- repoint it to the correct existing page (e.g. `../tutorial/10-live-monitoring.md` or `../reference/connect.md`), or
- write the missing `subscribing-to-events.md` page and link to that.

**WARNING — `limit` is described as "HTTP/MCP only" but is also a valid `EventStore.events()` argument**

The filter table says:

> `limit` | Max results (HTTP/MCP only) | `100`

`EventStore.events()` (verified at `src/litmus/data/event_store.py:267-289`) accepts `limit: int | None = None`, and the docstring documents it explicitly: "limit pushes the row cap into the SQL ... limit is applied to the **most recent** rows." The Python section's own example block silently omits it, but the table is wrong to say it's unavailable in Python. Either:

- Update the cell to say "Available everywhere; HTTP/MCP default to 100, Python default is unlimited."
- Or add a Python example with `limit=` so the section matches the others.

**WARNING — Role filtering description is incomplete vs. the implementation**

The page states:

> Role filtering checks the `role`, `instrument_role`, and `channel_id` prefix fields across all event types.

That matches `_event_filters.event_matches_role` (`src/litmus/data/_event_filters.py:6-15`). What it omits:

1. For the MCP tool, role filtering happens *client-side after a 4× over-fetch* (`tools.py:1245-1253`), so `limit=10 role="dmm"` returns at most 10 but may return fewer than expected if more than ~75% of the over-fetched rows don't match. Worth a sentence in the table footnote.
2. For `EventStore.events()`, role filtering is applied *after* the SQL limit, meaning Python users who pass `limit=` and `role=` can get under-fill silently. The MCP tool compensates; raw `EventStore` does not.

If the page is going to describe the filter's matching logic, it should also describe its cap interaction — otherwise readers writing dashboard queries will hit surprises.

**SUGGESTION — `since` example uses a timezone-naive ISO timestamp without comment**

`since="2026-03-10T14:00:00"` — no timezone. `datetime.fromisoformat` accepts this and produces a naive datetime, which DuckDB then compares against the stored `received_at`. Whether that comparison is correct depends on what timezone `received_at` is stored in. The page implies "ISO timestamp" is sufficient; in practice the safe form is `"2026-03-10T14:00:00+00:00"` or a `Z` suffix. Either annotate ("Naive timestamps are interpreted as local time", or whatever the actual contract is — verify and document) or show the timezone-aware form.

---

## Gaps

**WARNING — No mention of the prerequisite servers**

HTTP examples need `litmus serve`. MCP examples need either `litmus mcp serve` (stdio) or an IDE-wired MCP client. The page assumes both. A two-line "Prerequisites" callout would prevent the obvious failure mode.

**WARNING — Sessions query is missing from MCP and HTTP sections**

The Python section shows `store.sessions()`. The MCP tool has a parallel `litmus_sessions` tool (`mcp/server.py:499`) and the HTTP API has `GET /api/sessions` (`api/app.py:456`). Neither is shown. A reader scanning the page would conclude session listing is Python-only.

Either:

- Drop the `sessions = store.sessions()` line from Python (and just link to a separate "list sessions" page), or
- Add the MCP and HTTP equivalents so each section is symmetric.

**WARNING — No example of what an event row looks like**

A how-to about querying events that never shows a single example response leaves the reader unable to write the follow-up code (filtering, aggregating, parsing). One block showing the shape of a `test.measurement` event dict — and/or pointing at where in `event-types.md` to find every shape — would close this gap.

**WARNING — No example of the most common combination: "show me the last N failed measurements"**

How-tos earn their keep with realistic recipes. The current examples are individually trivial (one filter at a time, plus one "combine"). Add one or two end-to-end recipes:

- "List failed measurements for the last completed run."
- "Tail the most recent instrument reads from the DMM during today's session."
- "Find the step where a measurement first went out-of-limit."

These are the queries operators and tools actually want.

**SUGGESTION — No mention of `until` / `until_event_number`**

`EventStore.events()` accepts `until` (ISO string) and `until_event_number` (monotonic sequence), used for replay-bounded snapshots (`event_store.py:274-275`). They are not in the filter table. Either document them as Python-only, or hide them as internal-only and add a comment in the source noting they aren't user-facing.

**SUGGESTION — Pagination / cursors are unaddressed**

For event logs with millions of rows, paginating beyond `limit=100` (the HTTP default) requires `since=` cursoring. The page doesn't explain this idiom, and there's no `offset` or `before` parameter. A short "Paginating through history" section would help anyone building a viewer.

---

## Cross-links

**CRITICAL — Broken/self-referential See Also link**

(Same finding as Accuracy.) `[Subscribing to Events](querying-events.md)` points at itself. This is the only place the page references real-time monitoring, so a reader looking for that information is dead-ended.

**WARNING — Missing inbound link from reference pages**

`docs/reference/api.md` documents `GET /events` (lines 236-245) and `docs/reference/event-types.md` documents every event shape, but neither links back to this how-to. A reader landing on the reference looking for "how do I actually call this" has no signpost. Add reciprocal links:

- From `docs/reference/api.md` Events section: "See [Querying historical events](../how-to/querying-events.md) for end-to-end usage."
- From `docs/reference/event-types.md` lead: "To query events by these types, see [Querying historical events](../how-to/querying-events.md)."

**WARNING — `EventStore.on_event` is the obvious "next step" but isn't linked**

The page is titled "Query Historical Events" and ends with a (broken) link about real-time monitoring. The real-time API (`EventStore.on_event`, MCP/HTTP equivalents) is documented in `docs/reference/connect.md:89`. The relationship between historical (this page) and live (the other surface) is exactly the kind of edge a how-to should make explicit. Add an inline pointer in the body — "For live monitoring instead of historical queries, see [`on_event`](../reference/connect.md#on_event)" — not just a tail link.

**SUGGESTION — Link to `mcp-integration.md` from the MCP section**

The MCP section assumes the tool is callable. Readers who don't have MCP wired up need `docs/how-to/mcp-integration.md`. One inline link in the MCP section heading (e.g., "MCP tool: `litmus_events` ([setup](mcp-integration.md))") covers this without expanding the page.

**SUGGESTION — Link to `concepts/event-log.md` from the lede, not the tail**

The lede sentence "Three ways to query events" is the right place to introduce the *what* the reader is querying. "...query [events](../concepts/event-log.md): MCP tool..." gives the conceptually-curious reader one click to the explanation and costs the operationally-focused reader nothing.
