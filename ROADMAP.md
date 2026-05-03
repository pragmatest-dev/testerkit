# Litmus Roadmap

Active backlog (RICE-prioritized) and archive of shipped work. Items
graduate from **Backlog** to **Completed** on merge — never strike
through, just move.

---

## Backlog

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
flags to pass: `pytest --test-phase=production --product=tps54302`.
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
var lookup (`LITMUS_TEST_PHASE`, `LITMUS_PRODUCT`, …) as step 2; then
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
(`TestConfig`, `SpecContext`, `Limit`, `ProductCharacteristic`) must
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

### CLI fallback for operator prompts (multi-DUT aware)

When running without the UI/server, operator prompts (e.g. "insert
DUT", "press button X", "verify LED is green") should fall back to
**terminal prompts** rather than being no-ops or silently blocking on
a UI that isn't running.

Multi-DUT scenarios require context in the prompt: the prompt must
identify **which DUT** ("DUT-2 of 4: insert board into socket B") so
the operator doesn't act on the wrong unit. Resolution path:

- Single source of truth for the prompt API — one `request_input()`
  surface that dispatches to UI (when the server is running) or CLI
  (when it isn't).
- CLI renderer shows the active DUT slot / serial / position from the
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
  dut_pin: TP_VOUT
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
v1's "validate product X" use case. What they don't cover:
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
for product X) supports multiple sequences (smoke / full /
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

### Exporter access to row-level cascade outcomes

Surfaced by the Phase 6a.4 design review: ``MeasurementRow`` and
``MEASUREMENT_SCHEMA`` carry ``step_outcome`` / ``vector_outcome``
/ ``run_outcome`` (cascade rollups added in Phase 6a.2), but the
event-driven exporters (``EventSubscriber`` subclasses for CSV /
JSON / ATML / HDF5 / TDMS / MDF4 / STDF) consume the raw
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

### Channel EventStore-bridging subscription

`channels/__init__.py:channel_subscribe()` is restored after being
incorrectly flagged as "dead code" during the auto-picked Phase 6a.3
audit. Filters ``instrument.read`` / ``instrument.set`` events from
EventStore by ``channel_id`` — the EventStore-based subscription
path complementary to ``ChannelStore.on_channel()`` (in-process) and
``ChannelClient.on_channel()`` (Flight RPC).

Why it exists: queries via ``EventStore`` work cross-process via
Arrow Flight AND replay from history, so consumers (analytics
dashboards, MCP tools, post-hoc UI) can subscribe to channel
activity without the channel daemon running. The Flight
subscription path requires the live daemon and only delivers new
samples; the EventStore path can replay from any ``since`` cutoff.

What needs to land: a real consumer. Candidates: the analytics
metrics-store could subscribe to channels-of-interest for live
charts; the MCP "watch this channel" tool; the operator UI's
event-timeline panel. Flag if a use case materializes — otherwise
keep as the build-out hook.

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
Currently low-impact (zero rows = no observable bug) but worth
fixing alongside the Channel attribution work above.

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
covers a product's required range, treat ``guaranteed`` qualifiers
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

### Channel attribution — wire `instrument_role` / `resource` to ChannelDescriptor

Surfaced by the Phase 6a.3 `data/channels/` design review:
``ChannelDescriptor`` (in ``data/channels/models.py``) declares fields
``instrument_role: str``, ``resource: str``, and
``properties: dict[str, Any]`` that are never populated. Both
constructor call sites (``store.py:270``, ``client.py:161``) leave
them at default. Result: the ``_registry.json`` written at session
end carries no "which instrument owns this channel" data.

The data IS available at the call site:
``instruments/observer.py`` already caches ``self._role`` and
``self._resource`` from the connected instrument and writes
channels via ``self._channel_store.write(channel_id, value,
source=source)`` (line 69). The store's ``write()`` signature just
doesn't accept the attribution kwargs.

What needs to land:

- ``ChannelStore.write()`` accepts ``instrument_role: str | None``,
  ``resource: str | None``, ``properties: dict[str, Any] | None``.
- ``instruments/observer.py`` ``_store_value()`` passes
  ``instrument_role=self._role, resource=self._resource``.
- Harness ``observe()`` path (``execution/harness.py:203``) passes
  ``None`` for both — channels written from free-form
  ``context.observe()`` have no instrument context.
- Analytics / UI reads ``_registry.json`` to attribute channels to
  instruments in waveform pickers and filtering.

Decision points: should ``properties`` accept arbitrary kwargs as
a metadata bag, or stay typed? Should the daemon-side store
preserve attribution from cross-process producers (today the
client RPC has no place to pass it)?

### Consumer-side ref materialization (waveform viewing)

Surfaced by the Phase 6a.2 `data/backends/` design review: the
write path saves large observations (Waveform / ndarray / bytes /
Pydantic models) to `_ref/` sidecar files and stores
``file://_ref/abc.npz`` strings in parquet's ``out_*`` columns.
The read path is implemented but never wired up:

- `parquet.py:load_ref(value, *, parquet_path, channel_store)` —
  unified URI dispatcher (``channel://`` / ``file://`` / legacy)
- `parquet.py:load_file(parquet_path, ref)` — loads npz →
  ``Waveform``, npy → ndarray, json → dict/Pydantic, bin → bytes,
  pkl → object, arrow → ``pa.Table``
- `parquet.py:is_file_reference(value)` — predicate

Zero callers across `src/`, `tests/`, `scripts/`. So a consumer
fetching a measurement row gets the literal string
``"file://_ref/abc.npz"`` instead of a `Waveform` — there's
currently **no way for the UI / API / CLI / MCP / reports to load
waveform data for viewing**, even though the data is on disk.

What needs to land:

- **API**: a `GET /api/runs/{run_id}/measurements/{step}/{name}/waveform`
  (or similar) that materializes via `load_ref` and returns JSON
  (Y/t0/dt/attrs) or streams the raw file. Decide eager-in-RunView
  vs lazy-on-demand vs opt-in query param.
- **UI**: NiceGUI page renders the waveform via ECharts.
  Detect ``file://`` strings in `out_*` columns of the run-detail
  view; plot inline or in a modal.
- **Reports**: HTML/PDF embed waveform plots instead of showing
  the literal ref string.
- **MCP**: a tool that materializes a waveform for an LLM consumer.
- **CLI**: `litmus show <run> --waveform <name>` round-trips through
  the same loader.

Decision points: where does dereference happen, what's the wire
format (JSON vs binary stream), do `channel://` refs need a
parallel HTTP path now that `litmus serve` is the front door? The
existing `load_ref` / `load_file` keep the dispatch surface; the
consumer-side wiring is the missing layer.

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
  runs at once (multi-DUT view). Today subsumed by the events
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

### Parametric measurement viewer — follow-ups

The thin slice (`/explore` page, `MetricsStore.parametric()`,
schema-driven dropdowns, URL state, scatter / line / bar /
histogram) shipped 2026-05-02. Outstanding work:

- **Range filters and facet pickers.** Today filters are a JSON
  textarea — equality only. Add dedicated `since`/`until` inputs and
  multi-select pickers for `station_id` / `product_id` / `test_phase`
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
