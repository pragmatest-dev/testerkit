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

### Piece 4b — integration (migration/integration surface; competitor refs ALLOWED here for concept-translation/migration)
- integration/data/grafana — 3 CRIT accuracy (the page predated today's measurement_values source fix):
  listed only 4 views, called `measurements` the flat per-measurement source (it's the NESTED vector rows),
  said "four tables". Rewrote the SQL-views section to the 5 current views (added `measurement_values` =
  flat one-row-per-measurement UNNEST; `measurements` = nested; `runs` = per-run aggregate). Audience: dropped
  the brittle "ten dashboards" counts (heading/diagram/intro), removed the `src/litmus/grafana/dashboards/`
  source-path leak (→ `litmus grafana export`), uv-pip→pip, "operators pick part"→DUT part number,
  "plugin requirement"→data source, added a quickstart pointer + two verified example queries (runs FPY-by-
  station; measurement_values SPC). ✅

### Piece 4 — how-to (lean 2-lens; how-to quadrant = runnable recipes, pip-not-uv, no competitor refs)
**PIECE 4 COMPLETE — all 35 how-to pages done** (execution 11, data 14, configuration 4, catalog 2, overview 3,
root index 1). Sub-cluster summary: execution + data + configuration + catalog + overview all closed; biggest
single change = mcp-integration 466→157 (cut duplicated datasheet→test walkthrough). Real bugs found across
the cluster: unwired limit fields, verify(uut_pin=) TypeError, retired instrument.read event, fabricated
retention config, ChannelStore path bug, idPart/idProduct product→part leak, sidecar vectors:→sweeps:,
/catalog-scaffold non-command, Cpk→Ppk, missing Files screen, units→unit. ⚠️ SOURCE bugs flagged for follow-up:
(1) Grafana dashboards query flat columns on the nested measurements view; (2) mcp/server.py + skills
datasheet-to-test write invalid `vectors:`/`{ref:}` YAML; (3) instrument.read stale in 4 source docstrings.
- how-to/index (root) — fixed link descriptions to match revised pages (dropped "14 sidebar entries";
  "subprocess-per-slot"→parallel; "ATML metadata"→UUT/part/pin/instrument identity; "design guide"→pause-for-
  input; "time-series data plane"→instrument data) + ADDED the 5 missing Data links (choosing-a-channel-verb,
  capture-waveform, stream-live-channel, capture-an-artifact, benchmarking) so the root index is complete. ✅
- how-to/overview/mcp-integration — accuracy: litmus_discover scans ALL protocols (not VISA-only);
  "--print-only" is on the setup <client> commands (setup show IS the print cmd). The 13-tool count is
  CORRECT but dropped the brittle restatements (13×3, "7-value", "Four steps"); scrubbed stdio/transport +
  dispatcher/subprocess/returncode-table internals. BIG STRUCTURAL: CUT 466→157 lines (~309) — removed the
  duplicated datasheet→test authoring walkthrough (part-spec/station-YAML/sweeps/limit-shapes/test-pattern/
  checklist; it lives on datasheet-to-test.md) → "What the agent does next" + link. Registration recipe
  (setup table, mcp serve, verify step, 13-tool table, CLI-as-peer callout) INTACT. docs-writer; re-audit 0/0. ✅
- how-to/overview/index — dropped the wrong+brittle "14 sidebar entries" count. **OVERVIEW how-to sub-cluster
  (3 pages) DONE.** ✅
- how-to/overview/operator-ui-tour — 3 CRIT accuracy: Metrics tab Cpk→Ppk; dropped the wrong+brittle counts
  ("16 entries/15 screens" → actually 17/16); ADDED the missing Files `/files` screen to DATA STORES. Plus
  Inventory chip is 3 states (Configured/In use/Observed, not 2); Events "Session" is URL-only (removed from
  the visible-filter list); System Designer flagged experimental. Audience: "per-step instrument arrays"/
  "at runtime/by design" internals→operator-friendly; added the click-path sentence. NOTE: LEFT
  `/launch?station=<id>` — accuracy confirms the route uses the station id (s.id), NOT hostname, so I
  OVERRODE the audience agent's hostname suggestion (it's a click-populated route param). ✅
- how-to/catalog/datasheet-to-test — CRIT accuracy: sidecar `vectors:`→`sweeps:` (SidecarConfig extra=forbid
  rejects `vectors:` — the example would FAIL to load); `parts/<part_number>.yaml`→`parts/<id>.yaml` (filename
  from Part.id, not part_number); removed `/catalog-scaffold` (NOT an installed command — only
  /catalog-from-datasheet + /process-catalog are; fast path→natural-language ask). Audience: "Pydantic
  model"→"catalog schema", derivation narration→action+link, "highest-leverage flow Litmus ships" hype→
  factual, "six phases"(1/2/2b/3/4/5)→"the phases". pip install litmus-test LEFT (correct user install).
  docs-writer; re-audit 0/0. ✅ ⚠️ SOURCE BUG (flag to user): mcp/server.py datasheet-to-test prompt +
  skills/datasheet-to-test.md write `vectors:` sidecar + `{ref:}` limit-delegation examples that produce
  INVALID YAML per the models (real keys: `sweeps:`, `characteristic:`) — AI clients following the bundled
  prompts generate non-loading configs.
- how-to/catalog/index — reviewed, clean (links resolve; no changes). **CATALOG how-to sub-cluster (2 pages)
  DONE.** ✅
- how-to/configuration/custom-drivers — CRIT accuracy: `idPart`/`PART_ID` — the product→part rename WRONGLY
  hit pyusb's legit USB `idProduct` API (→ TypeError) — restored idVendor/idProduct + PRODUCT_ID. Rest
  verified (28 claims: Instrument/VisaInstrument bases, Mock(MyDMM)→isinstance True vs platform Mock(object)
  →False, driver_class(resource) single-arg, pyvisa-sim voltage→MEAS:VOLT?/current→MEAS:CURR?, _sim_responses).
  Audience: led with "a driver is just a Python class" + 6-line MyDMM, DELETED the ASCII inheritance tree +
  Architecture-overview; scrubbed ABC/importlib/instantiates/MockClass/mocks.py/_get_sim_value/three-pipelines;
  added pip install nidaqmx/pyusb; dropped "seven markers" count. docs-writer; re-audit 0/0. ✅
- how-to/configuration/index — reviewed, clean (value prop + descriptions accurate, links resolve; no
  changes). **CONFIGURATION how-to sub-cluster (4 pages) DONE.** ✅
- how-to/configuration/mock-mode — factually PERFECT (27 claims: Mock(object,…) substitution [not driver
  subclass], silent-None-on-typo, mock_config scalar/{nominal,sigma}/callable/dict shapes, enable paths
  CLI>env>litmus.yaml, litmus_mocks cascade file→class→test→profile, test_phase auto-demote, deleted
  *voltage*/*current* auto-mock fallback correctly ABSENT). Pure AUDIENCE restructure: added "pytest passes
  anywhere" value prop; demoted "what mock does"/"three layers" internals to a "How it works" note + a 3-row
  WHERE/WHEN/USE table; scrubbed Mock(object)/raw-patch.object/_mocks/isinstance/mermaid internals; surfaced
  the typo→None gotcha as one prominent callout; `uv add --dev`→`pip install pytest-mock`. docs-writer. ✅
- how-to/configuration/configuring-stations — 2 CRIT accuracy: station-TYPE examples omitted the required
  `InstrumentConfig.driver` (ValidationError) — added drivers; `load_station("str")` AttributeError snippet
  →`pytest --collect-only --station=`. Also: `name` required (table+examples), `id` defaults-to-stem,
  `supported_phases` display-only (no enforcement), `channels` dict[str,str], env-var best-practice REMOVED
  (loader doesn't expand ${VAR}). Audience: ADDED the missing role→fixture recipe ("Using a station's
  instruments in a test"); scrubbed Shared-Instruments InstrumentServer/RemoteInstrumentProxy/file:line→
  1-line role-based-sharing + multi-uut link; removed L36 src file:line; dotted-path jargon; capability
  resolution-chain. docs-writer; re-audit 0/0. ✅
- how-to/data/index — accuracy: removed ATML from the export interchange list (`litmus export` =
  CSV/JSON/STDF/HDF5/TDMS/MDF4; the ATML exporter was dropped). Audience: push-style→"as samples land",
  data-plane→"instrument data", PIL.Image→"image". **DATA how-to sub-cluster (14 pages) DONE.** ✅
- how-to/data/benchmarking — in good shape (18 claims: `litmus benchmark` CLI, --full/--rounds/-o/--no-save
  flags, 4 stores, best-of-N=min, dated .benchmarks/<date>/ with report.md+report.json, psutil footprint).
  Accuracy: --full concurrency sweep is 1/2/4/8 not 1/2/4. Audience: coefficient-block/"extrapolated from
  coefficients"→"per-operation time and size". (pip install 'litmus-test[benchmark]' correctly LEFT — pip
  is the user workflow; audience agent's uv suggestion declined.) ✅
- how-to/data/mcp-query-runs — factually PERFECT (34 claims: litmus_runs/steps/metrics + 6 metrics actions
  summary/pareto/ppk/trend/retest/time_loss, filters part/station/phase/since/until, phase default excludes
  development, period day/week/month, run_id[:8] prefix). Audience: de-jargoned client-side/in-memory/
  parquet-store/step_path-derived/"JSON instead of pixels"; removed the "Assets tab has no MCP equivalent
  yet" tombstone; trimmed the UI-tab↔MCP-action concept prose→action table; station prod-1→bench-3.
  docs-writer. (pip/uv: agent suggested switching to uv — NOT done; pip is the user workflow.) ✅
- how-to/data/mcp-debug-failures — factually PERFECT (27 claims: litmus_runs/steps/events/sessions/
  channels/open tools + params, outcome taxonomy failed/errored/terminated/aborted, run_id[:8] prefix match,
  max_points/LTTB). Audience: fixed broken link `../how-to/data/querying-channels.md`→`querying-channels.md`;
  trimmed RunEnded/close-time-fallback + canonical-signal/catch-all event internals; "ship over the wire"/
  "server-side decimation"→plain; connect()-lifetime→"the session it ran in". ✅ (kept a4f8b201 worked-example
  prefix — verified runnable via run_id[:8] match, explained at the page's prefix tip.)
- how-to/data/grafana-dashboards — accuracy: the `measurements` SQL table is RAW NESTED run rows (the view
  is `SELECT * FROM read_parquet`, no UNNEST), NOT "one row per measurement" — corrected to say
  `UNNEST(measurements)` in panel queries; "naive UTC at pgwire layer"→"exposed as naive UTC" (conversion
  is in the view defs). Audience: `pip install 'litmus-test[grafana]'` (quoted; pip is the USER workflow —
  did NOT follow the audience agent's switch-to-uv suggestion, which contradicts policy); `<data_dir>`
  auto-resolves note. DEFERRED: did not add a from-scratch "build one panel" SQL section — can't verify the
  SQL runs against the live pgwire views. ✅
  ⚠️→✅ SOURCE BUG (now FIXED, 2026-06-25): the Grafana measurement dashboards queried flat
  `value`/`measurement_name`/`outcome` columns absent from the nested `measurements` view. A regression test
  (tests/test_grafana_server.py) revealed the bug was WORSE than analysis predicted: the `measurements` view
  itself raised `BinderException` on real data — its REPLACE clause referenced `measurement_timestamp`, a
  column removed in schema 2.0 (it's nested), so `create_connection` threw before any dashboard could load.
  FIX: removed the dead `measurement_timestamp` REPLACE; added a flat `measurement_values` view (UNNEST of
  the nested list, time = the measurement's own `m.timestamp` which defaults to record-time _utcnow);
  repointed the 5 measurement dashboards (13 `FROM measurements`→`FROM measurement_values`, leaving template
  vars + `FROM runs`). Verified by the new regression test (writes a nested vector row, asserts the unnest).
- how-to/data/find-flaky-tests — tone correctly frames flakiness as investigate-the-hardware (no
  mark-and-skip). 3 accuracy WARNs fixed: `m.outcome`→aliased `measurement_outcome` (prose referenced a
  column the query didn't expose); dropped the "same `vector_index` per retry" invariant (Mode-2/vectors-
  fixture only — wrong for the page's own unswept example); reframed `litmus_retry` from "can't fix root
  cause yet"→an auditable retry budget for genuinely non-deterministic hardware. Audience: moved the
  ProjectConfig note before the glob; cut "pytest-rerunfailures under the hood"; tuple→"one row per step". ✅
- how-to/data/export-results — factually PERFECT (24 claims: `litmus show -f html/pdf/json/csv` +
  `litmus export -f csv/json/stdf/hdf5/tdms/mdf4` BOTH real; -t templates HTML/PDF only; exports/<fmt>/
  default; PDF via WeasyPrint gated on [pdf] extra). Audience: DELETED the exporter-architecture paragraph
  (Arrow IPC/subscriber/format_name); scrubbed Jinja2/src-exporters-path/denormalized-parquet/events-file
  leaks; added the PDF extra `pip install 'litmus-test[pdf]'`. docs-writer. ✅
- how-to/data/compare-runs — factually PERFECT (18 claims: SQL UNNEST(measurements), record_type=vector,
  struct fields name/value/outcome/limit_low/high, /results+/channels routes, litmus show -f csv). Audience:
  trimmed at-rest-parquet narration→MeasurementsQuery steer (../../reference/data/query-api.md) + schema
  link; "~10-step" count→decision rule; .tmp/→cwd. ✅ (read_parquet glob into runs-store layout =
  established sibling-page convention; framed as the power-user fallback under the Query API.)
- how-to/data/stream-live-channel — factually PERFECT (18 claims verified: channels.stream/.write,
  latest/live/window/query, connect/instrument, ChannelStarted). Audience: cut Flight-transport internals
  leak (L73→link); renamed "sink"→`ch` and dropped "sink"/"context manager" framing; added the in-test
  `stream` fixture vs store-direct `litmus.channels.stream` distinction (key gap); de-jargoned
  "subscriptions", trimmed store-on-disk narration. docs-writer. ✅
- how-to/data/capture-an-artifact — CRIT accuracy: removed the `load_file` from `litmus.data.backends.parquet`
  read-back example — claimed it returns a PIL.Image but `load_file` on a .png ref returns raw BYTES (no
  decode on the read path; serializer registry is write-only) + backend-internal import → UI read-back.
  Fixed routing (scalars INLINE on the measurement row, not ChannelStore). Audience: blob→file/artifact,
  routing-theory→link, serializer-registry→handler, lifecycle-events/active-vector→plain. docs-writer;
  re-audit 0/0. ✅
- how-to/data/capture-waveform — in good shape (13 claims verified: observe→ChannelStore routing,
  Waveform Y/dt/t0, channel:// URI, Mock(Scope, capture=)). Accuracy: LTTB threshold 500→1,000 points;
  Waveform import `litmus.data.models`→`litmus` (match the examples). Audience: observe URI-stamp narration
  →action+link, vector/parquet-row→"this test's measurement rows", synthesize_psu helper one-liner. ✅
- how-to/data/querying-channels — CRIT accuracy: removed the `ChannelStore(Path("<data_dir>/channels"))`
  example — wrong (store appends channels/ itself → `channels/channels/`, empty) AND the direct-store glob
  read is discouraged (boundary breach). Lead with `channels.query` (in-process), `ChannelClient` for remote.
  Audience: UUID→placeholder + source note, LTTB-algorithm section→benefit+link, decimation/daemon-index/
  instrument-proxy jargon, added end-to-end plot snippet (verified cols `received_at`/`value`). docs-writer;
  re-audit 0/0. ✅
- how-to/data/choosing-a-channel-verb — factually PERFECT (23 claims; ALL/LATEST = live-every-sample vs
  latest-newest-only verified correct). Audience: de-jargoned subscription/push/pull/conflated/coalesced →
  bench language; added a decision tree (the page's key job); collapsed channel-store concept narration→link.
  docs-writer. ✅
- how-to/data/querying-events — 2 CRIT accuracy: `event_type="instrument.read"` (×3) returns ZERO rows —
  InstrumentRead retired 0.2.0 → `channel.started`. Audience: non-runnable truncated UUIDs → `<session-id>`
  placeholders + Python example restructured to capture a real id from `sessions()`; role-filter narration
  + data-dir-precedence prereq trimmed. ✅ NOTE (code, not docs — track): `instrument.read` is STALE in
  source docstrings: mcp/server.py:489, mcp/tools.py:1228, connect.py:330, event_log.py:296.
- NEW POLICY 2026-06-24: NO competitor references in docs (TestStand/LabVIEW/OpenTAP/OpenHTF/NI/Keysight)
  except concept-translation or migration guidance. Marketing made separately. See memory
  feedback_no_competitor_references_in_docs. Worth a corpus-wide vendor-name sweep.
- how-to/execution/managing-sessions — 2 CRIT accuracy: the whole Data Retention section was fabricated
  (a `litmus.yaml` `retention:` key — ProjectConfig is extra=forbid, would ValidationError; a nonexistent
  `~/.config/litmus/config.yaml` global file) + labeled the REAL `litmus data prune` command "(planned)".
  Rewrote to the real command (--older-than/--dry-run/--data-types). Audience: lead reframe (drop
  "lifecycle"), close-step note, SessionStarted-event/rich-context scrub, non-runnable UUID→captured id. ✅
- how-to/execution/index — updated link descriptions to match the scrubbed pages (drop "lifecycle"/
  "subprocess-per-slot"/"design guide"/"ATML metadata"). Execution how-to sub-cluster (11 pages) DONE. ✅
- how-to/execution/operator-prompts — factually accurate (24 claims: litmus_prompts confirm/choice/input
  types, prompt fixture, PromptUnavailableError, LITMUS_AUTO_CONFIRM all verified). Audience: retitled
  "Design operator prompts"→"Pause a test for operator input" (was a design-checklist, not a task);
  "one ask() entry point"→"one fixture call" (ask is internal; the `prompt` fixture is the public surface);
  dropped the `src/litmus/api/dialogs/` source link; blocks→waits, dialog-manager→prompt, abstraction heading. ✅
- how-to/execution/multi-uut-testing — accuracy: parquet filename +run_id8; --fixture multi only with 2+
  slots; softened crash-cleanup. HEAVY internals scrub: InstrumentServer/RemoteInstrumentProxy/
  SyncCoordinator/orchestrator/worker/subprocess-per-slot all removed → plain "connect once, serialized,
  mocks not shared" + sync.wait behavior; dropped private `_LITMUS_*` env rows. docs-writer; re-audit 0/0. ✅
- how-to/execution/vector-expansion — factually accurate (18 claims: litmus_sweeps shape, linspace/
  arange/logspace/geomspace/repeat/range generators, vectors fixture all verified). Fixed error-text
  `litmus_sweeps zip`→`sweep zip`. CRIT audience: cut competitor design-validation (TestStand/OpenTAP/
  Spintop, per no-competitor policy); removed @parametrize collection-order concept-narration; scrubbed
  parametrize-layer/axis-group/curriculum jargon. Outer-to-inner ordering (the load-bearing bit) kept. ✅
- how-to/execution/profiles — 2 CRIT audience (no create-task block → added "Create and run a profile";
  broken See-also link `how-to/writing-tests.md`→`writing-tests.md`); accuracy: `profile_facets` is
  file-metadata not a column, `litmus show` does NOT display profile name/description (removed false
  claims), +station_type/fixture/verify_requires_limit to field table. Scrubbed facet-jargon/merge-ladder/
  UsageError class names/"escape hatch". docs-writer; re-audit 0/0. ✅
- how-to/execution/spec-driven-testing — factually PERFECT (38 claims verified: characteristic= kwarg,
  SpecBand resolution, guardband math, spec_ref format all correct). Pure audience/quadrant scrub:
  removed resolver-internals narration (page narrated HOW the resolver decides, not what to type),
  consolidated the condition-binding rule that was stated 3×, fixed internal-notation leaks
  (`Part.pins[primary_pin_id].name`, "ContextVars from the driver layer", `litmus.execution.verify`),
  de-jargoned Bind/Delegate, landed the measurement-layer-separation framing, softened "config bug" tone.
  docs-writer. ✅
- how-to/execution/traceability — CRIT accuracy: "Manual instrument traceability" recipe showed
  `verify(uut_pin=, instrument_name=, instrument_channel=)` → TypeError (verify rejects those kwargs);
  replaced — those fields auto-stamp from the part-spec characteristic + active fixture connection, no
  hand-stamp path. Heavy audience scrub: cut EAV/daemon-projection internals; reconciled the table-name
  contradiction (`measurements` is a real VIEW backed by `measurements_materialized` — SQL now uses the
  view); added CSV `measurement_`-prefix-drop note. docs-writer; re-audit 0/0. ✅
- how-to/execution/test-context — CRIT accuracy: `Limit` field `units`→`unit` (would AttributeError).
  Audience: reframed L3 lead off the writer/stash mechanic to the active-context view; cut a
  fixture-connection definition mid-recipe → task + link; fixed the `station_id` bullet. Context API
  verified accurate (30 claims: get_param/changed/last/observe/configure/.connections/.run/.station/.part). ✅
- how-to/execution/limits — CRIT accuracy: removed `expr`/`lookup`/`steps`/`callable` from the
  policy-field list — they're declared but UNWIRED (ROADMAP-deferred); a user writing them silently
  gets an *unchecked* measurement. Noted `tolerance_pct`/`tolerance_abs` need a `characteristic:`.
  CRIT audience: stripped the "Where limits come from" resolver-mechanism narration to an actionable
  precedence rule; cut resolver/short-circuit/vector-params jargon + the `litmus.execution.verify`
  import path. Cascade direction (inline<sidecar<profile, last-wins) verified. ✅
- how-to/execution/writing-tests — CRIT: 11-row litmus_characteristics×litmus_connections resolution
  MATRIX was reference content in a how-to → condensed to 3 common-binding recipes + link to markers.md
  (TRACKED: the full matrix should land in reference/pytest/markers.md — Piece 5). Cut TestStand/OpenTAP/
  Spintop comparison (new policy); uv sync→pip install -e .; scrubbed ContextVars/seen_names/FixtureConnection/
  resolved_pins/litmus.execution.verify internals; lifecycle/first-class-container jargon. Verified touched
  facts directly (context.connections, DuplicateMeasurementError/allow_repeat, MissingLimitError) — the
  audit-accuracy agent was cut off by a session rate-limit, so this page got audience + spot-accuracy. ✅

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
