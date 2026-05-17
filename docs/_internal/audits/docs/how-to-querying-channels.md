# Page audit: docs/how-to/querying-channels.md

**Quadrant:** How-to (querying channel data / time-series — MCP, HTTP, Python)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 0 | 2 |
| Audience | 0 | 2 | 2 |
| Accuracy | 2 | 3 | 2 |
| Gaps | 2 | 4 | 3 |
| Cross-links | 1 | 3 | 4 |
| **Total** | **5** | **13** | **15** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| WARNING | L73-77 | "LTTB Decimation" section appears AFTER all three usage surfaces (MCP/HTTP/Python) reference `max_points` and call it "LTTB". A reader encounters the term at L17 ("Downsample for visualization (LTTB)") and L52 ("LTTB decimation") with no inline gloss. The explanatory section comes ~60 lines after first use. |
| SUGGESTION | L1-3 | How-to pages should state prerequisites first. Page has no "Prerequisites" or "Before you start" section — nothing says "you need a session that has written channel data" or "for the HTTP API the server must be running". Reader hitting `curl http://localhost:8000/api/channels` cold will not understand why it 404s. |
| SUGGESTION | L79-90 | The "Query Parameters" reference table appears at the END after all three usage surfaces. For a how-to that branches by transport, a summary table FIRST (so the reader can see all knobs) or per-transport sections each with their own column would scan better. Currently the reader can't cross-check "is `start` an MCP param?" until after the examples. (Spoiler: per accuracy findings, MCP doesn't accept `start`/`end` at all — see accuracy section.) |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| SUGGESTION | L75 | Marketing-adjacent superlative | "much better than naive stride decimation" — the comparative is fine, "much better" is a hedge-boast; just say "preserves peaks and valleys, where stride decimation would drop them" or similar. |
| SUGGESTION | L75 | Hedging / vague claim | "visually lossless algorithm" — "visually lossless" is a defensible term of art but the qualifier "visually" already softens "lossless"; tighten to "preserves peaks and valleys" which the source docstring uses. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L37, L42, L45, L62, L64 | Cold cross-page drop | `ChannelStore` and `ChannelClient` are used as Python entry points with no inline gloss. The link at L37 (`[`ChannelStore`](../concepts/three-stores.md)`) points to the concept page but the concept doesn't document the constructor signature; reader needs an API reference. There is no `reference/` page for ChannelStore/ChannelClient (confirmed by grep). |
| WARNING | L37 | Programmer-jargon framing | The Python section is titled "Python: `ChannelStore`" — a test engineer who has come from LabVIEW/TestStand reads "ChannelStore" as an internal class. The sibling page `querying-events.md` does the same with `EventStore`. Consider a more task-framed heading like "Python — query channel data directly" with the class name in the body. |
| SUGGESTION | L40-45 | Wrong vocabulary / confusing example | The example imports `uuid4` and constructs `ChannelStore(channels_dir, uuid4())` with a fresh UUID. A test engineer reading this will think they need to mint a UUID to query — but `session_id=` filter is the actual lever. The constructor session_id is irrelevant for queries. Either (a) explain why a throwaway UUID, or (b) use the canonical resolver pattern from CLAUDE.md (`resolve_data_dir()`) and clarify "the session_id constructor arg is required but unused for read-only queries". |
| SUGGESTION | L44 | Implicit path assumption | `channels_dir = Path("results/channels")` — test engineer doesn't know if `results/` is project-relative, cwd-relative, or platformdirs. Reuse `resolve_data_dir()` from `litmus.data.data_dir` to match how every other example in the codebase resolves data_dir (see CLAUDE.md "Test Storage Convention"). |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L34, L86 | HTTP API query params `?start=` / `?end=` for time range | HTTP endpoint accepts `since` and `until`, NOT `start` and `end`. Curl example at L34 will silently ignore both params and return unfiltered data. Table row L86 also wrong for the HTTP column. | `src/litmus/api/app.py:504-528`, `src/litmus/mcp/tools.py:1299-1308` |
| CRITICAL | L45, L48 | `ChannelStore(channels_dir, uuid4())` where `channels_dir = Path("results/channels")` | Constructor takes `data_dir` (the **parent** results dir), then appends `channels/` itself: `self._channels_dir = data_dir / "channels"` — so the example builds `results/channels/channels/...` and queries will find nothing. Should be `ChannelStore(Path("results"), uuid4())` or, per project convention, `ChannelStore(resolve_data_dir(), uuid4())`. | `src/litmus/data/channels/store.py:184-198` |
| WARNING | L84-88 | Table claims `start` / `end` apply to both Python and HTTP | Python `ChannelStore.query()` does accept `start`/`end` kwargs. HTTP accepts `since`/`until`. MCP tool `litmus_channels` does NOT accept any time-range param at all (see `query_channels` signature). Single row implies parity that doesn't exist — needs split or MCP column noting "not supported". | `src/litmus/mcp/server.py:514-531` (no start/end/since/until), `src/litmus/data/channels/store.py:475-500`, `src/litmus/api/app.py:509-516` |
| WARNING | L9-19 | MCP examples show only `channel_id`, `session_id`, `last_n`, `max_points` | MCP tool also accepts `project: str | None = None` (project root path). Documented in `reference/api.md:125` but omitted here. Minor but the page reads as the canonical how-to. | `src/litmus/mcp/server.py:514-521` |
| WARNING | L31 | Curl example `?session_id=abc123` (3 chars) | Channel session-id filter matches "first 8 chars of UUID" (the code computes `session_id[:8]` — see store.py:501). 3 chars will silently match nothing or wrong sessions. Use 8 chars to be consistent with L50 comment and HTTP-API truth. | `src/litmus/data/channels/store.py:501` |
| SUGGESTION | L64 | `ChannelClient("grpc://localhost:8815")` — implies 8815 is the default | The literal `grpc://localhost:8815` IS the default value in `client.py:32`, but the actual Flight server picks port 0 (ephemeral) by default and writes the assigned port to `_flight_port`. So a user who runs `ChannelClient()` with no args will rarely hit a real server at 8815 — that constant is misleading. Either drop the explicit URL (so reader sees `ChannelClient()` and the docstring guides them) or note that the location comes from `<data_dir>/channels/_flight_port`. | `src/litmus/data/channels/client.py:32`, `src/litmus/data/channels/_flight_daemon.py:33-38`, `src/litmus/data/channels/flight_manager.py:22` |
| SUGGESTION | L66-71 | "Same query API as ChannelStore" | Mostly true, but `ChannelClient.query()` has kwargs in a different order (`max_points` before `last_n`) and the client query enforces a `?` separator in the ticket (a server-protocol detail). Minor — say "same query parameters" to be precise. | `src/litmus/data/channels/client.py:100-135` |
| VERIFIED | — | 14 claims verified against source: MCP tool name `litmus_channels`, MCP params (`channel_id`, `session_id`, `last_n`, `max_points`), HTTP routes (`GET /api/channels`, `GET /api/channels/{channel_id}`), HTTP params (`session_id`, `last_n`, `max_points`), Python imports (`from litmus.data.channels.store import ChannelStore`, `from litmus.data.channels.client import ChannelClient`), LTTB algorithm name, "first 8 chars of UUID" session-id matching, `ChannelStore.query()` filter-order claim ("session → time → last_n → max_points"), return type PyArrow Table, `ChannelClient.channels()` method | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | L21-35 (HTTP API) | No statement that the litmus server must be running. New reader who copy-pastes `curl http://localhost:8000/api/channels` gets connection refused with no signpost. State the prerequisite ("Run `litmus serve` in another terminal first") and link to wherever that's documented. |
| CRITICAL | L37-71 (Python) | Page does not say how to obtain the **right** `data_dir` — no mention of `resolve_data_dir()`, no mention of project-vs-platformdirs resolution, no mention that the `Path("results/channels")` example is illustrative not canonical. Combined with the accuracy bug, a reader who copies the snippet will silently query an empty store. |
| WARNING | L9-19 (MCP) | No mention of how to list available channel ids before querying. MCP has `litmus_channels(channel_id=...)` but no `list_channels` shown — first-time reader doesn't know what channel ids exist. HTTP shows `curl /api/channels` at L25 for this, but MCP section doesn't direct readers there or to `litmus_schema` or a registry. |
| WARNING | L23-35 | No example of the returned JSON shape. Reader wiring up a dashboard cannot tell what fields the response has (`{channel_id, data: [...]}` per `channels_query`). At minimum show a 5-line truncated response. |
| WARNING | L73-77 | LTTB section says "Use `max_points` when displaying data in charts. For analysis, query without decimation." — but no guidance on what `max_points` value to pick. 500? 2000? Charting libs vary. State a working default (e.g., "500 is enough for a 1000-px wide chart"). |
| WARNING | L92-95 (See also) | No link to the MCP tool reference (`reference/api.md#litmus_channels`) or to the HTTP API reference (`reference/api.md`). For a how-to that runs three transports, the reference for each should be one click away. |
| SUGGESTION | L37 | No note about reading mid-session vs post-session. The `ChannelStore.query()` docstring says "Works mid-session (reads from buffer) and post-session (reads from files)" — this is a real test-engineer concern (can I watch live and re-query history?) and should be surfaced. |
| SUGGESTION | L37-57 | No mention of `Table.to_pylist()` / `Table.to_pandas()` tradeoffs. Example uses `to_pandas()` (line 56) without saying pandas is optional. Test engineers running on minimal installs will hit ImportError. |
| SUGGESTION | L73-77 | LTTB section is the only place tradeoffs are discussed. Add a similar callout on `last_n` vs `start/end` ("use `last_n` for sparklines, `start/end` for zoom windows"). |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| CRITICAL | L37, L62 | First use of `ChannelStore` (L37) and `ChannelClient` (L62) — no reference-doc link exists because there is no `reference/` page for these classes (confirmed: `grep -rn 'ChannelStore' docs/reference/` returns only `connect.md` mentions, not a dedicated reference). The concept page `three-stores.md` is linked but it doesn't document constructor signatures. Either add a `reference/channels.md` or document inline. |
| WARNING | L92-95 (See also) | Missing related-page links: `reference/api.md#litmus_channels` (MCP tool reference), `reference/api.md` HTTP endpoints section, `tutorial/10-live-monitoring.md` (which already back-links to this page at L108), `integration/logging.md` (which back-links from L320). Cross-link relationships should be bidirectional. |
| WARNING | L37 | `[`ChannelStore`](../concepts/three-stores.md)` — the link resolves (file exists, line 23 has `## ChannelStore — Time-Series Data` anchor). But the link uses the bare filename without anchor; the link should target `#channelstore--time-series-data` to land readers on the right section. |
| WARNING | L95 | `[Flight Streaming](../concepts/flight-streaming.md)` link resolves (file exists). But this is the only reference for cross-process access — and the concept page doesn't tell readers *how* to point a `ChannelClient` at the right port (which is dynamic, see accuracy finding on 8815). |
| SUGGESTION | L17, L52, L73 | First mention of "LTTB" at L17 has no link or gloss; L52 has no link; L73 finally defines it. The L17 occurrence should at least say "(see LTTB Decimation below)" or be a Markdown anchor link `#lttb-decimation`. |
| SUGGESTION | L5 | First use of "MCP" — no link to MCP integration how-to (`how-to/mcp-integration.md`) for readers unfamiliar with MCP. |
| SUGGESTION | L37 | First use of "PyArrow Table" (L55-56) — no link to PyArrow docs. External link is optional per audit rules, but a brief note "PyArrow's columnar table — call `.to_pandas()` or `.to_pylist()` to materialize" would help. |
| SUGGESTION | L75 | "LTTB" / "Largest Triangle Three Buckets" — no citation. The source code (`store.py:50-58`) cites Steinarsson 2013; one-line reference would be useful for engineers who want to understand the algorithm. |
