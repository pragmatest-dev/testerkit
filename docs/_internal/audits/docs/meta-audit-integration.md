# Meta-audit: Integration section
**Date:** 2026-05-17
**Scope:** 7 pages

## Severity totals
| Page | ❌ | ⚠️ | 💡 |
|---|---|---|---|
| pytest-existing | 10 | 17 | 17 |
| openhtf-adapter | 7 | 19 | 16 |
| harness | 5 | 14 | 14 |
| results-api | 8 | 17 | 18 |
| logging | 6 | 16 | 14 |
| instruments | 4 | 15 | 14 |
| lakehouse-import | 5 | 13 | 16 |
| **Total** | **45** | **111** | **109** |

45 CRITICAL across 7 pages — averages ~6.4 per page, on par with how-to.

---

## Auditor accuracy check (source-verified)

### ✅ Confirmed correct

**`pytest_addoption` collision:** Litmus's plugin already registers `--dut-serial` / `--station` / `--mock-instruments` at `src/litmus/pytest_plugin/hooks.py:896-913`. The `pytest-existing.md` example that re-adds these in a user's conftest would raise `argparse.ArgumentError`. Verified.

**`TestHarness.measure` kwargs:** `src/litmus/execution/harness.py:824-833` — signature is `(name, value, units=None, limit=None, dut_pin=None, instrument_channel=None, fixture_connection=None)`. The `low=` / `high=` kwargs called out by the auditor on `pytest-existing.md` are NOT in this signature. Auditor was right.

**`prompt_type` Literal:** `src/litmus/models/test_config.py:531` — `Literal["confirm", "choice", "input"]`. The `harness.md` "text" value would fail validation.

**`RunBuilder.finish()` does NOT promote to PASSED:** `src/litmus/client.py:319-327` — only saves; the only outcome transitions are FAILED (line 312) and ERRORED (line 317). `TestRun.outcome` default is None (`data/models.py:439`). The `results-api.md:199` example `result.outcome == "passed"` is always False; CLI always exits 1.

**`RunBuilder.abort()` silently discards data:** `client.py:329-340` — stamps `Outcome.ABORTED` but never calls `_backend.save_test_run`. The `logging.md` Approach 2 try/finally pattern with `abort()` on exception loses the partial run.

**POST /api/runs launches, doesn't accept results:** `api/app.py:311-318` — calls `runner.start(request)` to launch a pytest subprocess. There is no HTTP results-submission endpoint. The `results-api.md` HTTP section is misleading.

**`StepBuilder.measure` does not accept `dut_pin`/`instrument_channel`:** That signature is on `TestHarness.measure`, not `StepBuilder.measure`. The `logging.md` Measurement Metadata snippet conflates the two.

**`allow_repeat` is not a `LitmusClient` kwarg:** Lives on `MeasurementLogger.measure` (pytest plugin path) via `src/litmus/execution/logger.py:948`. The `LitmusClient.StepBuilder.measure` has no such kwarg. `logging.md` mixes the two APIs.

**Parquet filename format:** `src/litmus/data/backends/parquet.py:205` — `timestamp = test_run.started_at.strftime("%Y%m%dT%H%M%SZ")`, file is `{timestamp}_{serial}.parquet`. So `20260508T120000Z_SN001.parquet`, not `12-00_SN001.parquet`. `lakehouse-import.md` example is wrong.

**Ref URI format:** `src/litmus/data/ref.py:7` — `file://{date}/{session_id}_ref/waveform.npz` with `.npz` extension, no `{vector_id}/` subdir. `lakehouse-import.md:176` shape is wrong.

---

## Cross-page patterns

### Pattern AA: API confusion across results-api / client / logging / harness
Three pages document the non-pytest path with **overlapping but inconsistent claims**:
- `reference/client.md` covers `LitmusClient`/`RunBuilder`/`StepBuilder`
- `integration/results-api.md` re-covers same surface differently
- `integration/logging.md` mixes `LitmusClient` API with `MeasurementLogger` / `TestHarness` kwargs that don't apply

A reader has no contract for which page is canonical. The `client.md` ↔ `results-api.md` duplication was already flagged in the reference meta-audit (Pattern W). `logging.md` adds a third overlap.

