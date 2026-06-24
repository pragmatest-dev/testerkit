# Docs corpus review — execution diary

Living record of the corpus-wide documentation review (accuracy / approach / tone /
document lenses). Per-page loop: audit-coordinator → fix → re-audit → 0 critical → next.
This file is the cross-session source of truth for what's been done.

## Why this exists

Per-change docs checks haven't prevented accumulated drift — each change is reviewed in
isolation, so cross-page inconsistencies and post-refactor stale claims pile up. This is a
dedicated corpus-wide sweep across four lenses, worked one page at a time.

## Scope

113 pages in `docs/` (tutorial 14 · how-to 35 · concepts 22 · reference 42) plus
README.md, CHANGELOG.md, ROADMAP.md, the litmus-starter repo (README/WELCOME), and the
pragmatest.com landing copy (`src/app/litmus/page.tsx`). pragmatest syncs `docs/` via
`scripts/sync-litmus-docs.mjs`, so fixing `docs/` propagates — never double-edit synced
content.

7 generated reference pages are verify-only (regenerate, never hand-edit):
`reference/cli.md`, `reference/configuration.md`, `reference/data/event-types.md`,
`reference/data/query-api.md`, `reference/data/models.md`, `reference/runtime/api.md`,
`reference/overview/pytest-native.md`. Regenerate:
`uv run python scripts/generate_reference_docs.py --all`.

## Method (REVISED 2026-06-24 — cost + focus)

Per user direction, to control token spend and prioritize the highest-value work:
- **Two lenses per page:** `audit-accuracy` (factual safety net — I keep introducing subtle
  format/default errors, e.g. data_dir, row-per-measurement) + `audit-audience` (jargon→plain
  T&M language, prose simplification — the value the user most wants). Skip the full 6-lens
  coordinator; spot-fix obvious voice/marketing myself.
- **Self-verify small/factual fixes**; full re-audit ONLY after a critical or a structural rewrite.
- **Prioritized subset:** tutorial → concepts → hand-written reference. DEFER the 13 operator-UI
  reference pages + low-traffic how-tos to a later pass.
- Verify load-bearing format/schema/default claims DIRECTLY against source before writing — the
  audits miss these.

## The four lenses → audit agents

`audit-coordinator` runs all six on one page in parallel (writes `.tmp/page-audits/<slug>.md`):
accuracy → `audit-accuracy`; approach → `audit-ordering` + `audit-gaps`; tone →
`audit-voice` + `audit-audience`; document → `audit-crosslinks` + `audit-coverage`;
rendered site → `docs-reader` (Playwright).

## Resolved decisions

### pip vs uv — canonical install story (2026-06-23)

- **`pip install litmus-test` is the headline, universal install.** Works without uv. Use
  it as the primary install command everywhere.
- **uv is the litmus repo's own dev tool** (and a fine power-user choice for user projects),
  but is **not required for users**. Don't imply it is.
- The `litmus init` scaffold produces a standard PEP 621 `pyproject.toml` (`init.py:127`) —
  installable with plain pip. Present a pip path for scaffold deps; don't show `uv sync` as
  the only option.
- Don't mix `pip install` and `uv sync` within one flow without noting they're two tools
  (the quickstart bug: `pip install litmus-test` only fetches the CLI; `uv sync` then builds
  the project venv).
- **Examples stay on the uv workspace.** uv hard-errors if a workspace member drops
  `litmus-test = { workspace = true }` (verified 2026-06-23) — the line is mandatory for
  local-HEAD testing. Examples are repo-internal and not part of the user install story.

Known out-of-scope code follow-up (flagged, NOT changed in this sweep): `litmus init` prints
`uv sync` as its next step and warns when uv is missing (`project.py:92,191`), which implies
uv is required. Separate code decision for the user.

### Platform framing — what actually ships (2026-06-23, verified)

CLAUDE.md's "OpenHTF adapter" is loose positioning, NOT a shipped module. Verified:

