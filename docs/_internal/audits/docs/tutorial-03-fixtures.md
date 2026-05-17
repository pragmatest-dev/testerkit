# Page audit: docs/tutorial/03-fixtures.md

**Quadrant:** Tutorial (step 3 of 10 — logger, verify, and context fixtures)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 0 | 2 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 1 | 1 |
| Accuracy | 2 | 2 | 1 |
| Gaps | 0 | 3 | 1 |
| Cross-links | 0 | 2 | 1 |
| **Total** | **2** | **9** | **8** |

---

## Ordering

**Dimension:** Ordering
**Page:** docs/tutorial/03-fixtures.md
**Quadrant:** Tutorial

### Findings

No critical or warning ordering issues. The page follows a logical progressive build: introduces the three fixtures by name and purpose, demonstrates each in isolation (logger.measure first, then verify as a shorthand, then context for environmental stamping), then shows composition patterns (classes, parametrize, multiple measurements, streaming). The "What gets stored" table and "What you learned" recap appear at the end, consistent with tutorial convention.

**SUGGESTION (O-1):** The "Streaming samples under one name" section (logger with allow_repeat=True) appears after "Multiple measurements per test". A reader building up from the earlier pattern may not realize they need allow_repeat only when they call the same name twice in a loop — a one-sentence bridge ("if your loop calls the same name on every iteration, you'll hit a DuplicateMeasurementError — that's the next pattern") would smooth the jump.

**SUGGESTION (O-2):** The `litmus_sweeps` comparison-with-parametrize block interrupts the parametrize narrative mid-section. The "Parametrize is first-class" heading introduces `@pytest.mark.parametrize`, shows the `context` example, then pivots to `litmus_sweeps` before returning to explain what both paths share. Splitting into two headings ("Parametrize is first-class" and "Native sweep marker: litmus_sweeps") would make each concept scannable in isolation.

---

## Voice

**Dimension:** Voice
**Page:** docs/tutorial/03-fixtures.md
**Quadrant:** Tutorial

### Findings

The page is active and generally present-tense. Most code commentary is direct ("Add logger and record the measurement explicitly") and action-oriented. A few inconsistencies:

**WARNING (V-1):** The fixture summary table uses three different register styles in the "Verbs" column:
- `logger`: `measure(name, value, limit=...)`, `record` (signature fragment + bare verb)
- `verify`: `verify(name, value, limit=...)` — `characteristic=` links to a product-spec characteristic; covered in step 6 (long prose note inside a table cell)
- `context`: `configure(key, v)` stamps a stimulus input (in_*), `observe(key, v)` stamps an environmental reading (out_*), `.product`, `.station`, `.run` (mix of calls-with-explanations and property names)

A table cell should contain only a verb list or short fragment. The `verify` row's "covered in step 6" note belongs in a prose sentence below the table. The `context` row should list just the four or five verbs, not inline their definitions.

**SUGGESTION (V-2):** "Same control flow, but now there's a row in the run record with the value, units, limits, and outcome" (line 44). "Now there's a row" is passive-adjacent and slightly weak for a tutorial that should feel hands-on. Prefer: "Litmus now writes a row to the run record carrying value, units, limits, and outcome."

**SUGGESTION (V-3):** "Use `@pytest.mark.parametrize` when you want pytest's per-row `pytest.param(..., id="...")` metadata; use `@pytest.mark.litmus_sweeps` when you want range expanders or sidecar parity." (line 112). This is good decision-guidance prose. However, the sentence fragment "See [litmus_sweeps](...) and the [Litmus markers reference](...) for all seven `litmus_*` markers." appended on the same line mixes guidance with navigation. Split into two sentences.

---

## Audience

**Dimension:** Audience
**Page:** docs/tutorial/03-fixtures.md
**Quadrant:** Tutorial

### Findings

The page is pitched at a test engineer who has completed steps 1-2 and is comfortable with pytest fixtures and basic driver-level test code. That is correct for this position in the tutorial sequence. No jargon beyond standard pytest vocabulary is assumed without introduction.

**WARNING (A-1):** The parametrize example on line 91 adds a `temp_probe` fixture to the signature without explanation:

