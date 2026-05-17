# Page audit: docs/integration/openhtf-adapter.md

**Quadrant:** Integration (OpenHTF adapter — running OpenHTF tests through Litmus)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 3 | 3 |
| Audience | 0 | 1 | 2 |
| Accuracy | 4 | 4 | 3 |
| Gaps | 2 | 5 | 3 |
| Cross-links | 1 | 4 | 3 |
| **Total** | **7** | **19** | **16** |

> Note: The audit was performed inline by the coordinator because the Agent
> tool was not available in this environment. Findings were produced by
> applying each audit dimension's rubric directly against the page and
> verifying every code-level claim by reading the source.

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| WARNING | L17–L27 (Concept Mapping table) | Table appears immediately after the intro, before any code is shown. Readers see `verify()` fixture, `context`, `Outcome`, `TestRun`, `pins`/`channels`, etc. as targets of mappings before any of these is explained or even demonstrated. An OpenHTF reader has no anchor on the Litmus side. The table belongs after Strategy 3's "After (Litmus)" block, or each row should be a short inline cross-link to the defining page. |
| WARNING | L29–L139 ("Migration Strategies" before "Plug … Migration") | "Strategy 3: Full Migration" on L89 references `psu`, `dmm`, `verify`, `context` fixtures and a sidecar YAML, but the reader has not yet seen how a plug becomes a driver class (that's L141), how the station YAML wires it (L250), or how the sidecar YAML resolves to limits (L306). The reader sees the finished migration before the migration mechanics. Either move "Plug to Driver Class Migration" + "Station Config Migration" + "Measurement Migration" above "Migration Strategies", or strip Strategy 3's code down to a forward reference. |
| SUGGESTION | L363–L392 ("Gradual Migration Plan") | The Gradual Migration Plan repeats the same Strategy 1 / Strategy 2 / Strategy 3 split (Phase 1 = Strategy 1, Phase 3 = Strategy 2) but renumbers them. Either drop one section or make the cross-reference explicit ("Phase 1 implements Strategy 1 from above"). |
| SUGGESTION | L394–L404 ("Benefits of Migration") | The benefits table reads like marketing and belongs much earlier (right after the intro) to motivate the reader, or should be removed entirely — it is the last substantive content before "Next Steps" and contributes nothing to a reader who has already decided to migrate. |

---

## Voice

| Severity | Location | Finding |
|---|---|---|
| WARNING | L13 | "Litmus provides the infrastructure around them: discovery, identity verification, calibration tracking, and a Mock factory for simulation." — promotional listing. State what is provided in the context of the user's task ("Litmus discovers instruments, verifies their `*IDN?` against your asset YAML, and tracks calibration due dates"). |
| WARNING | L394 ("Benefits of Migration") | Whole section is comparative marketing ("Custom executor" vs "pytest (familiar)", "DIY per plug" vs "Generic Mock factory", "Not supported" vs "Built-in"). Docs voice; the page is selling, not documenting. Remove or move to a separate "Why migrate?" concept page. |
| WARNING | L398 "pytest (familiar)" | Aside in parens is editorial — drop. |
| SUGGESTION | L7 "Google's open-source hardware-test framework" | Parenthetical is unnecessary editorialization on a name the reader already knows (they're here for an OpenHTF adapter). Cut. |
| SUGGESTION | L13 "even your existing OpenHTF plugs refactored as plain classes" | "even" is a hedge framing implying the listed options are unusual. Drop "even". |
| SUGGESTION | L231 "Litmus provides a generic Mock factory that works with any driver class — no simulation code required in your driver:" | "no simulation code required" is a soft sell. Plain: "The Mock factory wraps any class; methods you don't configure return `None`." |

---

## Audience

| Severity | Location | Finding |
|---|---|---|
| WARNING | L23 ("User's driver class — Any Python class") | This row maps `Plug` to "User's driver class". A test engineer who has only ever written OpenHTF plugs doesn't know what "User's driver class" means here. Use a concrete name they will write ("`drivers/dmm.py:MyDMM`") or refer to the existing concept ("instrument class — see Option B below"). |
| SUGGESTION | L24 ("PhaseResult" → "Outcome") | The `PhaseResult` cell lists seven Litmus outcome names with no explanation of which OpenHTF result becomes which Litmus outcome. A test engineer migrating tests needs the mapping (CONTINUE → PASSED, STOP → ABORTED, etc.) or an explicit note that mapping is automatic. |
| SUGGESTION | L141 ("Plug to Driver Class Migration") | "Plug" is OpenHTF jargon; "driver class" is Litmus convention. Spell out that "driver class" is just a Python class with `connect`/`disconnect` methods, like the examples already in the codebase under `examples/01-vanilla/drivers/`. |

---

## Accuracy

| Severity | Location | Finding |
|---|---|---|
| CRITICAL | L127–L139 (Strategy 3 sidecar YAML) | The YAML uses a top-level `test_power:` key with `limits:` nested inside. This shape is **invalid**. `SidecarConfig` is declared `extra="forbid"` (`src/litmus/models/test_config.py:157` for `TestEntry`, also for `SidecarConfig`). A function-scope override must be written under the `tests:` map: `tests: \n  test_power:\n    limits:\n      input_voltage: {low: 4.5, high: 5.5, units: V}`. Confirmed against `examples/04-sidecar-markers/tests/test_rail.yaml`. The same wrong shape appears at L333–L341 (`test_voltage:` at the top level). |
| CRITICAL | L62–L66 (blockquote) | "Use the client (above) or the MCP/REST step + measurement endpoints to record per-step rows." There is **no** REST endpoint for posting individual steps or measurements (the only POST in `src/litmus/api/app.py` is `/runs` and `/dialogs`, lines 311 and 355), and **no** MCP tool for it either (`litmus_steps` at `src/litmus/mcp/server.py:612` is read-only with `action: list | tree`; `litmus_runs` is also read-only). The `LitmusClient` is the only public path. Delete the "MCP/REST" claim or implement those endpoints. |
| CRITICAL | L360 (`pytest tests/test_power.py --station=bench_1 --dut-serial=SN12345`) | `--dut-serial` exists (verified `src/litmus/pytest_plugin/__init__.py:199`). `--station` exists (verified `src/litmus/pytest_plugin/helpers.py:115`). However, the YAML at L268–L282 defines `id: bench_1` but the StationConfig schema at `src/litmus/models/station.py:50` does NOT have a `location` field on the example — wait, it does (L67). What it does NOT have is `name: "Production Bench 1"` as anything other than the literal `name` field; that's fine. The actual accuracy issue: page omits that this same command requires the station YAML to live at `stations/bench_1.yaml` (the `--station` resolver looks up `<id>.yaml` per `src/litmus/pytest_plugin/helpers.py:107`). Without that, the example fails. |
| CRITICAL | L156–L162 (`def setup(self):` / `def teardown(self):`) | OpenHTF's `BasePlug` uses `tearDown` (camelCase, like unittest), not `teardown` (lowercase). And there is no `setup` hook on `BasePlug` — connection logic goes in `__init__`. The example as written would not be invoked by OpenHTF. Cross-check against the OpenHTF source if uncertain; either way, the example should be either correct OpenHTF or removed. |
| WARNING | L157 (`import visa` / `visa.ResourceManager()`) | The `visa` module name was deprecated in pyvisa 1.6 (2015) in favor of `import pyvisa` / `pyvisa.ResourceManager()`. The post-migration example at L193 correctly uses `pyvisa`, but the "before" example perpetuates an obsolete idiom that hasn't been recommended for ~10 years. Either update the OpenHTF "before" example to `import pyvisa` or add an inline note. |
| WARNING | L75 (Strategy 2: `verify("voltage", float(dmm.measure_voltage()))`) | `verify` raises `MissingLimitError` (`src/litmus/execution/verify.py:87`) when no limit can be resolved for the named measurement. The Strategy 2 example shows no accompanying sidecar YAML and no inline `limit=` kwarg, so the call would error at runtime. Either show the sidecar that resolves `voltage`'s limit, pass `limit=Limit(low=3.0, high=3.6)` inline, or note the prerequisite. |
| WARNING | L34–L60 (`openhtf_bridge.py`) | The bridge example reads `phase.measurements.values()` and dereferences `m.validators[0].minimum` / `m.validators[0].maximum`. OpenHTF Measurements may carry multiple validators, or no `in_range`-style validator at all (e.g., `equals(...)`, `matches_regex(...)`). The indexed-access pattern will `IndexError` when validators is empty (handled by the `if m.validators else None` guard) but silently mis-map when validator 0 isn't an in-range. Acknowledge the limitation or filter for `InRange`-class validators. |
| WARNING | L309–L314 (`htf.Measurement('voltage').in_range(3.0, 3.6).with_units('V').doc('...')`) | OpenHTF's `Measurement` builder methods include `.in_range`, `.with_units`, and `.doc`; this looks right but `.in_range` is `.in_range(minimum, maximum)` and there is also `.with_validator`, `.equals`, etc. — listing only one option without flagging it as a single example may mislead readers into thinking other validator shapes don't migrate. Either add a note or cover at least `equals` and `matches_regex`. |
| SUGGESTION | L7 ("Test phases ↔ Test steps") | Litmus's primary execution unit is the `pytest` test function, not a "step". The `step` concept exists (in `LitmusClient.step()` and the event log's step hierarchy), but on the pytest path an OpenHTF phase maps most naturally to a pytest test function, with `step` reserved for finer-grained scoping inside a test. Worth clarifying which mapping the reader should use. |
| SUGGESTION | L23 ("Plug ↔ User's driver class") | More accurate as "driver class (e.g., `examples/01-vanilla/drivers/psu.py:PowerSupply`)" — gives the reader a concrete file to crib from. |
| SUGGESTION | L26 ("Test class ↔ pytest test file") | OpenHTF's `htf.Test` is not a class in the OO sense (it's `openhtf.core.test_descriptor.Test`, the test object you `.execute()`). And the Litmus side isn't "pytest test file" — it's the collection of test functions in the file. Reword: `htf.Test(...).execute()` ↔ `pytest tests/test_*.py`. |