- **There is no OpenHTF adapter.** `openhtf` is only a PyPI **keyword** (`pyproject.toml:22`);
  it is never imported (`grep "import openhtf"` → nothing). The 8 source files that mention
  "OpenHTF" do so in prose/comments only.
- **Non-pytest / existing suites integrate via two real, shipped surfaces:** the imperative
  `TestHarness` API (`docs/integration/runtime/harness.md` — "OpenHTF bridges, hand-written
  loops") and the `LitmusClient` results API (`src/litmus/client.py:345`,
  `docs/integration/runtime/pytest-existing.md:129–150` — explicitly LabVIEW / TestStand /
  standalone scripts).
- **Correct front-door framing:** "hardware test platform, pytest-primary, results API records
  runs from any source." Do NOT write "OpenHTF adapter" anywhere user-facing.

This recurs on `concepts/overview/platform-vs-framework.md`, `concepts/overview/pytest.md`,
`reference/runtime/*`, and the whole `integration/` tree — check each against this note.

### litmus_match surface (2026-06-23, verified)

`litmus_match(requirements=[...])` is an **MCP tool only** (`src/litmus/mcp/server.py:363`).
NOT a Python function, NOT in any `__all__`, NO CLI `match` command, and HTTP `GET /match`
accepts only `part_id`/`station_id` (`src/litmus/api/app.py:737`) — not the ad-hoc
`requirements` shape. The requirements dict key is `"unit"` (singular), not `"units"`.

### Coverage scan findings (2026-06-23, `.tmp/page-audits/_coverage.md`)

Corpus coverage is in very good shape. Real gaps, all in `docs/reference/data/` + env-var ref:

1. `channels.write_many` — in `channels.__all__`, zero doc references (only batch-write path).
2. `channels.declare` — in `channels.__all__`, zero doc references (front-loads units/dtype).
3. `litmus.queries` field-ref helpers (`ColumnSchema`/`FieldRef`/`FieldRole`) — exported from
   the public Query API namespace, only incidentally mentioned; no defining entry.
4. `LITMUS_CHANNELS_SYNC_PUSH` — channels tuning knob missing from cli.md Environment Variables.
5. `XYData` — promoted to a top-level export, under-defined next to its sibling `Waveform`.

Process note: `.tmp/public-surface-inventory.md` (2026-05-16) is materially stale — regenerate
before any accuracy audit that diffs against it.

### Corpus-wide fact-fixes (verified against source; apply SOURCE-FIRST, then propagate)

- **FOUR-store model (NOT three).** There are 4 user-facing stores: EventStore, ChannelStore,
  **FileStore** (`data/files/`, `file://`, artifacts — verified `files/store.py:58`), RunStore
  (parquet). The "three stores" framing is STALE corpus-wide (predates FileStore; a known ROADMAP
  "four-store model" task). `concepts/data/three-stores.md` FIXED to four (2026-06-24). 17 files
  still say "three stores": tutorial/{03,10,11,12}, how-to/data/{index,grafana-dashboards,
  find-flaky-tests,querying-channels}, reference/data/{performance-limits,outputs,index},
  integration/data/index, concepts/data/{three-verbs,sessions,flight-streaming},
  concepts/overview/{platform-vs-framework(COMMITTED — enumerates 3, missing FileStore),architecture}.
  Distinguish ENUMERATION errors (list exactly event/channel/parquet → wrong, add FileStore) from
  LINK-TEXT/nomenclature ("[three stores](three-stores.md)" → just rename to "the data stores").
  **RESOLVED 2026-06-24: user chose FULL sweep + file rename.** `three-stores.md` → `data-stores.md`
  (git mv); all ~39 path refs updated; all "three stores" text gone (0 remnants in docs/ + src/);
  8 enumeration errors fixed (+FileStore, ParquetBackend→RunStore); grafana="event/channel/run",
  perf-limits="four"; 2 store-layer mermaids (overview.md + platform-vs-framework) got a Files node;
  ontology litmus.yaml docs: paths fixed. NOTE pre-existing dangling ref: ontology L721
  `docs_extra: docs/concepts/results-storage.md` — that file does not exist (NOT caused by rename).
  Pending user decision on sweep scope.