```python
def test_output_voltage(vin, psu, dmm, temp_probe, context, verify):
```

`psu` and `dmm` were established in step 2's conftest.py. `temp_probe` is new and undefined — it appears nowhere in steps 1, 2, 4, or 5. A reader who copies this example will get a `fixture 'temp_probe' not found` error. Either add a note ("add a temp_probe fixture to your conftest.py in the same pattern as psu and dmm") or use only the already-established fixtures and add a comment marking temp_probe as illustrative.

**SUGGESTION (A-2):** The page jumps to `context.configure()` / `context.observe()` and immediately explains the `in_*` / `out_*` column naming convention without giving readers who are unfamiliar with the convention a path to the full schema. The inline note "The in_* / out_* column naming comes from [traceability](../how-to/traceability.md)" at line 100 handles this, but only after the columns have been used in an example. A forward reference at first mention ("in_* columns — see [Parquet storage schema](../reference/parquet-schema.md) for the full column set") would help the reader follow along in parallel.

---

## Accuracy

**Dimension:** Accuracy
**Page:** docs/tutorial/03-fixtures.md
**Quadrant:** Tutorial

### Findings

**CRITICAL (ACC-1): All dict-literal `limit=` examples are wrong at runtime.**

Every code example on the page passes a raw dict as the `limit=` keyword argument:

```python
logger.measure("output_voltage", v, limit={"low": 3.2, "high": 3.4, "units": "V"})
verify("output_voltage", ..., limit={"low": 3.2, "high": 3.4, "units": "V"})
```

This pattern appears on lines 40, 54-55, 71-72, 75-76, 97, 122-126, and 140-143.

The actual signatures (verified in `src/litmus/execution/logger.py:941-948` and `src/litmus/execution/verify.py:39-44`) are:

```python
def measure(self, name, value, *, limit: Limit | None = None, ...) -> Measurement
def __call__(self, name, value, limit: Limit | None = None, ...) -> Measurement
```

`limit` expects a `Limit` model instance (`from litmus.models.test_config import Limit`), not a dict. There is no `@validate_call` decorator, no TypeAdapter coercion, and no dict-to-Limit branch in `_resolve_measurement_limit` (`logger.py:245`: `if limit is not None: return limit` — the dict is returned as-is and then `.low`, `.high`, `.units` etc. are accessed on it via attribute notation, raising `AttributeError: 'dict' object has no attribute 'low'`).

Every working test and example in the repo constructs `Limit(...)` objects explicitly:

```python
# examples/02-verify/tests/test_rail.py
from litmus.models.test_config import Limit
V_RAIL = Limit(low=3.2, high=3.4, units="V")
verify("v_rail", dmm.measure_dc_voltage(), limit=V_RAIL)
```

Fix: Replace every `limit={...}` dict with `Limit(...)`. Add an import line (`from litmus.models.test_config import Limit`) to the first code example that uses it. Note that `units` is a required field on `Limit` — `Limit(low=3.2, high=3.4)` raises a Pydantic `ValidationError`.

---

**CRITICAL (ACC-2): `context.configure()` / `context.observe()` do not write `in_*` / `out_*` parquet columns in the pytest-native path.**

The page states (lines 94-95, 100, 183, and in the fixture summary table):

> `configure(key, v)` stamps a stimulus input (in_*), `observe(key, v)` stamps an environmental reading (out_*)

and:

> Both land in the parquet row alongside the measurement

This is accurate for the `TestHarness` path (used in the traceability how-to with `harness.context.configure(...)`), but NOT for the pytest-native `context` fixture shown throughout this tutorial.

Traced path in pytest-native:
1. `_litmus_push_params` autouse fixture runs at setup and calls `set_active_vector_params(dict(ctx.params))` with only the `callspec.params` from `@pytest.mark.parametrize` / `litmus_sweeps` (`src/litmus/pytest_plugin/autouse.py:160`).
2. Test body calls `context.configure("psu.voltage", vin)` — this updates `ctx._params` (`harness.py:188`) but does NOT call `set_active_vector_params`.
3. `logger.measure()` calls `_snapshot_active_vector_params()` which reads the ContextVar set in step 1 — the configure'd value is absent.
4. `start_step` receives `inputs` from `_step_vector_for_item` which reads only `callspec.params` (`hooks.py:1079-1082`).
5. `build_input_columns(vector)` reads `vector.params` (set from `inputs` in step 4) plus `vector.stimulus` — `context.configure()` values appear in neither.

