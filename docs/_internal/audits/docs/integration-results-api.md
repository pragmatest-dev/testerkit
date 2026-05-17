# Page audit: docs/integration/results-api.md

**Quadrant:** Integration (Explanation/Reference — results API: submitting results from any external system via `LitmusClient` or HTTP)
**Audited:** 2026-05-17

---

## Summary

| Dimension | ❌ CRITICAL | ⚠️ WARNING | 💡 SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 1 | 2 | 2 |
| Accuracy | 3 | 4 | 3 |
| Gaps | 3 | 5 | 3 |
| Cross-links | 1 | 3 | 5 |
| **Total** | **8** | **17** | **18** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ⚠️ WARNING | L32–95 vs L97–219 | "API Reference" section sits before "Integration Patterns" but the Quick Start (L13) already used the API. Reader who needs more API detail after Quick Start gets it — good — but readers scanning for "how do I plug this into LabVIEW?" must scroll past dense API tables. For an integration page, the integration patterns are the lead value; consider Quick Start → Integration Patterns → API Reference (deepening detail) → Querying → Schema. |
| ⚠️ WARNING | L78 | `step.vector(**params)` is introduced in the StepBuilder method table with no prior mention or example. The first concrete use of `vector()` does not appear anywhere on this page — the reader sees the method name with a one-line description and no example, then never encounters it again. Either show an example, drop it from the table, or link to a page that shows it. |
| 💡 SUGGESTION | L97 ("Integration Patterns") | LabVIEW comes before TestStand alphabetically and operationally that's fine, but the "From Command Line" subsection at L171 is actually the simplest pattern (just a Python script) and would make a stronger lead-in to the language-specific adapters. |
| 💡 SUGGESTION | L256–269 ("Raw Parquet") | Parquet section appears under "Querying Results" but the Data Schema section that explains the columns it queries (`record_type`, `dut_serial`, `measurement_value`) is L271+ — readers hit the `df[df["record_type"] == "measurement"]` filter before the schema introduces `record_type`. Move "Data Schema" above "Raw Parquet", or forward-reference it. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L308 | Marketing / promotional language | "**Low effort** — Minimal code changes required" |
| 💡 SUGGESTION | L305 | Marketing-adjacent superlative | "**Unified view** — All results in one place" (acceptable as a benefit bullet, but "unified" is doing promotional work) |
| 💡 SUGGESTION | L307 | Marketing-adjacent | "**Analytics-ready** — Parquet format for data analysis" |
| 💡 SUGGESTION | L309 | Hedging / vague benefit | "**Incremental** — Add more integration over time" — vague; either delete or replace with a concrete pattern. |