- ATML mention: there is NO ATML exporter (exporters are csv/hdf5/json/mdf4/stdf/tdms).

- **Fixture count: DROP the literal (decided 2026-06-24, user).** Don't note a number
  anywhere — brittle, drift-prone (it WAS wrong: docs said "20" while the real public
  count is **22**), and unactionable; the fixture LIST is self-counting. The plugin
  defines 22 public `@pytest.fixture`s (`__init__.py`); `stream` (`:965`) and `observe`
  (`:997`) are genuine fixtures MISSING from the hand-written `reference/pytest/fixtures.md`
  (NOT generated). DONE: dropped the count from `reference/pytest/fixtures.md` L3 + concepts
  `overview/pytest.md`. DONE 2026-06-24: added `observe` + `stream` to `reference/pytest/fixtures.md`
  (at-a-glance "Recording outputs & streams" row + detail sections; verified signatures + `stream`
  returns the `channel://` URI). Dropped explicit fixture-number wording on the other citing pages: `tutorial/quickstart.md`, `tutorial/09-production.md`,
  `integration/runtime/pytest-existing.md`, `reference/index.md`,
  `reference/pytest/{index,markers}.md`, `how-to/execution/writing-tests.md`,
  `concepts/overview/pytest.md`, and `reference/overview/pytest-native.md` (GENERATED —
  trace its "20" to the source docstring/script, fix there, regenerate). README's
  current fixture wording was already corrected during its pass; recheck it carries 22.

## Pieces (worked in order; per-page loop within each)

- **Piece 0** — Corpus scans + pip/uv resolution. ✅ DONE (2026-06-23). Coverage scan run;
  pip/uv + examples decisions locked above.
- **Piece 1** — Install/entry cluster (pip/uv sweep): README.md, tutorial/quickstart.md,
  tutorial/index.md, how-to/overview/mcp-integration.md, reference/overview/skills.md,
  CHANGELOG.md, ROADMAP.md. Also re-audit this session's prior edits (Codespaces badge,
  "Explore without hardware", 0.3.0 Colab row).
- **Piece 2** — tutorial/ step pages (01–12).
- **Piece 3** — concepts/ (22).
- **Piece 4** — how-to/ (35).
- **Piece 4b** — integration/ (10): data/{grafana,index,lakehouse-import,logging,results-api},
  runtime/{harness,index,instruments,pytest-existing}. (Found 2026-06-23; the handoff folded
  these into "124" without breaking them out. Carry the platform-framing note above.)
- **Piece 5** — reference/ hand-written (35; excludes the 7 generated). Fold in coverage gaps
  1–5 here (data/channels + query-api + env vars).
- **Piece 6** — reference/ generated (7, verify-only; fix source + regenerate if wrong).
- **Piece 7** — External surfaces (starter README/WELCOME, pragmatest landing).
- **Piece 8** — Final rendered-site docs-reader pass.

## Per-page progress log

### Piece 3 — concepts (lean 2-lens)
- concepts/execution/outcomes — factually PERFECT (38 claims verified: enum/ladder/cascade/
  verdict-intent/builders all correct). Pure audience scrub: removed bottom-half internals
  (materializer fallback, accumulators, keyboard-interrupt hook chain, abort()-doesn't-save ×3,
  parquet readback fallback, check_limit/SlotResult-strings); cut the "Persistence path" subsection;
  "cascade"→"rolls up from" in table cells; kept the conceptual ladder/cascade-rule section. ✅
- concepts/data/data-stores — full four-store rework (see four-store sweep above). ✅
- concepts/configuration/parts — accurate; fixed `load_part("str")`→`load_part(Path(...))` (would raise
  AttributeError); relabeled Characteristics bullets to real YAML keys (direction/function/unit/pins/bands,
  defines `function`); specs→spec bands; de-stuffed part_number parenthetical; +`driver` inherited field.
  `uut_part_number` confirmed CORRECT (not dut_part_number here). ✅