`context.observe()` similarly stores in `ctx._observations` which is never copied to `TestVector.observations` in the pytest-native path (the assignment `test_vector.observations = self._vector_context.observations` at `harness.py:1146` is inside `TestHarness._run_vector`, not exercised in pytest-native tests).

The `context` fixture reference (`docs/reference/litmus-fixtures.md`) corroborates this: it lists `context.observe(key, value)` with purpose "Record a free-form observation" but does NOT list `context.configure` and does NOT claim either method writes `in_*` / `out_*` columns.

Fix: Remove or correct the `configure()` / `observe()` -> `in_*` / `out_*` column claims for the pytest-native path. In the parametrize example, note that `vin` already lands in `in_vin` automatically via `@pytest.mark.parametrize`. For environmental readings, note that `context.observe()` records a value accessible via `context.observations` but it is not promoted to `out_*` parquet columns in the pytest-native path — use a sidecar `conditions:` block or the `TestHarness` path for that.

---

**WARNING (ACC-3): `measurement_outcome` column description omits three valid outcome values.**

The "What gets stored" table (line 170) describes `measurement_outcome` as:

> `passed` / `failed` / `skipped` / `errored`

The actual parquet schema (`docs/reference/parquet-schema.md:203`) lists seven values:

> `passed` / `failed` / `skipped` / `errored` / `aborted` / `terminated` / `done`

`done` is the default outcome when `logger.measure` is called without a limit (no pass/fail judgment — the most common case for a pure recording call). `aborted` and `terminated` represent early-exit conditions. Omitting `done` is especially misleading: a reader who calls `logger.measure(...)` without a limit and then queries the run record will see `outcome = "done"` and may think their data is corrupt or their test failed silently.

Fix: Either list all seven values or replace the static list with a link: "see [Parquet storage schema](../reference/parquet-schema.md) for all outcome values."

---

**WARNING (ACC-4): Fixture summary table describes `context` as exposing "what's active right now: DUT, station, run, conditions" but the pytest-native `context()` fixture returns an empty `Context()` by default.**

The fixture summary table (line 17) implies that `context` automatically surfaces the active DUT, station, run, and conditions. The actual pytest-native `context` fixture (`__init__.py:974-977`) is:

```python
@pytest.fixture
def context() -> Context:
    return Context()
```

`Context()` is a fresh empty object. The `.product`, `.station`, `.run` properties do resolve live from ContextVars (`harness.py:366-402`) and return meaningful values when the corresponding session fixtures are loaded — but only then. In a vanilla project with no station YAML and no product YAML, all three return `None`. The description "what's active right now: DUT, station, run, conditions" overstates what a new user will see when they first try the fixture.

Fix: Qualify the description: "`.product`, `.station`, `.run` — read-only views into the active session; return `None` in a vanilla project with no station or product YAML loaded."

---

**SUGGESTION (ACC-5):** The page says `verify` "raises `LimitFailure` (a subclass of `AssertionError`, so `pytest.raises(AssertionError)` catches it)" and notes that `LimitFailure` carries `.name`, `.value`, and `.limit` attributes (line 48). The code (`verify.py:61-70`) confirms all three, plus two undocumented fields: `.dut_pin` and `.spec_ref`. These are populated only in spec-driven tests, so omitting them here is reasonable, but a brief note ("additional traceability attributes are available in spec-driven tests") would prevent surprise when readers encounter them in step 6.

---

## Gaps

**Dimension:** Gaps
**Page:** docs/tutorial/03-fixtures.md
**Quadrant:** Tutorial

### Findings

**WARNING (G-1): No explanation of how to import `Limit`.**

