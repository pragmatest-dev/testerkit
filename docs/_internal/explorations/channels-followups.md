# Channels — follow-ups to return to

Parked while we pivot to the files store. Done items are committed; the
open items below are the queue.

## Open

### 1. Channel identity ↔ the instrument's physical channel number/identifier
An instrument has **multiple physical channels**, each addressed by a
number/identifier — DMM ch1/ch2, DAQ ai0/ai1, scope ch1–ch4. Litmus's
channel today is a `channel_id` string + `instrument_role` + `resource`;
there is **no first-class field** for the instrument's physical channel
number. So a multi-channel instrument is only distinguishable by baking the
number into the `channel_id` string.

Decide: should channel identity carry the instrument channel
number/identifier as a first-class field, so the UI can show/select "DMM
channel 2"? This **subsumes** the earlier array-index question — a
multi-channel array sample's index maps to these physical channels:
- **waveform** array → index = intra-capture time (current offset-append
  overlay is correct),
- **multi-channel** array → index = physical channel (append is wrong;
  needs per-channel split + a channel selector + labeling).

### 2. Liveness as MCP tool + HTTP endpoint — blocked on a registry read timeout
Goal: expose `channels_liveness_query` as a `litmus_channels_liveness` MCP
tool + `GET /api/channels/_liveness`. Drafted then **reverted** (not
working): on a real store it 500s. `channel_registry()` → `do_get
__registry__` → `query_registry()` runs `_maybe_scan_disk()` inline, and on a
large store (example-09: 81,500 rows) the inline `_scan_disk` blocks the
Flight `do_get` past the client deadline → `FlightTimedOutError: Deadline
Exceeded`. Works on a small store (repo: 121 rows). **Fix the read first:**
decouple the scan from the registry read (no heavy scan inside it) — do not
just bump the Flight deadline. Then re-wire the tool + endpoint.

### 3. `offset` → `sample_offset` rename (was task #6)
Channel column + index + wire schema + `ChannelSample.offset` + the Half A
ticket field/URI param + the now-surfaced offset in `channels_query` results
+ the chart's `r.get("offset")`. No backcompat; needs a `data/channels`
clear. While here, correct `ChannelIndex._ensure_schema`'s comment: it
claims `CREATE TABLE IF NOT EXISTS` "auto-migrates a new column… no version
bump, no re-ingest", which is false (it keeps the old table) and contradicts
the sanctioned contract — the index is a disposable, regenerable projection,
so the migration on any schema change is "clear `data/channels`", not an
in-place column add.

### 4. Present polymorphic channels (list + detail)
A `channel_id` can have different shapes across sessions (type is locked
within a session, not across). Today last-write-wins hides it. Source from
the non-unique `channel_registry`: list Type cell flags divergence (e.g.
`number +bool`), sparkline scoped to the latest shape; detail page facets by
shape. Present, not prevent.

### 5. Smaller deferred
- Cross-host pid liveness (P4 self-heal is same-host only).
- `StationInfo` event for richer declared station metadata, separate from
  the runtime socket host.
- Tree-wide `event_binding` simplification — the other ~8 live panels still
  mutate elements inside `ui_subscribe` callbacks; converge them onto
  holder+timer (see `live-ui-pattern.md`).

### 6. Show live status of channels on the `/channels` list table
The detail badge derives live from lifecycle events (`ChannelStarted ∧
¬ChannelClosed`, latest-start vs latest-close); the **list** page has no live
column (any liveness it derives is activity-based, not lifecycle). Add a Live
column driven by lifecycle events — the channel twin of the new `/files` live
table. Reuse `LiveBadge` pills + the holder+timer rule.

### 7. Channel tuning consolidation → shared `StreamTuning` (files reuses)
Handoff deferred item 7 — NOT done. Collect the 9 scattered knobs
(`_ChannelSink._FLUSH_ROWS`/`_FLUSH_INTERVAL`, `ChannelStore(flush_threshold=)`,
the **unplumbed** `BufferedIPCWriter.flush_interval`, `_pending_threshold`, push
queue `maxsize=10_000`, `_PUSH_MAX_ROWS`/`_PUSH_MAX_WAIT`, `_SubscriberRing
maxsize=1024`) into one config object, plumb `flush_interval`, surface the
durability pair (`flush_threshold`, `flush_interval`) toward `litmus.yaml`.
**Files-streaming reuses the same object** — make it a shared `StreamTuning`
(channels + files), not channels-only.

## Done (committed)
- Live-UI convergence (P3a–d): `LiveBadge` state+timer, detail page
  async/io_bound + holder/timer, channel values panel, contributor note.
- Channel badge multi-session lifecycle fix (latest-start vs latest-close).
- Offset overlay (scalar + array) on the detail chart; integer offset ticks.