- concepts/configuration/capabilities — 4 audience CRITs: internals (`_directions_compatible`/
  `capability_satisfies`), `Domain+SignalType` tombstone, evaluator Lineage-table+hype, validator-narration.
  Accuracy: BIDIR is asymmetric (not "satisfies both"); readback NOT excluded by the matching service.
  KEPT real `find_compatible_stations`/`station_id`/`/api/match?part_id=&station_id=` (system identity, not
  operator labels). Re-audit 0/0. ✅
- concepts/configuration/stations — 2 accuracy CRITs: station-TYPE example missing required `driver:`;
  station-INSTANCE example missing required `name:`. `test_phase` (CLI/session) ≠ `supported_phases`
  (station field) — conflation fixed. `InstrumentServer`/RPC→user-facing; +`driver`/`mock` table rows.
  Re-audit 0/0. ✅
- concepts/configuration/fixtures — 35/37 accurate; scrubbed multiprocessing/RPC internals
  (`InstrumentServer`/`RemoteInstrumentProxy`/"not raw TCP" — it IS TCP localhost); `concurrent=True` is NOT
  a user flag → switches exempt because `type: switch`; cut "backward-compatible" tombstone + `src/...`
  citation + `extra="forbid"`. ✅
- concepts/overview/ai-integration — accurate (13 claims; page makes few specific claims, all
  correct — no MCP-count to go stale, litmus_run mock-only not violated). schema→config/YAML jargon,
  added MCP one-line gloss, dropped `src/litmus/skills/` path leak. ✅
- concepts/overview/pytest — accurate (platform-not-plugin framing correct; litmus_retry/flaky verified).
  Dropped brittle fixture count (docs said "20", REAL public count is **22** — observe+stream were
  uncounted), tightened LLM-training marketing, flags→markers. → triggered corpus-wide fixture-count
  DROP (8 pages, no number anywhere) + fixed 12+ broken `litmus-fixtures.md`/`litmus-markers.md` →
  `fixtures.md`/`markers.md` links. observe+stream reference entries still pending (Piece 5). ✅
- concepts/data/sessions — CRIT: added the missing "a session is derived from events grouped by
  session_id, not a stored table" framing; removed fabricated `channel_refs` RunStarted field;
  lifecycle/context-manager jargon + `EventLog` internals scrubbed. ✅
- concepts/data/event-sourcing — factually perfect (24 claims). Audience: scrub internals
  (`AccumulatorPool`/`materialize_run_to_parquet`/subscribers), de-disparage CRUD ("trap"/"unappealing"/
  "footgun") + gloss it once, WAL→event log, projection glossed. ✅
- concepts/data/event-log — 3 accuracy CRITs (retired `InstrumentRead` listed live; `RunMaterialized`
  "not in union" wrong; Test category 4→7 events) + storage filename `-{pid}` / retired `_ref` dir;
  heavy internals+tombstone scrub (title, "Previous approaches", EventBase/EventLog/EventSubscriber/
  EventStore/Flight do_put). Done via docs-writer; re-audit 0/0. ✅
- concepts/data/flight-streaming — accuracy: NOT "in-memory" (on-disk `_index.duckdb` + live overlay);
  no phantom `connect()`; `release()` is a no-op (no ref-decrement); bootstrap ingests (not registers);
  file-per-process. Heavy Flight/daemon/gRPC/do_put scrub. docs-writer; re-audit 0 crit. ✅
- concepts/execution/step-hierarchy — factually accurate (27 claims). Cut OpenTAP framework-comparison
  + private internal names (`_step_stack`/`assign_indices`/`_stamp_container_outcome`); container-not-
  sweep-only clarify; record_type projection plainened. ✅
- concepts/execution/step-manifest — 3 accuracy CRITs (`step['step_outcome']`→KeyError, should be
  `['outcome']`; `items` type missing `int`; fabricated `name` field + omitted manifest-critical
  vector_count_planned/step_index/vector_index). Heavy audience scrub: title (StepsDiscovered→manifest),
  How-it-flows impl-chain diagram cut, `materialize_run_to_parquet`/`AccumulatorPool` removed,
  never-ran reframed from NULL-jargon to plain, synthetic→placeholder. docs-writer; re-audit 0/0. ✅
