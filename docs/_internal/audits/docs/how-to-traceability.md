# Page audit: docs/how-to/traceability.md

**Quadrant:** How-to (full traceability — in_*, out_*, custom columns, spec_ref, characteristic_id, dut_pin)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 2 | 2 |
| Voice | 1 | 2 | 3 |
| Audience | 0 | 2 | 2 |
| Accuracy | 2 | 3 | 2 |
| Gaps | 2 | 5 | 3 |
| Cross-links | 1 | 4 | 5 |
| **Total** | **7** | **18** | **17** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| CRITICAL | L86–115 | "Setting Traceability in Tests" leads with "Automatic (via Fixture)" using `pins` — but `pins` is never introduced anywhere on the page. The reader hits `pins["VOUT"].measure_voltage()` cold. As a how-to, this section is the operational core; it should either define `pins` inline or move "Using ProductContext" first since ProductContext is named in the diagram (L55–60). |
| WARNING | L7–15 | The "What is ATML?" section appears before "Traceability Fields." For a how-to titled "Measurement Traceability," ATML history is background context that belongs after the reader knows what fields exist. Lead with what you provide, then optionally explain the standard you align with. |
| WARNING | L128–149 | "Hierarchical Context" introduces `TestHarness`, `harness.run_context`, `harness.context`, `harness.step()`, and `harness.run_vector(vector)` all at once with no prior mention. This is a how-to about traceability; the harness section drops the reader into runner-agnostic execution with no setup. Either move below the pytest-native examples with a clear "if you're not using pytest" framing, or remove. |
| SUGGESTION | L168–223 | "Comparators (ATML/IEEE 1671)" is a complete subtopic that breaks the flow from "Setting Traceability in Tests" → "Querying Traceable Results." Comparators are about limit evaluation, not traceability. Consider relocating to `docs/how-to/limits.md` (which already has a Comparators section at L152) and linking from here. |
| SUGGESTION | L17–44 | The "Traceability Fields" tables list `instrument_*` and `dut_pin` before the "Stimulus Signal Path" table, but the chain diagram below (L45–82) introduces them in reverse order (output first, then stimulus). The two structures match — good — but neither table includes `characteristic_id` or `spec_ref`, both of which appear in the chain diagram and are listed in the page subtitle. Add a row for each. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| CRITICAL | L319 | Marketing language | "Root Cause Analysis — When a test fails, identify exactly which instrument and channel were involved" — the "Benefits of" framing is product-marketing copy, not documentation. |
| WARNING | L5 | Throat-clearing + marketing | "**The framework automatically captures ALL metadata when a measurement is produced.** No user effort required." Both the all-caps "ALL" and the "no effort required" are promotional, not factual — and the rest of the page contradicts it (manual section, custom fields require `run_context.set`, ProductContext requires loading). |
| WARNING | L317 | Marketing header | "Benefits of Traceability" — six-bullet feature-list framing reads like a slide deck rather than a how-to. |
| SUGGESTION | L100 | Passive / vague | "When using instruments directly, set traceability manually" — "manually" here is fine; but "When using instruments directly" hides the actor — prefer "When you call the instrument directly without `pins`, …". |
| SUGGESTION | L331 | Generic "Best Practices" header | Six prescriptive bullets at the end of a how-to that already prescribed those exact patterns above. Either delete or replace with a short "common pitfalls" section that names failure modes. |
| SUGGESTION | L88 | Header weight | "### Automatic (via Fixture)" / "### Manual (Direct Instruments)" / "### Using ProductContext" — three peer subheaders inside "Setting Traceability in Tests" without explaining when to prefer which. The headers act as labels for code blocks rather than decision points. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| WARNING | L9 | Programmer jargon / academic | "ATML (Automatic Test Markup Language) is an IEEE standard (IEEE 1671) for exchanging test information." A test engineer who has run ATEs may know ATML; many won't. Either drop the section or anchor it to a concrete benefit ("you can export to ATML-compatible MES systems") instead of a definition. |
| WARNING | L130 | Cold-drop of core concept | "The [harness](../integration/harness.md) (Litmus's runner-agnostic execution wrapper) provides hierarchical context with scoped inheritance" — "runner-agnostic execution wrapper" is platform vocabulary the reader hasn't earned by this point. A test engineer on a how-to about traceability is not asking "how do I escape pytest?" — they're asking "where do these columns come from?". |
| SUGGESTION | L11 | Wrong terminology framing | "Litmus maps these onto its lowercase `passed / failed / skipped / errored / done / terminated / aborted` enum" — listing 7 outcomes mid-prose is dense and irrelevant on a traceability page. The outcome enum belongs in `parquet-schema.md`. |
| SUGGESTION | L304 | Operator-facing identifier | The compliance-report example uses `dut_serial` (correct) but also "DUT Pin: J1.3" and `instrument_name="dmm_main"`; consistency could be tightened by also showing `dut_part_number` since that's the operator-facing identifier. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| CRITICAL | L104–115 | `logger.measure("output_voltage", voltage, dut_pin="J1.3", instrument_name="dmm", instrument_channel="CH1")` — claims `logger.measure` accepts `dut_pin`, `instrument_name`, `instrument_channel` kwargs. | `TestRunLogger.measure` signature is `(name, value, *, limit, outcome, allow_repeat)`. None of the three traceability kwargs exist. Docstring explicitly says: "Callers never pass these." | `src/litmus/execution/logger.py:941–969` |
| CRITICAL | L122–126 | `verify("output_voltage", dmm.measure_dc_voltage())` "resolves the limit and traceability from the active ProductContext (configured via --product=products/power_board.yaml)". Doc implies `--product=` takes a path including `.yaml` extension as the canonical form. | `--product` accepts either a bare id (`--product=power_board` → `products/power_board.yaml`) OR a path (must contain `/` or end in `.yaml`/`.yml`). Bare id is the common form and is shown in fixtures.md / pytest_plugin docstrings. Path is the disambiguation form. | `src/litmus/pytest_plugin/__init__.py:443–478` |
| WARNING | L97 | `pins["VOUT"].measure_voltage()` — uses `.measure_voltage()` (no `_dc_`). | Pin objects route to instrument driver methods; the codebase examples consistently use `dmm.measure_dc_voltage()` (with `_dc_`) for DMM measurements. `measure_voltage()` works in `src/litmus/fixtures/manager.py` examples but is inconsistent with every other docs/init example. | `src/litmus/init.py:566`, `src/litmus/skills/templates/project-instructions.md:71` (uses `dmm.measure_dc_voltage()`); cf. `src/litmus/fixtures/manager.py:9,50,284` (uses `pins[X].measure_voltage()`) |
| WARNING | L160–166 | `run_context.set("calibration_due", "2026-06-15")` — implies any value type works. | `RunContext.set` docstring says "value: Field value (must be JSON-serializable for Parquet)". A bare date string is fine; the page never states the JSON-serializability constraint, which matters for `datetime`, `Decimal`, `numpy` types operators are likely to try. | `src/litmus/execution/logger.py:340–348` |
| WARNING | L208, L223 | `spec_ref: "output_voltage @ tolerance_pct=5"` — uses an `@`-and-kwargs string format. | `spec_ref` is a free-form string ("Human-readable spec reference with conditions" / "Table 4.2 @ temp=25, load=0.8" per Limit model docstring). The `@ tolerance_pct=5` syntax shown is not a parsed form — `tolerance_pct` is a sibling YAML key on `MeasurementLimitConfig`, not part of `spec_ref`. Using this string as `spec_ref` is legal but the doc implies parser semantics. | `src/litmus/data/models.py:182`, `src/litmus/models/test_config.py:246, 254, 646` |
| SUGGESTION | L227 | "Results are stored in Parquet files at `results/runs/{date}/{timestamp}_{serial}.parquet` (UTC timestamps)." | Correct path shape (`src/litmus/data/backends/parquet.py:193`) — but `results/` here is relative to the resolved `data_dir`, which defaults to `~/.local/share/litmus/data/` (platformdirs) or the project's `litmus.yaml` `data_dir:` override. The doc reads as if `results/` is always at the project root, which is only true when `litmus.yaml` sets `data_dir: results`. | `src/litmus/data/data_dir.py:32–61`, `src/litmus/data/backends/parquet.py:193–217` |
| SUGGESTION | L234 | `pd.read_parquet("results/runs/2026-01-15/20260115T143025Z_SN001.parquet")` — example path uses `2026-01-15` date and `20260115T143025Z` timestamp. | Format is correct (compact ISO 8601 basic, UTC `Z`) but the doc never connects them — a reader may wonder why the directory has dashes and the file doesn't. | `src/litmus/data/backends/parquet.py:204–217` |
| VERIFIED | — | 18 claims verified against source (schema field names spec_ref/dut_pin/instrument_name/instrument_resource/instrument_channel/fixture_connection/characteristic_id; comparator names GELE/GELT/GTLE/GTLT/GE/GT/LE/LT/EQ/NE; default comparator GELE; `in_{param}_{suffix}` patterns with suffixes `_instrument`, `_resource`, `_channel`, `_dut_pin`, `_fixture_connection`; `run_context.set` API; `TestHarness(step_name=...)` constructor; `harness.run_context` / `harness.context` / `harness.step()` / `harness.run_vector(vector)` APIs; `Comparator` enum members) | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| CRITICAL | L86–98 (Automatic) | `pins` fixture is used with zero prerequisites stated. To make `pins["VOUT"]` work, the reader needs (1) a product YAML with `pins:` and `nets:`, (2) a fixture YAML wiring nets to fixture connections, (3) a station YAML loaded with `--station=`, (4) a fixture config loaded with `--fixture=`. None of this is mentioned. A reader copying the example will get `KeyError: 'VOUT'` or `fixture 'pins' not found`. |
| CRITICAL | L117–126 (Using ProductContext) | "verify resolves the limit and traceability from the active ProductContext (configured via --product=...)" — but no example of what the ProductContext YAML needs to look like, or how `output_voltage` maps to a characteristic in it. The `dut_pin` / `instrument_*` columns won't be populated unless the product spec has `characteristics` with `pin:` entries — never stated. |
| WARNING | L32–43 (Stimulus Signal Path table) | The table claims `in_vin_instrument`, `in_vin_resource`, etc. are auto-populated, but never says **what populates them**. A reader who sets `psu.set_voltage(12.0)` directly won't get `in_vin_*` columns — these come from `context.configure("vin", 12.0)` plus a vectorised setup with traceability mapping. The how-to never shows what makes these columns appear. |
| WARNING | L100–115 (Manual) | The example claims you set traceability "manually" but the kwargs shown don't exist on `logger.measure` (see Accuracy). Even with the correct API (`context.measure(...)` or `verify(...)` + pre-populated context), the page doesn't say which call site accepts `dut_pin=` and which doesn't. |
| WARNING | L152–166 (Custom Metadata) | "Add custom traceability fields that become Parquet columns" — but the page doesn't say how the columns are named. From `RunContext.set` docstring: a key with a prefix (`operator_*`) is stored as-is; an unprefixed key gets `custom_` prepended. So `run_context.set("ambient_temp", 23.5)` becomes column `custom_ambient_temp`, not `ambient_temp`. This is the kind of surprise that breaks downstream SQL queries. |
| WARNING | L226–268 (Querying) | All examples assume `df` has columns named `dut_pin`, `instrument_name`, `measurement_value`, `measurement_outcome`. The page never confirms these are the actual column names or links to the parquet-schema reference for the full set. A reader who guesses `outcome` instead of `measurement_outcome` gets `KeyError`. |
| WARNING | L226–268 (Querying) | No mention that `value` is `measurement_value` in parquet (a common source of confusion since the in-Python `Measurement.value` attribute drops the prefix). Reader who SELECTs `value` from DuckDB will hit a column-not-found error. The DuckDB example at L274 even mixes `value` (wrong) with `measurement_outcome` (correct). |
| SUGGESTION | "How do I know it worked?" | The page has no `litmus show` command, no example output, no "open the run in the UI" step. A how-to should give the reader at least one way to verify their parquet has the expected columns after a run. |
| SUGGESTION | L84 (Setting Traceability in Tests) | No statement of "if you do nothing, what gets captured?" The page leads with three opt-in techniques but never says what the baseline coverage is when a test just calls `verify("vout", val)` with no fixtures, no product, no context. |
| SUGGESTION | L168–223 (Comparators) | No mention of what happens when `value` is `None`, `NaN`, or outside the comparator's domain (e.g., `EQ` without `nominal`). Doc lists pass conditions but not error/edge behavior. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| CRITICAL | L88 | First use of `pins` fixture — no link to `reference/litmus-fixtures.md#pins-session` and no inline definition. Reader cannot resolve what `pins["VOUT"]` means without that link. |
| WARNING | L91, L105, L122, L156 | First use of `logger`, `dmm`, `psu`, `verify`, `run_context` fixtures across examples — none link to `reference/litmus-fixtures.md`. The how-to assumes the reader knows the fixture catalog. |
| WARNING | L122 | "(configured via `--product=products/power_board.yaml`)" — `--product` CLI flag is mentioned without a link to `reference/cli.md` or `reference/pytest-native.md`. Reader doesn't know which `pytest` flag namespace this is or what other values it accepts. |
| WARNING | L130 | Link to `../integration/harness.md` is correct, but the harness page itself is integration-level (runner-agnostic); a more useful link for a pytest-native reader on a traceability how-to would be `reference/litmus-fixtures.md#context-function` for the `context.configure` / `context.observe` API actually shown in code blocks. |
| WARNING | "See also" section (L346) | Only two entries. Missing obvious neighbors: `how-to/limits.md` (comparators are duplicated here), `how-to/spec-driven-testing.md` (covers `--product` + characteristic-id population), `reference/litmus-fixtures.md` (every fixture used on this page), `concepts/products.md` (where `dut_pin` is defined), `reference/pytest-native.md` (CLI flags). |
| SUGGESTION | L168 | "Comparators (ATML/IEEE 1671)" duplicates content from `docs/how-to/limits.md#comparators`. If both pages keep the section, cross-link them. Better: keep the canonical definition in `limits.md` and link from here. |
| SUGGESTION | L11 | `passed / failed / skipped / errored / done / terminated / aborted` enum — link to `reference/parquet-schema.md#outcome-values` (L270 of that file) for the authoritative list and meaning of each. |
| SUGGESTION | L227 | "Parquet files at `results/runs/{date}/{timestamp}_{serial}.parquet` (UTC timestamps)" — link to `reference/parquet-schema.md#file-layout` (L12 of that file) which is the canonical source. |
| SUGGESTION | L155 | `run_context` first-use should link to `reference/litmus-fixtures.md#run_context-session` (L228). |
| SUGGESTION | L132 | `TestHarness` import shown as `from litmus.execution.harness import TestHarness` — link to `integration/harness.md#constructor-signature` (L28 of that file) where the full constructor signature lives. |

---
