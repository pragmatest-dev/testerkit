# Page audit: docs/reference/configuration.md

**Quadrant:** Reference (litmus.yaml, station YAML, fixture YAML, sidecar YAML, profile YAML schemas + merge semantics)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 2 | 1 |
| Voice | 0 | 2 | 1 |
| Audience | 0 | 2 | 1 |
| Accuracy | 7 | 6 | 2 |
| Gaps | 4 | 5 | 2 |
| Cross-links | 2 | 4 | 2 |
| **Total** | **14** | **21** | **9** |

---

## Ordering

### CRITICAL

- **Project Configuration is buried at the bottom.** `litmus.yaml` is the project root — the file every other YAML lives under and the file that resolves search paths / `catalog_dir` / `default_station`. Reference pages should lead with the entity that anchors all others. Today's order — Product → Station → Fixture → Test Config → Catalog → Project — forces a reader to scroll past five sections to find the file that determines where the others are found. Suggested order: Project → Station → Fixture → Catalog → Product → Test Config (sidecar) → Profile (which is inside Project but cross-cuts everything).

### WARNING

- **Catalog sits awkwardly between Test Config and Project.** The Test Config section is large and runner-flavoured; the Catalog section is structural / schema-flavoured. Place Catalog with the other entity schemas (Station / Fixture / Product), not as a wedge between session config and project config.
- **Test Config blends three distinct shapes — inline marker, sidecar, profile — but only the sidecar gets a real heading.** Profile YAML is documented under "Project Configuration" near the bottom, not in the Test Config section where users will look for it. Either pull profiles into Test Config or add a "Profile YAML" sub-heading inside Project that the Test Config resolution-order list links to.

### SUGGESTION

- The Comparator Reference and Pin Types tables live inside the Product Specification section but neither is product-specific (Comparator belongs to `Limit` on test config; pin role belongs to Product). Comparator should move to the Test Config / `litmus_limits` area; Pin Types is fine where it is.

---

## Voice

### WARNING

- **"When to Use Fixtures" table is how-to guidance, not reference.** Reference describes the schema; how-to recommends when to use it. The "When to Use Fixtures" table belongs in `docs/concepts/fixtures.md` or a how-to, not in a schema reference.
- **The Test Configuration prose drifts into tutorial-style explanation.** Sentences like "Per-test config is one vocabulary — pytest markers — delivered three ways" and "Marker fields live directly at each entry's root, alongside the reserved `runner:` and `tests:` keys; same shape inline (...)" are narrative justifications that read like a how-to introduction. A reference page should state the shape and let the reader infer; the "why" goes in a concepts page (or a one-line link).

### SUGGESTION

- Several inline comments in the YAML blocks editorialize (e.g., `# Or product family for shared fixtures`, `# Optional reference`, `# Bus interfaces`). These are useful but the page mixes "type comment" (`string`, `integer`) with "guidance comment" (`# Preferred`, `# Optional`) inconsistently. Pick a convention — either always show `Type — purpose` or always show `Type` only.

---

## Audience

### WARNING

- **"For pytest-ecosystem retries instead of the Litmus marker, `pytest-rerunfailures` still works"** assumes the reader knows what pytest-rerunfailures is, what `@pytest.mark.flaky(reruns=...)` does, and that this is the same `flaky` marker referenced in `runner.markers:`. A test engineer migrating from OpenHTF or LabVIEW will be lost.
- **Sidecar resolution order uses pytest-internal vocabulary** ("class-branch / per-test", "node IDs", `file::Class::method`) without first establishing what a pytest node ID is. Reference can assume pytest familiarity, but a one-line link to `pytest-native.md` (which defines node IDs) would let non-pytest users follow along.

### SUGGESTION

- The "Vector shape" subsection uses "argvalues" — pytest's `parametrize` internal term. Test engineers reading the configuration reference may not know that vocabulary; "values" or "values list" is clearer for a reference page.

---

## Accuracy

### CRITICAL

