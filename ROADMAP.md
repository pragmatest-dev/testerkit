# Litmus Roadmap

Active backlog (RICE-prioritized) and archive of shipped work. Items
graduate from **Backlog** to **Completed** on merge — never strike
through, just move.

---

## Prioritization

RICE scoring: **R** = reach (runs/users touched), **I** = impact
(0.5/1/2/3), **C** = confidence the work pays off, **E** = effort
in person-weeks. Score = R·I·C / E. Sorted within each release
bucket by score.

### 0.1.0 — required for first real users

Demo-quality coherence, no rough edges. Most of what's needed here
isn't in the backlog — it's in-flight session work (terminology,
design system, viewport-bound tables, browser-local time).
Backlog items that are 0.1.0 gates:

| Item | R | I | C | E | Score |
|---|---|---|---|---|---|
| `response_model=` coverage on FastAPI endpoints | high | 1.5 | 0.9 | 1.0 | high |
| `litmus plan --profile=X` — dry-run profile resolution | medium | 1 | 0.9 | 0.5 | medium |

### 0.2.0 — first adoption push

Things that make Litmus *good* (not just shippable). Sorted by RICE.

| Item | R | I | C | E | Score |
|---|---|---|---|---|---|
| Parquet compaction | medium | 2 | 0.7 | 3.0 | high |
| `ReactiveChart` shared chart primitive | high | 2 | 0.8 | 2.0 | high |
| Limit resolution strategies (expr / lookup / step / callable) | high | 2 | 0.5 | 3.0 | high |
| Capability-aware runnability inference | high | 2 | 0.6 | 2.0 | high |
| Live updates on Events/Channels store pages | medium | 1.5 | 0.6 | 2.0 | medium |
| Consumer-side ref: CLI `--waveform` + report embedding | small | 1 | 0.8 | 0.5 | small |
| Operator-UI store browser (Sessions + Artifacts) | medium | 1.5 | 0.7 | 1.5 | medium |
| Artifact viewer — inline previews + grid | medium | 2 | 0.6 | 2.0 | medium |
| Facet prompt fallback (TTY interactive) | medium | 2 | 0.7 | 1.0 | medium |
| Parametric viewer follow-ups | medium | 1.5 | 0.8 | 1.0 | medium |
| StationType → StationConfig inheritance | medium | 1 | 0.8 | 1.0 | medium |
| SpecQualifier matching scoring | medium | 1.5 | 0.6 | 1.0 | medium |
| Exporter row-level cascade outcomes | medium | 1 | 0.7 | 1.0 | medium |
| CLI fallback for multi-UUT operator prompts | low-med | 1 | 0.7 | 1.0 | medium |
| HTTP support for ImageDialog | small | 0.5 | 0.7 | 0.5 | small |
| Array channel empty-result schema | small | 0.5 | 0.9 | 0.2 | small |
| Runs daemon — record actual `row_count` in `_ingested` | small | 0.5 | 0.9 | 0.2 | small |

### 0.3.x — execution-model / schema / reservation line (recovered 2026-07-09)

The **actual** 0.3 series: data-model-at-rest, schema versioning, multi-site,
instrument reservation, and the identity / builder cleanups. This ladder was
planned in the 2026-07-08 session's task list but never written here — recovered
from the transcripts and made durable. The shipped half is authoritatively
recorded in `CHANGELOG.md`; the full ladder is kept here for sequencing.
**Analytics is NOT 0.3** (it was mislabeled) — see 0.4.0 below.

