# Meta-audit: Tutorial section
**Date:** 2026-05-17
**Scope:** 12 pages (00-quickstart through from-mocks-to-hardware, plus 03-fixtures re-audit)

---

## Auditor accuracy check

Before using the findings, I verified the auditors' most consequential technical claims against source. Results:

### ✅ Confirmed correct auditor findings

**`limit={"low": ..., "high": ..., "units": ...}` (raw dict) fails at runtime.**
`logger.measure()` and `verify()` both type-annotate `limit: Limit | None`. `_resolve_measurement_limit()` returns the dict unchanged (line 246 in `logger.py`: `if limit is not None: return limit`). Then `logger.py:1011` does `resolved_limit.low` — attribute access on a dict → `AttributeError`. A raw dict passed as `limit=` will crash. The tutorials must use `Limit(low=..., high=..., units=...)`. Every tutorial page that shows dict syntax is wrong.

**`context.configure()` / `context.observe()` do NOT write parquet `in_*`/`out_*` columns in the pytest path.**
`_litmus_push_params` (autouse) calls `set_active_vector_params(dict(ctx.params))` once at test setup from `callspec.params`. `context.configure()` updates `Context._params` in memory only — there is no subsequent call to `set_active_vector_params`. `logger.measure` reads `get_active_vector_params()` (the ContextVar, not `ctx._params`) to stamp parquet rows. Result: `context.configure("psu.voltage", vin)` in a test body does NOT produce an `in_psu.voltage` parquet column. The docstring says "→ in_*" — that's true in the TestHarness path but not in the pytest-native path. Tutorial pages teaching this as a parquet-stamping mechanism are wrong.

**`context.get_param(key)` returns `None` (not raises) on missing key.**
`harness.py:288-302`: `get_param(key, default=None)` walks the parent chain, returns `default` (None) if not found. Auditor finding on 05-configuration is correct.