- **`runner: string  # Optional default runner (e.g. "pytest")` is wrong.** `ProjectConfig.runner` is `dict[str, Any] = Field(default_factory=dict)` (see `src/litmus/models/project.py:86`), not a string. Users writing `runner: "pytest"` in `litmus.yaml` will pass Pydantic validation (Pydantic accepts a string as a `dict[str, Any]`? No — it raises `ValidationError`) and get a confusing error. The model also has no concept of "default runner" — runner selection happens via the active runner's plugin, not via `ProjectConfig`.
- **`required_inputs: list  # Optional list of session-required inputs` is wrong.** Model is `required_inputs: dict[str, PromptConfig] = Field(default_factory=dict)` (project.py:87). Users following the doc and writing `required_inputs: [...]` will get a Pydantic error.
- **Catalog `base:` merge rule for `capabilities:` is wrong.** Doc table claims `capabilities:` "Replaces base entirely". Actual code (`src/litmus/store.py:907` `_merge_capabilities` and `_deep_merge_cap`) merges capabilities by `(function, direction)` key and deep-merges `signals` / `conditions` / `controls` / `attributes` at the parameter level inside matching capabilities. Variants only need to declare the deltas — this is the whole point of `base:`. The doc as written tells users to redeclare every capability in the variant.
- **ProductCharacteristic claims fields that don't exist on the model.** Doc YAML lists `channel: string`, `channels: [string] | string`, and `schematic_ref: string` (the last marked "Deprecated: use net instead"). `ProductCharacteristic` (src/litmus/models/product.py:121) extends `Capability` and adds only `pin`, `pins`, `net`, `signal_group`, `datasheet_ref`. With `model_config = {"extra": "forbid"}`, writing any of these three rejected fields raises `ValidationError`. The doc is inventing schema.
- **`channels: [string]` on StationInstrumentConfig is wrong.** Doc says `channels: [string]  # Optional: channel keys (resolved from catalog if omitted)`. Model is `channels: dict[str, str] = Field(default_factory=dict)` (station.py:32). A YAML list will fail validation.
- **Fixture YAML example omits the required `name:` field on each `FixtureConnection`.** The example under "### Example" has:
  ```yaml
  connections:
    VIN:
      dut_pin: VIN
      ...
  ```
  But `FixtureConnection.name: str` is required (`test_config.py:404`) and there is no loader-side auto-fill from the key. A user copy-pasting this example into `fixtures/power_board_fixture.yaml` will get `ValidationError: name field required`. The shipped example fixture at `examples/07-profiles/fixtures/buck_3v3_bench.yaml` declares `name:` redundantly for every connection, confirming this is required.
- **"funcgen" is not a recognized instrument type or alias.** The "Common Instrument Types" table lists `funcgen` but `InstrumentType` enum has `FUNCTION_GENERATOR = "function_generator"` and `_INSTRUMENT_TYPE_ALIASES` (store.py:1533) maps the short form `fgen` → `function_generator`. There is no `funcgen` alias. Users writing `type: funcgen` will trigger the "Type 'funcgen' is unknown" warning.

### WARNING

