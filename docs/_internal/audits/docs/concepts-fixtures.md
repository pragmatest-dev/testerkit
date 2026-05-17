# Page audit: docs/concepts/fixtures.md

**Quadrant:** Concepts / Explanation (hardware test fixtures — DUT-pin → instrument-channel YAML, NOT pytest fixtures)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 3 | 2 |
| Voice | 1 | 4 | 2 |
| Audience | 2 | 3 | 1 |
| Accuracy | 3 | 4 | 2 |
| Gaps | 2 | 4 | 2 |
| Cross-links | 3 | 4 | 2 |
| **Total** | **12** | **22** | **11** |

---

## Ordering

### CRITICAL

- **Lines 189-196 — "CLI Usage" duplicates "Selecting a fixture at run time" (lines 164-175).** Two adjacent sections present the same `pytest tests/ --station=... --fixture=... --dut-serial=...` block with no new information. The earlier section already names the resolution mechanism; the second section is dead weight that breaks the reader's mental model ("did I miss something between these?"). One must be deleted or the two merged.

### WARNING

- **Section sequence does not match a concept-page arc.** The page jumps:
  1. What & when
  2. YAML shape
  3. Field tables
  4. Tests (`pins`)
  5. Tests *without* pin mapping (`psu`, `dmm`, `instrument`)
  6. Multi-channel routing
  7. Station relationship
  8. CLI selection
  9. `load_fixture` (Python API)
  10. CLI (duplicate)
  11. Multi-slot
  12. Shared instruments & switching
  13. Best practices
  14. Complete example

  A concept page should establish the mental model end-to-end before drilling into operational mechanics. The "Fixture and Station Relationship" diagram (line 136) is the single best explainer of the concept and belongs near the top, not buried between routing examples and run-time selection. Suggested order: definition → diagram → fields → single-DUT shape → multi-slot → routing/switching → run-time selection → loading API → example → best practices.

- **"Without Pin Mapping" (lines 76-99) breaks topical continuity.** The reader was just introduced to `pins` as the value proposition. Pivoting immediately to "you don't always need it" undercuts the concept being explained. Move this discussion either earlier (in "When to Use Fixtures" — that table already covers the same ground) or later, near best practices.

- **"Multi-Slot Fixtures" (line 198) and "Shared Instruments" (line 228) are arbitrarily separated from "Multi-Channel Routing" (line 101).** All three are about scaling the basic single-DUT shape; they should be one stacked section in increasing complexity (multi-channel → multi-slot → shared instruments with switching), not interleaved with relationship diagrams and CLI.

### SUGGESTION

- **"Best Practices" before "Complete Example" is the wrong cadence for a concept page.** The example is the recap; best practices are an aside. Swap them, or fold the best-practices bullets into the closing example commentary.
- **"Fixture Fields" and "Fixture Connection Fields" tables (lines 39, 47) appear before the model is established.** Consider promoting the diagram (line 136) above these tables, so the field list reads as annotation rather than schema dump.

---

## Voice

### CRITICAL

- **The page uses imperative how-to voice in a concepts/explanation quadrant.** Sections like "Using the `pins` Fixture" (line 56), "Loading Fixtures" (line 178), "CLI Usage" (line 189), "Best Practices" (line 247) read as task instructions, not as conceptual exposition. A concept page explains *why fixtures exist, what they model, and how they fit the rest of the system*; it should not be telling the reader to type `pytest tests/ ...`. The CLI / loading / best-practices content belongs in a how-to.

### WARNING