**`mock_config` keys must be method names, not signal names.**
`mocks.py` docstring and example: `Mock(DMM, measure_voltage=3.3)`. The key `measure_voltage` is the method name. Tutorial pages using `voltage:` or `current:` as mock keys produce silently broken mocks (the attribute doesn't match so `unittest.mock` creates a new MagicMock for the method, returning a Mock object instead of 3.3).

**`match.missing` is wrong; `match.match_result.missing` is correct.**
`StationMatch` (service.py:139): fields are `station_id`, `station_name`, `compatible`, `match_result`. `match_result` is a `MatchResult` which has `missing: list[CapabilityRequirement]`. There is no `.missing` directly on `StationMatch`.

**`logger.measure()` does not accept `units=`, `low=`, `high=` kwargs.**
Signature at logger.py:941: `measure(self, name, value, *, limit=None, outcome=..., allow_repeat=False)`. No `units=`, `low=`, `high=`. Any page showing these kwargs is wrong.

**20 public fixtures is correct (not 21).**
There are 21 `@pytest.fixture` decorators in `__init__.py`, but the 21st is `_route_manager` (private, leading underscore). The docs claim of "20 public fixtures" is accurate.

### ❌ Auditor findings that need scrutiny

**"dict passed as `limit=` causes `AttributeError`" — partially correct, but the actual flow matters.**
The dict is passed through `_resolve_measurement_limit()` unchanged (it just returns `limit` if not None). The AttributeError fires at line 1011 (`resolved_limit.low`) ONLY if `resolved_limit is not None` — which it is, because the dict is truthy. So yes, the error fires. Auditor conclusion is correct but the mechanism explanation was imprecise.

**Fixture count "21" flagged as a CRITICAL error in 00-quickstart.**
The page says "20 fixtures." There are 21 decorators but 20 public ones. The page is correct. This was a false CRITICAL from the auditor. Discard.

---

## Cross-page patterns

### Pattern 1: Raw dict instead of `Limit(...)` model (ALL pages) — CLAUDE.md violation
Every tutorial page that shows limits uses either wrong kwargs (`units=`, `low=`, `high=` directly on `logger.measure`) or raw dict syntax (`limit={"low": 3.2, "high": 3.4, "units": "V"}`). Both are wrong. `Limit` is a Pydantic model — you instantiate it: `limit=Limit(low=3.2, high=3.4, units="V")`. Passing a raw dict causes two failures: (1) `_compute_outcome` uses dict's `__contains__` instead of `Limit.__contains__`, so every measurement silently returns FAILED regardless of value; (2) `logger.measure` then crashes with `AttributeError: 'dict' object has no attribute 'low'`. This also violates CLAUDE.md: "NEVER pass raw dicts when a Pydantic model exists."

### Pattern 2: `context` framing (03, 05, 06, and implied throughout)
`context` keeps being introduced as a sweep/parametrize reading tool. The actual purpose is to be the test's context — it holds the stimulus and environmental state of the running test. `configure()` and `observe()` accumulate that state but don't stamp parquet directly in the pytest path. Multiple pages will teach an incorrect mental model if not fixed.

### Pattern 3: Scope creep in early tutorial steps (01, 02, 09)
Step 1 introduces Limit, verify, parquet columns, traceability, and MagicMock — all out of scope for "first test." Step 9 uses `logger` and `verify` without any prior introduction on that page. Tutorial steps should be thin: introduce one concept, make it work, move on.

### Pattern 4: Missing backward navigation links (02, 03, and most pages)
Almost every page has forward nav (`[Step N+1 →]`) but no backward link. Steps reference prior steps by name without a link to them. Affects reader wayfinding when landing mid-sequence.

### Pattern 5: `mock_config` key naming (07, 02)
Pages teach `voltage: 3.3` / `current: 0.5` as mock_config keys. The actual mechanism requires method names (`measure_voltage: 3.3`). Silent failure — the mock object is created but the method returns a MagicMock, not the specified value.

### Pattern 6: Missing install notes for third-party packages (02, 03)
`pytest-dependency` recommended without `uv add pytest-dependency`. Same pattern likely in other pages for `pymeasure`, `pyvisa`, etc.

### Pattern 7: Unanswered "what if" questions (all pages, especially 10)
Happy path only. No file-not-found paths, no instrument-unreachable paths, no "what does the error look like" guidance. Step 10 instructs the reader to `connect("bench_1", ...)` without saying the station YAML must exist and the role must be present.

### Pattern 8: Cold use of Litmus terms without links (01, 09, 10)
`logger`, `verify`, `sidecar`, `pins` appear in code examples on pages that don't introduce them, with no link to their definition. A reader landing on step 9 from a search engine has no path to understanding what `verify` is.

### Pattern 9: Consistency of guidance
Multiple pages give advice that conflicts with other pages. Step 05 recommends `@pytest.mark.flaky` for retries while the sidecar `retry:` block (which is the page's own subject) is never shown. Step 06 teaches manual limit derivation arithmetic while the platform has `characteristic: + tolerance_pct:` for automatic derivation. The tutorial should teach the canonical approach first, not the workaround.

---

## Severity distribution
| Page | ❌ | ⚠️ | 💡 |
|---|---|---|---|
| 00-quickstart | 3 | 10 | 14 |
| 01-first-test | 8 | 14 | 8 |
| 02-mock-instruments | 2 | 8 | 8 |
| 03-fixtures (re-audit) | 2 | 9 | 8 |
| 04-limits | 3 | 10 | 8 |
| 05-configuration | 1 | 10 | 10 |
| 06-specifications | 1 | 15 | 11 |
| 07-real-instruments | 4 | 11 | 10 |
| 08-capabilities | 2 | 7 | 10 |
| 09-production | 6 | 14 | 8 |
| 10-live-monitoring | 3 | 12 | 7 |
| from-mocks-to-hardware | 1 | 10 | 11 |
| **Total** | **36** | **130** | **113** |

One confirmed false CRITICAL (fixture count in 00). Real CRITICAL count: **35**.

---

## Recommended fix order

Fix these patterns first — they are factual errors that break working code:

1. **`Limit` model everywhere** — sweep all tutorial pages. Replace every occurrence of `limit={"low": ..., "high": ..., "units": ...}` (dict) AND every `logger.measure(..., units=..., low=..., high=...)` (wrong kwargs) with `limit=Limit(low=..., high=..., units=...)`. Import: `from litmus.models.test_config import Limit`. No exceptions — raw dicts cause silent wrong outcomes then crash.
2. **`mock_config` key names** — replace `voltage:` / `current:` with the actual method names.
3. **`context.configure()` / `observe()` parquet claim** — correct or remove the claim that these stamp `in_*`/`out_*` parquet columns in the pytest-native path.
4. **`match.missing`** — fix to `match.match_result.missing`.
5. **Step scope creep** (01, 09) — remove out-of-scope material.

Fix these second — they affect completeness but don't break code:

6. **Backward nav links** — add to every step.
7. **Cold Litmus term usage** — link `logger`, `verify`, `sidecar` on first use per page.
8. **`pytest-dependency` install note** — and any other third-party package.
9. **"what if" paths** — instrument unreachable, station YAML not found, etc.

---

## Consistency note (added per user request)

The tutorial teaches conflicting approaches:
- **Limits:** Step 4 teaches inline `Limit(...)`, step 5 teaches sidecar `limits:`, step 6 teaches product spec. None of the steps explain which approach is canonical — a reader doesn't know whether to use all three or pick one.
- **Retries:** Step 5 teaches `@pytest.mark.flaky` (third-party) rather than the sidecar `retry:` block (built-in).
- **Sweeps:** `@pytest.mark.parametrize` and `@pytest.mark.litmus_sweeps` are presented as equivalent alternatives without guidance on when to prefer each.
- **Mock mode:** `mock_config` in station YAML, `mocks:` in sidecar, and `@pytest.mark.litmus_mocks` are all mentioned across different pages without a unified "here is how mock mode works" narrative.

The tutorial should establish one canonical path and note alternatives as advanced options, not present them side-by-side as if equivalent.