Note: the "Benefits" section (L304–309) as a whole reads like marketing copy on what is otherwise a reference/integration page. Test engineers don't need to be sold on the integration once they've found the page.

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ❌ CRITICAL | L22, L51, L154, L188, L212 | Wrong vocabulary — operator-facing identifier | "`station_id="any_station"`", "`station_id="bench_1"`", "`station_id=context.station_name`", "`station_id="cli_test"`" — per CLAUDE.md / memory, operator-facing labels use `station_hostname`, not `station_id`. The whole page leans on `station_id` even though it accepts `station_id` at the API boundary (which is the actual parameter name). Recommend stating once that `station_id` here is a free-form identifier (often the hostname), or rename examples to use hostname-style values to match the convention. |
| ⚠️ WARNING | L141 | Vocabulary / framework comparison | "Use TestStand's Python adapter:" — acceptable framing, but the example uses `context.dut_serial`, `context.steps`, `step.measurements` which are not real TestStand API surfaces. A test engineer migrating from TestStand will recognize this as fictional/schematic and may distrust the rest of the page. |
| ⚠️ WARNING | L101–108 | Anti-audience content / fictional API | The LabVIEW "Python Node" block lists `Module: litmus, Function: submit_result` — `submit_result` is not a real Litmus function (see Accuracy finding). LabVIEW users will try this and fail. |
| 💡 SUGGESTION | L208 | Wrong vocabulary (mixed) | Comment in HTTP example refers to "`product_id`, `dut_serial`, `station_id`, `test_path`, `operator`, `mock_instruments`" — `product_id` here is a YAML key (acceptable in an API-body comment) but the surrounding prose elsewhere uses `dut_part_number` semantics. Make the contract explicit: "these are the LaunchRequest fields; `product_id` matches the `id:` key in your `products/*.yaml`." |
| 💡 SUGGESTION | L266 | Programmer jargon | "the schema multiplexes step + measurement rows" — "multiplexes" is programmer jargon for a test engineer; "the same schema holds both step rows and measurement rows, distinguished by `record_type`" is clearer. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L101–108 | LabVIEW Python Node: `Module: litmus, Function: submit_result, Inputs: serial, station, measurements[]` | No `submit_result` function exists anywhere in `litmus/`. The Python client surface is `LitmusClient().start_run(...)` / `run.step(...)` / `step.measure(...)`. A LabVIEW user following this Python Node spec will get `AttributeError`. | `src/litmus/client.py` (no such symbol) |
| ❌ CRITICAL | L199 | `sys.exit(0 if result.outcome == "passed" else 1)` | `RunBuilder.finish()` (`client.py:319`) never sets `self._test_run.outcome = PASSED`. Only `FAILED` and `ERRORED` get set (L311–317). A fully successful run leaves `outcome = None`, so `result.outcome == "passed"` is always `False` and the CLI always exits 1. | `src/litmus/client.py:259–327` |
| ❌ CRITICAL | L209, L214 | HTTP `POST /api/runs` example sends `dut_serial`, `station_id`, `test_path`, `operator` | Endpoint exists and `LaunchRequest` accepts these — but this endpoint **launches a pytest subprocess against `test_path`**, it does NOT submit pre-collected results. Using it as "the HTTP equivalent of `LitmusClient.start_run()`" (which submits results) is wrong. There is no HTTP endpoint that maps to the Results-API submission flow shown in the Python examples. The page conflates two different APIs under "Via HTTP API". | `src/litmus/api/app.py:311–318`, `src/litmus/api/models.py:10–18` |
| ⚠️ WARNING | L37 | `client = LitmusClient(data_dir="results")` shown as an "API Reference" signature | Constructor signature is `def __init__(self, data_dir: str | Path = "results")` — `data_dir` is a keyword-or-positional parameter with default `"results"`. The example is correct but the table at L40–45 lists methods without showing the constructor's optional parameter — the reader has to infer the default. | `src/litmus/client.py:360` |
| ⚠️ WARNING | L78 | `step.vector(**params)` listed as a StepBuilder method | Correct, but signature is `@contextmanager def vector(self, **params: Any) -> Generator[VectorBuilder, None, None]` — it is a context manager, not a regular call. The table presents it like `step.measure(...)` (which is not a context manager). Reader will likely call `step.vector(x=1)` directly and get an unused generator. | `src/litmus/client.py:165–187` |
| ⚠️ WARNING | L236–237 | `client.get_measurements(...)` returns "list[dict] keyed by parquet column names" and example uses `m['measurement_name']`, `m['measurement_value']`, `m['measurement_units']` | Correct (verified: `parquet.py:413, 960–962`), but the actual keys exposed include the full denormalized row context (`run_id`, `dut_serial`, `step_name`, `vector_index`, `limit_low`, `limit_high`, `vector_retry`, ~60 columns). Doc undersells what's available; readers may not realize they can read `m['dut_serial']` and `m['limit_low']` from the same dict. | `src/litmus/data/schemas.py:48–132` |
| ⚠️ WARNING | L288 | Run-level table column `run_id` | Schema column is `run_id` — but `RunSummary.test_run_id` (the Pydantic field used at L230) is a different name. The page uses both spellings without acknowledging that the parquet column is `run_id` and the Pydantic field is `test_run_id`. | `src/litmus/data/models.py:363`, `src/litmus/data/schemas.py:51` |
| 💡 SUGGESTION | L288, L301 | Outcome enum values for run vs measurement | `run_outcome` table lists 7 values (`passed/failed/errored/skipped/done/terminated/aborted`); `measurement_outcome` table lists 5 (omits `terminated/aborted`). Both columns hold `Outcome` enum values (same StrEnum), so the difference is "produced in practice" vs "possible". State that explicitly, or list all 7 in both. | `src/litmus/data/models.py:42–106` |
| 💡 SUGGESTION | L66, L80 | `run.abort(message)` and `step.fail(message)` / `step.skip(message)` parameter | All three accept `message: str | None = None`, returning `TestRun` (abort) or `None` (fail/skip). The table description "Abort without saving" is accurate per source (`abort()` does NOT call `save_test_run`); state this loud — readers may expect abort to save with `ABORTED` outcome. Also `run.abort()` returns the TestRun object; the table omits the return value. | `src/litmus/client.py:135–145, 235–245, 329–340` |
| 💡 SUGGESTION | L92 | `comparator="GELE"` description "comparison mode" | Source documents the full set: `EQ, NE, LT, LE, GT, GE, GELE, GELT, GTLE, GTLT`. Doc shows only the default. Either link to the comparator reference or list the values. | `src/litmus/client.py:96–97` |
| ✅ VERIFIED | — | 19 claims verified against source (parquet column names: `run_id`, `run_started_at`, `run_ended_at`, `dut_serial`, `station_id`, `run_outcome`, `step_name`, `measurement_name`, `measurement_value`, `measurement_units`, `limit_low`, `limit_high`, `measurement_outcome`, `record_type`; partition layout `results/runs/{date}/*.parquet`; `LitmusClient` import path; `start_run` kwargs; `run.step()` context-manager shape; CLI commands `litmus runs` / `litmus show`; HTTP endpoints `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/measurements`; default port 8000) | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | L13–30 ("Quick Start") | Where does data go? Quick Start runs `client.finish()` and ends. No mention of `./results/` being created in the cwd, no `litmus runs` invocation to confirm the run landed, no UI URL. Reader has no way to verify their setup worked. |
| ❌ CRITICAL | L37 | `LitmusClient(data_dir="results")` — what if I want this to flow into the same `data_dir` as my Litmus install (so runs show up in `litmus serve`)? The page doesn't say. In practice users want one shared data dir; doc should call out `resolve_data_dir()` / project `litmus.yaml`, or at minimum say "match the `data_dir` your `litmus serve` uses." |
| ❌ CRITICAL | "Via HTTP API" L202–219 | No HTTP equivalent shown for submitting measurements. The page advertises HTTP for "non-Python environments" but the example only starts a run (which actually launches a pytest subprocess — see Accuracy). A reader trying to push results from a LabVIEW HTTP client has nothing to follow. |
| ⚠️ WARNING | L26–29 | What outcome does the run get if nothing fails? The example never calls `step.fail` or has a failing measurement, but doesn't say what `run.finish()` produces. (In fact, `outcome` stays `None` — see Accuracy.) |
| ⚠️ WARNING | L66 | `run.abort(message)` description "Abort without saving" — no guidance on when to use it vs `run.finish()`. If the operator hits stop mid-run, do I call `abort` or `finish` with a `FAILED` step? Doc is silent. |
| ⚠️ WARNING | L75–80 | `step.fail(message)` and `step.skip(message)` are listed but never shown in an example. When does a manual `fail()` make sense vs letting a measurement violate its limit? When does `skip()` apply (the schema enum allows it at every level)? |
| ⚠️ WARNING | L196–199 | CLI example does `sys.exit(0 if result.outcome == "passed" else 1)` — the doc treats `result.outcome` as comparable to the string `"passed"` but never says what the legal values are at this layer. (And as Accuracy notes, the comparison is always False on success.) |
| ⚠️ WARNING | "Querying Results" L221+ | What if the run id I have is short / partial? `get_run("abc12345")` happens to work (the backend does prefix match on 8 chars) but the doc doesn't tell the reader they can shorten ids. Tutorial readers will copy the full UUID unnecessarily. |
| 💡 SUGGESTION | L271–301 ("Data Schema") | What's the full column list? Doc says "See `src/litmus/data/schemas.py` for the canonical column list" — fine, but linking to a source file from doc is a worse experience than a generated reference page. Consider linking to `docs/reference/parquet-schema.md` if it covers this. |
| 💡 SUGGESTION | "Quick Start" L13 | What dependencies are needed? `litmus` is the only import — does that imply the full `pip install litmus` is required just to push results? Is there a slim "client-only" extra (e.g. `pip install litmus[client]`)? Reader migrating a legacy system cares about this. |
| 💡 SUGGESTION | L156 | TestStand example calls `context.dut_serial` etc. — even with the "schematic, adapt to your TestStand setup" caveat (missing!), the reader gets no pointer to TestStand's actual Python adapter API docs. |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ❌ CRITICAL | First-use of `LitmusClient` (L16) | No link to `reference/client.md` (which exists and documents this class) on first use. The page is an integration / explanation, but a reader hitting `LitmusClient` here for the first time should be able to jump straight to the API reference. |
| ⚠️ WARNING | First-use of "Parquet" (L11, L256, L271) | No link to `concepts/results-storage.md` or `reference/parquet-schema.md` (both exist). "Parquet" is a Litmus storage concept here, not just a file format. |
| ⚠️ WARNING | L243–245 "CLI" subsection | `litmus runs` and `litmus show <run_id>` shown without links to `reference/cli.md`. |
| ⚠️ WARNING | L313 ("Next Steps") | `[Test Harness](harness.md)` is described as "Add measurement tracking to existing tests" — but `harness.md` is specifically about `TestHarness` for non-pytest runners (Robot Framework, unittest), not "existing tests" generically. Misleading link text. |
| 💡 SUGGESTION | L171 ("From Command Line") | Could link to `reference/cli.md` if a `litmus submit` style command exists, or to `reference/client.md` for fuller client API. |
| 💡 SUGGESTION | L249–253 ("HTTP API" subsection under Querying) | Could link to `reference/api.md` for the full HTTP endpoint list. The page links `reference/api.md` at L314 (Next Steps) but a contextual link at the section header would help. |
| 💡 SUGGESTION | L255 ("Raw Parquet") | Could link to `reference/parquet-schema.md` and `concepts/results-storage.md` rather than dropping `pyarrow` usage cold. |
| 💡 SUGGESTION | L99, L139, L171, L202 | Subsection headers ("From LabVIEW", "From TestStand", "From Command Line", "Via HTTP API") could each link out: LabVIEW → external Python Node docs; TestStand → external Python adapter docs. Currently each example stands alone with no escape hatch. |
| 💡 SUGGESTION | Missing "See also" coverage | Next Steps (L311–315) is fine but misses `integration/openhtf-adapter.md`, `integration/pytest-existing.md`, `integration/harness.md` (already linked but mislabeled), and `concepts/results-storage.md` — all directly related "where else do my results go" pages. |

Verified link targets exist:
- `/home/ryanf/repos/litmus/docs/integration/harness.md` exists
- `/home/ryanf/repos/litmus/docs/reference/api.md` exists
- `/home/ryanf/repos/litmus/docs/reference/client.md` exists

No broken file links on this page. The cross-link gap is one of missing inline links to definitions, not broken targets.
