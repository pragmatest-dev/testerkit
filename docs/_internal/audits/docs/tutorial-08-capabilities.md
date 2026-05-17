# Page audit: docs/tutorial/08-capabilities.md

**Quadrant:** Tutorial (step 8 of 10 — capability matching)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 0 | 2 |
| Audience | 0 | 1 | 1 |
| Accuracy | 2 | 2 | 1 |
| Gaps | 0 | 2 | 2 |
| Cross-links | 0 | 1 | 2 |
| **Total** | **2** | **7** | **10** |

---

## Ordering

**Dimension:** Ordering
**Page:** docs/tutorial/08-capabilities.md
**Quadrant:** Tutorial

### Findings

WARNING-ORD-01: "MeasurementFunction vs. Domain+SignalType" section (lines 180-196) appears after the hands-on exercise. This section explains the rationale for the schema the learner just typed YAML for. The learner needs the "why" before or alongside the YAML exercise — not after it. Moving this section to just before "How Matching Works" would give the reader the context to understand what `function: dc_voltage` means when they first see it.

SUGGESTION-ORD-02: The "Handling Missing Capabilities" code snippet (lines 200-213) uses a Python example that references `check_station_compatibility("my_product", "station_a")` — products and stations defined in the exercise. However, this section comes after the exercise block and after the MeasurementFunction tangent, making the flow: exercise → conceptual detour → follow-up code. Consolidating the exercise and its follow-up code (run matcher, inspect result) into one contiguous block would reduce the cognitive distance between action and consequence.

SUGGESTION-ORD-03: The "Benefits of Capability Matching" bullet list (lines 219-225) sits between the missing-capabilities example and "What You Learned." Benefits summaries work better immediately after the concept is introduced (after "The Solution: Capabilities") rather than at the end, where they read as a recap the learner did not need.

---

## Voice

**Dimension:** Voice
**Page:** docs/tutorial/08-capabilities.md
**Quadrant:** Tutorial

### Findings

