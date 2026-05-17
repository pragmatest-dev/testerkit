# Page audit: docs/tutorial/09-production.md

**Quadrant:** Tutorial
**Audited:** 2026-05-17

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 3 | 1 |
| Voice | 0 | 0 | 1 |
| Audience | 1 | 2 | 1 |
| Accuracy | 0 | 2 | 1 |
| Gaps | 2 | 4 | 2 |
| Cross-links | 2 | 3 | 2 |
| **Total** | **6** | **14** | **8** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ❌ CRITICAL | L63–72 | `logger.measure()` is called in the `test_output_voltage` example but `logger` is never introduced anywhere on this page. A reader following top-to-bottom encounters an unexplained fixture with no link and no definition. |
| ⚠️ WARNING | L84–95 | The "Why Use pins Instead of instruments?" comparison table references `instruments["dmm"]` at L85, but `instruments` as a pytest fixture has not been introduced or linked anywhere on this page. |
| ⚠️ WARNING | L105–116 | `verify` is used in every method of `TestPowerBoardProduction` (L108, L110, L115) with no prior definition, link, or explanation on this page. It appears silently in production test code without the reader knowing its contract. |
| ⚠️ WARNING | L249–268 | `--operator="Jane Doe"` appears in the "Running Production Tests" bash commands without any introduction. The flag is not mentioned earlier in the page or listed in the project structure section. |
| 💡 SUGGESTION | L337–339 | The page says "You've completed the tutorial" but Step 10 (`10-live-monitoring.md`) exists in the same directory. Either the closing claim should be removed or Step 10 should be linked as the next tutorial step. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| 💡 SUGGESTION | L337 | Exclamation mark in prose | "Congratulations!" — section heading with an exclamation mark. Acceptable in a closing tutorial page, but the body sentence "You've completed the tutorial. You now have a foundation for production hardware testing with Litmus." also ends flat. Consistent register would help. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ❌ CRITICAL | L61 | Cold cross-page drop — fixture concept without definition | The page introduces the `pins` pytest fixture with the parenthetical "distinct from the `pins:` block in the product YAML, which declares the pin set itself" — two different meanings of the word "fixture" (YAML fixture config vs. pytest fixture) appear in the same sentence. A new reader has no basis to distinguish them. The page uses "fixture" to mean both a hardware test fixture YAML and a pytest fixture throughout without ever labelling the distinction. |
| ⚠️ WARNING | L8–10 | Term "sidecar YAML" used cold | "Per-test limits, mocks, sweeps, and retries (sidecar YAML)" in "What You'll Build" uses the term "sidecar YAML" before defining it. The definition comes at L118–147. This is acceptable in a bullet list if it's defined before the reader needs to act, but the gap is 100+ lines. |
| ⚠️ WARNING | L25 | `conftest.py` listed in project structure with no explanation | The project structure table at L25 includes `tests/conftest.py` but the file's content and purpose are never explained anywhere on the page. A reader building this project from scratch has no idea what to put in it. |
| 💡 SUGGESTION | L147 | Jargon: "node-id structure" | "The sidecar mirrors pytest's node-id structure (the `path::Class::method` identifier pytest assigns each test)." The parenthetical helps but "node-id" is pytest internals vocabulary. A test engineer reading this page cares that the YAML keys must match class and method names — the term "node-id" is unnecessary. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ⚠️ WARNING | L61 | "`pins` fixture is a dict keyed by product-pin name" | `pins` returns a `PinAccessor` instance — a custom class described as "dictionary-like" in its docstring. It supports `__getitem__`, `__contains__`, and `__iter__`, but it is not a `dict`. Describing it as a dict sets incorrect expectations about methods like `.keys()`, `.values()`, `.items()`, and `.get()`. | `src/litmus/fixtures/manager.py:279–320` |
| ⚠️ WARNING | L291 | `pq.read_table("results/runs")` — implies results live at `results/runs` relative to CWD | Actual path is `resolve_data_dir() / "runs"`, which resolves (in order) from `--data-dir`, `litmus.yaml data_dir:`, `LITMUS_HOME`, or `platformdirs.user_data_dir("litmus")`. For a new project without `litmus.yaml`, the actual path is platform-specific (e.g., `~/.local/share/litmus/runs/` on Linux). The hardcoded `"results/runs"` will silently read an empty or non-existent directory on most fresh installs. | `src/litmus/data/data_dir.py:32–58`, `src/litmus/data/run_store.py:52` |
| 💡 SUGGESTION | L61 | "returning the instrument routed to that pin" | The `PinAccessor.__getitem__` return type is annotated as `Instrument` (the abstract base). In mock mode, it returns a `MagicMock`. Stating "returns the instrument" is accurate but omits the mock-mode variant, which matters in this tutorial that emphasises mock testing. | `src/litmus/fixtures/manager.py:298–307` |
| ✅ VERIFIED | — | 22 claims verified against source | — | — |

