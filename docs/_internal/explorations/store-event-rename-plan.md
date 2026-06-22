# Store event rename — execution plan (handoff)

Status: ready to execute. Scope is **renames only**. Author: design settled 2026-06-15.

## Goal

Make the store lifecycle event names consistent by applying a fixed set of
identifier renames. **This is a pure rename — no new events, no behavior change.**

## Scope — EXACTLY these renames, nothing else

| Current | New | Kind |
|---|---|---|
| `ChannelClosed` / `"channel.closed"` | `ChannelEnded` / `"channel.ended"` | class + event_type literal |
| `StreamStarted` / `"stream.started"` | `FileStarted` / `"file.started"` | class + event_type literal |
| `StreamEnded` / `"stream.ended"` | `FileEnded` / `"file.ended"` | class + event_type literal |
| `STREAM_EVENTS` | `FILE_EVENTS` | group constant |
| `stream_id` (field/key) | `file_id` | event field + `TYPED_PAYLOAD_COLUMNS` + streaming sink internals (`_stream_id`, `stream_id` property, relay dict key) + catalog usage |

**UNCHANGED — do not touch:** `ChannelStarted` / `"channel.started"` stays as-is.

## NON-GOALS (explicitly out of scope — do NOT add these)

- No `ChannelCheckpoint`, `FileCheckpoint`, or any checkpoint mechanism.
- No `FileCreated` / one-off `file.created` event, no `FileStore.write` emission change.
- No lease / session-keepalive wiring.
- No three-segment event types (`file.stream.*`). The file events are two-segment: `file.started` / `file.ended`.

If you think any of the above is "needed to make the rename consistent," STOP and ask — it is a separate, already-deferred piece of work.

## Preconditions / coordination (READ FIRST — the tree is shared)

Another agent is actively working this same branch and **uses these exact symbols**
(e.g. `tests/test_data/test_stream_checkpoint.py`, `src/litmus/models/data_options.py`,
`src/litmus/data/_duckdb_daemon.py`). Therefore:

1. **Re-grep for the live surface at execution time** — do not trust any frozen file
   list; the set of files referencing these symbols changes under you. Use the grep
   commands in the Verification section to discover the current surface.
2. **Use targeted token edits (Edit `replace_all` per identifier), never `git checkout`** —
   several affected files also contain the other agent's uncommitted work; a checkout
   would clobber it.
3. **Preserve, do not touch:** `_EventSequenceMonitor` (`_runs_duckdb_daemon.py`),
   `SessionRequired` / `require_open_session` (`_state.py`), and the channel lazy-open
   (`_opened`) in `channels/store.py`. Confirm they're intact after your edits.
4. **Before starting, confirm with the human that the other agent is paused** — concurrent
   edits to the same files will collide.

## Method (order matters)

1. **`src/litmus/data/events.py` first** — the definitions. Rename the classes, the
   `event_type` `Literal` values, the `stream_id` field → `file_id`, the
   `"stream_id"` entry in `TYPED_PAYLOAD_COLUMNS` (+ its comment), the `STREAM_EVENTS`
   group → `FILE_EVENTS`, the `CHANNEL_EVENTS` membership (`ChannelClosed`→`ChannelEnded`),
   the `Event` discriminated union, and the "Stream events" section header. No tombstone
   comments (no "renamed from…").
2. **Emitters / source** — `channels/store.py` (emits `ChannelEnded`), `files/streaming.py`
   (`_stream_id`→`_file_id`, `stream_id` property→`file_id`, relay dict key, emits
   `FileStarted`/`FileEnded`), `files/store.py`, `files/catalog.py`, `files/catalog_manager.py`,
   `files.py`, `channels.py`, `connect.py`, `execution/harness.py`, `models/data_options.py`,
   `mcp/tools.py`.
3. **UI** — `ui/components/event_timeline.py`, `ui/components/file_streams.py`,
   `ui/components/channel_values.py`, `ui/pages/files/list.py`, `ui/pages/channels/detail.py`,
   `ui/shared/components.py`. Update any user-visible labels to match.
4. **Ontology** — `src/litmus/ontology/litmus.yaml`.
5. **Generator + generated docs** — update class references in
   `scripts/generate_reference_docs.py`, then regenerate:
   `uv run python scripts/generate_reference_docs.py --all`. **Never hand-edit between the
   `<!-- GENERATED:...:start/end -->` markers** in `docs/reference/data/event-types.md`.
6. **Tests** — update every test referencing the old names (imports, assertions,
   `event_type` string comparisons). Note `tests/test_data/test_stream_checkpoint.py` may
   be the other agent's — coordinate before editing it.
7. **Docs prose** — non-generated pages under `docs/` (concepts, how-to, `_internal/explorations`).
   Inline name updates only; no tombstones.

## Verification gate (must pass before reporting done)

```
# ZERO stale identifiers anywhere:
grep -rn "ChannelClosed\|StreamStarted\|StreamEnded\|STREAM_EVENTS\|channel\.closed\|stream\.started\|stream\.ended" src tests scripts docs
grep -rn "stream_id" src tests scripts

# New names present in the definitions:
grep -n "ChannelEnded\|FileStarted\|FileEnded\|FILE_EVENTS\|file_id" src/litmus/data/events.py

# ChannelStarted untouched (must still exist):
grep -n "ChannelStarted\|channel\.started" src/litmus/data/events.py

# Preserved other-agent work intact:
grep -n "_EventSequenceMonitor" src/litmus/data/_runs_duckdb_daemon.py
grep -n "SessionRequired" src/litmus/execution/_state.py
grep -n "_opened" src/litmus/data/channels/store.py
```

Then:
```
ruff check src/litmus/data/events.py src/litmus/data/files/ src/litmus/data/channels/store.py
uv run pytest tests/test_data/test_events.py tests/test_data/test_event_store.py tests/test_data/test_filestore_streaming.py tests/test_execution/test_files_stream_verb.py tests/test_instruments/test_channel_lifecycle.py -q
```
Fix root causes of any failure. Never `--no-verify`. Never loosen/skip a test.

## Commit

Do **not** `git commit -a`. Stage only the rename-scoped files (the ones your edits
touched). If a file you edited also contains the other agent's uncommitted work, do
NOT commit it — flag it to the human for coordinated staging instead. Commit in the
foreground, never backgrounded. End the commit message with:

```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

## Report back

- Files changed, grouped (events core / emitters / UI / ontology / generator / tests / docs).
- The grep verification output (zero stale, new present, `ChannelStarted` intact, preserved work intact).
- The pytest + ruff output.
- Any file skipped due to other-agent overlap, and why.
- Anything you were unsure whether to rename — list it, don't guess.