- concepts/overview/architecture — four-store table VERIFIED correct (sweep held; RunStore not
  ParquetBackend; FileStore present). Fixed Framework→Litmus heading (platform conflation), lead diagram
  parquet→event-log-source-of-truth, `units`→`unit` (ER ×2), event path `-{pid}`, get_limit signature,
  cut Mermaid-internals note. ✅
- concepts/{index, overview/index, execution/index} — fixed severity-ladder ORDER (was passed/failed/
  errored/skipped/done/... — skipped+done are sev 1-2, belong FIRST): now skipped→done→passed→failed→
  errored→terminated→aborted. "framework's mental model"→"platform's"; added missing three-verbs link to
  concepts/index data section; "materializer"→"platform". configuration/index + data/index clean. ✅
- ONTOLOGY docs-ref check (src/litmus/ontology/litmus.yaml) — 10 stale FLAT doc paths fixed to their real
  subdirectory homes (sessions→data/, parts/stations/fixtures/capabilities→configuration/, step-manifest→
  execution/, event-log/flight-streaming→data/, capability-model→configuration/capabilities);
  results-storage.md docs_extra → reference/data/parquet-schema.md. LEFT (intentional, per file header):
  architecture-erd.md + ontology.md = GENERATED outputs. capability-schema.md RESOLVED 2026-06-24:
  repointed to `docs/reference/catalog/schema.md` across 6 refs (ontology, CLAUDE.md, 4 catalog skill
  files) — that page verified to fully cover the Capability model (signals/conditions/controls/
  attributes/SpecBand). No new page needed.
- concepts/overview/platform-vs-framework — CRIT MCP count 12→13 (+`litmus_files`, `Cpk`→`Ppk`);
  POST /api/runs verified real; no OpenHTF-adapter claim; audience prose. ✅ (MCP-count drift recurs
  corpus-wide — watch ai-integration, reference pages.)
- concepts/data/three-verbs — page highly accurate (verbs verified; `Observation` event IS real);
  10 jargon fixes (polymorphic/orthogonal/role-keyed/fused-prefixes/clobber/latching) + ERRORED outcome.
  Kept "stamps" (established framing) + the storage-partition section (legit for a concepts page). ✅


(Append one line per page as it converges to 0 critical: `<piece> <path> — <date> — <notes>`.)

- Piece 1 README.md — 2026-06-23 — fix pass 1 cleared 8 criticals (plugin→platform
  framing, 4 tests→1, ATML drop, 2 dead links, uv de-coequal, units→unit, MCP-only).
  Re-audit surfaced 3 pre-existing criticals (counts/path): chapter 10→12 ✓, results
  path→data/ gitignored ✓, examples framing→seven-step chain ✓. Spawned the
  examples-portability design (separate exploration doc + ROADMAP entry). CERTIFIED
  0 critical on 3rd pass (2 non-blocking warnings deferred to topic pages). ✅
- Piece 1 tutorial/quickstart.md — 2026-06-23 — fixed 12 criticals (uv two-tool flow
  → pip-only `pytest`; marketing voice ×3; plugin/framework framing ×2+1 stray;
  install-section dup removed; prereq Python 3.11+ added; plural→single test). The 9
  cross-link "criticals" were FALSE (paths exist). Cheat-sheet forward-links kept as an
  intentional quickstart device. Re-audit CERTIFIED 0 critical. ✅ One tracked warning:
  "20 fixtures" → DROPPED (no number; see fixture-count note above). observe/stream entries pending Piece 5.
- Piece 1 tutorial/index.md — 2026-06-23 — fixed 3 criticals (Batteries-included opener
  removed; canonical `pip install litmus-test` added to Quick Start block + Prerequisites,
  repo-dev `uv sync`/`-e .` removed). False broken-link + `--starter`-missing findings
  disproven. Re-audit CERTIFIED 0 critical. ✅ Tracked warning: "~17 other fixtures"
  (fixture-count cluster).