Once the ACC-1 accuracy issue is fixed, readers will need to write `from litmus.models.test_config import Limit`. This import is non-obvious: `Limit` is not re-exported from the `litmus` top-level namespace. A reader doing tab-completion on `litmus.` or following IDE suggestions will not find it easily. Step 2 showed how to import and instantiate mock fixtures; step 3 should similarly show the import for its key new type.

**WARNING (G-2): `temp_probe` fixture used in the parametrize example but never introduced.**

The `temp_probe` fixture appears on line 91 in the parametrize example alongside `psu` and `dmm`, but `temp_probe` is undefined in steps 1, 2, 4, or 5 of the tutorial (verified by search). A reader who copies the example verbatim will get `fixture 'temp_probe' not found`. The example needs either a conftest.py snippet showing how to add it, or an explicit inline comment marking it as illustrative ("# add a temp_probe fixture to conftest.py — same pattern as psu/dmm").

**WARNING (G-3): `allow_repeat=True` is introduced without explaining the error it prevents.**

The "Streaming samples under one name" section (line 130) tells readers to pass `allow_repeat=True` but does not name the error they would encounter without it. The class `DuplicateMeasurementError` (a subclass of `AssertionError`, defined in `logger.py:279-287`) has a descriptive message, but a first-time reader seeing `DuplicateMeasurementError: Measurement 'voltage_sample' already recorded in step` would not know to reach for `allow_repeat=True`. A single sentence naming the error closes the gap.

**SUGGESTION (G-4):** The `litmus runs` / `litmus show <run_id>` commands (lines 157-158) are introduced without a link to `docs/reference/cli.md`. Readers who want to understand available flags (e.g., `litmus show <id> -f html` to generate a report) have no navigation path from this page. A parenthetical "(see [CLI reference](../reference/cli.md))" on the first mention of `litmus runs` would suffice.

---

## Cross-links

**Dimension:** Cross-links
**Page:** docs/tutorial/03-fixtures.md
**Quadrant:** Tutorial

### Findings

All eight unique linked files exist and resolve correctly:
- `../how-to/traceability.md` — exists
- `../concepts/results-storage.md` — exists
- `../reference/litmus-fixtures.md` — exists
- `../concepts/step-hierarchy.md` — exists
- `../reference/models.md#outcome` — exists (heading `### Outcome` generates anchor `#outcome`)
- `../reference/litmus-markers.md#litmus_sweeps` — exists (heading `## \`litmus_sweeps\`` generates anchor `#litmus_sweeps`)
- `../reference/litmus-markers.md` — exists
- `../reference/parquet-schema.md` — exists
- `02-mock-instruments.md`, `04-limits.md` (nav footer) — both exist

**WARNING (CL-1): The anchor `../reference/litmus-fixtures.md#limits--function` may not resolve.**

Line 60 links to `../reference/litmus-fixtures.md#limits--function`. The target heading is `### \`limits\` — function` (with an em dash U+2014). MkDocs Material's default slugifier strips backticks, lowercases, and converts runs of non-alphanumeric characters to single hyphens. The em dash is typically treated as a word separator and collapsed with adjacent spaces into a single hyphen, producing `#limits-function` — not `#limits--function` (two hyphens). The anchor should be verified against the built site. If it does not resolve, replace with `#limits` (the heading word alone) or update to match the generated anchor.

**WARNING (CL-2): `litmus runs` and `litmus show <run_id>` are mentioned without a link to the CLI reference.**

Lines 157-158 show the CLI commands for inspecting run data. There is no link to `docs/reference/cli.md`, which documents command flags including `litmus show <id> -f html` for generating HTML reports. This is especially important because the page explicitly teaches that measurements are now visible via the CLI — readers who want to explore further have no navigation path.

**SUGGESTION (CL-3):** The `context` fixture is described in a full section with multiple code examples, but its reference entry at `../reference/litmus-fixtures.md#context--function` is never linked. Adding a link from the fixture summary table's `context` row or from the "Parametrize is first-class" section to the reference entry would give readers a path to the full API table (which includes `context.get_param`, `context.changed`, `context.last`, `context.params` — methods not mentioned in this tutorial step at all).