| Epic | Refs | Status |
|---|---|---|
| **[0.3.0]** Release finish — schema-versioning → main | — | ✅ v0.3.0 |
| **[0.3.0]** Operator-UI bug fixes (Sonnet smoke test) | — | ✅ v0.3.0 |
| **[0.3.0]** AI test-writing surfaces — accuracy + start-simple compliance | #66 | ✅ ~v0.3.1 (`b38470a5`) |
| **[0.3.1]** Schema-versioning hardening + content-addressed index epoch | #47/#53 | ✅ v0.3.1 |
| **[0.3.2]** Instrument reservation / coordinator — step-lease, read-only observe, station-scoped join-on-connect | #11/#12/#18 | 🔶 per-step reserve shipped (v0.3.0); class-container hold on `feat/class-container-instrument-reservation`; coordinator + observe pending. `docs/_internal/explorations/instrument-reservation.md` |
| **[0.3.3]** Multi-site launch UI + live execution view (gantt) | #8/#14 | ⬜ |
| **[0.3.4]** Fixture as a first-class entity | — | ⬜ |
| **[0.3.5]** StepsQuery inputs/outputs field-query surface — query by role+name | #26 | ⬜ |
| **[0.3.6]** Tech-debt / standalone cluster (#34 flaky = do-now) | #34 | ⬜ |
| **[0.3.7]** Observed-entity identity model — read + write single-sourcing | — | ⬜ `docs/_internal/explorations/best-available-identity.md` |
| **[0.3.8]** Builder overhaul — objects correct by construction | — | ⬜ |
| **[0.3.9]** Retry collapse view — first / last / all | — | ⬜ |
| **[0.3.10]** Cross-store parity indexing — events/channels/files → runs-parity | #64 | 🔶 shared DaemonManager fingerprint + `_index_epoch` spine landed; channels + files catalog daemons to parity remain (deferred) |
| **[0.3.11]** Versioning & index resiliency backlog — copy-seed, coexisting daemons, retention, XDG runtime-dir hygiene | #65 | ⬜ deferred backlog. `docs/_internal/explorations/versioning-resiliency-backlog.md` |

### 0.4.0 — analytics release

The deferred quality-metrics work (**was mislabeled 0.3.0** — the 0.3 series is
the execution-model line above). 0.2.0 shipped the Tier-1 set + the cheap
wins (Ppk, RTY, DPMO/DPPM); 0.4.0 is the capability + SPC + cross-tab pass.
Design: `docs/_internal/explorations/0.3.0-analytics-metrics.md`.

| Item | R | I | C | E | Score |
|---|---|---|---|---|---|
| Per-measurement SPC control charts (I-MR / X̄-R + Western Electric rules) | high | 2.5 | 0.7 | 3.0 | high |
| True Cpk / Cp (within-subgroup / I-MR σ) beside the shipped overall-σ Ppk | high | 2 | 0.8 | 1.5 | high |
| Yield cross-tab by station / fixture / operator / shift | high | 2 | 0.8 | 2.0 | high |
| What-if limit analysis — retune limits across history for yield (detailed in Backlog) | medium | 2 | 0.6 | 2.0 | medium |
| Generic `pareto(by=measure)` — measure-agnostic row + cumulative % + Other | medium | 1.5 | 0.7 | 1.5 | medium |
| Per-condition Ppk grouping — extend the homogeneous-population grain (0.2.0 splits by characteristic / pin / limit pair) to also split/pool by condition values (e.g. temperature / vin) via the `measurements_dynamic` EAV join; decide whether `spec_ref` joins the grain | medium | 2 | 0.6 | 2.0 | medium |
| Colab analytics notebook — no-install, notebook-native test-data analytics (Query API + DuckDB + plotting); distinct from the Codespace starter eval | medium | 1.5 | 0.8 | 1.5 | medium |

### Later — strategic but not pre-1.0

Big architectural moves or features that depend on adoption signals
to confirm direction.

| Item | R | I | C | E | Notes |
|---|---|---|---|---|---|
| UI Extensions API — third-party plugins | high | 3 | 0.5 | 8.0 | The OpenHTF-killer pitch; needs early adopters to shape the API |
| Alternate runner wrappers (OpenHTF / unittest / Robot) | high | 3 | 0.4 | 6.0 | Migration story; build *one* (OpenHTF) once we know which mappings stick |
| Split into `pytest-litmus` + `litmus-test` | high | 2 | 0.5 | 4.0 | Packaging refactor; only worth it once API surfaces stabilize |
| Switch-matrix routing | low (specialized) | 2 | 0.5 | 4.0 | Needed by some shops, irrelevant to many |
| Sequences for fine-grained execution control | low | 1 | 0.4 | 4.0 | Was deleted in v1; revisit if pytest's primitives prove insufficient |
| Transports — read side (download / fetch / replay) | medium | 1.5 | 0.6 | 2.0 | Wait until storage layer settles |
| `@litmus.judges` marker | low (escape hatch) | 0.5 | 0.7 | 0.5 | Only if the runtime `pytest_assertion_pass` + measurement-with-limits inference proves insufficient in practice |
| `execution_index` global pre-order counter on step rows | medium | 1 | 0.7 | 0.5 | Today `step_started_at` is enough for total ordering; revisit if hierarchical-sequence reports need a stable pre-order key independent of timing. |
| `litmus export --to delta/iceberg/snowflake` | medium | 1.5 | 0.5 | 2.0 | Built-in transform from Litmus parquets to lakehouse table formats. The 3-line SQL pattern is documented at `docs/integration/lakehouse-import.md`; turn it into a first-class command once a real adopter asks. Don't pre-build. |
| Table-format catalog evaluation (DuckLake / Delta / Iceberg) | medium | 2 | 0.5 | 3.0 | Replace ~3K lines of `_runs_duckdb_daemon.py` ingest sweep + `_materialized` table management with a managed catalog. DuckLake the closest fit (DuckDB-as-catalog, parquet-as-data); Delta/Iceberg as interop options. See `docs/explorations/data-architecture.md` open questions. |
| Pluggable `Materializer` interface (parquet / postgres / snowflake / etc.) | medium | 2 | 0.6 | 4.0 | The runs daemon currently materializes runs to parquet only. Event payload already carries `materializer` + `destination`, so adding a `Materializer` plugin contract is forward-compatible. Wait until a real consumer asks. |
| Runner-invocation capture (full vs ad-hoc) | high | 2 | 0.4 | 3.0 | Demoted: the naive `is_adhoc` (CLI `-k`/`-m`/node-ids) is wrong with profiles — a profile injects `markexpr`/`keyword` via `PYTEST_ADDOPTS`, so profile runs mislabel as ad-hoc and "Full" overclaims (a profile already scopes the test set). Needs a profile-aware model: scope = (profile, selection beyond the profile) + `profile` on `RunStarted`. |

---

## Backlog

### Channels — streaming & liveness

Open follow-ups deferred from the 2026-06 branch work. Source:
`docs/_internal/explorations/channels-real-stream-handoff.md`,
`live-ui-pattern.md`.

_RICE: R=med, I=1.5, C=0.7, E=3w → **med**. Target 0.2.0 for the list
live-column + liveness MCP/HTTP wiring; Later for declare→standing-watch
and physical-channel-id (open design forks)._

- **`/channels` list live-status column.** The detail badge derives live
  from lifecycle events (`ChannelStarted ∧ ¬ChannelEnded`, latest-start vs
  latest-close); the **list** page has no live column (any liveness it
  derives is activity-based, not lifecycle). Add a Live column driven by
  lifecycle events — the channel twin of the `/files` live table. Reuse
  `LiveBadge` pills + the holder+timer rule. (Also tracked under "Live
  updates on Events and Channels store-browser pages" below.)
- **Wire `channels_liveness_query` as an MCP tool + HTTP endpoint** —
  `litmus_channels_liveness` + `GET /api/channels/_liveness`. Drafted then
  reverted: on a large store the inline `_maybe_scan_disk()` inside the
  registry read blocks the Flight `do_get` past the client deadline
  (`FlightTimedOutError`). **Decouple the disk scan from the registry read
  first** (no heavy scan inside the read) — do not bump the deadline — then
  re-wire the tool + endpoint.
- **Declare → standing-watch + active-match (Half B Slice 2).** Has open
  design forks; needs a shaping pass before build.
- **Live-badge "idle while streaming" finding.** Investigate (instrument,
  don't guess) the case where a channel is streaming but the badge reads
  idle.
- **Present polymorphic channels (list + detail).** A `channel_id` can have
  different shapes across sessions (type locked within a session, not
  across). Today last-write-wins hides it. Source from the non-unique
  registry: list Type cell flags divergence, sparkline scoped to the latest
  shape; detail page facets by shape. Present, not prevent.
- **Channel identity ↔ physical channel number.** A multi-channel
  instrument (DMM ch1/ch2, DAQ ai0/ai1, scope ch1–ch4) is only
  distinguishable today by baking the number into the `channel_id` string.
  Decide whether channel identity carries the instrument's physical channel
  number as a first-class field. Subsumes the array-index question
  (waveform array index = intra-capture time; multi-channel array index =
  physical channel, which needs a per-channel split + selector + labels).
- **Stacked cross-session compare on `/channels/{id}`.** Overlay the same
  channel across sessions for comparison.
- **`offset` → `sample_offset` rename** (was task #6). Column + index + wire
  schema + `ChannelSample.offset` + the ticket field/URI param + the
  surfaced offset in `channels_query` results + the chart's `r.get("offset")`.
  No backcompat; needs a `data/channels` clear. (Note: the `offset` →
  `sample_offset` column rename itself landed via `a6c11fc`; verify what
  remains before scoping.)
- **Ephemeral (non-persisted) streams — channel and file.** A creation flag
  ("this will not be persisted" / loss-acceptable, stream-only) that keeps the
  live fan-out tier but skips the persistence tier: no segment/object writes, no
  checkpointing, no at-rest registration, and no `run_id`/`session_id`
  affiliation (which today exists to persist related data). Late subscribers get
  no history; overflow drops (the bounded-queue overflow-gap model already in
  place). Use case: transient interactive UI streams purely for data exchange,
  unaffiliated with run IDs. More important for channels than files. Build on the
  existing live-fan-out abstraction (live = push frames), not a bespoke side path.
- **Auto-associate a stream with the vector (`stream` → `out_<name>`).** Today only
  `observe` stamps the vector's `outputs`; a `stream` opened in a vector scope must be
  manually `observe(sink)`-d to contextualize. Could auto-stamp `out_<name> = channel://|file://`
  once on stream open (URI is stable; no per-sample churn), unifying `stream` with `observe`.
  Discuss-first (see task notes): the lifecycle/checkpoint events carry `run_id`+`uri` but
  NO step/vector coords (would need time-correlation or new fields); open questions —
  multiple streams per vector iteration, async/external streams not in the vector's scope,
  and name collision with an explicit `observe`. Punted from 0.2.0; add later if needed.
### Channels — write-path & relay performance

Source: `docs/_internal/explorations/channels-write-scaling.md`,
`store-perf-writemany-handoff.md`, `data-stores.md`.

_RICE: R=low-med, I=2, C=0.6, E=4w → **low-med**. Target Later (bites at
multi-producer / HIL scale, not single-station). Quick win now: extend the
`litmus benchmark` sweep to `write_many` / `stream` (E≈0.3w) so a regression
like the Phase-5 one is visible next time._

- **Daemon write path (`serve=True`) doesn't scale across writers** — one
  `_index_lock` serializes ingest. Shard parallel ingest per writer.
- **Index as a pull-consumer tailing segments by offset.** (Note:
  `pyarrow.dataset` was ruled OUT for this; pull-by-offset is the path.)
- **Batched-relay fan-out** to raise the ~4k/s live-relay ceiling.
- **`litmus benchmark` concurrent sweep covers only `channels.write`** —
  extend to `write_many` / `stream`.
- **R4 backend-swap proof** (Redis / S3) for the channels store.
- **Streaming-relay convergence (broken contract).** Channels and files
  duplicate the producer/consumer relay (bounded queue + drop-oldest
  overflow + gap count + drain-coalesce); `files/catalog_manager._FrameRelay`
  *mirrors* the channel push relay instead of reusing it. Extract ONE shared
  relay component; converge both. Daemons may stay separate; the
  optimization must not be duplicated.

### FileStore — streaming, atomicity, perf

Large/blocking item per the diaries — the next store after channels.
Source: `docs/_internal/explorations/data-stores.md`,
`streaming-media.md`, `streaming-unification.md`,
`data-store-backends.md`.

_RICE: R=med, I=2.5, C=0.7, E=5w → **high**. Target 0.2.0 — the headline
next store after channels. Atomicity F1/F2 is a correctness sub-item; the
warm index + real Range removes today's O(days) `rglob` read path._

- **Files-streaming as the next store** — segment-objects + manifest, S3
  has no append. Live = push frames (bounded queue, signal overflow gap);
  history = warm index / at-rest object; persist = local append → ONE
  immutable object on close. PUT = new record per call; STREAM = append one
  record. `raw`/`jsonl` byte-drop vs `tdms`/`h5` boundary-rejoin.
- **Files-streaming perf gate is `@skip`'d and distrusted — re-measure.**
  (Memory bans flaky/skip markers; this gate must be made trustworthy, not
  carried skipped.)
- **FileStore atomicity (F1 / F2).** Temp + rename; emit the index row only
  after durability.
- **Files warm index + real HTTP Range (F3).** Today the read path is an
  O(days) `rglob`.
- **EventStore dual-write is unmeasured** — benchmark it.

### Streaming media (after files-streaming)

Sequenced after files-streaming. Source:
`docs/_internal/explorations/streaming-media.md`.

_RICE: R=low-med, I=2, C=0.5, E=4w → **med**. Target Later — gated on
FileStore streaming landing first; encode-bound, narrower audience
(video/audio capture)._

- **Media codec / muxer formats** — mp4 via PyAV, wav / flac.
- **Container rejoin / fragment-boundary checkpoints.**
- **Flight → HTTP fMP4 / HLS browser bridge.**
- **Per-media-format benchmark.**

### Store federation & retention remainder

Most of the 2026-06 federation/retention sweep shipped (reference-aware
channel + file retention #262/#272, `litmus data import` #271,
promote-carries-refs #269, dangling-ref resilience #263). Remaining open.
Source: `docs/_internal/explorations/data-stores.md`,
`data-store-backends.md`, `data-store-unification-invariants.md`.

_RICE: R=med, I=1.5, C=0.7, E=2.5w → **med**. Target 0.2.0 for run
seal/export + the `materialize` boundary fix; Later for the layout reorg
(v0.3.0) and req-6 (no real remote server yet — don't ship dead env vars)._

- **Run seal / export bundle.** A `litmus data` verb that seals a run and
  exports it (with its referenced channel/file data) as a portable bundle.
  `litmus data import` is the inbound half; the outbound bundle isn't built.
- **`materialize` globs channel `.arrow`** — store-boundary violation; it
  reads channel segments through an ephemeral non-indexed `ChannelStore`
  instead of the channels daemon API. (Gated on the #262 ref-vs-copy
  decision, which has since landed — re-scope.)
- **Cross-store retention coordination.**
- **req-6 serving-tier daemon-location swap.** The remote-daemon-location
  hook is proven (1 helper + ~4 one-line hooks + a test add) but not
  shipped — deferred until a real server exists (don't ship dead env vars).
  Recipe recorded at `docs/_internal/explorations` (req6 swap recipe).
- **Session-first layout reorg** (v0.3.0).
### Session / liveness foundation remainder

Session core P1–P4 landed on `spike/session-overhaul` (will + spine-only
reaper, terminal-fence finality). The remainder is below. Source:
`docs/_internal/explorations/session-foundation.md`.

_RICE: R=med, I=1.5, C=0.7, E=4w → **med**. Target 0.2.0 for P8 liveness
projection + `StationInfo` (#35) — they unblock the live UIs; Later for the
P6 `event_log` optional→required flip (~51 sites, internal) and the P7
rename (high churn, low user impact)._

- **P5 — envelope-naming discipline.** Per-writer gap detection landed
  (`def605f`); the envelope-naming half is pending.
- **P6 remainder.** The session-less `ChannelIndex` reader split landed
  (`f15a06b`); still open: flip `event_log` from optional → required (~51
  sites), add a first-class session-less reader entry point, and the
  public "Store" naming pass.
- **P7 — rename `StationConnection` → `Session`** (flagged suspect:
  collides with `SessionScope`).
- **P8 — liveness projection → UI / MCP / HTTP.**
- **Multi-participant join / leave emitters.**
- **Auto-root permissive session creation** (strict → permissive is
  additive; deferred until the foundation is solid).
- **Client-side `SessionExpired` typed exception** (the seal IS
  `SessionEnded`; a typed client exception is the optional follow-on).
- **Cross-host pid liveness** (P4 self-heal is same-host only).
- **`StationInfo` event / auto-capture station info at session creation
  (#35)** — stamp richer station context (instruments + roles/resources,
  fixture, calibration/asset refs, config snapshot) onto `SessionStarted`,
  degrading gracefully when a field is unavailable.
- **Hardware safe-state on abrupt death (#36).**

### Emission-grammar remainder

Part of the uniform `{Entity}Started` / `{Entity}Ended` grammar landed
this branch (`ChannelClosed → ChannelEnded`, `StreamStarted/Ended →
FileStarted/FileEnded`, `StreamCheckpoint → ChannelCheckpoint +
FileCheckpoint`). Remaining inconsistencies. Source:
`docs/_internal/explorations/session-foundation.md`,
`store-event-rename-plan.md`, `checkpoint-split-plan.md`.

_RICE: R=low, I=1, C=0.9, E=1w → **small-med**. Target 0.2.0 — cheap, and
finishes the uniform `{Entity}Started/Ended` grammar. The missing one-shot
file-write event is the meatier sub-item (a one-shot PUT emits nothing today)._

- **`SlotCompleted` → `SlotEnded`** (the lone non-`Ended` lifecycle event).
- **`RouteOpened` / `RouteClosed`** + **`SyncArrived` / `SyncRelease`** —
  align to the verb-keyed tense.
- **Missing one-shot file-write event.** A one-shot file PUT emits nothing
  today; add a discrete file-write event (1 event per discrete write, never
  per-sample).

### Run materialization — failure handling & recovery (#37)

When `materialize_run_to_parquet` raises (e.g. a mixed-type `out_*` column),
the runs daemon swallows it to a `logger.warning` and returns — the run
**silently vanishes from `/results`**. Two failures: it's invisible (a green
CI loses runs with no signal), and the run stays in the "unmaterialized" set
forever, so the daemon **re-replays + re-fails it on every launch**. The
unmaterialized auto-replay (`events_for_unmaterialized_runs`) is only bounded
*because it assumes runs eventually materialize* — a persistent failure breaks
that bound, turning the replay set into unbounded, growing per-launch cost.

The data isn't lost, though: parquet is a *derived projection*; the events
are the source of truth and are retained. So a failure is **recoverable** —
fix the bug, upgrade, re-materialize, and the runs reappear retroactively.
Making that real is a small lifecycle sub-system (the pieces are coupled —
the marker alone fixes neither the cost nor recovery):

- **`RunMaterializationFailed`** — a *terminal* event. Run states become
  in-flight | materialized | failed. Durable + queryable: "deferred, not lost."
- **Exclude failed from the unmaterialized auto-replay** — replay becomes
  `RunStarted AND NOT (RunMaterialized OR RunMaterializationFailed)`, so a
  failing-bug class no longer re-replays every daemon launch. This is the
  marker's operational point — emitting it *without* excluding doesn't fix
  the per-launch cost.
- **`litmus data rematerialize [--run <id> | --all-failed]`** — clears the
  marker, replays the cohort, re-materializes. Run after upgrading with the
  fix. Surface a count ("N runs failed to materialize → run
  `litmus data rematerialize`").
- **Retention must pin un-materialized / failed cohorts** — `prune_date_dirs`
  is date-blind today; it must skip date-dirs holding not-yet-materialized
  runs, mirroring reference-aware channel retention. (The `RunMaterialized`
  docstring already *claims* retention is materialization-gated, but the code
  isn't — close that gap, else a recoverable run's events get pruned before
  the fix lands.)

Independent of the JSON redesign below (that fixes the common *cause*; this
handles the failure *mode* for any cause) and profile-independent — safe to
build now, and it's the safety net that makes the breaking redesign
recoverable.

_RICE: R=med, I=2.5, C=0.7, E=1.5w → **high**. Target 0.2.0 — build alongside
or ahead of the redesign as its recovery net._

### Measurement-storage redesign — nested-struct at-rest + EAV projection (#37 / #38)

**Large + blocking; sequenced after the session overhaul + files branch.**
Design + phased plan: `docs/_internal/explorations/measurement-storage-eav.md`.
Benches: `scripts/bench_measurement_storage.py`, `scripts/bench_at_rest_encoding.py`.

_RICE: R=high, I=3, C=0.7, E=4w → **high**. Target 0.2.0 — today it
**silently drops runs** (mixed-type column fails materialization on a green
CI = data loss); the cause fix is below, the recovery net is the Run
materialization item above._

Today `out_*` / `in_*` are wide dynamically-typed columns: mixed types in one
column raise → materialize swallows → run **silently dropped**; across files
`union_by_name` flips a column to VARCHAR corpus-wide (`1.5` → `'1.5'`); distinct
names explode to tens of thousands of columns; `int` collapses to `float64`.

What needs to land:

- **At-rest:** store `in` / `out` / `custom` as one nested **`LIST<STRUCT<name,
  kind, value_int, value_double, value_bool, value_text, value_json, unit?>>`**
  column each — typed lanes, `kind` reusing `observation_kind()`. Benched
  smallest on disk, fastest rebuild (native `UNNEST`), lossless incl. `int`,
  and the most portable nested shape (Dremel-native Parquet; `ARRAY<STRUCT>` in
  BigQuery/Spark, `FLATTEN` in Snowflake). Chosen over VARIANT (6% larger, 2.4×
  slower rebuild, immature reader) and JSON (2.3× larger). `unit` slot reserved,
  not plumbed.
- **Projection:** runs-daemon index `UNNEST`s the nested column into a LONG/EAV
  table (`run_id, step_index, side, name, kind, value_* lanes`), indexed on
  `name`; query API reads the long table, lane selected by the query's type
  expectation. Numeric query on a lane runs at clean-typed speed (~0.8ms); mixed
  types never coerce (numbers/strings in different lanes → no VARCHAR flip).
- **Enum drop-downs** stay on the maintained enum index (distinct values +
  counts per name) — encoding-independent, O(distinct), ~0.2ms flat.
- Failure handling + recovery is its own item (**Run materialization —
  failure handling & recovery**, above) — the safety net that makes this
  breaking change recoverable.
- Consolidate the ~14 wide instrument fields (`step_instruments_*` parallel
  array columns, `_INSTR_ARRAY_TYPES`) into the same nested representation —
  same wide-column smell, same swap-readiness win.

0.2.0-breaking (wipe data, no backcompat). Spiked on `spike/variant-at-rest-eav`.

### Consumer SDK & live API surface

Source: `docs/_internal/explorations/data-stores.md`,
`streaming-unification.md`.

_RICE: R=med, I=2, C=0.6, E=3w → **med-high**. Target 0.2.0 for the
`litmus.live` subscribe/deref SDK (the external-consumer + AI adoption
surface); v0.3.0 for channels/files as test inputs._

- **Consumer SDK — `litmus.live`** (`subscribe_events` / `subscribe_channel`
  / `subscribe_file` / `run_live`, plus deref). The subscribe-and-deref
  surface for external consumers (build item 20).
- **Channels + files as test INPUTS** (v0.3.0) — read live channel / file
  data into a running test, not just write it.
- **`observer.read` → `record_read` rename.**

### Long-term store / transport / sync

Source: `docs/_internal/explorations/data-stores.md`,
`streaming-media.md`.

_RICE: R=low, I=1.5, C=0.5, E=high → **low**. Target Later — all explicitly
long-term / symptom-driven (shared-mem transport already deferred after its
PoC)._

- **Local shared-memory transport** (build item 22) — DEFERRED; revisit on
  symptoms.
- **Per-store attribute indexes (L1).**
- **Frame-accurate video ↔ measurement sync** — needs a frame-index event
  reintroduced.
- **Materialize-on-prune output format** (`.arrow` → `.parquet` / `.npz`).

### UI — live-panel convergence

Source: `docs/_internal/explorations/live-ui-pattern.md`.

_RICE: R=internal, I=1, C=0.8, E=2w → **low-med**. Target Later — tech debt,
no user-visible change; do opportunistically as each panel is touched._

Tree-wide `event_binding` → holder+timer convergence. The channel detail
page, channel values panel, and `LiveBadge` are converged; the other ~8
live panels (`event_timeline`, `instrument_activity`, `session_table`,
`file_streams`, `results/detail`, `metrics`, `explore`, `results/list`)
still mutate elements inside `ui_subscribe` callbacks via the loop
marshalling. Converge them onto holder+timer, then drop the render-path
marshalling. Deliberately deferred to avoid changing every live panel at
once.

### Exporter conformance follow-ups

Source: `docs/_internal/explorations/exporter-conformance-audit.md`.

_RICE: R=low-med, I=1, C=0.9, E=0.5w → **small-med**. Target 0.2.0 — cheap
export-format correctness for STDF/CSV/TDMS consumers._

- **STDF conformance** — `MRR.FINISH_T`, `PARM_FLG`.
- **CSV NaN / Inf as strings + TDMS dtype** handling.

### Docs sweeps

Source: session/channels diaries.

_RICE: R=med, I=1, C=0.9, E=1w → **med**. Target 0.1.0 for the verb prose
sweep (published docs must match the shipped API); 0.2.0 for items 25–30._

- **Prose-docs + skills-template verb sweep** — `logger.measure` → `measure`,
  `logger` → `RunScope` across user-facing pages and skill templates.
- **v0.2.0 docs items 25–30** — 5 stale operator-UI pages, uuts / profiles
  pages, the four-store model.

### Example portability — copy-out + `litmus init --from-example`

Make the bundled examples something a user can **get and run** after installing
Litmus, bound to their installed `litmus-test` and with data isolated from theirs.
Design: `docs/_internal/explorations/examples-portability.md`.

_RICE: R=med, I=2, C=0.7, E=1.5w → **med-high**. Target 0.2.0 (adoption /
getting-started). The relocate-to-root prerequisite is do-now-cheap and unblocks
"copy an example from the repo and it runs with pip or uv"; the command + wheel
bundling is the larger half._

- **Relocate uv source to the workspace root** (prerequisite, small) — move
  `litmus-test = { workspace = true }` from the 11 example tomls up into the root
  `pyproject.toml`. Verified: root sources propagate to members, so example tomls
  become clean PEP 621 (bind to the user's installed `litmus-test`) while in-repo
  `uv sync` still resolves examples to local HEAD (`editable = "."`).
- **Bundle examples into the wheel** — they ship in the sdist but not the wheel
  today; needed for `init --from-example` to pull from a `pip install`ed package.
- **`litmus init <name> --from-example <id>`** — scaffold an example as a fresh
  standalone project the user names.
- **`.examples/<id>/` mode** (`litmus pull-example`, name TBD) — the *only*
  sanctioned in-project placement; dot-dir, so hidden from the user's pytest
  collection and data, isolated by the example's own `litmus.yaml`.
- **Project-aware guard** — refuse to scaffold over an existing project root
  (resolve via `_find_project_config`); no silent merges. The blessed in-project
  path is `.examples/` only.
- **Cleanup** — delete the `examples/05-product-spec/` orphan (verboten "product"
  term; stale product→part leftover) and refresh `examples/README.md` (advertises
  "Seven" but 08–11 exist).

### Test audit — find brittle / implementation-coupled tests

Sweep the existing test suite for patterns that test the *shape* of
an implementation rather than the *behavior* an operator or
end-user would observe. Each finding is a candidate to either rewrite
behavior-first or delete.

What "brittle" looks like:

- Tests that import private helpers (``_foo``-prefixed) and assert on
  their internal return shapes instead of going through the public
  API the rest of the system uses.
- Tests that read/write parquet directly, poke ContextVars, or check
  internal in-memory dicts when an equivalent ``RunsQuery`` /
  ``StepsQuery`` / HTTP route would exercise the same flow.
- Tests that recreate the production logic in fixtures (helper
  functions that mirror what the production code does), making the
  test pass when the helper agrees with itself rather than when the
  system behaves correctly.
- Tests that pin specific exception types, log messages, or column
  orders without reason — making refactors loud without catching
  real regressions.
- Tests asserting on outcomes derived through several layers of
  knowledge of how outcomes flow internally, rather than just
  invoking pytest and reading the run row through the same surface
  the UI uses.

Goal posture: a test should look like *"someone runs this command, then
inspects the recorded result through the public API and asserts on
the observable outcome."* Minimal new logic, minimal coupling to
internals.

Deliverable: a checklist (or follow-up tickets) of specific files /
test classes that need rewrites, plus the rewrite for the worst
offenders. Establish a "behavior-first" pattern others can copy when
adding new tests.

### Runner-invocation capture — distinguish full sweeps from ad-hoc subsets

> **Demoted to Later — design unsettled.** A first cut derived `is_adhoc`
> from CLI `-k`/`-m`/node-ids alone, but **profiles** compose a
> `markexpr`/`keyword` (injected via `PYTEST_ADDOPTS`), so a normal profile
> run (`--test-phase=validation`) mislabels as ad-hoc, and "Full" overclaims
> because a profile already scopes the test set. Corrected model: scope =
> **(active profile, selection beyond the profile)**; also stamp `profile`
> onto `RunStarted` (today it's only on `TestRun` — a materialize-from-events
> drift gap). Rework around profile-awareness before building.

The runs table records *what* was collected (every step that ran)
but not *how* the runner was invoked. Two runs with identical
collected sets but different intent — a production sweep vs a
debug-by-node-id cherry-pick — look indistinguishable in the
record. This breaks several real workflows:

- **Yield analytics** double-count ad-hoc reruns of failures alongside
  production runs, biasing first-pass yield.
- **Triage** can't filter "show me only the runs that exercised the
  full suite" from a results page that mixes everything.
- **Audit** can't tell whether a passing run was a comprehensive
  qualification or a single-test smoke-check.

Pytest exposes everything we need on ``config`` /
``config.option`` / ``config.invocation_params``:

| Field | Captures |
|---|---|
| ``config.invocation_params.args`` | Literal positional args (paths, node-ids the user typed) |
| ``config.invocation_params.dir`` | CWD pytest ran from |
| ``config.option.keyword`` | ``-k expression`` |
| ``config.option.markexpr`` | ``-m expression`` |
| ``config.option.exitfirst`` | ``-x`` |
| ``config.option.maxfail`` | ``--maxfail=N`` |
| ``config.option.lf`` / ``ff`` | ``--last-failed`` / ``--failed-first`` |
| ``config.getini("addopts")`` | Sticky options from pytest.ini / pyproject.toml |
| ``config.pluginmanager.list_plugin_distinfo()`` | Active plugins (audit trail) |

New persistent shape on ``TestRun``:

```python
class RunnerInvocation(BaseModel):
    argv: list[str] = []
    cwd: str | None = None
    keyword_filter: str | None = None
    marker_filter: str | None = None
    exit_first: bool = False
    maxfail: int | None = None
    last_failed: bool = False
    failed_first: bool = False
    addopts: list[str] = []
    collected_count: int = 0
    deselected_count: int = 0
    pytest_version: str | None = None
    is_adhoc: bool = False  # derived: argv has node-ids OR -k/-m set

# TestRun
runner_invocation: RunnerInvocation = Field(default_factory=RunnerInvocation)
```

Capture site: ``pytest_sessionstart`` — read once, attach to
``logger.test_run`` before any test runs. Persists with the run via
the existing parquet schema (the field becomes a JSON column or a
dedicated set of columns on the steps sidecar — TBD).

Display:

- **/results table** — a small chip column "Scope" with values
  ``Full`` / ``Filtered`` / ``Selected``, colored. At-a-glance scan
  separates production sweeps from triage runs.
- **Run-detail "Invocation" card** — full literal ``argv``, active
  filters, plugin list, collected/deselected counts.
- **Filter on /results** — facet by Scope so analytics surfaces can
  exclude ad-hoc reruns from yield calculations.

Out-of-scope for this entry but likely follow-up: similar capture
for non-pytest runners (OpenHTF, ``with conn:``) once the runner-
adapter shape is settled.

### `@litmus.judges` marker — explicit verdict-intent override

The runner's pass-vs-done decision for a step that ends without an
exception or a measurement-level verdict relies on a static AST
scan of the test's module: any `assert` or `limit*=` kwarg in any
function in that module marks the test as "judging", which means a
clean pass becomes `PASSED`. Otherwise (no judgment signal in the
module) a clean pass becomes `DONE` — the recorded-but-unjudged
semantic.

The AST scan handles the common cases:
- bare asserts in the test body or a same-module helper
- `logger.measure(..., limit=...)` anywhere in the same module
- Litmus wrappers that internally record limits — the cascade
  from the measurement layer surfaces the real verdict regardless

The gap: a test that delegates judgment to a **cross-module**
helper. AST static analysis can't follow imports cheaply or
reliably; we'd over-stamp `DONE` for what's actually a judging
test.

The escape hatch:

```python
@litmus.judges
def test_with_imported_helper(measure):
    _check_thing(measure)  # asserts live in another module
```

`@litmus.judges` flips the inference to "this test makes a verdict";
clean exit → `PASSED`, no AST guessing. The complementary
`@litmus.records_only` would force `DONE` for a setup-style step
that explicitly opts out of a verdict even when its module has
asserts elsewhere.

Both markers are tiny: one entry in the marker registry, a check
in `_stamp_step_from_call_outcome` (`hooks.py:642`-ish) ahead of
the AST cache lookup, and the corresponding rows in
`tests/test_pytest_plugin/test_outcome_inference.py`.

This is what makes Litmus's combination of pytest's organic style
and OpenHTF's typed verdict semantics actually robust — pytest
gives you fixtures / parametrize / conftest / plugins; OpenHTF's
declarative-intent story is recovered via these two markers when
the AST inference can't reach.

### UI Extensions API — third-party Python plugins ship as native UI

The "Litmus is Python-only" pitch has a hidden second beat: a test
author writing Python should get a **native UI extension surface**
for free. They `pip install my_power_dashboard` and a new page
shows up in the operator UI — no JS toolchain, no separate build,
no bundling, no API to version. Same Pydantic models they already
use in their tests.

This is the feature that justifies NiceGUI as a strategic choice
rather than a tactical one: SPAs (React / Next / Vue) cannot do
this without iframe embedding or bundle federation, and other
Python UI frameworks (Streamlit, Dash, Reflex) can't do it either
because their plugin models require JS or compile steps.

What needs to land:

- **Extension contract** — small set of decorators that register
  contribution points:
  - `@register_page(path, icon, section, label)` — full page on
    a new route, picked up by the sidebar
  - `@register_run_tab(label, icon, predicate)` — adds a tab to
    `/results/{run_id}`; predicate decides whether to show
  - `@register_dashboard_card(order)` — card on `/`
  - `@register_results_column(name, render_fn)` — extra column
    in run lists
  - Start narrow (page + run_tab); widen on demand.
- **Discovery via entry points** — `[project.entry-points."litmus.ui_extensions"]`
  in the extension's `pyproject.toml`. Litmus walks the group at
  server startup and imports each module; registration happens via
  decorator side-effects, same as pytest plugins.
- **Versioning handshake** — extension declares the Litmus minor
  version it was built against; mismatch → warning + load anyway,
  link to migration notes.
- **Theme helpers** — surface the small set of consistent UI
  primitives (`info_field`, `metric_card`, `format_datetime`) so
  most extensions don't reach for raw HTML / CSS and the visual
  language stays coherent.
- **Reusable chart primitive** — see "Shared chart primitive"
  below; extensions need this so they don't re-invent zoom-refetch /
  decimation / debounce per page.
- **Process isolation note** — extensions share the uvicorn process.
  Document the rule: heavy work in `asyncio.to_thread(...)`. Lift to
  subprocess only if a real misbehaving extension shows up.

Decision points: which contribution points ship in v1 (page +
tab is enough; resist the urge to ship all five at once); how
strict to be on extension API stability (semver minor for breaking
changes feels right); whether to bundle a starter `litmus-extension-template`
repo so authors get a working example without cargo-culting from
the main repo.

### Shared chart primitive — `ReactiveChart` for zoom-refetch and decimation

The parametric viewer (`/explore`) and channel detail page
(`/channels/{id}`) both need:

- Initial render with optional decimation when row count exceeds
  a threshold (LTTB for ~10k rows; consider Datashader-via-`ui.html`
  for ~1M+)
- Debounced `dataZoom` listener that re-queries on the new range
- Cancel-in-flight via generation counter so a slow query that
  started before the latest zoom doesn't overwrite the current
  view with stale data
- "Don't re-fetch unless the window changed materially" guard
  (≥95% overlap → keep cached set) to stop wheel-jitter

Today both pages either hard-`LIMIT` (parametric) or apply LTTB
once at load time (channel detail) — no zoom-refetch anywhere.
With the UI Extensions API on the horizon, this primitive becomes
load-bearing: every third-party page that plots engineering data
needs the same machinery, and we don't want each extension
reimplementing debounce + cancel + decimation differently.

What needs to land:

- `litmus.ui.shared.reactive_chart.ReactiveChart` taking
  `query=Callable[[XRange], list[Row]]`, `decimate=...`,
  `debounce_ms=...`, `chart_type=...`. Wraps `ui.echart` and
  manages the zoom listener internally.
- Move `_lttb_indices` from `data/channels/store.py:51` into a
  shared `data/_lttb.py` so both `ChannelStore` and `ReactiveChart`
  pull from the same place.
- Migrate `/explore` and `/channels/{id}` onto it (proves the
  primitive on real pages before extensions land).
- Datashader-via-`ui.html` as an optional render mode for the
  big-data case; selected automatically when row count crosses a
  threshold.

### Parquet compaction — consolidate per-run files into fewer larger ones

Post-0.1.0. Tables (``runs`` / ``steps``) already solve hot-path
query cost; compaction is the right answer for the ``measurements``
view at 10k+ runs but is not gating the 0.1.0 cut.

Today every run writes its own ``{timestamp}_{serial}.parquet`` (and
companion ``_steps.parquet``). For interactive UI hot paths
(``runs`` / ``steps`` queries) we precompute tables in the runs
daemon — bounded query cost regardless of file count. But the
``measurements`` view and any analytics that read raw rows hit the
parquet glob directly: every query opens every file's footer
(~80μs/file), giving a 1k-files = 80ms baseline that scales linearly.

Compaction job (background sweep, daily / weekly):
- Group "completed" runs (older than some grace window so streaming
  writes have finished) by some bucket — date, part, station
- Read all parquets in a bucket, write a single combined parquet,
  attach provenance metadata
- Same for ``_steps.parquet`` sidecars

**Strategy: provenance-tracked supersedes (not blind delete).**
Each compacted parquet embeds the source-file list in its parquet
file metadata. The daemon's ``_ingested`` ledger records the
"compacted X supersedes [Y, Z]" relationship. The ``measurements``
view filters out rows from any source file marked ``superseded`` so
the result is deduplicated even when both forms are present on
disk.

Why provenance > blind-delete: re-uploads (S3 resync, restored
backups, sync-from-archive) are realistic in field deployments.
Compacting and deleting originals locally doesn't help if a sync
brings them back; the ledger is the only mechanism that survives.

**Delete rules** (lifecycle tiers, all configurable):
1. ``ok`` — just-written, original
2. ``superseded`` — a compacted parent exists; row data filtered
   from ``measurements`` view, file kept on disk for grace window
3. ``hard-deleted`` — source files removed after grace window
   expires; cascade-delete (already implemented for vanished files)
   removes any stale ledger rows

Grace window default: keep originals for 30 days post-compaction.

**Open design points:**
- **Dedup race**: if a re-sync re-uploads originals AFTER hard-delete,
  daemon sees them as "new" again. Ledger must record the
  superseded-ness durably (separate ``_compaction_log`` table) so
  re-uploaded originals can be re-marked without re-ingesting their
  rows.
- **Bucket granularity**: date is the obvious default (keeps queries
  bounded by date range). Cross-day buckets defeat date-pruning.
- **Cross-schema compaction**: different ``in_*`` / ``out_*``
  columns from different tests would force ``union_by_name`` writes
  with mostly-null columns — defeats the size win. Likely
  within-schema only (group by test signature).
- **The view's ``filename`` column**: some queries probably depend
  on it for "which file did this row come from". Compaction breaks
  that — need to either preserve original filename as a per-row
  column at compaction time, or accept that ``filename`` becomes
  the compacted filename.
- **Conflict-free rewrite during ongoing test runs**: only compact
  files older than the grace window so streaming writes have
  finished.
- **Cluster awareness**: in multi-station deployments, who runs
  compaction? Single coordinator or per-station with leader
  election?

This is the right scaling answer for the ``measurements`` view at
10k+ runs. ``runs`` and ``steps`` perf is already solved by the
precomputed tables.

### Capability-aware station/test runnability inference

Today's catalog integration in discovery (`cli.py:342-358`) reads only
`entry.type` from a catalog entry to pick a default role name. The
catalog has rich capability data (signals / conditions / accuracy
bands per `MeasurementFunction`) that's never queried by the
runnability path.

The full chain that *should* close the loop:

```
test consumes fixture `dmm`
  → fixture wired by station_type "production_bench"
    → station_type declares it needs role `dmm` with type DMM
      → catalog defines: "Keithley 34461A measures dc_voltage / dc_current"
        → discovery finds Keithley 34461A at GPIB::1
          → ✓ this station can run that test
```

Build the inference layer on top of the schema landed in the
profile-binds-station_type+fixture work:

- Walk a test's used fixtures → derive instrument-role requirements.
- Walk station_type's declared roles → derive instrument-type
  requirements.
- Match against catalog capabilities (`signals.dc_voltage`,
  `conditions.range`, etc.).
- CLI: `litmus check --test=<test_id>` returns "can this station run
  it" + missing-role explanations. Optionally `--all-stations` to
  list every station_type that could run the test.

**Why:** the data is all there (capability schema, station_type,
fixture station_types, catalog by manufacturer+model); the inference
just isn't wired. Closes the gap where an operator with a partial
bench can't tell which tests they can actually run today.

### StationType → StationConfig inheritance

Today a concrete `StationConfig` declaring `station_type:
production_bench` must still redeclare `type:` and `driver:` for
every instrument role — there's no inheritance from the
`StationType` template. Verbose for users with a fleet of
identically-typed benches.

Add inheritance: when `StationConfig.station_type` is set, instrument
`type:` and `driver:` come from the `StationType`'s `instruments:`
dict; the concrete YAML only carries `resource:` + per-instrument
overrides. Concrete station YAMLs shrink to:

```yaml
id: bench_07
name: Bench 7
station_type: production_bench
instruments:
  dmm:  {resource: GPIB::1::INSTR}    # type/driver inherited
  psu:  {resource: GPIB::2::INSTR}
  scope: {resource: GPIB::3::INSTR}
```

Pure ergonomics; doesn't change runtime behavior. Implementation:
extend the StationConfig YAML loader to merge `StationType.instruments`
into `StationConfig.instruments` when the type is loadable.

**Why:** "constrained first, open later" — the schema we shipped
requires duplicating type+driver per role, which is fine for one
bench but tedious for a fleet. Adopters with multi-bench setups
will hit this within a week of the multi-station plan landing.

### `litmus plan --profile=X` — dry-run what a profile resolves to

Profiles declaratively override vectors, limits, markers/facets, and
addopts. Today the only way to see what a profile *actually does* to a
given test suite is to run it.

A `litmus plan` subcommand would shell out to `pytest --collect-only`
under the given profile/station, then annotate each collected node
with:

- Vectors matrix (base vs profile override)
- Resolved limits per measurement label (base vs profile override,
  band match for condition-indexed limits)
- Active facets / spec / connections / markers
- Effective addopts

Implementation constraint: must **share** the plugin's resolution
helpers (`_resolve_entry`, `resolve_test_connections`, etc.), not fork them
— otherwise plan output drifts from actual runs.

**Why:** declarative config needs a companion "what does this
declarative config actually do" surface. Useful for CI triage, for
explaining a production run, and for catching profile/sidecar
mistakes before hitting hardware.

### Facet prompt fallback — `pytest` interactive on a TTY when facets are absent

Today, profile selection requires the operator to know which facet
flags to pass: `pytest --test-phase=production --part=tps54302`.
Forget one and you get a `UsageError` listing the available facet
combinations — workable for a developer, friction for a lab tech.

`required_inputs` (`src/litmus/execution/profiles.py:422-470`) already
solves the same problem for things like `serial_number`: at session
start, walk the declared keys and resolve each via a three-step chain:

1. CLI flag `--<key>`
2. Env var `LITMUS_<KEY>`
3. Operator prompt via `litmus.prompts.ask(PromptConfig)` — respects
   `LITMUS_AUTO_CONFIRM=1`, custom handlers, TTY
   fall-through; raises `ProfileError` if it can't resolve.

Extend the same chain to **facets**: the auto-registered
`--<facet>` flags (`hooks.py:450-458`) already gate step 1; add env
var lookup (`LITMUS_TEST_PHASE`, `LITMUS_PART`, …) as step 2; then
prompt the operator with the union of declared values across the
profile catalog as the choice list as step 3. Only invoke the prompt
when no flag and no env var supplied a value — CI runs and explicit
invocations stay headless.

Conflict detection (`profiles.py:262-271`) already handles
`--test-profile=<name>` + facet flags that disagree; this change
doesn't touch it.

**Out of scope:**
- A `litmus run` subcommand. Pytest IS the test-execution interface;
  the existing `litmus` subcommands (`runs`, `show`, `serve`,
  `discover`, `mcp`) are all observability / infrastructure. Adding
  the fallback inside the pytest invocation keeps the model coherent.
- Changing the way profiles are written or facets declared.

**Why:** the lab-tech path shouldn't require remembering which facet
keys are mandatory for a given project. The prompt machinery already
exists for `required_inputs`; reusing it for facets is a small
extension that closes the friction without introducing a new CLI
surface.

### Split into `pytest-litmus` + `litmus-test` (monorepo, two wheels)

Today `litmus-test` bundles CLI + platform + pytest plugin + UI + MCP
+ all deps (NiceGUI, FastAPI, uvicorn, duckdb, …). Users who only
want pytest integration pull in the full surface.

Split into:

- **`pytest-litmus`** — thin plugin wheel. `pytest_generate_tests`,
  marker registration, `context` / `verify` / `logger` / `spec` /
  `limits` fixtures, sidecar parsing. Depends on `litmus-test`.
- **`litmus-test`** — CLI, config/store, instruments, results/parquet,
  limits/derivation, models. Server + MCP gated as `[server]` /
  `[mcp]` extras.

Layout: `packages/pytest-litmus/` + `packages/litmus-test/` under a
uv workspace. Shared tests stay at repo root (or split per-package
for independent CI). Watch for circular imports — models
(`TestConfig`, `SpecContext`, `Limit`, `PartCharacteristic`) must
live in `litmus-test`; the plugin is strictly a consumer.

Two steps — low-risk first:

1. Move UI/MCP/server deps into extras on the current single wheel
   (`litmus-test[server]`, `litmus-test[mcp]`). Captures ~80% of the
   install-weight benefit.
2. Carve `pytest-litmus` into its own wheel under the workspace.

**Why:** "platform, not framework" story — pytest is one consumer of
the platform, not the platform itself. Matches the
`pytest-django` / `pytest-asyncio` convention. Cheaper pre-1.0 than
after users pin transitive deps.

### CLI fallback for operator prompts (multi-UUT aware)

When running without the UI/server, operator prompts (e.g. "insert
UUT", "press button X", "verify LED is green") should fall back to
**terminal prompts** rather than being no-ops or silently blocking on
a UI that isn't running.

Multi-UUT scenarios require context in the prompt: the prompt must
identify **which UUT** ("UUT-2 of 4: insert board into socket B") so
the operator doesn't act on the wrong unit. Resolution path:

- Single source of truth for the prompt API — one `request_input()`
  surface that dispatches to UI (when the server is running) or CLI
  (when it isn't).
- CLI renderer shows the active UUT slot / serial / position from the
  current run manifest.
- Non-interactive mode (CI, `--yes`, `--no-prompt`) returns a default
  or fails loudly — never blocks silently.

**Why:** the bench-user / lab-tech path without the UI is
first-class; operator prompts shouldn't require running `litmus
serve`. Terminal is a perfectly good UI for one-operator-one-bench.

### Alternate runner wrappers — OpenHTF, unittest, Robot

The two-wheel split (above) carves pytest integration into
`pytest-litmus`. The same pattern extends to other test runners —
each one becomes a thin wrapper that consumes `litmus-test` core:

- **`openhtf-litmus`** — OpenHTF phase/plug wrapper. Primary
  migration path for existing OpenHTF suites. Phases call into the
  same `verify` / `logger` / `spec` surface; results land in the
  same parquet store.
- **`litmus-unittest`** — unittest `TestCase` mixin (`LitmusTestCase`)
  that exposes `self.verify(...)` / `self.logger.measure(...)`.
  For shops already on unittest who don't want to adopt pytest.
- **`litmus-robot`** — Robot Framework library that wraps the same
  verbs as keywords.

All three depend on `litmus-test`, share config/store/instruments/
results, and produce identical parquet rows. Differences are surface
only — how the test author declares a step and how the runner
dispatches it. Different entrypoints, same platform.

**Why:** reinforces the "platform, not framework" story. Existing
investments in OpenHTF / unittest / Robot shouldn't force a full
rewrite to benefit from Litmus's config system, instrument layer,
and results store. Each wrapper is a week or two of work once the
two-wheel split lands.

### Switch-matrix routing — `FixtureConnection.route` + `connection.connect()` / `.routed()`

Real benches with relay matrices need explicit switching: a single
pin reaches different instruments through different relay paths, and
the test author (or platform) needs to actuate the right path before
measuring. Today's `FixtureConnection` is implicitly "always wired" —
the `function:` field added in the multi-char relax lets the resolver
pick *which* connection routes for a given char's measurement, but
doesn't actuate any switching.

Add a `route:` field on `FixtureConnection` describing the relay /
switch state needed to land the path:

```yaml
TP_VOUT_dc:
  uut_pin: TP_VOUT
  function: dc_voltage
  instrument: dmm
  instrument_channel: ch1
  route:
    - relay: rly_main
      state: closed
    - relay: rly_aux
      state: open
```

Add `connection.connect()` / `connection.disconnect()` (imperative)
and `connection.routed()` (context-managed) methods:

```python
for connection in ctx.connections.for_characteristic("rail_3v3"):
    with connection.routed():
        verify("voltage",
               float(dmm.measure_dc_voltage(connection.instrument_channel)))
```

Implementation seed: the existing `_route_manager` / `RoutedProxy`
infrastructure (referenced by the `_route_cleanup` autouse at
`autouse.py:81`) is the closest analog. The new design folds in
conflict detection (two connections claiming the same relay),
multi-stage routing (path through several relays), per-bench safety
rules (don't actuate while another path is live), and session-level
cleanup (release on test teardown).

**Why:** the multi-char + per-function design picks the right
connection for each measurement, but doesn't actuate the bench. For
benches without switching it doesn't matter — for any bench with
relays it does. The forward-compatible design landed in the
multi-char relax means this can be added cleanly without reshaping
`FixtureConnection`.

### Sequences for fine-grained execution control

Profiles (config overlay) and pytest classes (test grouping) cover
v1's "validate part X" use case. What they don't cover:
operator-pickable, ordered bundles with step-level dependencies —
"run smoke, then load only if smoke passed, with a dialog before
load." Today the curriculum has zero examples that need this; v1
ships without sequences and the existing `TestSequenceConfig` + UI
get deleted on `experiment/pytest-native-sequences` rather than
maintained as dead code.

If real factory-line demand emerges post-v1, design a minimal
sequence model that translates straight to pytest primitives:

- `tests:` list (test IDs / class IDs) → pytest argument order
- `markers:` filter expression → `-m "<expr>"`
- `steps[].depends_on:` → `pytest-dependency` semantics injected at
  collection time
- `abort_on_failure:` → `-x`

That's the whole shape — about 80% smaller than the deleted
`TestSequenceConfig`. Operator UI lists sequences by `id` /
`description`; picking one runs the translated pytest invocation
under the active profile.

**Why:** profile and sequence are orthogonal axes — profile is the
config lens, sequence is the execution plan. Same profile (config
for part X) supports multiple sequences (smoke / full /
characterization) without duplicating limits or mocks. Worth
rebuilding when there's a real operator-bundle requirement; not
worth carrying dead model surface in the meantime.

### Runs daemon — record actual row_count in ``_ingested``

Surfaced (twice) by the design review on the runs DuckDB daemon:
``_runs_duckdb_daemon._mark_ingested`` hardcodes ``row_count=0`` in
its INSERT, so the ``_ingested`` table never carries the real
ingest size. The schema declares the column with ``DEFAULT 0``,
making this look like a placeholder waiting to be wired.

What needs to land:

- ``_mark_ingested`` accepts an explicit ``row_count: int = 0``
  kwarg.
- ``_bulk_insert_runs`` / ``_bulk_insert_measurements`` /
  ``_bulk_insert_steps`` return the count they inserted (DuckDB
  ``SELECT changes()``-style follow-up, or a ``RETURNING`` clause
  on the INSERT).
- Per-file fallback in ``_index_parquet_file`` /
  ``_index_steps_file`` likewise returns its row count.
- Call sites pass the count through to ``_mark_ingested``.

Useful for ingest-progress monitoring + per-file diagnostics
(``litmus data status`` could surface "indexed N rows from
``<file>``"). Doesn't gate any current functionality; safe to
defer.

### Daemon ingest — harden quarantine against stale/malformed parquets

Two options, either or both:

**Schema pre-validation** — before attempting the SQL ``INSERT INTO
runs_persisted BY NAME … SELECT … FROM read_parquet(…)``, sniff the
parquet's column list via pyarrow. If a required column is missing,
quarantine immediately (no SQL attempt, no ``BinderException`` in
the log). Keeps the daemon log clean and avoids burning a write
transaction on known-bad files.

**Move-aside quarantine** — once a file is quarantined, move it to a
``_quarantine/`` sibling directory so the disk-glob sweep never
picks it up again. Eliminates (mtime, size) ledger churn for files
that keep appearing in the scan even after they're quarantined.
Currently quarantined files that change on disk (different mtime /
size) are re-attempted correctly; move-aside trades that
re-attempt capability for a cleaner sweep. Appropriate if
quarantined files are expected to be genuinely dead.

Background: the ingest sweep now correctly skips files already in
``_ingested`` at the same (mtime, size) regardless of status, so
re-ingest loops are already fixed. These are hardening options, not
correctness fixes.

### Exporter access to row-level cascade outcomes

Surfaced by the Phase 6a.4 design review: ``MeasurementRow`` and
``MEASUREMENT_SCHEMA`` carry ``step_outcome`` / ``vector_outcome``
/ ``run_outcome`` (cascade rollups added in Phase 6a.2), but the
event-driven exporters (``EventSubscriber`` subclasses for CSV /
JSON / HDF5 / TDMS / MDF4 / STDF) consume the raw
``MeasurementRecorded`` event stream and don't see those rolled-up
columns directly. They reconstruct step outcome from
``StepEnded.outcome`` (which works for executed steps) but have
no equivalent for vector or run outcomes — they recover those
from ``RunEnded`` and from each measurement individually.

What needs to land:

- Either a thin adapter that materialises a ``MeasurementRow``
  from each ``MeasurementRecorded`` event (using cached
  ``StepEnded`` / ``RunEnded`` for cascade fields), then exposes
  it to the subscriber lifecycle, OR
- A ``MeasurementRecorded`` event-level extension that stamps
  ``step_outcome`` / ``vector_outcome`` / ``run_outcome`` at emit
  time so subscribers see them inline. This requires resolving
  the ordering: vector and run outcomes aren't known at the time
  of measurement emission, only at vector / run end.

The ``replay_to_subscriber`` path in ``data/subscribers/replay.py``
is where this would naturally land for post-hoc replay; the live
path needs the cascade backfill.

### Array channel empty-result schema

`channels/models.py:ARRAY_SCHEMA` is restored after being flagged
"dead" in Phase 6a.3. ``ChannelStore.query()`` falls back to
``SCALAR_SCHEMA`` when no writer schema is available (channel
registered but unwritten, or session filter excludes the live
writer). For array-type channels (waveforms, sample blocks), this
forces empty results into scalar shape — the consumer reading zero
rows still gets a mismatched schema header.

What needs to land: ``query()`` should branch on
``ChannelDescriptor.data_type`` (which is recorded at registration
time) and pick ``ARRAY_SCHEMA`` for array channels' empty fallback.
Currently low-impact (zero rows = no observable bug) but cheap to fix
when next in the channels store.

### SpecQualifier matching — capability scoring honors `qualifier`

The ``SpecQualifier`` enum (``guaranteed`` / ``typical`` /
``nominal`` / ``supplemental``) and the ``qualifier:`` field on
``SpecBand`` / ``Signal`` / ``Attribute`` (``models/capability.py``)
are restored after being flagged "dead" in Phase 6b.1. Industry-
standard datasheet semantic (Keysight / Keithley / Rohde-Schwarz):
distinguishes warranted specs (must be met, guardbanded) from
typical-only specs (informational, not warranted).

What needs to land: capability matching at session start should
honor this. When checking whether an instrument's ``signals[v].range``
covers a part's required range, treat ``guaranteed`` qualifiers
as warranted (must satisfy with margin) and ``typical`` qualifiers
as advisory (warn but don't block). The matcher in
``litmus.matching`` ignores ``qualifier`` today; when a station has
only typical-spec instruments for a critical signal, we should
surface that.

Tied into: capability-aware station/test runnability inference
(separate Backlog item above).

### Limit resolution: expression / lookup / step / callable strategies

`MeasurementLimitConfig` (``models/test_config.py``) declares
fields ``expr: str``, ``tolerance_pct``, ``tolerance_abs``,
``lookup: LimitLookupConfig | None``, ``steps: LimitStepConfig | None``,
and ``callable: str``, but only the direct ``low``/``high``/
``nominal``/``characteristic`` resolution paths are wired through
``execution/verify`` and the limit resolver. The Phase 6b.1 audit
correctly removed the ``LimitExprConfig`` and ``LimitCallableConfig``
sub-models because their fields were already flat on the parent —
but the *features* themselves are unwired:

- **expr-based limits** — ``output_voltage: {expr: "0.66 *
  vector.input_voltage", tolerance_pct: 5}``. Resolver evaluates
  the expression against the active vector params, applies
  tolerance to derive low/high.
- **lookup-table limits** — ``LimitLookupConfig`` (kept) typed
  with ``key: str`` and ``table: dict[str, Limit]``. Resolver
  picks the table entry whose key matches the active vector
  param. Unused today.
- **step-function limits** — ``LimitStepConfig`` (kept) with
  ``param`` and ``ranges: list[{below: X, limit: {...}}]``.
  Resolver picks the first range whose ``below`` exceeds the
  param. Unused today.
- **callable-based limits** — ``callable: "myproject.limits.x"``
  — dotted path to a Python function returning a ``Limit``.
  Unused today.

What needs to land: extend ``execution.verify._resolve_measurement_limit``
to honor each shape, with sensible precedence (direct > char-derived
> expr/lookup/step/callable > fallback). Each shape has a real
test-engineering use case (load-curve specs, temperature-derated
limits, formula-driven limits) — they're not aspirational, just
not built yet.

### What-if limit analysis — retune limits across history for yield (v0.3.0 analytics)

A differentiating analytics surface: take an existing measurement and apply
*hypothetical* limits to its full historical value distribution, then report the
resulting yield (% pass) — so a test engineer can tune a limit against real data
*before* committing it, and see exactly how much yield a tighter/looser bound buys
or costs.

- **Input:** a measurement (by name / characteristic) + candidate limit(s)
  (low/high/nominal/comparator), optionally scoped by conditions (DUT / product /
  station / date, or input-vector values).
- **Output:** pass-rate over the matched historical values under the candidate
  limits vs the current limits — a before/after **yield delta**, ideally with the
  value distribution and where the proposed bounds cut.
- **Extension:** sweep a limit across a range → plot **yield-vs-limit** (find the
  knee / the bound that hits a target yield); Cpk under the candidate limits.

Why it's differentiating: limit-setting is usually a matter of engineering judgment;
this adds a data-driven "what does this limit do to my yield?" loop on top of that,
answered from history. No mainstream HW-test stack offers it as a first-class loop.

Enabled by the runs redesign: measurements are long-form with the raw typed
``measurement_value``, and conditions are queryable via the EAV — so re-evaluating
arbitrary limits across the historical value set is a scan + comparison, no re-run.
Current limits live in config (``MeasurementLimitConfig``), so "current vs candidate"
is a clean diff.

### Consumer-side ref materialization (waveform viewing)

Surfaced by the Phase 6a.2 `data/backends/` design review: the
write path saves large observations (Waveform / ndarray / bytes /
Pydantic models) to `_ref/` sidecar files and stores
``file://_ref/abc.npz`` strings in parquet's ``out_*`` columns.
The deref path is now wired: the API (`GET /api/runs/{run_id}/ref`), the
`artifact_viewer` UI component, and MCP all call
`parquet.py:load_ref` (the `channel://` / `file://` URI dispatcher) →
`load_file` (npz → `Waveform`, npy → ndarray, json → dict/Pydantic, …).

Remaining gaps (re-scoped 2026-06):

- **Reports**: HTML/PDF still embed the literal ref string instead of a
  rendered waveform plot.
- **CLI**: `litmus show <run> --waveform <name>` round-trip through
  `load_ref`.
- **RunView eager-vs-lazy**: decide whether run-detail materializes
  waveforms inline or on demand.

### HTTP support for ImageDialog

Surfaced by the Phase 6c.1 `api/` design review. The dialog system
has four variants — `ConfirmDialog`, `ChoiceDialog`, `InputDialog`,
`ImageDialog` — and the manager (`api/dialogs/manager.py:470-483`)
exposes `register_image_dialog(...)` so in-process callers can
trigger an image prompt. But the HTTP layer skips it:

- `api/models.py:DialogCreate` declares only `type:
  Literal["confirm", "choice", "input"]` and lacks `image_url`,
  `image_path`, `show_confirm`, `capture_enabled` fields.
- `api/app.py:_create_dialog_from_request` has no ``"image"``
  branch.

So a test subprocess running over HTTP can't ask the operator to
review or capture an image. What needs to land:

- Add `image_url`, `image_path`, `show_confirm`, `capture_enabled`
  to `DialogCreate` (or a dedicated `ImageDialogCreate` and a
  discriminated union).
- Add the ``"image"`` case to `_create_dialog_from_request`.
- Extend the dialog UI page to render image previews and
  capture buttons (`ui/pages/dialogs/`).
- Decide how captured images flow back to the test subprocess:
  base64 in `DialogResponse.image_data` (already exists) vs an
  uploaded file path the test fetches separately.

Decision points: should `DialogCreate` become a discriminated
union, or stay a flat optional-fields model? Where does captured
image data live (response payload vs server-side artifacts)?

### `response_model=` coverage on FastAPI endpoints

Surfaced by the Phase 6c.1 `api/` design review: only one endpoint
(`GET /api/runs/{run_id}`) declares `response_model=`. The other
~39 endpoints return either a typed Pydantic model (without telling
FastAPI what its schema is) or an ad-hoc dict envelope
(`{"runs": [...]}`, `{"run_id": ..., "status": "running"}`,
`{"data": ...}`).

Consequences:
- OpenAPI schema for these endpoints is opaque (no JSON schema for
  responses; clients can't generate types).
- No response validation at the HTTP boundary, so a regression in
  the underlying model can leak through unnoticed.
- Envelope shapes (`{"runs": [...]}`, `{"data": ...}`,
  `{"active_runs": [...], "count": N}`) are scattered conventions
  rather than declared contracts.

What needs to land:

- Define small response DTOs for each envelope shape (likely 5-10
  in `api/schemas.py`: `ListRunsResponse`, `StartRunResponse`,
  `ListEventsResponse`, `MetricsResponse[T]`, etc.).
- Add `response_model=` to every endpoint.
- Pick a convention for collection wrappers (`{"X": [...]}` vs
  `{"data": [...]}` vs unwrapped list) and apply it uniformly.

Decision points: do we keep dict envelopes for backward-compat with
existing clients, or normalize? Is a generic `Response[T]` wrapper
worth the type gymnastics? Coordinate with the MCP-tool / HTTP-API
parity rule (CLAUDE.md) so both layers expose the same shapes.

### Artifact viewer — inline previews + grid layout

Headline shipped in the artifact-viewing PR: a "View ..." button per
ref opens a dialog with the right viewer (ECharts for waveform, image
embed, video embed, PDF iframe, text). What's missing is **inline
previews** so the operator can scan a run at a glance without opening
each dialog.

What needs to land:

- **Card grid** instead of a row of buttons. One card per artifact,
  grouped by step + measurement. Title = output key; subtitle = type +
  size; click anywhere on the card opens the full dialog.
- **Inline previews**:
  - Image / SVG → small `<img>` thumbnail at fixed height.
  - Video → `<video>` with `preload="metadata"` for the poster frame.
  - Waveform — small ECharts sparkline (100×40), no axes.
  - Text — first 3 lines in a `<pre>` with overflow-hidden.
  - PDF → page-icon SVG plus "PDF" badge (no native browser preview API).
  - Unknown / `.bin` without recognized magic — generic file icon.
- **Type detection for `.bin`**: read the first 64 bytes from the
  ``_ref/`` file directly (already on disk; no HTTP round-trip) and
  pass through ``sniff_mime``. Cache per-page render so the same file
  isn't re-read.

Decision points: do we materialize previews lazily (intersection
observer) for runs with many artifacts? Should the seed/write path
gain a ``mime_type=`` hint so we don't have to sniff at all? Track
the latter under the existing "Write-path MIME hint" follow-up.

### Operator-UI store browser — Sessions + Artifacts pages

The first cut shipped Events + Channels under "DATA STORES" in the
sidebar (poll-and-refresh tables, click-through detail). The
remaining surfaces:

- **Sessions page** — drill into a session and see all sibling slot
  runs at once (multi-UUT view). Today subsumed by the events
  ``session_id`` filter, but a dedicated page makes the multi-slot
  cohort obvious.
- **Artifacts page** — search across every ref ever written, group
  by run / output key / MIME type. Reuses the artifact-viewer
  dialog. Needs cheap MIME detection for `.bin` refs (covered by
  the inline-previews entry above).

Decision points: server-side pagination for stores that grow
unbounded (events especially). What's the right cross-store search
syntax — DuckDB SQL via a console, or facet filters? Coordinate
with the existing Yield Analytics page so we don't duplicate.

### Transports — read side (download / fetch / replay)

The ``Transport`` abstraction (``data/transports/``) currently
handles **upload only**: parquet / event files / refs are flushed to
S3 / GCS / Azure / SFTP / HTTP via background workers. There's no
counterpart for **reading back** — `litmus serve` and the analytics
layer can only see what's on the local disk.

What needs to land:

- A ``Transport.fetch(remote_path) -> bytes`` (or ``open()`` /
  ``stream()``) sibling to the upload path. Must compose with the
  existing per-backend auth.
- A ``RemoteResultsBackend`` or equivalent that proxies
  ``ParquetBackend`` reads through a transport. Cache locally so
  repeat queries don't pay the round-trip.
- API parity: ``/api/runs/{id}/ref?uri=s3://bucket/run123/...`` should
  work the same as the local-file path. The endpoint dispatches on
  URI scheme.
- Sync helper: ``litmus pull <run_id>`` to fetch a remote run into
  the local results dir for offline analysis.

Decision points: do we cache full files locally on first read, or
stream byte-ranges? How do we surface remote runs in the UI without
materializing them all (lazy entries with a "fetch" button)? Does
this share machinery with the upload-queue worker (one queue, two
directions) or run separately?

### Live updates on Events and Channels store-browser pages

The first cut of the Events / Channels browser is poll-and-refresh:
hit the page, scan the table, click Refresh to re-query. The
existing `/live/{run_id}` page already proves that live tailing
works (`EventStore.on_event` / `ChannelStore.on_channel` +
`ui_subscribe` thread-safe bridge in
``ui/shared/event_binding.py``), it just isn't wired into the
browse pages yet.

What needs to land:

- **Events page** — toggle to start a live subscription with the
  current filters as the catch-up + ongoing filter. New events
  prepend to the table; pause toggle stops the firehose. Subscription
  closes on page navigation.
- **Channel detail page** — when viewing a channel, the chart and
  table auto-extend with new samples as the test that's writing them
  runs. The most important UX: **watching a waveform fill in
  during capture** without manual refresh.
- **Cross-process delivery** — `EventStore.on_event` already does
  500ms-poll fallback for cross-process; `ChannelStore.on_channel`
  doesn't yet have a cross-process path, so the live-channel chart
  needs a Flight subscription on the channel daemon (the daemon
  already serves Flight; the client side is the missing piece).

Decision points: throttle / batch updates so a 10kHz channel doesn't
flood the websocket? Coalesce by Nth sample before pushing to the
chart's `appendData` call. Subscription lifecycle when the page
unmounts — do we reuse the existing `event_binding` cleanup pattern
or extend it for Flight subscriptions?

**Update (2026-06 branch work):** the **channel detail page** sub-bullet
shipped — live tail via a page-level deque + `ui.timer` + `LiveBadge`,
with a cross-process Flight subscription on the channel daemon
(`ui/pages/channels/detail.py`). Remaining open scope: the **Events
page** live-subscription toggle (no live wiring today) and the
**`/channels` list page** live-status column (deferred item below;
2s poll-refresh only, no lifecycle column).

### Parametric measurement viewer — follow-ups

The thin slice (`/explore` page, `MetricsStore.parametric()`,
schema-driven dropdowns, URL state, scatter / line / bar /
histogram) shipped 2026-05-02. Outstanding work:

- **Range filters and facet pickers.** Today filters are a JSON
  textarea — equality only. Add dedicated `since`/`until` inputs and
  multi-select pickers for `station_id` / `part_id` / `test_phase`
  / `outcome` so the common filters don't require typing JSON.
- **Derived metrics for Y.** Pure column queries today — no yield
  rate, Cpk per group, or sigma. Decide where these live: keep them
  in `analysis/metrics_store.py` alongside the hardcoded queries, or
  carve out `analysis/metrics/` with one module per derived metric.
- **HTTP / MCP symmetry.** No `/api/parametric` endpoint or MCP tool
  yet. The MetricsStore method is in place; just needs the wrappers.
- **Row caps and decimation.** Hard `limit=5000` cap on raw
  scatter / line. LTTB-decimate large series before sending to the
  chart so 50k-point queries don't lock the browser.
- **Per-test grounding.** Today the user picks Y/X over all silver
  rows. For the "test selector" use case (filter to one test or one
  measurement name) we already have `measurement_name` as a filter
  column — but a dedicated dropdown would be more discoverable.

---

## In progress

_None._

---

## Completed

### Channel/file tuning consolidation — per-store data options — 2026-06-16

The scattered channel/file durability + cadence knobs were consolidated into
per-store Pydantic options in `models/data_options.py` — `ChannelOptions`
(sink/writer flush rows + interval, push relay), `FileOptions` (frame relay),
`SessionOptions` (the liveness will), and a shared `StreamTuning` (`cadence`)
reused by both the channel and file checkpoint paths — all settable in
`litmus.yaml` under `channels:` / `files:` / `session:` / `stream:`.
`StreamTuning` keeps its name ("stream" = the streaming activity, a verb),
and the cadence field is already `cadence` (the `checkpoint_cadence` rename
was considered and declined — redundant under the `stream:` block). Landed
across the streaming-unification Phase 2 work.

### Channel attribution — `instrument_role` / `resource` on the descriptor — 2026-06-16

`ChannelStore.write()` now accepts `instrument_role` / `resource` and
`instruments/observer.py` passes `self._role` / `self._resource` on every
channel write, so the channel descriptor carries "which instrument owns
this channel". The daemon serves the full descriptor from segment Arrow
schema metadata (`_registry.json` retired), and the producer hostname is
stamped on it — the analytics / UI read path attributes channels to
instruments via `channels()` rather than a side file. Key commit
`e8f7742` (daemon serves descriptors; #231); hostname `b43ea50`; write-path
wiring `46a109c`.

### Unified per-run parquet — 2026-05-07

One parquet per run replaces the prior `{run}.parquet` +
`{run}_steps.parquet` sidecar pair. A single denormalized
`RUN_ROW_SCHEMA` carries both row kinds:

- **Measurement rows** — `measurement_name IS NOT NULL`, full step
  + vector context denormalized.
- **Step-summary rows** — `measurement_name IS NULL`, one per
  `(step_path, vector_index)` for steps that recorded no
  measurements (containers, action steps, planned-but-unrun sweep
  vectors).

Per-vector identity becomes load-bearing: PK is
`(run_id, step_path, vector_index)` across the unified file. The
streaming subscriber and the batch `save_test_run` writer now share
the `build_step_summary_row` helper so a step row has identical
shape regardless of which path produced it.

Companion fixes:

- Events DB closes orphan runs — the 30s sweep emits
  `RunEnded(outcome=aborted)` to the events DB alongside the
  `_write_orphan_parquet` call, so abandoned runs drop out of
  `events_for_active_runs()` and don't accumulate as zombies.
  Test helpers stop hardcoding `pid=1` so the sweep can
  pid-liveness-check real test processes.
- Inflight measurements wired into the `measurements` view —
  `LiveRunsSubscriber` registers an `inflight_measurements` Arrow
  table from the `AccumulatorPool` snapshot; the `measurements`
  view is now `measurements_persisted UNION ALL inflight ...`,
  matching the `runs` / `steps` pattern. Live run detail pages
  see measurements appear as events arrive, not at run end.
- StepRow surfaces `inputs` / `outputs` in the run detail UI;
  unrun-vector entries from `build_step_manifest` make it into
  the parquet so partially-run sweeps account for every planned
  vector.

What this unblocks: parquet compaction (one file per run is the
right granularity for compaction), warehouse-style reads (single
file = single SELECT * FROM read_parquet), and per-run identity
in tooling (no more "which file holds the steps?" ambiguity).

### Parametric measurement viewer (thin slice) — 2026-05-02

`/explore` page for cross-run measurement comparison. Pick any
silver column for Y / X, optionally split by a categorical group,
toggle between scatter / line / bar / histogram. URL query string
holds all selections so the view is shareable by copy-paste.

What landed:

- `MetricsStore.parametric(y, x, filters, group_by, chart_type, bins,
  limit)` returns long-format `{x, y, group}` rows. Histogram bins
  server-side; bar aggregates AVG; scatter / line return raw rows
  capped at `limit`.
- `MetricsStore.describe_silver()` for schema introspection — the
  Y / X / group_by dropdowns are populated from real columns rather
  than a hardcoded list.
- Column identifiers are validated against `^[A-Za-z_][A-Za-z0-9_]*$`
  before going into SQL; filter values escape via `sql_escape`.
- Time-axis sniffing: `datetime`-typed X gets ECharts `type: time`,
  strings get `type: category`, numerics get `type: value`.

Sibling to `/events` / `/channels` (browse) and `/metrics` (canned
dashboards) — the freeform counterpart that lets an operator ask
"how does X track Y across runs" without writing SQL.

### Profiles bind station_type + fixture (test-phase wiring) — 2026-04-27

Profiles can now select `station_type` + `fixture`, closing the
"profile is a half-config" gap from before. Selecting
`--test-phase=production` sets limits, the required station-type,
AND the fixture in one flag — the operator no longer has to remember
a matching `--fixture=...` per phase.

Schema additions (all optional, additive):

- `StationConfig.hostname: str | None` — auto-match key for
  `socket.gethostname()`. When set, the resolver picks the matching
  station before falling back to `ProjectConfig.default_station`.
- `StationConfig.station_type` (existing field) — promoted from
  advisory to load-bearing. Cross-checked at session start.
- `FixtureConfig.station_types: list[str]` — declares which
  station-type layouts the fixture can wire against.
- `ProfileConfig.station_type: str | None` — required station-type
  contract for the phase. Profile cascades merge it last-wins via
  `extends:`.
- `ProfileConfig.fixture: str | None` — fixture id; CLI `--fixture`
  wins on conflict (warning emitted).

New `validate_station_against_type` (pure data check) +
`validate_phase_wiring` (raises `ProfileError` on mismatch, wrapped
as `pytest.UsageError` by the existing hook). Run-record stamps
already covered `station_type` and `fixture_id` (no `TestRun` schema
change needed).

Profile portability is preserved — profiles bind a *type*, never a
concrete station instance. Same `production` profile runs on any
bench whose `station_type` matches.

The `litmus_connections(connections=[...])` narrowing mode stays a
niche escape hatch — fixtures + characteristics auto-derive
connections per phase via this work; the explicit narrowing mode is
for rare deliberate scoping.

Curriculum: `examples/07-profiles/` demonstrates all four bindings
(station type definition, station instance with type+hostname,
fixture compatibility, profile binding). Examples 01-04 untouched
(bringup tier doesn't need stations). 1489 tests pass.

### Runner-neutral logic extracted from plugin.py — 2026-04-26

Pulled the test-execution code that doesn't depend on pytest into a
new `litmus.runner` package. The pytest plugin now delegates to it
through thin pytest-shaped adapters; `pytest-litmus` as a separate
distribution depending on `litmus-test` is one rename + entry-point
edit away. OpenHTF / Robot / unittest wrappers consume the same
shared modules.

New modules under `litmus.runner`:

| Module | Surface |
|---|---|
| `markers` | `MarkerSpec`, `entry_to_marker_specs`, `normalize_inline_list_payload`, `enforce_no_inline_stacking`, `extract_specs_characteristic` |
| `sweeps` | `sweep_to_parametrize_args`, `parametrize_calls_for_entry`, `parametrize_call_rows`, `runner_marker_parametrize_calls` |
| `cascade` | `cascade_for(sidecar, profile, cls, func) → TestEntry`, `find_unmatched_profile_keys` |
| `audit` | `audit_traceability(logger, *, strict, spec_active)` |
| `metadata` | `build_run_metadata(...)` taking already-resolved inputs |
| `instrument_events` | `emit_instrument_events(logger, event_log, records)` |
| `outputs` | `make_transport_callback`, `find_format_transport_callback`, `create_subscriber`, `run_configured_outputs` |
| `mocks` | `install_mocks(by_target, *, resolve_fixture, register_cleanup, fixture_lookup_error)` |
| `retry` | `retry_policy_to_flaky_kwargs(RetryPolicy)` |

`plugin.py` shrinks from 2,777 → 2,353 lines (~15%). What's left is
the pytest contract: hooks, fixtures, `pytest.Item` / `metafunc` /
`request` adapters. None of it is pytest-CAN-be-removed; it's
pytest-IS-the-API.

Also killed `parse_retry_marker_kwargs` — it was a one-line wrapper
around `RetryPolicy.model_validate`. Pydantic owns YAML / kwargs
validation; helper functions that re-implement it are dead weight.

**Followups (separate Backlog entries):**
- Rename `litmus.pytest_plugin` → `litmus.pytest_plugin` (clearer
  it's the pytest adapter; touches ~20 references).
- Concrete `pytest-litmus` package split — entry-point + wheel
  packaging only; the code is already organized.
- First non-pytest runner wrapper (OpenHTF preferred) to validate the
  `litmus.runner` interface against a second consumer.

### YAML schema generalization — flat marker scope, typed sub-models — 2026-04-26

Sidecar / profile / per-test entries now share one flat shape.
`SidecarConfig`, `ProfileConfig`, and `TestEntry` are all the same
marker-scope model: Litmus marker fields (`limits`, `sweeps`, `mocks`,
`specs`, `connections`, `retry`, `prompts`) live directly at each
entry's root, alongside the reserved `runner:` and `tests:` keys.
Reserved keys are the only namespacing; everything else is a Litmus
marker name with a typed Pydantic sub-model.

```yaml
# tests/test_rail.yaml
limits:
  v_rail: {low: 3.2, high: 3.4, units: V}
sweeps:
  - {vin: [3.3, 5.0]}
runner:
  markers:
    - flaky: {reruns: 2}

tests:
  TestRails:
    limits:
      i_idle: {low: 0.0, high: 0.1, units: A}
    tests:
      test_strict:
        limits:
          v_rail: {low: 3.25, high: 3.35}
```

**Typed end-to-end.** Every Litmus-marker field is a Pydantic model
(`MeasurementLimitConfig`, `SweepEntry` with zip-coherence validator,
`MockEntry` with target shape validator, `ConnectionsBinding`,
`RetryPolicy`, `PromptConfig`). Pydantic validates at YAML load —
typos and type errors fail with structured messages before any test
runs. The hand-rolled parsers (`parse_limits_block`, `_LimitRef`,
`_PolicyLimit`, `_BandSet`, etc.) are gone; one resolver
(`resolve_limit`) walks the typed model directly.

**Catch-all bands.** `MeasurementLimitConfig.bands: list[Self]` makes
the model recursive: every band is itself a `MeasurementLimitConfig`
with its own `when:`. The parent (siblings to `bands:`) acts as the
catch-all when no band matches, by design of the type — no
`{when: {}}` workaround needed.

**Flat `runner:` block.** One runner per session means one schema
validates the whole runner block. `PytestRunner` (Pydantic,
`extra="forbid"`) catches `addopst:`-style typos at session start.
Ecosystem markers go under `runner.markers:` per scope.

**No-stacking enforcement.** Multiple `@pytest.mark.litmus_X(...)`
decorators on one function raise `pytest.UsageError`. Multi-axis
goes in the single payload list; `parametrize` is the explicit
exception via `runner.markers`.

All 1496 tests pass; all 7 example chapters pass end-to-end. Inline
`@pytest.mark.litmus_X` syntax unchanged in user code; YAML drops the
prefix because the entry's root is already Litmus-scoped. Pre-release,
no shims.

JSON Schema falls out of every model (`Model.model_json_schema()`),
ready for VS Code autocomplete via the Red Hat YAML extension once
schema-export is wired into `litmus init`.

**Followups:**
- Schema export → `.vscode/settings.json` for autocomplete in user
  projects (small, deferred).
- Lift runner-neutral logic out of `plugin.py` (separate Backlog
  entry; this PR was the prerequisite).
- Align runtime vocabulary to industry — `spec_*` → `characteristic_*`
  rename (separate Backlog entry; touches parquet schema, exporters,
  every measurement query).