### Pattern AB: `submit_result` and other fictional client functions
- `results-api.md`: LabVIEW Python Node example calls `submit_result()` — doesn't exist
- `client.md`: also flagged for same `submit_result` (Reference Pattern V)
- `logging.md`: `step.measure(dut_pin=..., instrument_channel=...)` — wrong API surface
- `harness.md`: `harness.context.set(...)` — `Context` has no `.set()`

Pattern: integration pages invent convenience functions/methods that don't exist, mixing up which API surface owns what.

### Pattern AC: Sidecar YAML shape repeats wrong shape
Already-flagged Bug J (configuration.md, multi-dut-testing.md, mock-mode.md, pytest-existing.md, openhtf-adapter.md). The deprecated top-level `test_X: → limits:` form vs canonical `tests: → test_X: → limits:` shows up everywhere. `extra="forbid"` on `SidecarConfig`/`TestEntry` makes the old form fail validation.

### Pattern AD: Promised integrations that don't ship
- `openhtf-adapter.md`: no `litmus.openhtf` module, no `OpenHTFOutputCallback` class. The whole page is "here's how to write a hand-rolled bridge" but the title/nav says "adapter."
- `openhtf-adapter.md`: MCP/REST step + measurement endpoints "promised" in a blockquote; neither exists.
- `results-api.md`: HTTP non-Python submission path "promised"; doesn't exist (the only POST is the launcher).
- `instruments.md`: documented `pyvisa.resources.MessageBasedResource` driver pattern doesn't work — `lifecycle.py:147` calls `driver_class(record.resource)` which `MessageBasedResource` doesn't accept.

This is a more serious version of Pattern V from reference: not just missing methods, but missing **entire integrations** that the docs imply exist.

### Pattern AE: `station_id` in operator-facing copy (operator-identifier rule violation)
- `results-api.md` (5 sites)
- `harness.md`, `logging.md`, `lakehouse-import.md` likely

The project rule (per CLAUDE.md memory) is `dut_part_number` and `station_hostname` for operator-facing identifiers. Integration pages use raw IDs.

### Pattern AF: No "did it work?" verification step
Multiple integration pages give a copy-pastable code snippet with no signpost for "run this; you should see X in `litmus runs` / the UI / etc." Affects: results-api (Quick Start), logging, harness, openhtf-adapter, pytest-existing.

### Pattern AG: Path/data_dir resolution silent
- `lakehouse-import.md`: never says where `results/` lives
- `logging.md`: same
- `client.md`: `LitmusClient(data_dir="results")` defaults to CWD-relative, bypassing `resolve_data_dir()`
- `results-api.md`: never mentions aligning `data_dir` with `litmus serve`'s data_dir so results show in the UI

The integration audience is exactly the one that needs explicit `data_dir` guidance — they're not in pytest where `resolve_data_dir()` runs automatically.

### Pattern AH: No run-close lifecycle on imperative pages
- `harness.md`: no end-to-end runnable example; never says how to finalize the run
- `logging.md`: same gap; `run.finish()` mentioned but not shown in lifecycle context

The whole point of a non-pytest imperative entry point is owning the lifecycle. Pages glossing over it leave readers guessing.

### Pattern AI: Same A/B/F bugs cascade from earlier sections
- `harness.md`: still mentions wrong `logger.measure` kwargs (Bug F)
- `pytest-existing.md`: harness.measure(low=,high=) variant of Bug A
- `openhtf-adapter.md`, `logging.md`: same Bug A/F shapes

---

## Severity-distribution insights

- **`pytest-existing.md` (10 critical)** — adoption path for the biggest audience (existing pytest users) is the most-broken page in this section
- **`results-api.md` (8 critical)** — the "submit from anywhere" promise doesn't match what ships
- **`openhtf-adapter.md` (7 critical)** — page titled "adapter" but ships no adapter code; the bridge is hand-rolled by each user

---

## Recommended fix order

(After all 5 sections complete — Integration is the last; this list extends the cross-section sweep order in earlier meta-audits.)

**Cross-cutting sweeps that now span all 5 sections:**

