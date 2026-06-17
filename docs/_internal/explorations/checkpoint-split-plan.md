# Checkpoint split — execution plan

Status: design settled, ready to execute **after** the store-event rename. Scope is a
**structural split + per-store field rename** (not a pure token rename).

## Goal

Split the single cross-store `StreamCheckpoint` into two entity-scoped events —
`ChannelCheckpoint` and `FileCheckpoint` — each carrying its own correctly-named
offset. This completes the entity-named event grammar and dissolves the last
generic `offset`.

## Why

`StreamCheckpoint` is the lone grammatical holdout after the store-event rename:

1. **"Stream" mis-describes the channel case.** A channel written via `write` /
   `write_many` (no streaming) still emits checkpoints for liveness — the event is a
   producer-liveness marker for *any* long-running silent producer, not a streaming-mode
   thing. Calling it `Stream*` is wrong for the non-stream write modes.
2. **It's the only action-named event** once lifecycle events are entity-named
   (`ChannelStarted`/`ChannelEnded`, `FileStarted`/`FileEnded`). Entity-named checkpoints
   complete the grammar: `Channel{Started, Checkpoint, Ended}` / `File{Started, Checkpoint, Ended}`.
3. **It forced a generic `offset`.** A single shared event meant the offset field had to
   stay bare and polymorphic (sample-offset for channels, byte-offset for files). Splitting
   it lets each event name its offset correctly — `ChannelCheckpoint.sample_offset`,
   `FileCheckpoint.byte_offset` — the same per-store scoping as the `sample_offset` /
   `byte_offset` / `event_offset` pattern. (See the `offset → sample_offset` rename.)
4. **It mis-groups under the rename.** `StreamCheckpoint` is in `STREAM_EVENTS`; the
   store-event rename's `STREAM_EVENTS → FILE_EVENTS` would put the cross-store checkpoint
   in `FILE_EVENTS`. The split routes `ChannelCheckpoint → CHANNEL_EVENTS` and
   `FileCheckpoint → FILE_EVENTS` cleanly.

## Dependencies / ordering

- **After** the store-event rename (`store-event-rename-plan.md`) — that plan creates
  `FILE_EVENTS` / `FileStarted` / `FileEnded`; this one slots the file checkpoint beside them.
- The `offset → sample_offset` (channels) and `byte_offset` (files) names already exist, so
  the per-store offset fields land on established names.

## Scope — exactly these changes

| Current | New | Kind |
|---|---|---|
| `StreamCheckpoint` / `"stream.checkpoint"` | `ChannelCheckpoint` / `"channel.checkpoint"` **and** `FileCheckpoint` / `"file.checkpoint"` | split one class into two |
| `StreamCheckpoint.offset` | `ChannelCheckpoint.sample_offset` / `FileCheckpoint.byte_offset` | per-store field rename |
| `STREAM_EVENTS` membership of the checkpoint | `ChannelCheckpoint` ∈ `CHANNEL_EVENTS`; `FileCheckpoint` ∈ `FILE_EVENTS` | group membership |

Both keep `uri: str` (already `channel://` vs `file://`). The cadence config
`StreamTuning.checkpoint_cadence` is shared by both producers and **stays as-is** — it tunes
one liveness cadence for both; it is not a checkpoint event. (If its name bothers us later,
that's a separate, smaller call.)

## NON-GOALS (do not add)

- No change to *when* a checkpoint fires (cadence, `_maybe_checkpoint` logic) — emission
  timing is unchanged; only the class/field/group change.
- No reaper / liveness consumer change — see precondition 2.
- No new fields, no resume mechanism beyond what `offset` already recorded.
- No rename of `StreamTuning` / `checkpoint_cadence`.

## Preconditions (READ FIRST — the tree is shared)

1. **Re-grep the live surface at execution time** — the store-event rename and other work
   move these symbols under you. Discover the current surface with the verification greps.
2. **Confirm no consumer special-cases `stream.checkpoint`.** As of this writing, the session
   reaper uses *generic* max-recency (any durable event renews the session lease), so it needs
   no change — both new events renew the lease exactly as `StreamCheckpoint` did. Re-verify:
   `grep -rn "stream.checkpoint\|StreamCheckpoint" src/litmus/data/_session_reaper.py src/litmus/data/_duckdb_daemon.py src/litmus/data/_runs_duckdb_daemon.py` → expect none. If a special-case has appeared, update it to read both event types.
3. **Use targeted edits, never `git checkout`** — shared tree; confirm the human says the
   concurrent agent is paused before editing shared files.

## Method (order matters)

1. **`src/litmus/data/events.py`** — replace the `StreamCheckpoint` class with `ChannelCheckpoint`
   (`channel.checkpoint`, `uri: str`, `sample_offset: int = 0`) and `FileCheckpoint`
   (`file.checkpoint`, `uri: str`, `byte_offset: int = 0`). Add each to the right group set
   (`CHANNEL_EVENTS` / `FILE_EVENTS`), update the `Event` discriminated union, and the section
   header. No tombstone comments.
2. **Channel emitter** — `data/channels/store.py` `_maybe_checkpoint`: emit
   `ChannelCheckpoint(uri=make_channel_uri(...), sample_offset=offset)` (the channel offset value
   is already in hand; this also retires the last generic `offset=offset` on the channel side).
3. **File emitter** — `data/files/streaming.py`: emit `FileCheckpoint(uri=..., byte_offset=self._byte_offset)`.
   Update `files.py` references.
4. **Ontology** — `src/litmus/ontology/litmus.yaml`: split the `stream.checkpoint` entry into
   `channel.checkpoint` + `file.checkpoint`.
5. **Generator + generated docs** — update class references in `scripts/generate_reference_docs.py`
   if any, then regenerate: `uv run python scripts/generate_reference_docs.py --all`. Never hand-edit
   between the `<!-- GENERATED:...:start/end -->` markers.
6. **Tests** — `tests/test_data/test_stream_checkpoint.py`: split assertions for the two events
   (consider renaming the file `test_checkpoints.py`). Assert `ChannelCheckpoint.sample_offset` and
   `FileCheckpoint.byte_offset`, and that both renew the session lease (liveness).
7. **Docs prose** — `session-foundation.md`, `streaming-media.md`, `data_options.py` docstrings
   (`StreamTuning` doc mentions `StreamStarted`/`StreamEnded`/`StreamCheckpoint`) — inline name
   updates only, no tombstones.

## Verification gate

```
# zero stale checkpoint identifiers:
grep -rn "StreamCheckpoint\|stream\.checkpoint" src tests scripts docs
# new events present + correctly grouped:
grep -n "ChannelCheckpoint\|FileCheckpoint\|channel\.checkpoint\|file\.checkpoint" src/litmus/data/events.py
# per-store offset fields:
grep -n "sample_offset" src/litmus/data/events.py   # ChannelCheckpoint
grep -n "byte_offset"   src/litmus/data/events.py   # FileCheckpoint
# reaper unchanged + still generic:
grep -rn "checkpoint" src/litmus/data/_session_reaper.py   # expect none / generic only
```
Then:
```
uv run ruff check src/ tests/
uv run pyright
uv run pytest tests/test_data/test_checkpoints.py tests/test_data/test_events.py tests/test_data/test_filestore_streaming.py tests/test_instruments/test_channel_lifecycle.py -q
uv run pytest -q   # full suite
```
Fix root causes. Never `--no-verify`; never loosen a test. Regenerate docs if `reference-docs-drift` fails.

## Commit

Stage only the split-scoped files. Foreground commit. End with:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```
