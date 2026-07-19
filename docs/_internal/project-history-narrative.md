# TesterKit — A Development Narrative

*A high-level story of the project as told by its commit history.*
*Span: 2026-01-27 → 2026-07-05 · ~1,350 commits · 6 releases (v0.1.0 → v0.3.0) · 67 merged PRs.*

This is not a changelog. It's the arc of *how the project thought* — what got
built, what got torn out, where the direction turned, and what the team learned
the hard way. Details (individual bug fixes, one-off doc edits) are summarized,
not enumerated.

---

## The shape of it at a glance

| Period | Commits | What was happening |
|---|---|---|
| **Jan** | ~60 | Genesis. The whole skeleton appears in a week: config, instruments, pytest plugin, Parquet, MCP, UI. |
| **Feb** | ~120 | The capability/catalog era. Datasheet→test workflow, SpecBand model, two big model rewrites (V1→V2). |
| **Mar** | ~110 | The data-architecture pivot. Event-sourcing replaces the journal; parallel multi-DUT; Arrow Flight + DuckDB daemons. |
| **Apr** | ~175 | The great convergence. `src/` layout, pytest-native rewrite, marker consolidation, and a codebase-wide design-review sweep. |
| **May** | ~300 | Shipping. v0.1.0 hits PyPI; a full docs corpus is written and audited; the v0.2.0 data-plane begins. |
| **Jun** | ~510 | Peak throughput. v0.2.0 (channels, files, streaming) lands; performance engineering; then the 0.3.0 schema reshape. |
| **Jul** | ~75 | Consolidation. slot→site, schema-version epoch, derived-index versioning. |

The tempo is telling: the project accelerated as it matured. June alone —
after the foundations were solid — carried more than a third of all the work.

---

## Act I — Genesis (late January): a platform in a week

The first commit lands a Pydantic config system. Within seven days the *entire
skeleton* of TesterKit exists: instrument HAL with built-in simulation, a pytest
plugin with a `@measure` decorator and a Parquet backend, retry/skip logic,
a test-vectors model for parametric testing, a demo power-board test, an
operator UI (NiceGUI), capability-based station↔sequence matching, a product
specification system, and — crucially — an **MCP server** exposing TesterKit to AI
agents plus a Python client library.

Two decisions from this week set the tone for everything after:

1. **Integrate, don't reinvent** — pytest, Pydantic, Parquet, NiceGUI, PyVISA.
   Libraries LLMs already know deeply.
2. **AI-native surface from day one** — the MCP server wasn't bolted on later;
   it was in the first week, and it immediately got a hard lesson: the initial
   36 MCP tools were consolidated down to **8**, prefixed `testerkit_` to avoid
   builtin shadowing. Fewer, sharper tools beat many.

The month closes wrestling with the MCP `run` tool actually invoking pytest
correctly (a run of five successive "fix run_tool" commits — the first taste of
how fiddly the AI-execution boundary would be) and adopting a **product-centric
folder structure**.

## Act II — Capabilities & catalog (February): teaching the machine to read datasheets

February is dominated by one ambition: **turn a datasheet into a test**. This
drove the deepest modeling work of the whole project.

- The **`catalog-from-datasheet` skill** arrives with 19 reprocessed catalog
  entries, then grows to 50+ instrument entries (Keithley, Keysight, NI, Rigol…).