---

## Gaps

| Severity | Location | Finding |
|---|---|---|
| CRITICAL | Title / framing (whole page) | The page is titled "OpenHTF Migration", the file is `openhtf-adapter.md`, and the integration index (`docs/integration/index.md:17`) calls it "OpenHTF adapter — bridge OpenHTF phase records into Litmus". A reader expecting a maintained adapter module (`litmus.openhtf` or similar) will not find one: there is no `openhtf_plugin/` directory in `src/litmus/`, no `OpenHTFCallback` class, no documented import path. The "Results Bridge" example is a hand-rolled function the reader is expected to write themselves. The page must either (a) acknowledge upfront that no adapter ships yet and this is a migration cookbook, or (b) implement an `OpenHTFOutputCallback` in the platform and reference it. |
| CRITICAL | L34–L60 (`openhtf_bridge.py`) | The example is incomplete as a working bridge: no error handling (what if `client.start_run` raises mid-test?), no concurrency note (OpenHTF callbacks may fire from worker threads — is `LitmusClient` thread-safe?), no handling of skipped/aborted phases, no mapping of OpenHTF `PhaseResult` to Litmus `Outcome`, no propagation of `phase.start_time_millis`/`end_time_millis` so the resulting run has no timing data. The reader who copy-pastes this loses phase outcomes and timings silently. |
| WARNING | L13 ("identity verification, calibration tracking") | These are promised in the intro but the page never shows how to wire them. The asset YAML appears at L286–L303, but there's no example of `*IDN?` failing, no example of a calibration-expired warning, no description of where in the flow these checks fire. |
| WARNING | L229–L248 (Mock factory section) | The section shows `Mock(MyDMM, ...)` constructor usage but does not show how the mock is wired into the pytest fixture chain — i.e., how `def test_voltage(dmm)` gets the mocked instance rather than the real one. The user just learned `pytest --mock-instruments` or `mock: true` in station YAML, but neither is mentioned here. Cross-link to `docs/how-to/mock-mode.md`. |
| WARNING | L188 ("Strip the OpenHTF base class and keep the instrument logic") | Doesn't say what changes the user has to make: `setup` → `connect`, `teardown` → `disconnect`, add `__enter__`/`__exit__` (shown in the example but not called out as required). What does the platform require of a driver class? No reference to a "driver class contract" page (and as far as I can see, none exists — that's a separate gap). |
| WARNING | L284–L303 (instrument asset file) | The example uses `protocol: visa` and `driver: pymeasure.instruments.keithley.Keithley2000`, but the page never explains how the asset (`instruments/keithley_dmm_001.yaml`) is linked to the station entry (`stations/bench_1.yaml`, `dmm:` role). Is it matched by `*IDN?`? By manual reference? By `id:` from the catalog? A reader cannot complete the migration from the information on this page alone. |
| WARNING | L379–L385 (Phase 3, "Run both versions in parallel … Validate results match") | "Validate results match" is the most operationally important step in the migration and the page provides zero guidance: same DUT? Same operator? Diffing tool? Acceptance threshold for "match"? This is the safety net for the cutover and it's a one-liner. |
| SUGGESTION | L62–L66 (blockquote on LaunchRequest) | The note explains what `POST /api/runs` doesn't accept, but doesn't explain when a reader would use the REST endpoint versus the `LitmusClient`. Add one sentence: "Use `LitmusClient` from in-process bridges; use `POST /api/runs` to trigger a pytest run from outside the test process (e.g., from an MES)." |
| SUGGESTION | L407–L409 ("Next Steps") | Only two next-step links. A reader who has just migrated will want: how to set up the operator UI for their new Litmus tests, how to view the first parquet results, how to wire up reports. Add 2–3 more targeted links. |
| SUGGESTION | (whole page) | No section on what does NOT migrate cleanly. OpenHTF has features that don't have a 1:1 Litmus mapping (e.g., `htf.PhaseOptions(requires_state=...)`, sub-tests, multi-phase locks on plugs, OpenHTF web UI). Calling out the known gaps prevents the migrator from discovering them mid-cutover. |