- **"They're optional but essential for production testing with traceability" (line 3) is self-contradictory.** "Optional" and "essential" cannot both qualify the same thing. The honest reframe: fixtures are optional in development and mandatory once you need pin-named addressing, multi-station portability, or per-pin traceability.
- **Marketing tone in "Benefits" (lines 69-74).** Four numbered bullets reading "Decouples tests from wiring / Self-documenting / Traceability / Portability" is a sales pitch, not an explanation. Concepts pages explain the *consequence chain* (because connections name pins, the test body is independent of channel assignments; because every measurement resolves through a named connection, the parquet row records the DUT-side identifier). Convert from feature list to causal narrative.
- **"Best Practices" voice is generic and tonally off** for a concept page — bullets like "Use descriptive connection names" and "Include all connections" are checklist items that would suit `how-to/configuring-stations.md`, not an explanation of the data model.
- **"Auto-registered as pytest fixtures" (line 78) silently switches vocabulary** from "fixtures" (the YAML pin-map subject of the page) to "fixtures" (pytest's fixture concept). The page is one of the few places in the docs where the collision matters and the prose should mark the switch explicitly.

### SUGGESTION

- **"You don't always need pin mapping" (line 76) and "For simple setups" (line 78) are conversational throat-clearing.** Concept-page prose should make the same claim with a single clause: "For simple benches, instrument-role access is sufficient."
- **The mermaid block uses arrows that imply data flow (`---`) but the surrounding prose calls them connections** — clarify whether the diagram models *configuration linkage* or *runtime signal flow*.

---

## Audience

### CRITICAL

- **The page never names the audience-critical disambiguation: "fixture" the YAML file vs "fixture" the pytest concept.** A reader landing here from anywhere in the docs (or from `pytest --help`) has to infer that `fixtures/*.yaml` is a *different thing* from `@pytest.fixture`. A single sentence at the top — "Hardware test fixtures (this page) are YAML pin-maps; the `pins`, `dmm`, etc. names you take in test signatures are pytest fixtures the plugin synthesizes from them" — would prevent the entire reading confusion. As written, the page treats this collision as if it doesn't exist.
- **Audience contradicts itself within one page.** Line 11 ("Pin mapping (fixtures) — Production, complex routing, compliance") tells the test engineer this is advanced/production-only. Line 78 then tells them they can skip it for "simple setups". The earlier table positions fixtures as the heavyweight option; the later prose positions them as one of several peer styles. The reader cannot tell which framing to adopt.

### WARNING

- **The pin-map intent is buried behind the word "fixture".** A new hardware-test engineer reading the docs index sees "Fixtures — pin-to-instrument mapping". That subtitle is good. The page itself opens with "Fixtures define pin-to-instrument mappings" and immediately drifts into pytest-fixture territory. Hold the hardware concept consistently for the first half of the page before any pytest crosstalk.
- **"Compliance" (line 11) is unexplained.** In what way are fixtures a compliance artifact? Either expand (audit trail of pin-level measurements) or drop.
- **"Sequences" / "profiles" mentioned in passing (line 166, "through a profile that sets it")** without defining profiles. Concepts pages should either link out or footnote the term — readers new to Litmus will not know what a profile is.

### SUGGESTION

- **"Use descriptive connection names" (line 251) presupposes the reader already knows what makes a name descriptive.** Show, don't tell — the existing examples already use `vout_measure` vs `VIN` inconsistently, which would be a much better teaching moment to address directly.

---

## Accuracy

### CRITICAL

- **Lines 174-175 — "The plugin validates that the fixture's `product_id` (or `product_family`) matches the active product spec before any test runs" is false.** I grepped `src/litmus/pytest_plugin/`, `src/litmus/execution/`, `src/litmus/fixtures/` for any check that compares `fixture_config.product_id` to the active product, and there is none. The only reference to `fc.product_id` in the plugin (`pytest_plugin/__init__.py:636`) just copies the field into a worker-mode slot config — no validation. This claim should be removed or, if validation is intended, filed as a bug.
- **The `instrument_terminal` field is entirely omitted from "Fixture Connection Fields" (lines 47-54),** despite being a documented field on `FixtureConnection` (test_config.py:407) with semantic significance ("hi", "lo", "signal") used by the resolver. The Fixture Connection table currently lists only 4 of 8 actual fields. Missing: `instrument_terminal`, `description`, `function`, `route`.
- **The `function` field (MeasurementFunction) is invisible in this concepts page,** but it is the mechanism by which a single DUT pin can route to different instruments for different measurement characteristics (test_config.py:414-421, "DMM for DC, Scope for AC ripple"). For a concepts page this is a *conceptually load-bearing* field and its absence is a meaningful omission — not just a missing table row, but a missing idea.

### WARNING

- **Lines 78-85 — "instrument roles from the station config are auto-registered as pytest fixtures."** Verified accurate (init.py:223, hooks.py:258 — yes), but `logger` is not part of the auto-registered set; it's separately defined. The example signature `def test_voltage(psu, dmm, logger):` mixes two registration sources without naming the distinction — which a concepts page should do.
- **Line 230 — "Locking is per-resource (keyed on the instrument's connection string)" is imprecise.** Per `instruments/server.py:42-46`, locking is keyed on the `resources` map passed to `InstrumentServer` (typically the VISA address or similar). "Connection string" is not a defined Litmus term; "resource" or "VISA address" is. Also, the doc omits the `concurrent=True` exception (switches), which is the relevant nuance for the fixture/route discussion that immediately follows.
- **Line 226 — "A fixture uses either `connections` (single-DUT) or `slots` (multi-DUT), never both."** Accurate, but understates the enforcement: `FixtureConfig._validate_connections_or_slots` (test_config.py:496) *raises*. Worth stating as "the validator rejects fixtures with both" so the reader understands this is a hard schema constraint, not a convention.
- **Line 230 — "an internal RPC server that lets multiple test workers share one physical instrument, using TCP" is technically right** (server.py:32, "TCP-based RPC server") but the implementation is `multiprocessing.connection`, not raw TCP; calling it "TCP" is imprecise in a way that will confuse readers who try to connect a different language client.

### SUGGESTION

- **Lines 56-67 example calls `pins["VIN"].set_voltage(...).enable_output()`** — these are PSU methods, but the YAML at line 24-29 names the VIN connection as `instrument: psu, instrument_channel: "1"`, which is fine. However the example test asserts `float(voltage) > 3.0` while the "Complete Example" version at line 319 asserts `3.0 < float(voltage) < 3.6`. Pick one to use throughout; the inconsistency confuses readers tracing what the test is supposed to verify.
- **Line 230 — "transparent proxy objects".** The actual class is `RemoteInstrumentProxy` (server.py:4); naming it lets the reader find the code.

---

## Gaps

### CRITICAL

- **No explanation of how a fixture connects to a measurement.** This is the central concept. The page shows `pins["VOUT"].measure_voltage()` returning a value, but never explains that the value flows through the resolved connection to (a) a measurement row with `dut_pin=VOUT` and (b) the connection name as the addressable identifier in the event log. The whole point of fixtures is traceability and this is asserted in passing (line 73) but never explained.
- **No coverage of the `function` field as a routing dimension** (see Accuracy). A single pin can resolve to different instruments based on measurement function — this is a first-class fixture concept the page entirely omits.

### WARNING

- **No discussion of fixture resolution order / fallback semantics.** When a connection has `dut_pin`, `net`, *and* `function`, in what order does the resolver match? `FixtureConnection.function` docstring (test_config.py:414-421) explains the (dut_pin, function) match with first-match-by-pin fallback — none of this surfaces in the concept page.
- **No mention of `station_types` (test_config.py:484),** which is the mechanism a fixture uses to declare which abstract station-type layouts it can wire against. For a concepts page on the YAML, this is a relevant scoping concept.
- **No mention of `dut_resource`** (test_config.py:487), the per-fixture DUT connection string used by the platform — relevant given the page covers multi-slot fixtures (which can override it per slot, test_config.py:448).
- **Switching/routing concept is underdeveloped.** Lines 232-243 introduce `route` but don't explain *why* you need it (single DMM serving multiple DUT positions via a matrix), *when* it activates (lazy on first instrument access via `RoutedProxy`, manager.py:271), or *how it interacts with locking* (concurrent=True switches skip the lock, server.py:38). All of these are concept-level questions.

### SUGGESTION

- **No mention of `description`** as a documentation field on both `FixtureConnection` and `FixtureSlot`. A small thing but listed in the fields table this would round out completeness.
- **No discussion of fixture revisioning beyond `product_revision`.** "Version fixtures" in Best Practices (line 253) waves at a real workflow question: how do production teams track fixture board revisions independently of product revisions? Concept-level guidance would help.

---

## Cross-links

### CRITICAL

- **No link to `tutorial/03-fixtures.md`.** This concepts page is the natural conceptual partner of step 3 of the tutorial. Readers learning fixtures via the tutorial should be able to jump here for the model and back. Currently it's a one-way ref (tutorial → here? No — tutorial 03 is about *pytest* fixtures, but reciprocal linkage is still essential to clarify the naming collision).
- **No link to `how-to/multi-dut-testing.md`** despite a full "Multi-Slot Fixtures" section (lines 198-226). The how-to explicitly links *here* (line 7) but there is no return link — readers studying the concept have no path to the operational guide.
- **No link to `reference/litmus-fixtures.md`** for the `pins`, `instruments`, `instrument`, `fixture_manager` pytest fixtures the page repeatedly demonstrates. Every code example in this page leans on those — the reference exists, the page should anchor each example to it.

### WARNING

- **No link to `concepts/products.md`** even though the page references "product pins", "product_id", "product_family", and concludes with a product-spec YAML excerpt. Concept pages in the same group should cross-anchor.
- **No link to `concepts/stations.md`** despite "station instruments", "station_types", and the relationship diagram that explicitly draws Product → Fixture → Station.
- **No link to `concepts/capabilities.md` / `capability-model.md`** despite the `function` field's role in routing being capability-matching adjacent.
- **No link to `how-to/configuring-stations.md`** which already links here (line 247). Reciprocal linkage missing.

### SUGGESTION

- **No link to `reference/configuration.md`** despite "Configuration Reference" being the *only* link in the Next Steps section (line 325) — confirm the reference target's anchor includes the fixture-specific section and consider a deep link.
- **Next Steps (lines 322-326)** could include `how-to/multi-dut-testing.md` and `reference/litmus-fixtures.md` to round out the navigation triangle (concept → how-to → reference).