- The **capability model** gets rewritten twice. V1's `TestRequirement` /
  `ConditionPoint` vocabulary is ripped out entirely ("FULL V2 AUDIT: Remove all
  V1 remnants") in favor of a **unified `SpecBand`** — one model expressing both
  what an instrument *can do* and what a product *requires*, with condition-aware
  matching (`when:` clauses).
- A recurring theme starts here and never stops: **kill hand-rolled parsers, run
  everything through Pydantic `model_validate`**, centralize all YAML I/O in
  `store.py`.

There's also a visible tension the team keeps re-litigating: **how prescriptive
should the MCP server be with its AI client?** Commits swing from "CRITICAL:
Force use of `ask_user_input_v0` for all approval gates" to "REVERT: Make MCP
server instructions client-agnostic" within the same day. The lesson: don't
hard-code one AI client's affordances into a client-agnostic protocol.

A quiet but consequential pivot near month-end: **"Sequence as single source for
test config."** Test configuration (vectors, limits, mocks) consolidates into
the sequence. This unification is a preview of a much bigger reckoning in April.

## Act III — The data-architecture pivot (March): event-sourcing and going parallel

March is where TesterKit stops being "a pytest plugin that writes Parquet" and
starts being a **data platform**. Two large arcs run in parallel.

**Arc 1 — Event sourcing.** In a numbered "Phase 1…7" march, the streaming
JSONL journal is *replaced as the source of truth by an event log*. This brings:
instrument proxy + channel store (telemetry, later renamed ChannelStore),
sessions, `harness.record()`, retention, and outcome consolidation. Then the
heavy machinery: **Arrow Flight streaming**, an **EventStore**, and a **DuckDB
singleton daemon** for event queries — with an in-memory-DuckDB dual-write
pattern and cloud transports (S3/Azure/GCS). This becomes PR #1, the
`event-log` branch. The architectural bet — *everything is derived from an
append-only event stream* — is the single most important idea in the codebase.

**Arc 2 — Parallel multi-DUT.** Subprocess-based slot execution lets multiple
DUTs test at once, with signal switching / route management, shared instruments,
per-resource locking (the thread-mode execution model is removed in favor of an
`InstrumentServer`), a `slot_id` in Parquet, and an execution Gantt chart. PR #2.

Interleaved through both: an intense **design-review discipline** emerges —
"Design review Execution: dedup, consolidation"; "Config & Schemas: 5 audit
rounds, 30+ fixes"; subsystem by subsystem. This audit-until-clean loop becomes
a permanent fixture of how the project works.

March ends with a telling non-event: a 46-post blog brainstorm is added, then
**removed** ("moved to pragmatest.com repo") — the product and its marketing get
separate homes.

## Act IV — The great convergence (April): pytest-native and one honest shape

April is the most *conceptually turbulent* month — and the most important for
the API users actually touch. Several threads converge on a single principle:
**stop inventing TesterKit-specific machinery; lean all the way into pytest.**

- **Layout:** `testerkit/` moves to `src/testerkit/`.
- **pytest-native rewrite:** the plugin is merged and split into three clean
  objects (context / spec / logger). The bespoke `TesterKitSequence` gating is
  removed — **pytest-native becomes the default**. The `@testerkit` and
  `@testerkit_step` decorators are *deleted* ("rip:"). Sequences-as-a-concept are
  deleted outright.
- **Markers over decorators:** a family of markers (`testerkit_vectors`/`sweeps`,
  `testerkit_limits`, `testerkit_specs`/`characteristics`, `testerkit_mocks`,
  `testerkit_prompts`) replaces the old scaffolding. This shape is **churned
  relentlessly** — you can watch the naming converge in real time:
  `inputs→params`, `outputs→observations`; `spec→specs→characteristics`;
  `vectors→sweeps`; `binding→connections`; config-wrapper added then dropped
  ("flat marker-scope"). Vocabulary is treated as a first-class design surface.
- **Package hygiene:** runner-neutral logic is extracted into a `testerkit.runner`
  package, `execution.plugin` becomes `testerkit.pytest_plugin`, circular imports
  are broken, and the `config/` package is *deleted* — schemas fold into
  `models/`, helpers into `store.py`.

Then, late April, the **audit convergence**: a relentless sweep across every
subsystem ("audit batch A: user-types-this CLI/env surface"; "models audit";
"data/channels audit"; …) that dedupes, deletes dead code, tightens the public
surface, and standardizes vocabulary. Two vocabulary decisions worth flagging:
outcomes standardize on **past-participle** verbs, and the verb surface collapses
toward **one verb, `verify`**, with `logger.measure` as a pure recorder.

## Act V — Shipping v0.1.0 and writing the book (May): the docs marathon

Early May, the pytest-native branch merges (PR from `pytest-native-sequences`)
and the project **turns toward release**: pyright driven to zero, ruff-format
baseline, pre-commit gates, `results_dir → data_dir` rename, curated docs bundled
into the wheel. **v0.1.0 ships to PyPI as `testerkit` on 2026-05-15**, followed
quickly by 0.1.1–0.1.3.

But the defining work of May isn't the release — it's the **documentation
marathon**. This is where the project's docs philosophy is forged in fire:

- A **parametric measurement viewer** (`/explore`) and typed Query API classes
  (`RunsQuery`, `StepsQuery`, `MeasurementsQuery`) give the UI and docs a public
  data path — "never read Parquet directly."
- The **unified step/vector model** lands (collection-time `step_index` /
  `vector_index`, container steps), and the `_steps.parquet` sidecar is folded
  into **one unified per-run Parquet** with an explicit `record_type`
  discriminator. `attempt` is renamed `retry`, 0-based throughout.
- Then **"Phase H"**: seven purpose-built **audit agents** are written and run
  across the *entire docs corpus* (71 audit reports), followed by dozens of
  per-page rewrites — each verified against source, each scrubbing framework
  internals from user-facing prose. A **reference-doc generator** is built so
  `event-types`, `models`, `api`, `cli` are regenerated from source with a
  pre-commit **drift check**. The rule crystallizes: *docs are verified against
  code, not written from memory, and generated where possible.*

May also quietly starts the next big thing: an **"ideal data architecture"
exploration** and a v0.2.0 design doc. The four-store model (runs / events /
channels / files) begins to take shape.

## Act VI — The data plane (June, first half): channels, files, and streaming

June is the busiest month by far, and its first half delivers the **v0.2.0
data plane** — a rapid-fire sequence of small, merged PRs (the numbers run
#14→#57) each adding one capability:

- **FileStore** — a claim-check pattern: blobs written to `file://` URIs,
  attributes + MIME sidecars, a pluggable `pyarrow.fs` backend, streaming sinks,
  and reference-aware retention (pin what's referenced, prune orphans).
- **Channels** — typed leaf types, lifecycle-only events, schema renames
  (`timestamp → received_at`/`sampled_at`, `data_type → value_type`), a warm
  DuckDB index in the daemon, live waveform plots, LTTB decimation, consumer
  verbs (`latest`/`live`/`query`/`window`), and monotonic per-session sequences.
- **The verb surface** — `observe` becomes the polymorphic router (scalar,
  waveform, blob, URI), `verify` stays scalar-only, `stream` for continuous data,
  and a power-user `files.write` / `channels.write` surface. Later, `measure` is
  promoted to a public verb and the `logger` fixture is de-exposed.
- **The operator UI** grows `/files`, `/explore`, live monitors, filter-above-
  content everywhere, and **session scoping via URL only** (no UUID pickers) —
  the UI-consistency rules in CLAUDE.md are enforced page by page.

Two rounds of serious **performance engineering** run alongside: lock-free
parallel reads/writes on the daemons (cursor-per-thread), lossless push
replacing a 500ms poll, vectorized inserts (~22× durable emit), a `testerkit
benchmark` CLI with a real cost model, and self-healing write paths that resend
un-acked batches across a daemon kill. A recurring nemesis, **projection drift**
(the daemon's derived view disagreeing with Parquet — issue #228/#233), gets
chased down with equivalence-test guards that assert "query clients read the
daemon, not Parquet."

A big **terminology correction** lands here too: `product → part` and `dut → uut`
renamed across the whole codebase (#258) — "product" was overloaded (the DUT
entity vs. the business's software products).

**v0.2.0 ships 2026-06-22**, followed by 0.2.1. Along the way the metrics suite
gets honest: `Cpk/Cp → Ppk/Pp` (it computes overall sigma), RTY + DPMO/DPPM
added, Ppk computed over homogeneous populations rather than pooled by name.

## Act VII — Getting the grain right (June second half → July): the schema reckoning

With the data plane shipped, attention turns to a subtle, foundational problem
the team had been circling for months: **what is the unit of execution, and how
is it stored at rest?** This is the 0.3.0 arc, and it's the most careful,
design-contract-driven work in the history — nearly every feature commit is
paired with an `_internal` design-diary commit.

The reckoning, in order:

1. **Execution-model v2** — events gain `VectorStarted`/`VectorEnded`; the
   at-rest Parquet becomes a **vector-grained chronological telling** with an EAV
   (entity-attribute-value) projection for dynamic measurement axes. Measurements
   nest *under the vector*. `units → unit` (scalar) swept everywhere.
2. **At-rest reshape** — instruments reshaped to `list<struct>` + a materialized
   table, `parent_path` dropped and derived from `step_path`, and
   `uut_serial → uut_serial_number`. Marked `feat(runs)!` — breaking,
   deliberately, pre-1.0.
3. **Instrument reservation** — re-entrant timeout-aware locks, `attach/release`
   renamed `connect/disconnect`, per-step reserve/release auto-wrap, and
   `instrument.reserved/released` events (#11).
4. **Step/vector grain reshape** — the model finally settles: **vectors are
   condition points, steps are code**; one *relative* `vector_index`; scope-aware
   `context` so in-loop `configure()`/`observe()` land on the right vector (#32).
5. **slot → site** — the last 0.3.0 blocker: 0-based `site_index` everywhere
   (including STDF `SITE_NUM`), a frozen denormalized `site_name`, and a naming
   law codified — `*_index` = position, `count` = quantity, `*_number` = string
   identifier.

**v0.3.0 ships 2026-07-03** — "execution-grain reshape + at-rest schema freeze."
It's accompanied by a **schema-versioning system**: per-store `schema_version`
stamps, a whitelist-dispatch reader at all four store boundaries, deferral
(not permanent quarantine) of newer-version files, an opt-in migrate sink, and a
golden-corpus coexistence test. The baseline is reset to a **pre-1.0 epoch
signal (0.1)** — a deliberate "we're still allowed to break things" marker.

The final days (0.3.1, the current branch) push into **derived-index
versioning**: the daemon's DuckDB projections become **content-addressed**
(`_index.<fingerprint>.duckdb`), rebuilt on fingerprint mismatch, with `testerkit
data index` lifecycle tooling and a `seen_by` ledger — so the derived index can
evolve without corrupting or blocking readers on older/newer shapes. Daemon
reuse is keyed on the projection fingerprint (#64), the work in flight right now.

---

## Threads that run the whole length

Some concerns recur from January to July — they're the project's *values*, not
phases:

- **Everything derives from an append-only source.** The event log (Mar) and
  later the content-addressed derived index (Jul) are the same idea applied at
  different layers: store the truth once, derive every view, guard against drift.
- **Pydantic everywhere; kill hand-rolled parsers.** Stated in Feb, enforced
  repeatedly. Raw dicts are a code smell; `model_dump()` only at write boundaries.
- **Audit until clean.** The design-review loop (find → fix → *re-audit* →
  repeat) appears in March and never leaves. Docs got their own version of it
  (seven audit agents, per-page fix→re-audit).
- **Vocabulary is design.** An astonishing share of commits are renames:
  results_dir→data_dir, product→part, dut→uut, slot→site, attempt→retry,
  units→unit, spec→characteristics, telemetry→channels, logger→RunScope. The team
  treats *naming the concept correctly* as load-bearing, and pays the rename cost
  again and again to keep it honest.
- **Pre-1.0 means break freely.** Backcompat shims are added and then
  *deliberately dropped* ("pre-1.0 is loud-fail"). Schema versions are reset to
  0.1 twice as an explicit "not frozen yet" signal.
- **AI is a first-class consumer.** MCP + CLI + skills are peers to the human
  UI, present from week one and maintained in lockstep (every MCP tool has an
  HTTP equivalent; skills are re-synced whenever the shape changes).
- **Docs are verified, generated, and audience-fit.** The May marathon
  established that user-facing prose is checked against source, stripped of
  internals, generated where markers allow, and written for *test engineers*
  (LabVIEW/TestStand background), not framework authors.

## Direction changes worth remembering

- **Journal → event log** (Mar): the single biggest architectural turn.
- **Sequences → pytest-native** (Apr): the bespoke test-orchestration concept was
  deleted in favor of plain pytest + markers.
- **Capability model V1 → V2** (Feb): `TestRequirement` scrapped for unified
  `SpecBand`.
- **Thread-mode → InstrumentServer** (Mar): thread-based instrument concurrency
  dropped for a server with per-resource locking.
- **Execution-grain reshape** (Jun–Jul): vectors become condition points and
  steps become code — the at-rest schema rework that claimed the 0.3.0 release.

---

*Generated from `git log` on 2026-07-05. For the authoritative, living design
records behind each arc, see the execution diaries under
`docs/_internal/explorations/` and the architecture maps in `docs/_internal/`.*