Claims verified correct: `FixtureConnection.name` required field, `product_id` optional on `FixtureConfig`, `PromptConfig` fields (`message`, `prompt_type`, `timeout_seconds`), `RetryConfig` fields (`max_retries`, `delay`, `on`), `MockEntry.target` format `"<fixture>.<attr>"`, `SweepEntry` as `RootModel[dict[str, list[Any]]]`, `pins` fixture scope (`session`), `verify` fixture scope (function), `prompt` fixture scope (function), `logger` fixture scope (`session`, autouse), all seven `litmus_*` marker names, "20 fixtures" count in reference, `--dut-serial` flag exists, `--operator` flag exists, `--mock-instruments` flag exists, `--fixture` flag exists, `--station` flag exists, `record_type`, `measurement_name`, `measurement_value`, `measurement_units` parquet column names, `StationInstrumentConfig` fields (`type`, `driver`, `resource`, `mock_config`).

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | L25, throughout | `conftest.py` appears in the project structure with no content shown. A reader building this project from scratch cannot follow the tutorial step without knowing what belongs in `conftest.py`. Is it empty? Does it register a fixture? Is it carried over from a prior step? The page never says. |
| ❌ CRITICAL | L337–339 | The page declares "You've completed the tutorial" but Step 10 (`10-live-monitoring.md`) exists in the tutorial directory. A reader following the numbered series will reach Step 10 from the index but find Step 9 told them they were done. Either Step 9 is not the final step and should link to Step 10, or the tutorial numbering is misleading. |
| ⚠️ WARNING | What if — `pins` raises `pytest.UsageError` | The page does not tell the reader what happens if they run `pytest` without `--fixture`. The `pins` fixture raises `pytest.UsageError` when no fixture config is loaded. A reader who forgets the flag gets a cryptic framework error with no connection to this page. |
| ⚠️ WARNING | What if — sidecar YAML is missing | The "sidecar" approach is a key feature introduced here, but the page never says what happens when the sidecar YAML does not exist: `verify` limits fall back silently to unchecked (characterization mode). A reader not knowing this may believe limits are always enforced. |
| ⚠️ WARNING | What if — `name:` omitted in fixture connection | The page says `name: Required — matches the dict key` (L43) but does not explain what error the user sees if they omit it. Runtime Pydantic validation raises a missing-field error at session start; this is worth a one-liner since the dict-key/name duplication is surprising. |
| ⚠️ WARNING | L270–296 | The "Programmatic" result-access section uses `pq.read_table("results/runs")` without explaining where results are actually stored or how to find that path. The `litmus runs` CLI command (L274) implicitly solves this, but the programmatic example silently assumes a path that will be wrong for most installs. |
| 💡 SUGGESTION | L98–116 | The "Production Test Class" section shows `verify` but does not mention when a test engineer would use `logger.measure()` instead (characterization mode, sweeps where FAIL should not abort). This is the primary branching decision for test code authoring and is not covered. |
| 💡 SUGGESTION | L149–178 | "Sidecar Features" shows `retry:` and `prompts:` but does not show `sweeps:` at the sidecar level (file-wide or class-wide), even though the class example uses `test_load_sweep`. A reader who wants to see the full sidecar feature set would not know `sweeps:` can also appear at root level. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ❌ CRITICAL | L68 | First use of `logger` fixture in code (`logger.measure("output_voltage", voltage)`) — no link, no inline definition. The `logger` fixture is a core Litmus concept (autouse session fixture that opens the event log). The reader has no way to know it is a fixture, let alone what `logger.measure()` accepts. |
| ❌ CRITICAL | L105–116 | First use of `verify` in the production test class — no link and no inline definition. `verify` is the primary recording verb in Litmus; a new reader has no idea what it does, what it raises, or how its limit chain works. The concept is only linked in "Next Steps" at L345. |
| ⚠️ WARNING | L85 | First use of `instruments["dmm"]` in comparison table — no link and no inline definition. `instruments` is a session-scoped fixture returning `dict[role_name, driver_instance]`. The reader seeing this cold cannot distinguish it from a regular Python dict. |
| ⚠️ WARNING | L249–268 | `--operator`, `--dut-serial`, `--mock-instruments`, `--fixture`, `--station` CLI flags used in bash examples with no link to `reference/cli.md` where they are documented. A reader wanting to see all available options has no pointer. |
| ⚠️ WARNING | L274–281 | `litmus runs`, `litmus show`, and `litmus serve` CLI commands are shown with no link to `reference/cli.md`. These are referenced from "Next Steps" only indirectly via the general Configuration Reference. |
| 💡 SUGGESTION | L30 | "The Fixture: Pin-to-Instrument Mapping" section introduces the fixture YAML concept but does not link to `concepts/fixtures.md`, which explains the fixture model in depth. A cross-link here would help readers who want the conceptual grounding before copying YAML. |
| 💡 SUGGESTION | L343–348 | "Next Steps" section does not include a link to `docs/tutorial/10-live-monitoring.md`. If Step 10 is the natural continuation of the tutorial, it should appear first in "Next Steps." |
