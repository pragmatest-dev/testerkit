# Page audit: docs/how-to/mcp-integration.md

**Quadrant:** How-to (MCP server integration — 12 tools, Claude/Cursor/Copilot/etc setup)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 3 | 2 |
| Voice | 1 | 0 | 3 |
| Audience | 1 | 3 | 1 |
| Accuracy | 3 | 4 | 3 |
| Gaps | 2 | 5 | 3 |
| Cross-links | 1 | 4 | 6 |
| **Total** | **9** | **19** | **18** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ❌ CRITICAL | L128 ("The five-step workflow") | Section is titled "five-step" but contains six steps (Step 0 through Step 4 = five steps, then "Workflow Example: Full End-to-End" repeats them — readers can't reconcile "five" with what they see). Resolve by either renaming to "Initialize + four steps" or counting 0–4 explicitly in the heading. |
| ⚠️ WARNING | L9 | "Architecture: Spec-driven testing with SpecBands, vector-based test parameters, and automated limit derivation" drops three undefined Litmus-specific terms (SpecBand, vector, limit derivation) in the Overview before the page introduces any of them. They are defined later under "Key Concepts" (L338) — but the reader hits them on line 9. |
| ⚠️ WARNING | L39 ("## The 12 MCP Tools") + L56 ("### litmus — Unified CRUD") | The summary table at L41 foreshadows 12 tools, but only six tools get individual subsections (`litmus_project`, `litmus_discover`, `litmus_match`, `litmus_run`, `litmus_open`, `litmus_schema`). The other six (`litmus_events`, `litmus_sessions`, `litmus_channels`, `litmus_metrics`, `litmus_runs`, `litmus_steps`) are listed in the table and then never shown — readers expect symmetry. |
| ⚠️ WARNING | L411 ("## Test Code Pattern") | "Correct" and "Wrong" patterns appear AFTER the test code is already shown in Step 3 (L255-281). The reader has already written the test from Step 3 before encountering the rules that govern it. Move ahead of Step 3 or fold inline. |
| 💡 SUGGESTION | L489 ("Checklist Before Generating Tests") | Checklist appears AFTER the "Full End-to-End" example. A pre-flight checklist belongs before the steps it governs, not after them. |
| 💡 SUGGESTION | L284, L287 | "Test configuration (`tests/test_<module>.yaml`)" — placeholder `<module>` is used as a literal path, but Step 1's example used `tps54302`. Mixing concrete and placeholder forms in adjacent steps makes the example harder to follow. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ❌ CRITICAL | L13 | Marketing / promotional | "(Recommended)" header — promotional flag that doesn't belong on a how-to step heading. |
| 💡 SUGGESTION | L19 | Passive voice (hides actor) | "Detects WSL and configures Claude Desktop to connect to Litmus MCP server." — could be "The setup command detects WSL and writes a Claude Desktop config that connects to the Litmus MCP server." |
| 💡 SUGGESTION | L7 | Hedging / soft | "let AI assistants orchestrate the complete datasheet-to-test workflow" — "let" is hedging. Try "expose the datasheet-to-test workflow to AI assistants." |
| 💡 SUGGESTION | L19 | Throat-clearing | "Restart Claude Desktop to connect." appears as trailing sentence; merge into the prior sentence or lift to its own labelled step. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ❌ CRITICAL | L9 | Cold-drop of core Litmus concepts | "Spec-driven testing with SpecBands, vector-based test parameters, and automated limit derivation." — three Litmus-specific concepts (SpecBand, vector, limit derivation) dropped in the Overview without link or one-liner. A reader new to the MCP page who has not read the concept pages cannot ground these. |
| ⚠️ WARNING | L143-147 | Programmer jargon for test engineers | "**Characteristic:** A measurable property... **SpecBand:** One specification with `when` clause, nominal value, and accuracy... **When clause:** Operating-point parameters" — these are defined inline but use type-system phrasing ("nominal value", "when clause") with no example before the definition. Test engineers will read "characteristic" and "spec band" as Litmus inventions; tie them to datasheet vocabulary ("Electrical Characteristic table row", "condition row in the EC table"). |
| ⚠️ WARNING | L60-76 | Missing T&M grounding | The `litmus_project` block jumps straight into CRUD vocabulary ("Initialize project", "List entities", "Get entity details", "Save entity") with no orientation for what "entity" means in test-engineer terms (product YAML, station YAML, fixture YAML). Use the artifact name, not "entity". |
| ⚠️ WARNING | L78 | Programmer jargon | "**Entity types:** product, station, fixture, catalog, instrument_asset, project, test" — "entity" is generic CRUD-speak. Test engineers think in artifacts (product spec, station config). Also: `instrument_asset` is named here with no explanation, and `project` and `test` are not real entity types in `ENTITY_TYPES` (they're action-specific paths). |
| 💡 SUGGESTION | L264, L266 | Could be tightened | "Get test parameters from vector (context)" — "vector" appears for the first time in a code comment. Test engineers know "vector" from ATE, but a one-liner upfront ("each vector = one row in the parametrize matrix") would land it. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L284-313 | Doc shows `litmus_project(action="save", type="test", id="tests/test_<module>.yaml", content={"code": "tests:\n..."})` — i.e. saving a YAML file via the `test` save type. | `_save_test` forces `.py` extension on every path that doesn't already end in `.py` (line 608: `if not path.endswith(".py"): path = f"{path}.py"`). Calling this with `id="tests/test_foo.yaml"` writes a file at `tests/test_foo.yaml` (which ends in `.py` is false → becomes `tests/test_foo.yaml.py`). Even the existing `.yaml` extension survives — the file is saved as Python content but with `.yaml.py` suffix. Either way, the resulting sidecar YAML will not be loaded as the test sidecar that pytest's Litmus plugin reads. | `src/litmus/mcp/tools.py:602-628` |
| ❌ CRITICAL | L111, L334 | "Returns: run_id, outcome (passed/failed/errored/skipped), measurements, errors" and `print(result["outcome"])  # "passed" / "failed" / "errored" / "skipped" / "done" / "terminated" / "aborted"` | `run_tool` returns keys `run_id`, `status`, `summary`, `test`, `station`, `serial`, `started_at`, `output` — there is no `outcome` key and no `measurements` / `errors` keys. `status` is one of only three values: `"passed" | "failed" | "error"`, derived from pytest exit code, NOT the seven `Outcome` enum values. | `src/litmus/mcp/tools.py:1150-1173` |
| ❌ CRITICAL | L78 | "**Entity types:** product, station, fixture, catalog, instrument_asset, project, test" | `ENTITY_TYPES = ["station", "product", "fixture", "catalog", "instrument_asset", "run"]` for list/get; `SAVEABLE_TYPES = ["station", "product", "fixture", "catalog", "instrument_asset", "test"]` for save. `"project"` is not an entity type for list/get/save. `"run"` is missing from the doc. The set differs by action. | `src/litmus/mcp/tools.py:224-240` |
| ⚠️ WARNING | L323 | "type: Entity type (product, station, fixture, sequence, catalog, instrument_asset, project, test)" — the litmus_project tool docstring on the page hints `sequence` is a type. | `_save_test` and `_get_entity` do not handle `sequence`; the tool's own ENTITY_TYPES list above excludes it. Sequences are deferred (per MEMORY.md). | `src/litmus/mcp/tools.py:224-240` (n/a — `sequence` not listed) |
| ⚠️ WARNING | L441 | `litmus_open` example signature: `litmus_open(type="product", id="tps54302")` returns `{"url": "http://localhost:8000/products/tps54302"}` | Actually returns `{"success": True, "url": "...", "message": "Open ... to view/edit ..."}` — the doc shows only the `url` key but the real return has more. Minor, but readers parsing the return shape will be surprised. Allowed types in the source: `product`, `station`, `run`, `fixture` (no `sequence` despite tool docstring at L441 listing it). | `src/litmus/mcp/tools.py:1186-1204` |
| ⚠️ WARNING | L405 | Comparator table says `"GELE", "EQ", "LE", "GE", ...` | Source defines `EQ, NE, LT, LE, GT, GE, GELE, GELT, GTLE, GTLT` (10 comparators). The doc's enum-style call-out `(`EQ`/`LE`/`GE`/`GELE`/...)` is incomplete; `NE/LT/GT/GELT/GTLE/GTLT` aren't hinted. | `src/litmus/models/test_config.py:217-230` |
| ⚠️ WARNING | L7 | "litmus_run ... return results" (overview) | `litmus_run` returns parsed stdout summary, not structured measurement results. Use "summary" or "exit status + run_id" to be precise. | `src/litmus/mcp/tools.py:1150-1173` |
| 💡 SUGGESTION | L99 | `# Returns: [{"model": "keysight_e36312a", "coverage": 0.95, "accuracy": "..."}]` | `match_tool` requirements-mode delegates to `recommend_from_catalog`; the return shape isn't documented inline. Either link to the function or stop guessing the shape — the example shape (`model`, `coverage`, `accuracy`) is illustrative, not a contract. | `src/litmus/mcp/tools.py:984-988` |
| 💡 SUGGESTION | L155 | Example product fields use `"pins": {"VIN": {"name": "Pin 1", ...}}` | `Pin.name` field expects "pin designator" like `"J1.1"`, `"TP5"`, `"U3.14"` (see model docstring). `"Pin 1"` is awkward as a value for this field — closer to a description than a designator. Use `"J1"` or `"1"` to match real-world YAML. | `src/litmus/models/product.py:70` |
| 💡 SUGGESTION | L334 | `print(result["summary"])  # Test statistics` | Actual `summary` is a string parsed from the pytest stdout summary line (e.g., `"1 passed in 0.42s"`), not structured statistics. Clarify. | `src/litmus/mcp/tools.py:1144-1148, 1167` |
| ✅ VERIFIED | — | 22 claims verified against source (12 MCP tool names; `litmus mcp serve` CLI; `litmus setup claude-desktop/cursor/cline` subcommands; `Outcome` enum values; `Comparator` enum; `Product`, `ProductCharacteristic`, `Pin`, `StationInstrumentConfig`, `StationConfig`, `SpecBand`, `MeasurementLimitConfig`, `Limit`, `SweepEntry` field names; `LITMUS_MARKER_NAMES`; `Context.get_param` signature; `verify` / `context` / `logger` fixtures exist; `extra="forbid"` on the cited models). | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | L11-L37 ("Setup" section) | The page covers `claude-desktop`, `cursor`, `cline`, and a manual fallback — but `litmus setup` also has `claude-code` and `copilot` subcommands (source: `src/litmus/cli.py:1130, 1303`). Readers using GitHub Copilot Chat or Claude Code see this page as the authoritative integration guide and will conclude (wrongly) that their tool isn't supported. |
| ❌ CRITICAL | L33-L37 ("Manual") | "Manual" header followed by just `litmus mcp serve` — no guidance on what config file to edit, where it lives, what transport / command / args the AI client needs in its JSON. A reader on an unsupported client has no path forward. Point to `litmus setup show` (which exists at `cli.py:1431`) or document the config snippet. |
| ⚠️ WARNING | L16, L24, L30 | None of the `litmus setup <client>` commands document what they actually change (file path, registry key, JSON keys added). Operators on locked-down corporate machines need to know what's being written before they run it. The flag `--print-only` exists on these commands but isn't mentioned. |
| ⚠️ WARNING | L130-L137 ("Step 0: Initialize Project") | "Run `uv sync` after" — but readers using AI assistants are doing this through the assistant; the assistant cannot run shell commands without explicit user action. No statement that the user must drop to a terminal here, no statement of what happens if `uv sync` fails (missing uv install? old pyproject?). |
| ⚠️ WARNING | L82-L86 ("litmus_discover") | What happens if no instruments are found? What if the VISA backend isn't installed? What if running in WSL without USB passthrough? All common failure modes for the very first MCP-tool invocation a new user makes. |
| ⚠️ WARNING | L102-L112 ("litmus_run") | No failure-mode coverage: what if the test file doesn't exist? What if the station id is wrong? What if `serial` collides with an existing run? What if mocks aren't configured and there's no hardware? Each of these is the failure mode a brand-new user hits first. |
| ⚠️ WARNING | L319-L322 ("What happens at runtime") | Implies the resolver is deterministic — but does not state the precedence when multiple bands match, or what happens if no band matches at the active vector. `MeasurementLimitConfig` docstring says "if no band matches, the parent config itself is the catch-all" — that promise isn't surfaced here. |
| 💡 SUGGESTION | L489-L501 ("Checklist") | Checklist is good but item 9 says `Test config uses 'characteristics:' + spec_ref limits` — `spec_ref` is documented at L405 as "a free-form annotation only, it does NOT look anything up" — directly contradicts this checklist item. Drop `spec_ref`, say `characteristic:` (which IS the auto-derive trigger). |
| 💡 SUGGESTION | L13 ("Claude Desktop (Recommended)") | Why is it recommended? Recommendation without rationale is just marketing. Either explain (e.g., "first-class MCP support, no extension required") or drop the parenthetical. |
| 💡 SUGGESTION | L455-L487 ("Workflow Example: Full End-to-End") | The "example" is just `# (Use example from Step 1 above)` references — it provides no new information. Either remove the section or run a real concrete example with copy-pasteable success output to compare against. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ❌ CRITICAL | L13, L22, L27, L33 (the four setup blocks) | No cross-link to a `litmus setup` reference or to `litmus setup show` (a real subcommand at `cli.py:1431` that prints the active config). A reader troubleshooting setup needs the reference page. |
| ⚠️ WARNING | L7 | Link `[API reference](../reference/api.md#mcp-tools)` — the anchor in `docs/reference/api.md:32` is `## MCP tools` which markdown auto-slugifies to `#mcp-tools`. Verified present. But the link uses `../reference/` from a `how-to/` page — sibling directories should use `../reference/` (correct), but readers may not notice that the entire 12-tool reference exists. Strengthen by linking again at L41 (the summary table). |
| ⚠️ WARNING | L255 ("Test code (`tests/test_tps54302.py`)") through L313 | First use of `context`, `verify`, `psu`, `dmm` fixtures on this page with no link to `reference/litmus-fixtures.md#context--function` / `#verify--function`. Test engineers landing here from a "what fixtures do I have?" question get no link. |
| ⚠️ WARNING | L411-L452 ("Test Code Pattern") | First use of `verify(name, value)` and `logger.measure(name, value)` patterns with no link to `reference/litmus-fixtures.md`. Item 8 of the checklist (L498) references both — link them. |
| ⚠️ WARNING | L391-L394 | `context.get_param("temperature", 25)` shown three times without linking to `reference/litmus-fixtures.md#context--function` (where Context is documented). |
| 💡 SUGGESTION | L9 ("SpecBands, vector-based test parameters, and automated limit derivation") | Link SpecBand to `concepts/capabilities.md` (or wherever SpecBand is defined), vector to `how-to/vector-expansion.md`, limit derivation to `how-to/limits.md`. All three target files exist. |
| 💡 SUGGESTION | L143 ("Characteristic"), L146 ("SpecBand") | Each Key-Concept bullet should link to its concept page (`concepts/capabilities.md` or `concepts/capability-model.md`) for the long-form definition. |
| 💡 SUGGESTION | L405 ("`MeasurementLimitConfig`") | Mentions the model name and module path in prose but no link to `reference/models.md`. |
| 💡 SUGGESTION | L508 (See also) | Link path `[Specification Format](../how-to/spec-driven-testing.md)` — page lives in `docs/how-to/`, so the relative path should be `spec-driven-testing.md` not `../how-to/spec-driven-testing.md`. The current path resolves (Markdown will follow it), but the parent-dir hop is wrong for a sibling. |
| 💡 SUGGESTION | L503-L508 (See also) | Missing obvious siblings: `how-to/writing-tests.md` (already there), `how-to/limits.md`, `how-to/vector-expansion.md`, `reference/api.md` (the MCP tool reference — the page links to it inline at L7 but not in See also), `reference/litmus-fixtures.md`, `reference/cli.md`. |
| 💡 SUGGESTION | L505 ("[Writing Tests](writing-tests.md)") | Link is to "Detailed test patterns" — but this page's own "Test Code Pattern" section also covers patterns. Cross-reference asymmetry — make clear what's in writing-tests that isn't here. |