- Piece 1 how-to/overview/mcp-integration.md — 2026-06-23 — DEEP drift, 4 audit passes.
  Fixed 10 initial criticals (tool count 12→13 + `litmus_files` row; 5 uncallable query
  examples rewritten with verified signatures — events/metrics are NOT run-scoped;
  `Mock(driver_class)`→"a mock"; `Cpk`→`Ppk`; `results/` dir removed; uv→pip; file:line
  internals scrubbed; 2 setup gaps filled). Then 2 fix-introduced criticals
  (`run["run_outcome"]`→`["outcome"]` per RunRow.outcome; `setup show` can't verify) + 1
  deeper pre-existing (`litmus_run` ALWAYS `--mock-instruments`, `tools.py:1128`). Final
  ordering "critical" = tool-inventory forward-refs, downgraded (same call as the quickstart
  cheat-sheet). CERTIFIED 0 blocking critical. ✅
  CODE BUG to flag: `litmus setup show` (`setup_cmd.py`) prints a STALE hardcoded tool list
  (`list_parts`/`get_part_spec`/… — names that no longer exist) and reads no client config.
- Piece 1 reference/overview/skills.md — 2026-06-23 — fixed 10 criticals (tool count 12→13 +
  `litmus_files`; prereq + stdio-spawn model added; ASCII three-layer diagram → markdown list
  killing the "Task tool" jargon; "model tier→source file" softened; "confabulate"→neutral;
  `prompts/get` protocol jargon → plain; wrong `src/litmus/skills/` install path → `litmus/skills/`).
  Kept GitHub source links (shipped-artifact refs for a skills reference); "3 workflows" is the
  correct count. Re-audit CERTIFIED 0 critical. ✅ Non-blocking: `refs/` table lists 1 of 5 files.
- Piece 1 CHANGELOG.md — 2026-06-23 — fixed 6 criticals: 4 accuracy (all describing APIs
  renamed/removed BEFORE their release tag — `ChannelClosed`→`ChannelEnded`,
  `StreamStarted/Ended`→`FileStarted/Ended`, `MeasurementRole`/`Axis`→`FieldRole`,
  `FileStore.resolve_uri`→`read`/`read_range`/`open_input`) + 2 internal-path leaks
  (`designer/page.py`, `_wait_for_run` test path) reworded to user-facing symptoms. Also
  `@litmus_test`→pytest-native (0.1.0 never shipped it) and scrubbed the `test_perf_daemon.py`
  path. Re-audit CERTIFIED 0 critical. ✅

### Piece 2 — tutorial (lean 2-lens method from 06-24)
- 02-mock-instruments — accurate as-is; 5 jargon fixes (quacks-like→stand-in, factory→helper,
  seam→fails-loudly, lift-conditional→move-the-choice). ✅
- 03-fixtures — 1 CRIT (`measure(..., allow_repeat=True)` via fixture = TypeError; allow_repeat
  is RunScope-only → replaced w/ channels `stream` pointer) + storage reframed to query-view +
  `done` outcome added + brittle fixture count DROPPED (sidesteps 20→22 drift) + jargon. Re-audit 0. ✅
- 04-limits — accurate (outcome ladder + full comparator table verified); 6 jargon fixes. ✅
- 05-configuration — 2 CRIT: (a) `get_param("key")` does NOT raise, returns None/default
  (harness.py:831); (b) precedence was BACKWARDS — actual is inline<sidecar<profile, sidecar
  WINS (cascade appended after inline). Plus `@pytest.mark.flaky`→`litmus_retry` (respects the
  no-flaky axiom; litmus_retry wraps rerunfailures) + `changed()` first-vector + jargon. Re-audit 0. ✅
- 06-specifications — CRIT: step 6 never showed the `characteristic:` AUTO-DERIVE (the whole point) —
  page hand-computed limits + leaned on `spec_ref` (a no-op note); Conditions example claimed
  per-condition resolution with NO `characteristic:` binding (resolved nothing). Introduced
  `characteristic:` (alone uses band's own accuracy → 3.3±5%=3.135/3.465; verified), fixed conditions,
  `tolerance_pct` vs `guardband_pct`. Re-audit 0. ✅
- 07-real-instruments — `Zero`→`None` mock default; `measure`→`verify` (limit was inert with measure);
  `litmus_mocks`(marker) vs `mocks:`(sidecar key) disambiguated; `--station=bench_1` id form; jargon. ✅
- 08-capabilities — CRIT `match.missing`→`match.match_result.missing` (AttributeError); removed false
  `MatchDepth.ACCURACY` knob claim (API hard-codes RANGE); CUT old Domain+SignalType tombstone. Re-audit 0. ✅
- 09-production — promote glob `*/` level, `results/`→`data/`, `--station`/`--fixture` id forms,
  Abstraction/node-id jargon, dropped brittle fixture count. ✅
- 10-live-monitoring — CRIT: "Channel Data" built on RETIRED `InstrumentRead` + invented
  `{"_ref","length"}` event shape → reads route to ChannelStore, only `ChannelStarted`, `channel://`
  is a URI STRING. Channel ids `dmm.voltage`/`scope.waveform` (GenericObserver PREFIX-STRIPS
  measure_/read_; re-audit caught my OWN wrong `.measure_voltage` fix). Cut under-the-hood internals. Re-audit ✅.
- 11-waveforms-and-evidence — accurate; added missing `import math`; URI→link, dropped sample count,
  synthesizer→mock. (observe stamps out_<name>, channel id = user-given name — verified.) ✅
- 12-continuous-monitoring — filename uses channel_id VERBATIM (`dmm.voltage_…arrow`, dot not sanitized);
  cut Flight-subscription/`out_*`/`Observation`-event internals; lifecycle/proxied/push-style jargon. ✅
- PIECE 2 COMPLETE — all 12 tutorial steps at 0 critical (6 had real criticals). Lean 2-lens method
  held ~50–150k/page; re-audit only on criticals (caught 2 of my own fix-introduced errors).

- Piece 1 ROADMAP.md — 2026-06-23 — fixed 1 tone critical (L1277 "limit-setting today is
  intuition + guesswork" → "engineering judgment … this adds a … loop on top of that") + ATML
  removed from the exporter list (no ATML exporter) + split a malformed concatenated RICE row
  (L46). My examples-portability entry verified consistent with source. ✅ FLAGGED for user
  (not fixed — can't determine intent): L1691 no-op self-rename
  "`litmus.pytest_plugin` → `litmus.pytest_plugin`" (typo; intended target unknown).
  PIECE 1 COMPLETE — install/entry cluster all 7 pages at 0 critical.
- Piece 2 tutorial/01-first-test.md — 2026-06-23 — full rewrite resolving 6 structural
  criticals (dual reader-context, two conflicting first-tests, `verify`-before-precondition,
  repo-dev clone+uv-sync install). Now centers on the real `litmus init --tier=bringup`
  scaffold: pip install → scaffold → `pytest -v` (3 smoke tests); `verify` shown as scaffold
  output with precondition stated + full explanation deferred to steps 3-4. Every code/CLI/
  path/fixture claim verified against source (init.py:147-179,644-682; pytest_plugin verify).
  CERTIFIED 0 critical (5 passes). USER caught 2 errors the audits missed: (a) bringup
  `litmus.yaml` has NO `data_dir` — `data_dir: data` is bench/starter-only (`init.py:73-74,234`),
  so bringup runs go to the GLOBAL platformdirs store; (b) measurements are NOT "a parquet row
  per measurement" — at rest they're a nested LIST<STRUCT> on the vector row (schemas.py v2),
  flat-per-measurement is only a query-time UNNEST. Also: the store is the "run store" (RunStore),
  used everywhere — not "data store". Cut the premature parquet/data_dir/traceability detail from
  step 1 entirely. WATCH for these traps on later pages.
