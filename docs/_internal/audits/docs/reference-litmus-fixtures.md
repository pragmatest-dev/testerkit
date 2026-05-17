# Page audit: docs/reference/litmus-fixtures.md

**Quadrant:** Reference
**Audited:** 2026-05-17

> Note: the six dedicated audit-* agent types referenced in the coordinator brief
> are not available as tools in this environment. The coordinator performed all
> six audits inline against the same rubric. Findings are organised below by
> dimension; the format matches the standard combined-report layout.

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 2 | 3 |
| Audience | 0 | 2 | 2 |
| Accuracy | 3 | 4 | 2 |
| Gaps | 1 | 4 | 3 |
| Cross-links | 0 | 2 | 3 |
| **Total** | **4** | **15** | **15** |

---

## Ordering

The page is grouped by intent ("what you'd reach for it for") with an at-a-glance
table at the top and one H2 per group. For a Reference page that is consulted
non-linearly, this is the right shape. A few small frictions:

### WARNING — `_route_manager` is mentioned in the intro but `routes` (the public
wrapper) and `_route_manager` (the internal session-scoped backing fixture) are
visually tangled

The intro (line 3) calls out `_route_manager` and `_litmus_push_params` as
examples of underscore-prefixed internals. Good. But the body only documents
`routes` (function-scoped wrapper) without ever circling back to clarify that
`routes` reads through `_route_manager`. A reader who skipped the intro and
landed on the `routes` section via the TOC has no way to know what is doing the
real work, and the source code reveals the wrapper does basically nothing except
forward and re-deactivate. Either drop the `_route_manager` mention from line 3
(it's not consumer-facing) or add a one-line "backed by the session-scoped
`_route_manager`" note in the `routes` block.

### SUGGESTION — `instruments` → `instrument` → `instrument_records` reads
slightly out of frequency order

Inside "Talking to instruments", `instruments` (the dict) leads, then
`instrument` (the accessor), then `instrument_records` (rare metadata view).
That's fine, but `dut` and `pins` are higher-traffic than `instrument_records`
and they sit *after* it. Consider moving `instrument_records` to the bottom of
the group (just above `fixture_manager`) so the order reads from "what most
tests take" downward.

### SUGGESTION — the "Flow control" group sits between "Reading loaded
configuration" and "Per-role auto-fixtures"

The current order is: Recording → Talking to instruments → Per-test state →
Loaded configuration → Flow control → Per-role auto-fixtures. The per-role
auto-fixtures section is structurally a sibling of `instruments` (it's the
station-derived shortcut for the same dict). Either:
  - move "Per-role auto-fixtures" up into "Talking to instruments" as its
    final sub-section, or
  - put it directly after `instruments` with a forward link from the latter.
  The current placement after "Flow control" buries it at the bottom of a
  reference that 90% of tests will reach for.

---

## Voice

The page's voice is generally good for Reference — declarative, signature-first,
present tense, no second-person tutorial verbs. A handful of slips:

### WARNING — second-person tutorial voice creeps in

Lines 11, 17, 95, and 285 use "you'd reach for it for" / "you take role names
directly" / "your station YAML". Reference voice should describe behaviour, not
narrate the reader's actions. Suggested rewrites:
  - line 11: "Grouped by intent:" (drop "Grouped by what you reach for the
    fixture **for**:")
  - line 95: "Most tests take role names directly as fixtures (`def test_x(dmm,
    psu)`); see [Per-role auto-fixtures](#per-role-auto-fixtures). `instruments`
    itself is rarely needed."
  - line 285: "These names are not hard-coded — they come from the station YAML
    at session start."

### WARNING — opinion masquerading as fact in `verify` vs `logger.measure`

Line 38: "Use `verify` when a fail should stop the line." That's how-to/concept
voice, not reference. Reference says what each does; the *guidance* between them
belongs in a how-to. Suggested: "`logger.measure` records without raising;
`verify` records and raises on FAIL." (Same content, no second-person
imperative.)

### SUGGESTION — "the verbs you write into test bodies" (line 27)

This is conversational and adds nothing the heading doesn't already say. Drop or
replace with "Measurement recording primitives."

### SUGGESTION — "honest" / "no silent default" (line 62)

The note is correct but the framing ("there is no silent default") is editorial.
Just say: "`limits[name]` raises `KeyError` for unconfigured names."

### SUGGESTION — em-dashes and parentheticals are dense

Lines 31, 50, 211, 252, 261, 274 stack em-dash clauses inside parentheses inside
prose. Reference tends to break these into bullet lists for the parameter
clarifications. Worth a copy-edit pass.

---

## Audience

Target audience is a working test engineer using the pytest runner — someone who
already knows what pytest fixtures are. Page mostly holds that line. Two soft
spots:

### WARNING — "active chain (sidecar / inline marker / product spec)" on line 31
is unexplained jargon at first encounter

The page never defines what the "active chain" is in order. `verify`'s
description references it as if known. A first-time reader (the audience for a
*reference*, even a power-user one) will not know whether sidecar beats marker
or vice versa. Suggested: link the phrase to the resolution-chain section in
[`limits`](../how-to/limits.md) or [Models](models.md), or add a footnote-style
line under `verify` showing the order.

### WARNING — "self-loop mode" is introduced cold at line 252

The `vectors` section assumes the reader knows what "self-loop mode" is. The
phrase is bolded as if defined, but the definition (consolidate matrix at
collection, run as single case) is folded into the same paragraph. A
reference-audience reader scanning for `vectors` would benefit from a one-line
definition first, then the consolidation behaviour:

> **Self-loop mode** — when a test takes `vectors`, pytest collects it as
> *one* case and the matrix is iterated in the test body, instead of pytest
> generating one case per row.

### SUGGESTION — operator-facing terminology drift

Memory note `feedback_operator_facing_identifiers.md` is hard: "Product →
`dut_part_number`; Station → `station_hostname`. Never `product_id`/
`station_id`/`station_name` in operator-facing labels." This is a developer
reference, so the rule may not bind, but lines 208 ("`--product <id-or-path>`")
and 210 ("content match against `product.part_number:`") mix `id` and
`part_number` freely. Consider clarifying which is the operator-facing input
(part number) vs the developer-facing one (id).

### SUGGESTION — "vanilla project" (line 21, 201)

"Vanilla project" is colloquial. A reference reader might be unsure what
qualifies (no station? no products? no fixture?). Suggested: "a project with no
station, fixture, or product YAML".

---

## Accuracy

I read `src/litmus/pytest_plugin/__init__.py`, `hooks.py`, `autouse.py`,
`execution/verify.py`, `execution/logger.py`, `execution/harness.py`, and
`execution/connections.py` to verify the page's claims. Several material
inaccuracies.

### CRITICAL — `verify` signature is wrong

Page line 31:

> Callable: `verify(name, value, *, limit=None)`.

Actual signature in `src/litmus/execution/verify.py:159-164`:

```python
def _verify(
    name: str,
    value: float | int | None,
    limit: Limit | None = None,
    characteristic: str | None = None,
) -> Measurement:
```

Two errors:
1. There is **no `*` keyword-only separator** — `limit` is positional-or-keyword.
2. The signature **also accepts `characteristic=`**, an undocumented public
   parameter that lets a caller override the active characteristic for a single
   `verify(...)` call. The `VerifyFn` Protocol (verify.py:38-44) advertises it,
   so it's part of the public surface.

Correct: `Callable: verify(name, value, limit=None, characteristic=None)` and a
brief sentence about what `characteristic=` does (matches the active
`litmus_characteristics` marker scope so spec resolution sees the override).

### CRITICAL — `logger.measure` signature is wrong (units= does not exist)

Page line 50:

> `logger.measure(name, value, *, units=None, limit=None)` records a
> measurement row without raising.

Actual signature in `src/litmus/execution/logger.py:941-949`:

```python
def measure(
    self,
    name: str,
    value: float | int | None,
    *,
    limit: Limit | None = None,
    outcome: Outcome = Outcome.DONE,
    allow_repeat: bool = False,
) -> Measurement:
```

There is **no `units=` keyword**. Units are pulled from the resolved limit
(line 1014). Passing `units=` will raise `TypeError: unexpected keyword
argument`. The example on line 47 (`logger.measure("output_voltage", v,
units="V")`) is therefore a runtime error.

Correct signature: `logger.measure(name, value, *, limit=None, outcome=Outcome.DONE, allow_repeat=False)`,
and fix the example by either dropping `units="V"` or by passing
`limit=Limit(..., units="V")`.

### CRITICAL — example on line 47 will not run

Direct consequence of the previous finding. The page's `logger` example raises
TypeError. Reference pages must have runnable snippets.

### WARNING — "raises `AssertionError`" is technically true but misleading

Page line 31 says `verify` "raises `AssertionError`". The actual exception is
`litmus.execution.verify.LimitFailure`, which *subclasses* `AssertionError`
(verify.py:51). `pytest.raises(AssertionError)` matches it, so the behaviour
claim isn't wrong, but consumers reading the reference and grepping their
codebase for "what does verify raise" will not find `AssertionError` anywhere
near the call site. Recommended: "raises `LimitFailure` (an `AssertionError`
subclass)".

Note also that `verify` raises `MissingLimitError` (a `ValueError`) when no
limit can be resolved (verify.py:196). That's an undocumented failure mode.

### WARNING — `routes` "yields a `RouteManager` ... or `None`" understates the
single-test contract

Page line 137:

> Yields a `RouteManager` for explicit switch routing, or `None` when no
> routes exist

The actual `routes` fixture (`__init__.py:877-894`) is function-scoped and
yields the *session-scoped* `_route_manager`. The session-scoped manager is
created once per session if any fixture point has a route; the function-scoped
`routes` fixture is just a per-test handle that re-runs `deactivate_all()` at
teardown. The page's "runs automatically at test teardown" line is correct, but
the lifetime model is hidden: `routes` does not own the RouteManager, it
borrows it.

### WARNING — `pins` description omits the RoutedProxy behaviour

Page line 124:

> Looks up the instrument that the fixture YAML maps to each DUT pin,
> transparently activates the route if any switch is in the path.

Actual behaviour from `__init__.py:898-916` and the FixtureManager docstring:
the instrument is wrapped in a `RoutedProxy` that activates the route on
*first method call*, not on lookup. The distinction matters for a reader
trying to understand when locks are taken. Suggested: "...wraps the
instrument in a `RoutedProxy` that activates the route on first method
call."

### WARNING — `mock_instruments` description omits the test-phase demotion side
effect

Page line 242 says the fixture "Returns `bool`. True when `--mock-instruments`
was passed or `LITMUS_MOCK_INSTRUMENTS=1` is set." Correct. But the fixture
docstring (`__init__.py:560-568`) also documents the test-phase demotion to
`development` when mocks are active, and *that* is the behaviour a test engineer
cares about. Worth a sentence: "Mocks demote the run's `test_phase` stamp to
`development` (so dashboards ignore mocked runs)."

### WARNING — `instruments` does not always "auto-mock when --mock-instruments
is on"

Page line 87: "Auto-mocks when `--mock-instruments` is on." Actual logic
(`__init__.py:747-754`): the per-role mock flag is `mock_instruments or
(inline_config.mock if inline_config else False)`. So `--mock-instruments`
forces every role to mock, **and** individual roles can be flagged
`mock: true` in the station YAML to mock only that role. The current wording
hides the per-role override path.

### SUGGESTION — `Context.run` table row says it returns `TestRun | None`

Page line 177: "context.run | TestRun | None | The current TestRun." Per
`harness.py:365`, the property returns `TestRun | None`, but in practice it is
only `None` when no logger has been wired (impossible in normal test runs
because `logger` is autouse). Worth a parenthetical: "(always present in
normal test runs since `logger` is autouse)."

### SUGGESTION — line range citation `hooks.py:232–274` is approximate

Page line 297 cites `src/litmus/pytest_plugin/hooks.py:232–274` for the
per-role auto-fixture registration. The actual block runs roughly 232–274 in
the current file (232 = "Auto-register instrument role fixtures" comment;
274 = `config.pluginmanager.register(...)`), so the citation is correct
today. Recommend pinning to an anchor (a function name) instead of line
numbers, since line numbers drift on every refactor.

---

## Gaps

Things a reader of this reference would expect to find and won't.

### CRITICAL — Six autouse internals (`_litmus_*`, `_reseat_current_logger`,
`_route_cleanup`) are entirely undocumented

The page (line 3) says names beginning with `_` "are internal and may change
without notice." That's policy. But the page positions itself as the
comprehensive fixture reference, and there are SIX session/function autouse
fixtures in `src/litmus/pytest_plugin/autouse.py` that materially shape what
every test sees:

- `_reseat_current_logger` — reseats `set_current_logger` in xdist worker
  subprocesses (otherwise the session logger ContextVar is empty per-test)
- `_route_cleanup` — autouse teardown that runs `routes.deactivate_all()`
  even when the test didn't take `routes`
- `_litmus_push_params` — pushes `parametrize` / sweep params into the
  active-vector ContextVar (this is what makes `context.get_param` work)
- `_litmus_push_limits` — merges marker + sidecar + profile limits into
  the active-limits ContextVar (this is what makes `verify` resolve limits)
- `_litmus_resolve_connections` — resolves
  `litmus_characteristics`/`litmus_connections` markers (this is what
  makes the `connections` fixture work)
- `_litmus_apply_mocks` — installs `litmus_mocks` marker patches

A reader debugging "why does `verify` see my marker limits but not my
sidecar?" needs to know which autouse fixture composes them and in what
order. At minimum, the page should add a short subsection under "Per-test
state" or as its own H2:

> ### Autouse internals
>
> Six autouse fixtures (`_litmus_push_params`, `_litmus_push_limits`,
> `_litmus_resolve_connections`, `_litmus_apply_mocks`, `_route_cleanup`,
> `_reseat_current_logger`) wire ContextVars before each test so the
> public fixtures (`context`, `verify`, `limits`, `connections`, `routes`)
> see populated state. They are subject to change without notice; see
> `src/litmus/pytest_plugin/autouse.py` for the current set.

### WARNING — `logger.measure` example silently bypasses the limit chain

Line 45-50 shows `dmm.measure_dc_voltage()` → `logger.measure(...)` and says
"use it when a failing measurement shouldn't abort the test". The page never
mentions that without a limit, `logger.measure` stamps `Outcome.DONE` (not
`PASSED`/`FAILED`) — i.e. it does not judge at all. This is exactly the
difference from `verify` and is the reason `verify` raises `MissingLimitError`
when no limit is configured. The page describes the two as "same record-side
effect", which is misleading.

### WARNING — `sync.wait` failure modes are undocumented

Page line 267 shows `sync.wait("thermal_soak", timeout=300)`. What happens on
timeout? Does it raise? What's the return type? The `SyncPoint` class is the
only thing this fixture exposes and the page doesn't link to its docs or
describe its surface. At minimum, add a one-line "raises TimeoutError if peers
don't arrive within `timeout`" or a link to a fuller reference.

### WARNING — `context.observe` example missing

`context` is in the per-test state group with a method table that lists
`observe(key, value)`. There's no example. Because `observe` is the API for
recording free-form per-row context that lands in `out_*` parquet columns, an
example is highly valuable for a Reference.

### WARNING — `vectors` does not describe what gets pushed into ContextVars per
iteration

Line 261 says "Each `for` iteration pushes the row's params + index into active
state so `logger.measure`, `verify`, and `context` see the same row-scoped
context". From `_VectorIterator.__next__` (`__init__.py:1066-1097`), the
iterator also appends a fresh `TestVector` to the current step per iteration so
parquet rows land on distinct records. That's a side effect with observable
downstream consequences (per-row rows in parquet) that the reference omits.

### SUGGESTION — `fixture_manager` mentions "net-name → connection" lookup but
nothing else

Line 149 shows two methods (`get_connection_for_net`,
`get_instrument_for_connection`). The actual `FixtureManager` exposes a broader
surface; this page is the natural place to link out to its full method list (or
its module docstring), even if the methods don't get their own reference.

### SUGGESTION — `--guardband` is referenced indirectly but never explained

`product_context` uses `--guardband` (`__init__.py:464`) but the page never
mentions the option or its effect on resolved limits. Reference pages should
cross-link to or summarise the CLI options that materially change fixture
output.

### SUGGESTION — no mention of pytest-xdist behaviour

Several fixtures behave differently under xdist (`_reseat_current_logger`
exists for exactly this reason). The page doesn't say anything about parallel
execution semantics. At minimum, a line under `logger` noting "in xdist worker
mode the session logger reseats per-worker" would help.

---

## Cross-links

The "See also" section at the bottom links to four pages, which is a reasonable
floor for a reference. Several inline cross-links could be tightened.

### WARNING — `litmus_prompts` link uses an unverified anchor

Line 77 links `[litmus_prompts](litmus-markers.md#litmus_prompts)`. The actual
heading in `litmus-markers.md:180` is `## \`litmus_prompts\`` (backticks). MkDocs
typically strips backticks in slug generation, so `#litmus_prompts` should
resolve, but verify in CI or with `mkdocs build --strict` — backtick handling
varies across renderers.

### WARNING — `[tutorial](../tutorial/index.md)` is broad

Line 5 says "For a guided introduction see the [tutorial](../tutorial/index.md)".
The tutorial has 11 numbered steps; pointing the reader to the index forces
them to figure out which step covers fixtures. Recommend deep-linking to the
specific step that introduces `verify`/`logger` (likely
`tutorial/01-first-test.md` or `04-limits.md`).

### SUGGESTION — `connections` block (line 187-195) does not link to
`litmus_characteristics` / `litmus_connections` marker references

The fixture's docstring references both markers; the rendered page mentions
them but doesn't link to the marker reference. Suggested:
> Returns the `ConnectionIterator` resolved from
> [`litmus_characteristics`](litmus-markers.md#litmus_characteristics) /
> [`litmus_connections`](litmus-markers.md#litmus_connections) markers...

### SUGGESTION — `vectors` block does not link to `parametrize` / sweeps
resolution

Line 252 references `@pytest.mark.parametrize`, `litmus_sweeps`, sidecar
`sweeps:`, and profile overrides as sources of vectors. The
[Test vectors & sweeps](../how-to/vector-expansion.md) page is in "See also"
but linking it inline here would help readers jumping straight to the
`vectors` reference.

### SUGGESTION — `dut` does not link to product-driver setup

Line 115 says "resolved from `Product.driver` + `FixtureConfig.dut_resource`".
A reader who doesn't have a DUT driver yet won't know where to declare one.
Link to whichever how-to or product YAML reference documents the `driver:`
field.

---