- **Common Instrument Types table uses informal short names but doesn't say they're aliases.** `scope`, `eload`, `funcgen` are not the canonical enum values — they're aliases (or, in `funcgen`'s case, invalid). The canonical values are `oscilloscope`, `electronic_load`, `function_generator`. The page should either show canonical names with aliases noted, or explicitly say "aliased to" and point at `_INSTRUMENT_TYPE_ALIASES`.
- **Resolution-order list claims "Inline `@pytest.mark.<name>(...)` on method or class" is order #3 (between sidecar leaf and profile chain), implying inline beats sidecar leaf but loses to profile.** The actual cascade (`src/litmus/execution/cascade.py:cascade_for`) merges sidecar then profile via `_merge_entry_into`; inline markers are pytest markers attached at collection time, not part of the cascade. Profile-injected markers and inline markers both appear in `item.own_markers` after `_apply_cascade_to_items`, and pytest's `iter_markers` ordering — not the doc's numbered list — determines the effective precedence. The neat 5-step list oversimplifies a real interaction the user will have to debug eventually.
- **Sidecar "Resolution order" mentions "CLI flags — always win" as item #5, but the doc never says which CLI flags can override which fields.** Some CLI flags (e.g. `--mock-instruments`) override `ProjectConfig.mock_instruments`; others (`-k`, `-m`) compose with `runner.keyword` / `runner.markexpr` per `flatten_profile_chain`. Treating "CLI flags always win" as a single tier hides this nuance.
- **`base:` field on Product is missing from the doc.** Model has `base: str | None = None` (product.py:255) for product-level inheritance, parallel to catalog's `base:`. The doc's Product Specification block doesn't mention it, so users don't know products can extend a base product the same way catalog entries can.
- **`driver: string` on Product is missing.** Model has `driver: str | None = None  # Dotted import path` (product.py:260). Used by the DUT driver fixture chain. Doc omits it.
- **`part_number: string` on Product is missing.** Model has `part_number: str | None = None` (product.py:254). User-facing operator label per the project's "operator-facing identifiers" rule.
- **Comparator pass-condition column is misleading for the single-bound forms.** Doc table says `LT  → value < high`. Actual code (`test_config.py:220` `_COMPARATOR_CHECKS`): `"LT": lambda lim, v: lim.high is None or v < lim.high`. The "or `high is None`" branch — pass-on-missing — means `LT` with no `high:` always passes, which is rarely what a user expects. Document the None-pass behaviour or call it out as a warning.

### SUGGESTION

- The opening line "Litmus uses YAML files for configuration, validated by Pydantic models." undersells the architecture — store.py's filename-as-id rule, `extra="forbid"` validation, and `base:` inheritance all matter for a reference reader and are documented inline elsewhere but absent from the page opener.
- The "Pydantic Models" section at the bottom suggests `from litmus.store import load_product, load_station` but lists only two of ~30 loader functions. A user wanting to load a fixture or catalog entry has no signal that `load_fixture` / `load_catalog_entry` exist. Either show the full surface or point at `models.md` / API reference.

---

## Gaps

### CRITICAL

- **Multi-DUT slots are entirely missing from the Fixture Configuration section.** `FixtureConfig.slots: dict[str, FixtureSlot]` (test_config.py:492) is a top-level entity with its own per-slot `connections`, `dut_resource`, and `description`. Multi-DUT testing is the only way to run more than one DUT per session — and the reference for the fixture schema doesn't mention slots at all. There's a separate how-to (`multi-dut-testing.md`) that demonstrates the shape, but the reference is silent.
- **`station_type:` on `StationConfig` is missing.** Model field at station.py:61 with extensive docstring explaining it's "load-bearing: when set, the resolver checks at session start that the station's declared instruments cover the roles its named StationType requires". The doc's Station Configuration block omits this field entirely.
- **`hostname:` on `StationConfig` is missing.** Model field at station.py:65: "When set, the session-start resolver auto-matches against `socket.gethostname()` so operators don't need to pass `--station=<id>` on the matching machine." This is one of the most operator-visible station features and the reference is silent on it.
- **The whole `StationType` template schema is missing.** `StationType` (station.py:84) is a separate YAML at `stations/types/*.yaml` referenced by `StationConfig.station_type` and by `ProfileConfig.station_type`. The doc never mentions station-type templates, so a user reading the Station Configuration section cannot author a `stations/types/bench.yaml` and reference it from their station file.

### WARNING