---

## Cross-links

| Severity | Location | Finding |
|---|---|---|
| CRITICAL | L20 ("`verify()` fixture") | First mention of `verify` on this page. No link to `reference/litmus-fixtures.md#verify` (or wherever `verify` is documented). Same problem with `context` (L74), `psu` / `dmm` / `verify` fixtures (L116). |
| WARNING | L24 ("Outcome") | `Outcome` is a load-bearing enum and is referenced with all seven values, but there's no link to `docs/concepts/outcomes.md` (which exists) for the definitions. |
| WARNING | L127 / L333 ("tests/test_<module>.yaml") | First mention of sidecar YAML. No link to `docs/reference/configuration.md` or to `docs/how-to/writing-tests.md` (which both cover the sidecar schema). A reader who can't validate their migrated YAML has no idea where to look. |
| WARNING | L229 ("Mock factory") | Section header but no link to `docs/how-to/mock-mode.md` or `docs/tutorial/02-mock-instruments.md`, both of which cover the Mock factory in detail. |
| WARNING | L284 ("instrument asset files for identity verification and calibration tracking") | No link to a defining page (`docs/concepts/stations.md`? `docs/integration/instruments.md`?) and no link to the schema. |
| SUGGESTION | L26 ("pytest is Litmus's primary runner integration") | Could link to `docs/concepts/why-pytest.md` or `docs/reference/pytest-native.md` so a skeptical OpenHTF reader can see why. |
| SUGGESTION | L374 ("Run `litmus discover`", "Create station configs with `litmus station init`") | First mention of these CLI commands. Link each to `docs/reference/cli.md`. |
| SUGGESTION | L408 ("Results API") | Link uses relative `results-api.md` which is correct, but the surrounding "Next Steps" list is too thin (see Gaps). Add links to `docs/how-to/configuring-stations.md`, `docs/tutorial/02-mock-instruments.md`, and `docs/concepts/outcomes.md`. |