SUGGESTION-VOI-01: The inline annotation on line 59 is written in a parenthetical aside style that breaks the instructional flow:

  > Station provides capabilities: (`catalog_ref` points at an entry in the instrument catalog — `catalog/*.yaml` — that declares this instrument model's full capability shape. See [reference/catalog-schema](../reference/catalog-schema.md).)

This is reference exposition injected into a YAML code comment label. Tutorials should show, then explain briefly, then link. The parenthetical should become a short sentence after the code block: "The `catalog_ref` key points at a catalog entry that declares the instrument's full capability shape — see [Catalog schema](../reference/catalog-schema.md)."

SUGGESTION-VOI-02: Line 75 introduces `MatchDepth` with a parenthetical definition: "(an enum naming how deep the match check should go)." For a tutorial reader who has not yet seen `MatchDepth` used in code, this abstraction is premature. The tier list is self-explanatory. The parenthetical definition of the enum can be dropped here and introduced only if the learner needs to pass it as an argument — which this page never actually demonstrates in the Try It section.

---

## Audience

**Dimension:** Audience
**Page:** docs/tutorial/08-capabilities.md
**Quadrant:** Tutorial

### Findings

WARNING-AUD-01: The "Try It: Using the Matcher" section (lines 85-117) shows Python API and HTTP API examples but never tells the reader how to actually run them. A tutorial reader at step 8 expects "do this, see that." The Python snippet requires `get_product` to succeed, which requires a products/ directory with a `power_board.yaml` — a file that does not exist in a fresh project and that this page never tells the reader to create. The hands-on exercise (lines 119-178) creates `my_product.yaml`, not `power_board.yaml`. The "Try It" section therefore cannot be executed as written without the reader first doing the exercise. The ordering implies the reader can try the API first, but they cannot.

SUGGESTION-AUD-02: The "MeasurementFunction vs. Domain+SignalType" section (lines 180-196) uses "old model" vs "new model" framing that assumes the reader has context about a previous schema. A tutorial reader encountering Litmus for the first time has no prior acquaintance with a domain+signal_types model. This framing answers a question the beginner has not asked. Reframe as: "Why `function: dc_voltage` instead of `domain: voltage, signal_type: dc`" and lead with the user benefit ("The function name is specific enough to distinguish a DMM from a scope") rather than the migration history.

---

## Accuracy

**Dimension:** Accuracy
**Page:** docs/tutorial/08-capabilities.md
**Quadrant:** Tutorial

### Findings

CRITICAL-ACC-01: Line 104 — `match.missing` does not exist on `StationMatch`.

The page shows:
```python
print(f"✗ {match.station_id} missing: {match.missing}")
```

`StationMatch` (service.py lines 139-145) has fields: `station_id`, `station_name`, `compatible`, `match_result`. It has no `.missing` attribute. The missing capabilities are at `match.match_result.missing`, which is a `list[CapabilityRequirement]`. The correct line is:
```python
print(f"✗ {match.station_id} missing: {match.match_result.missing}")
```

CRITICAL-ACC-02: Lines 201-213 — The `check_station_compatibility` code example accesses `result["missing"]` items as `cap['direction']` and `cap['function']`. Per service.py lines 677-684, each missing dict has three keys: `"characteristic"`, `"function"`, and `"direction"`. The code snippet on line 207 only uses `direction` and `function`, which are present — so the access is not wrong. However, the inline annotation on lines 210-211 describes the shape as:

> The `missing` value is a list of dicts shaped `{characteristic, function, direction}`.

This is accurate (three keys). No error here — reverting this finding.

WARNING-ACC-02: Line 210 describes `check_station_compatibility(product_id, station_id)` as taking "ID strings (not loaded objects) and returns a `dict | None`." This is correct per the signature (`product_id: str, station_id: str`). However, the function also accepts a third parameter `project: str | Path | None = None` (service.py line 651) that is silently omitted. This is not an error in itself, but the statement "takes ID strings (not loaded objects)" could mislead a reader into thinking `get_product` (used in the first Python example) accepts a string the same way — which it does, but the two functions have different optional argument shapes. Minor documentation elision.

WARNING-ACC-03: The Direction Flip table (lines 33-35) states "BIDIR satisfies both" in the tier description (line 78) but the table itself has only two rows (OUTPUT and INPUT) with no BIDIR row. The matching logic in service.py (_directions_compatible, lines 199-218) shows BIDIR on the instrument side satisfies any product direction, but DUT BIDIR only matches instrument BIDIR. The table is not wrong but it is incomplete: a learner who configures a BIDIR product characteristic and wonders why it only matches BIDIR instruments has no explanation here.

SUGGESTION-ACC-04: The ASCII flow diagram (lines 17-25) uses `direction: OUTPUT` for the product characteristic and says the station DMM "provides dc_voltage INPUT." The matching logic does implement the direction flip (OUTPUT → INPUT) correctly. However, the diagram says "Required: dc_voltage measurement capability (direction: INPUT)" as if the product spec produces an intermediate requirement with a flipped direction. That intermediate is not a YAML field — the flip happens inside `_directions_compatible()`. A reader following the diagram may look for a `direction: INPUT` field in their product YAML that does not exist. Clarify that the flip is internal to the matcher, not expressed in any file.

---

## Gaps

**Dimension:** Gaps
**Page:** docs/tutorial/08-capabilities.md
**Quadrant:** Tutorial

### Findings

WARNING-GAP-01: The hands-on exercise (lines 119-178) creates two station YAML files that reference `catalog_ref: generic_dmm` and `catalog_ref: generic_current_clamp`. There is no explanation of where these catalog entries come from or how to create them. A learner who follows the exercise verbatim and then runs the matcher will get a silent skip (service.py line 266: instruments without a resolvable `catalog_ref` are skipped with a warning) resulting in zero capabilities — making every station appear incompatible. The exercise is broken without pre-existing catalog entries. The page should either point to an existing catalog file in the examples directory or provide a minimal catalog YAML to create.

WARNING-GAP-02: The page tells the reader to "Run the matcher" (line 175) but never shows the actual command to do so. The HTTP API curl command is shown earlier, but the exercise uses `my_product` not `power_board`, and the exercise section has no corresponding API call or Python invocation. The step "3. Run the matcher" has a description of expected output but no executable command. This is the most actionable part of a hands-on exercise and it is missing.

SUGGESTION-GAP-03: The page does not mention what happens when an instrument has no `catalog_ref`. The matching service silently logs a warning and skips it (service.py lines 264-269). A tutorial reader whose station YAML is missing a `catalog_ref` will see every station report as incompatible with no obvious error. A one-sentence note — "Instruments without a `catalog_ref` are skipped during matching; the CLI will warn you" — would prevent a common debugging dead end.

SUGGESTION-GAP-04: `MatchDepth` is mentioned (line 75, 83) as something the user can use for tighter validation, but the page never shows how to pass it to any API call. The Python API example uses `find_compatible_stations(product)` with no depth argument. If the reader wants to use `MatchDepth.ACCURACY`, they have no example to follow. Either show a depth argument in the Python example or remove the forward reference to `MatchDepth.ACCURACY` / `MatchDepth.RESOLUTION` from the tutorial — save that for a how-to guide.

---

## Cross-links

**Dimension:** Cross-links
**Page:** docs/tutorial/08-capabilities.md
**Quadrant:** Tutorial

### Findings

WARNING-CLK-01: The page links to `../reference/models.md` (line 80) for `SpecBand`, and to `../reference/catalog-schema.md` (line 59) for the catalog shape. Both files exist. However, there is no link to `docs/concepts/capabilities.md` or `docs/concepts/capability-model.md` — both of which exist and are the conceptual home for the capability model, direction flip, and MatchDepth explanations. A tutorial reader who wants to understand the model more deeply should be directed there. At minimum, a "Learn more" line at the bottom (before the Next Step) pointing to the concepts pages would complete the Diátaxis loop (Tutorial → Concept).

SUGGESTION-CLK-02: The tutorial references `products/power_board.yaml` in the Python API example but never links to tutorial step 06 (specifications) where product specs are first introduced. A reader who skipped step 06 and arrived at step 08 via search has no pointer back to where product YAML syntax is taught. Adding "See [Step 6: Specifications](06-specifications.md) for product YAML syntax" near the first product YAML block would close this gap.

SUGGESTION-CLK-03: The HTTP API section shows `curl http://localhost:8000/api/match?product_id=power_board`. There is no link to `docs/reference/api.md` (which exists) where the full `/api/match` route is documented. A reader who wants the full query parameters, response schema, or error codes has no pointer to the reference. Add a note: "Full API reference: [reference/api](../reference/api.md)."