- **`ProfileConfig.station_type` and `ProfileConfig.fixture` are missing.** Both fields exist on the model (project.py:45, 49) and are part of the documented profile resolution chain. The Profile YAML block under Project Configuration shows `description`, `facets`, `extends`, `runner`, `limits`, `tests` but omits the two fields that bind a profile to its target station-type and fixture.
- **`MultiSlotConfig` shape is undocumented.** Doc has `multi_slot: MultiSlotConfig   # Optional multi-DUT slot configuration` but never expands the model. It has one field (`child_grace_seconds: float = 5.0`) used to bound SIGTERM-to-SIGKILL escalation for child pytest processes. Users hitting timeouts on slow GPIB disconnects need this knob and won't find it.
- **`runner:` subfields are undocumented.** Doc shows `addopts`, `markexpr`, `keyword` on profile `runner:` but the validated `PytestRunner` model (`src/litmus/execution/profiles.py:48`) also accepts `plugins: list[str]`, `parallelism: int | None`, `timeout: int | None`, and `markers: list[dict[str, Any]]`. The last is especially load-bearing — it's how ecosystem markers like `flaky` / `skip` get applied via a profile.
- **`SwitchRoute` for switched fixtures is missing.** `FixtureConnection.route: SwitchRoute | None` (test_config.py:424) lets a fixture declare a switch matrix route — `{switch, channels, settling_ms}` — that the platform closes before the instrument is used. Documented inline in the model's class docstring with a full example; absent from the reference.
- **`MeasurementLimitConfig` is not documented on this page.** The `limits:` section examples show `{low, high, units}` shape only. The real `MeasurementLimitConfig` model (test_config.py:579) supports `characteristic + tolerance_pct / tolerance_abs` (derived from product spec), `bands:` (condition-indexed), `callable`, `expr`, `lookup`, `steps`, `guardband_pct`, `comparator`. The page mentions some of these elsewhere ("Full shape" in litmus-markers.md) but the configuration reference itself shows only the simplest direct-limit shape, missing the entire characteristic-driven and condition-banded patterns.

### SUGGESTION

- The page never mentions filename-as-id agreement (`store.py:_check_id_matches_filename`). Users who name their station YAML `bench1.yaml` and put `id: bench_1` inside will hit a confusing ValueError. The rule applies to every id-keyed entity and belongs in this reference.
- `extra="forbid"` is referenced implicitly but never explained. Most Litmus Pydantic models reject unknown fields; users typo-ing `descriptin:` will get a Pydantic error. A short note up top would prevent confusion.

---

## Cross-links

### CRITICAL

- **`docs/how-to/profiles.md` is mentioned as a raw path inside a YAML comment, not as a markdown link** (line 394). The page should link to it: `[profiles](../how-to/profiles.md)`. This is the single canonical how-to for the entire Profile YAML section and currently the only pointer to it is buried in a `#` comment.
- **No link to `catalog-schema.md`.** The Instrument Catalog section in this page duplicates content that's authoritative in `docs/reference/catalog-schema.md` (which is explicitly labelled "Authoritative shape of a `catalog/...` entry"). The reference page should either defer to `catalog-schema.md` and show a short stub, or at minimum link to it. Today it does neither.

### WARNING

- **No link to `docs/how-to/limits.md`** even though the Test Configuration section discusses `limits:` extensively. `limits.md` covers the characteristic-driven and banded limit shapes this page omits.
- **No link to `docs/how-to/spec-driven-testing.md`** despite the Product Specification section being the source of truth for characteristics that spec-driven testing reads from.
- **No link to `docs/concepts/fixtures.md`.** The fixture section discusses the pin → instrument routing model that `concepts/fixtures.md` explains; the reference should point at it for the conceptual backing.
- **No link to `docs/reference/catalog-cookbook.md`.** The catalog section shows a single dc_voltage example; the cookbook has one recipe per recurring datasheet shape and is the natural follow-up for anyone trying to write a real catalog entry.

### SUGGESTION

- The "Next Steps" list at the bottom should include `catalog-schema.md` and `catalog-cookbook.md` (the natural deep-dive after seeing the in-page catalog summary) and `profiles.md` (the natural deep-dive after seeing the in-page profile summary).
- The page references `src/litmus/models/...` paths inline (e.g., "see `src/litmus/models/product.py`") but never links to `docs/reference/models.md`, which is the doc-side authoritative model surface. Most readers will prefer the doc.
