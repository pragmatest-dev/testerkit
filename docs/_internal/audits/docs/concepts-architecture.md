# Page audit: docs/concepts/architecture.md

**Quadrant:** Concepts/Explanation (architectural overview of how the Litmus platform fits together)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 3 | 2 |
| Voice | 0 | 1 | 2 |
| Audience | 1 | 2 | 2 |
| Accuracy | 3 | 6 | 3 |
| Gaps | 1 | 4 | 2 |
| Cross-links | 0 | 4 | 4 |
| **Total** | **6** | **20** | **15** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ❌ CRITICAL | L3 ("How the Framework Works") + L5 (vocabulary primer) | A Concepts page should open with the *why* / the problem it solves. Instead the page jumps straight into "How the Framework Works" with a vocabulary primer that names ten Litmus-specific terms (product, station, sidecar, verify, context, logger, characteristic, capability, sidecar YAML, fixture) before establishing what problem the architecture solves or what the reader is supposed to take away. Diátaxis Concepts: motivate first, model second. |
| ⚠️ WARNING | L31 "Key Concepts" table | The Key Concepts table arrives *after* the first big flowchart (L7–L29) which already references Product, Station, Sidecar, Characteristic, Capability. Readers meet the terms in a diagram before the table that defines them. Either move the table above the first diagram, or merge the vocabulary primer with the table. |
| ⚠️ WARNING | L43 ("System Overview") vs L3 ("How the Framework Works") | Two overview diagrams (L7 and L45) both claim to describe "how it fits together," with overlapping content (Product spec, Station YAML, test code, runtime, storage). A reader reads diagram #1, thinks they understand the model, then encounters diagram #2 with a different decomposition (Definitions/Runtime/Storage swimlanes) and has to re-orient. One should subsume or follow from the other with a transition sentence ("the previous diagram showed the data flow; this one shows the type-vs-instance split"). |
| ⚠️ WARNING | L185 (TestEntry footnote) | The aside "TestEntry is a recursive node — file-scope, class-scope, method-scope all share the same shape; the recursion is described in the field list rather than drawn as a self-edge (Mermaid routes self-edges through neighbouring entities and the line reads as a phantom relationship)" is a tool-limitation footnote that breaks reader flow at exactly the moment they should be parsing the diagram. Move to a trailing note or delete — readers don't need to know about Mermaid's edge-routing quirks. |
| 💡 SUGGESTION | L248 ("Type vs Instance") | The Type vs Instance table appears after three Entity-Relationship diagrams. This conceptual distinction (definition vs runtime instance) is more fundamental than the ER fields it cross-references and would better serve readers if introduced *before* the ER diagrams — it gives them a lens to read each diagram through. |
| 💡 SUGGESTION | L322 ("File Locations") + L337 ("Data Architecture") | The two tables at the end (File Locations, Data Architecture) read as appendix material that lost a home rather than the natural close of a Concepts page. Consider either folding them into the System Overview section earlier, or wrapping the page with a brief "Next steps / where to go from here" pointing to the deeper concept pages (three-stores, event-log). |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L275 | Passive voice (hides actor) | "Product-spec bands derive a production limit by applying any configured guardband (tightening the spec for manufacturing margin)." — the actor doing the derivation is the limit-resolution layer; "derive" + "applying" obscures who. |
| 💡 SUGGESTION | L75 | Throat-clearing | "Each diagram below covers one concern." — the table-of-contents framing ("the following sections", "below") is a soft setup. Could go straight into "Three diagrams: products, stations, execution." |
| 💡 SUGGESTION | L185 | Throat-clearing / meta-commentary | "the recursion is described in the field list rather than drawn as a self-edge (Mermaid routes self-edges through neighbouring entities and the line reads as a phantom relationship)" — meta-commentary about diagramming tools is throat-clearing. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ❌ CRITICAL | L5 (vocabulary primer) | Cold drop of `verify` / `context` / `logger` on a Concepts page | "**`verify` / `context` / `logger`** are three of the 20 pytest fixtures Litmus adds — the common per-test entry points". A test engineer who's never written a Litmus test sees three identifier names with no idea what `context` is *for*. One-liners ("verify checks a value, logger records measurements, context exposes run metadata") would land it. |
| ⚠️ WARNING | L185 | Programmer jargon ("recursive node", "self-edge", "phantom relationship") on a page test engineers will read | "TestEntry is a recursive node — file-scope, class-scope, method-scope all share the same shape; the recursion is described in the field list rather than drawn as a self-edge…" — "recursive node" and "self-edge" are graph-theory / data-structure jargon. Say "tests can nest inside classes, classes inside files; every level uses the same shape." |
| ⚠️ WARNING | L180 (ERD edge label) | Programmer-ish term | `"matches (direction-flipped)"` as an edge label is dense — "direction-flipped" pre-supposes the reader knows the OUTPUT→INPUT pairing rule. The Capability Matching flowchart later (L301) does explain this, but on first encounter it's opaque. Either inline a short note ("DUT outputs are matched to instrument inputs") or anchor-link to the explanation. |
| 💡 SUGGESTION | L254 | Wrong vocabulary level for operator-facing material | "`StationConfig` (Runtime instance for Station)" in the Type-vs-Instance table — `StationConfig` is the Pydantic class name and reads as code, not as the operator's mental model of "the actual bench I'm running on." Consider naming the instance "deployed station" or "Station (instance)". |
| 💡 SUGGESTION | L75 | Anti-audience phrase | "splits cleanly into three concerns" — "concerns" is software-design vocabulary (separation of concerns). A test engineer would understand "splits into three areas: what, how, and what was run." |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L347 | doc says "Parquet files are a materialized view produced by `ParquetSubscriber`" | No `ParquetSubscriber` class exists in the source. The parquet write path is a free-standing function `materialize_run_to_parquet` called by the runs-daemon's event-dispatch loop. Source explicitly says: "No subscriber class needed — projection lives on the accumulator, writing lives here." | `src/litmus/data/backends/parquet.py:546-562` |
| ❌ CRITICAL | L335 | doc says "Session index | `results/sessions/sessions.json`" | No `sessions.json` file is produced or referenced anywhere in the source tree under `src/litmus/data/`. Sessions are tracked in the EventStore (Arrow IPC + DuckDB) and exposed via the `/sessions/{session_id}` HTTP endpoint, not via a JSON index file. | (no source produces `sessions.json`; only `docs/concepts/three-stores.md:80` mirrors the same stale claim) |
| ❌ CRITICAL | L209 (TestEntry ERD) | doc says `runner string` | TestEntry's `runner` field is `dict[str, Any] = Field(default_factory=dict)` — not a string. It carries an opaque per-runner config block (e.g., a `markers:` list for pytest). | `src/litmus/models/test_config.py:179` |
| ⚠️ WARNING | L213-L221 (TestRun ERD) | doc says TestRun has `dut_serial string FK` | TestRun has no `dut_serial` field. DUT identification lives in a nested `dut: DUT` object whose `.serial` field carries the serial. There is also `session_id`, `ended_at`, and ~15 other traceability fields the ERD omits. | `src/litmus/data/models.py:385-446` (esp. L396 `dut: DUT`) |
| ⚠️ WARNING | L149-L162 (Fixture ERD) | doc shows model named `Fixture` with fields `id PK, product_id FK` | Actual class is `FixtureConfig`, not `Fixture`. It has many more fields (`name`, `product_family`, `product_revision`, `station_types`, `dut_resource`, `slots`, `description`). | `src/litmus/models/test_config.py:452-494` |
| ⚠️ WARNING | L105-L110 (SpecBand ERD) | doc shows SpecBand with only `when, value, accuracy, resolution` | SpecBand also has `range: RangeSpec`, `units: str`, and `qualifier: SpecQualifier`. The first two are load-bearing for derated specs (the page even mentions "range derated at high frequency" indirectly via guardband). | `src/litmus/models/capability.py:171-206` |
| ⚠️ WARNING | L141-L148 (Capability ERD) | doc shows Capability with `function, direction, signals, conditions, controls, attributes` | Capability also has `units: str` and `bands: list[SpecBand]` — `bands` is what the Spec → Config flow at L264 actually traverses. | `src/litmus/models/capability.py:431-453` |
| ⚠️ WARNING | L95-L104 (Characteristic ERD) | doc shows Characteristic with `name PK, direction, function, units, signals, conditions, controls, attributes` | ProductCharacteristic (the actual class) extends Capability and adds the physical-interface fields `pin`, `pins`, `net`, `signal_group`, plus `datasheet_ref` and the parent's `bands`. The ERD omits the physical-interface fields, but the prose at L79 calls out "pins, chars, bands" — so they belong in the diagram. | `src/litmus/models/product.py:121-206` |
| ⚠️ WARNING | L334 | doc says event log file is `results/events/{date}/{session_id}.arrow` | Actual path template: `results/events/{date}/{session_id}-{pid}[_{segment}].arrow`. The `-{pid}` and optional `_{segment}` are part of the on-disk filename. | `src/litmus/data/event_log.py:9` |
| 💡 SUGGESTION | L83-L89 (Product ERD) | doc lists `id, name, revision, description` | Product also has `part_number`, `base`, `datasheet`, `schematic`, `driver`, `pins`, `signal_groups`, `characteristics`. ERD is fine to abbreviate, but `part_number` is operator-facing per project conventions and probably belongs. | `src/litmus/models/product.py:250-267` |
| 💡 SUGGESTION | L126-L131 (Station ERD) | doc shows `id, station_type, location` | StationConfig also has `name`, `hostname` (operator-facing per project conventions), `description`, `instruments`, `supported_phases`. `hostname` is load-bearing for auto-selection. | `src/litmus/models/station.py:50-71` |
| 💡 SUGGESTION | L344 | "Arrow IPC + DuckDB via Flight" | Accurate but compressed — the DuckDB-via-Flight bit is the daemon (`_duckdb_flight_server.py`). A test engineer reading this won't know Flight is gRPC and may infer it's an in-process call. Either drop "via Flight" or link to flight-streaming.md for context. | `src/litmus/data/_duckdb_flight_server.py` |
| ✅ VERIFIED | — | 14 claims verified against source (Pin/StationInstrumentConfig/FixtureConnection field lists, fixture names verify/context/logger, "20 pytest fixtures" against reference doc count of 20 ### entries, EventStore/ChannelStore/ParquetBackend class existence, `results/runs/{date}/...parquet` path, ParquetBackend module location, SidecarConfig/TestEntry field overlap, ProductCharacteristic.bands inheritance, band_matches existence, MeasurementFunction enum usage, Direction enum, file/folder conventions for products/stations/fixtures/catalog) | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | L1–L29 ("How the Framework Works") | The page opens with implementation mechanics ("Load specs → Expand vectors → Run test code → Check limits") but never states *what an architecture page is for here* or *what the reader is supposed to take away*. A Concepts page should set up the question it answers; this one launches straight into the answer. A reader who came to "understand how Litmus is put together" doesn't get an anchoring thesis (e.g., "Litmus splits hardware testing into four immutable pieces: what you test, where you test it, what you measure, and what gets recorded — this page maps each piece to a YAML file and a runtime object"). |
| ⚠️ WARNING | L260 ("Spec → Config → Test Flow") | Says "**Limits can come from three places** — product spec, sidecar override, or inline in the test" but never says **which one wins** when more than one is set. Resolution priority is exactly the question a reader would have. |
| ⚠️ WARNING | L275 | Guardband appears as a casual aside ("applying any configured guardband") with no link to where guardband is defined or how to configure it. A reader who's never set a guardband is now lost. |
| ⚠️ WARNING | L299 ("Capability Matching") | Diagram shows the direction-flip rule but doesn't say *what happens when no instrument matches* (does the run abort? skip the test? mark errored?). For a Concepts page this is the natural follow-up question. |
| ⚠️ WARNING | L337 ("Data Architecture") | Three stores listed with one-liners, but the page never explains *why three stores instead of one* — the rationale that justifies the complexity. The deferral to three-stores.md is fine, but a single sentence ("events for source-of-truth replayability, channels for high-rate sample data that would blow up an event log, parquet for analytics queries") would close the loop. |
| 💡 SUGGESTION | L46 ("System Overview") | Diagram introduces "DUT (serial)" and "Station instance" as runtime objects but never says where these *come from* at runtime — operator CLI flag? auto-detect? prompt? A short note ("DUT serial is supplied per-run via `--dut-serial` or operator prompt; station is selected by `--station` or hostname auto-match") would ground the runtime side. |
| 💡 SUGGESTION | L248 (Type vs Instance) | Table rows are useful but each cell is just a class name. Adding *one example value* per cell (e.g., "Product: `power_board_v1`" / "DUT: `SN-001234`") would make the abstract type/instance distinction concrete for a hardware reader. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ⚠️ WARNING | L5 (vocabulary primer) | First use of "**fixture**" in the primer means *test fixture YAML* (DUT-interface routing), not pytest fixture — yet two sentences earlier the same primer lists `verify / context / logger` as "pytest fixtures." Two different "fixture" meanings in the same paragraph with no link disambiguating them. Link the YAML-fixture sense to `concepts/products.md` or `reference/configuration.md#fixture-configuration`. |
| ⚠️ WARNING | L185 (TestEntry block) | First and only mention of "**profile**" on the page — `TestEntry` is described as the recursive node in a "sidecar / profile" tree (per the model docstring, though the architecture page elides this) — but profile is never linked. If the page intends to gloss profiles, add a one-line link to the profile concept or reference page. |
| ⚠️ WARNING | L262 ("Spec → Config → Test Flow") | First use of `Limit` in `logger.measure(name, v, limit=Limit(...))` carries no link to the `Limit` model documentation (`reference/models.md` or `reference/configuration.md`). |
| ⚠️ WARNING | L337 ("Data Architecture") section + page end | Page has no "See also" / "Next steps" / "Further reading" closing block. For a hub Concepts page that fans out to ~6 deeper concept pages (products, stations, capabilities, three-stores, event-log, sessions), this is the obvious place to surface the navigation. Currently only three-stores and event-log get inline pointers at the very last line. |
| 💡 SUGGESTION | L43 ("System Overview") | "DUT" mentioned for the first time without link to where DUT is defined (`reference/models.md#dut-device-under-test`). The audience knows what a DUT *is*, but the Litmus `DUT` model fields are specific. |
| 💡 SUGGESTION | L341–L345 (Data Architecture table) | EventStore / ChannelStore / ParquetBackend are linkable to source-reference pages or to `concepts/three-stores.md` directly from the table rows, not only via the trailing prose at L347. |
| 💡 SUGGESTION | L322 ("File Locations") | Each entity row could link to its concept page (Product specs → concepts/products.md, Station configs → concepts/stations.md, Instrument catalog → reference/catalog-schema.md). The page already cross-links these terms elsewhere; the File Locations table is a natural index. |
| 💡 SUGGESTION | L7 (first flowchart) | The flowchart names `verify / context / logger` in the Test-code node and `results/*.parquet + event log` in the storage node. Mermaid supports click-handlers for nodes — link the test-code node to `reference/litmus-fixtures.md` and the storage node to `concepts/three-stores.md` so a reader can drill in from the diagram. |