1. **Bug A (Limit dict vs model)** — tutorial, how-to, integration
2. **Bug E (`litmus_sweeps` kwargs)** — tutorial, how-to, reference, integration
3. **Bug F (`logger.measure` kwargs)** — tutorial, how-to, reference, integration
4. **Bug G/T (HTTP query params `?since=`/`?until=`)** — how-to, reference
5. **Pattern J (sidecar YAML top-level shape)** — how-to, integration, possibly more — sweep all docs
6. **Pattern R (`results/` prefix vs `<data_dir>/`)** — concepts, how-to, reference, integration
7. **Pattern Q (`record_type` 3 values not 2)** — reference
8. **Pattern S (exporter `-o` double-append)** — reference
9. **Pattern AE (operator-facing IDs)** — integration, possibly more

**Integration-section-specific fixes:**

10. `pytest-existing.md`: rewrite Quick Integration step 2 (no addoption collision), fix harness.measure kwargs, fix `load_station` vs `get_station`, fix sidecar shape, resolve litmus_run vs logger competing paths
11. `openhtf-adapter.md`: decide — build the adapter or rename the page; either way fix sidecar shape and obsolete `import visa` example
12. `harness.md`: fix `harness.context.set` → `.configure`/`.observe`, fix `prompt_type` literal, add end-to-end runnable example with finalize step
13. `results-api.md`: kill the HTTP submission promise OR build it; rewrite to point at canonical `reference/client.md`; fix `result.outcome == "passed"` check
14. `logging.md`: deduplicate against `client.md`/`results-api.md`; remove API mixing (StepBuilder vs MeasurementLogger vs TestHarness)
15. `instruments.md`: fix non-functional `pyvisa.resources.MessageBasedResource` example; align station YAML `resources:` claim with what the model actually accepts
16. `lakehouse-import.md`: fix filename format, fix ref URI format, add data_dir resolution guidance, add See Also

---

## Cross-section bug catalogue (final, all 4 meta-audits)

Verified bugs to grep for across all 80 docs:

| Bug | Description | Verified at |
|---|---|---|
| A | `limit={dict}` instead of `Limit(...)` | logger.py:1011, test_config.py:233 |
| B | `mock_config: {voltage: ...}` instead of method-name keys | mocks.py |
| C | `context.configure/observe` parquet claim | autouse.py:160 (one-shot snapshot) |
| D | `match.missing` instead of `match.match_result.missing` | matching/service.py:139 |
| E | `litmus_sweeps(vin=[...])` kwargs rejected | markers.py:126-130 |
| F | `logger.measure(units=, low=, high=)` instead of `limit=Limit(...)` | logger.py:941-948 |
| G/T | HTTP channel `?start=`/`?end=` vs `?since=`/`?until=` | api/app.py:512-513 |
| H | `ChannelStore(Path("results/channels"))` double-appends | channels/store.py:198 |
| I | `_ensure_connected()` doesn't exist | instruments/base.py, visa.py |
| J | Sidecar YAML `test_X:` at top-level instead of `tests: → test_X:` | test_config.py:157 (extra=forbid) |
| Q | `record_type` 3 values (run/step/measurement) not 2 | parquet.py:374 |
| R | `results/{events,runs,channels}` path prefix doesn't exist | data_dir.py + data/event_store.py:133 |
| S | Exporter `-o exports/<fmt>` double-appends | csv_exporter.py:64 |
| AE | `station_id`/`product_id` in operator-facing copy | CLAUDE.md operator-identifier rule |
| AD | `submit_result`, `OpenHTFOutputCallback`, HTTP results POST — fictional | n/a — these don't exist |
| (stale) | `ParquetSubscriber`/`LiveRunsSubscriber` class names | replaced by `materialize_run_to_parquet` + `AccumulatorPool` |
| (stale) | `sessions/sessions.json` file | doesn't exist |
| (stale) | `--profile` flag (real: `--test-profile`) | hooks.py |
| (stale) | `LITMUS_RESULTS_DIR` env (real: `LITMUS_HOME`) | data_dir.py |

Total: **17 distinct cross-cutting bug patterns** verified against source. Sweepable.
