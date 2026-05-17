# Page audit: docs/how-to/vector-expansion.md

**Quadrant:** How-to (test vectors and sweeps — litmus_sweeps, parametrize, vectors self-loop fixture, range expanders)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 0 | 2 | 2 |
| Accuracy | 5 | 3 | 1 |
| Gaps | 1 | 4 | 2 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **7** | **15** | **12** |

---

## Ordering

**WARNING — "The basics" section starts with single-axis but then immediately jumps to paired-values (zip), then back to nested loops (cross-product), then to class-level sweeps, then back to "outer simple, inner paired."**
The narrative bounces between independent concepts (axis count, axis pairing, decorator placement) without finishing one before starting the next. Suggested order: (1) one axis, (2) cross-product of axes (stacked decorators), (3) paired/zipped axes, (4) class-level scope, (5) module/function placement table, (6) combinations.

**WARNING — "Where you put the decorator matters" table (lines 138-149) is dropped mid-stream inside the "Basics" section.**
Decorator placement is a separate axis of concern from "how many values per axis." Either pull it into its own top-level section or move it to the end of "Basics" as the integrating summary.

**SUGGESTION — `context.changed()` is introduced twice.**
First in the lead-in (lines 13-17) as motivation, then re-introduced under "Loop ordering" with `### Skip expensive setup with context.changed()` (lines 192-214). The second appearance reads as if the concept is brand-new; the lead-in could either be shorter (just promise it) or the dedicated section could open with a back-reference.

**SUGGESTION — The "Choosing where to declare your vectors" decision matrix (lines 352-363) sits between "Self-loop mode" and "Performance tips."**
This is a recap table — readers usually want it near the top (right after the "three places" intro) or at the very end as a takeaway. Mid-document placement makes it easy to miss.

---

## Voice

**WARNING — The page slips into first-person editorializing in a few places.**
- Line 27: "Don't *mix* the two on a single test — pick one." — direct second-person imperative, fine for how-to, but the parenthetical "(chapter 1 of the curriculum)" is conversational filler.
- Lines 186-189: "well-known footgun; `litmus_sweeps` flips it so your code reads the way you'd write the equivalent nested `for` loop. (One of the reasons `litmus_sweeps` is its own marker rather than a rename.)" — this is design-rationale aside, which is reference/explanation voice, not how-to voice. How-to should say "do this, like this" and trust the reader.

**SUGGESTION — "all using the same shape" (line 19), "all behave the same" (line 304), "behave the same as in normal parametrized mode" (line 304) — these reassurances are filler.**
Trim or replace with a concrete cross-reference ("see [reference] for the field shape").

**SUGGESTION — Heavy use of em-dashes for parenthetical asides ("— top to bottom reads as outer-to-inner —", "— see [Step Hierarchy] —", etc.).**
A how-to benefits from short declarative sentences and bullets. Most em-dash asides could be promoted to their own sentence or pushed into a note callout.

**SUGGESTION — Inconsistent emphasis vocabulary: "recommended" (line 25), "must" (line 53), "should" (implicit), "want" (multiple).**
How-to should consistently use "do X" / "use X" — reserve "must" for hard constraints (like equal-length pairing).

---

## Audience

**WARNING — Mixed audience between test engineers and pytest-savvy developers.**
The page assumes pytest knowledge (decorator stacking semantics, `pytest.mark.parametrize` conventions, "collection time", `metafunc`) in some passages, but other passages spell out basic Python concepts (nested for-loops). A typical Litmus user is a test engineer; the parametrize comparison and "decorator stacking convention" footnote in the note on lines 184-189 will read as inscrutable to readers who have never used vanilla pytest.

**WARNING — The "Why split outer from inner?" justification (lines 345-348) and the "matches the way T&M frameworks treat sequences" aside (lines 122-126) are framework-design rationale, not user-task guidance.**
A test engineer reading "how do I sweep three voltages at three temperatures" doesn't need to know about OpenTAP/TestStand/Spintop. Move these to a concepts page or a collapsed "Why" block.

**SUGGESTION — "chapter 1 of the curriculum" (line 27) presupposes a structure the docs don't expose.**
Drop the phrase or replace with a concrete tutorial link.

**SUGGESTION — The mention of "T&M frameworks (TestStand, OpenTAP, Spintop OpenHTF)" (lines 122-123) assumes the reader knows what those are.**
For a how-to, either drop the comparison or briefly translate ("traditional test sequencers like TestStand").

---

## Accuracy

**CRITICAL — Every `litmus_sweeps(vin=[...])` kwargs-form example in the page is WRONG and will raise `pytest.UsageError` at collection time.**
The codebase explicitly rejects the kwargs form. From `src/litmus/pytest_plugin/markers.py:126-130`:
```python
if kwargs:
    raise ValueError(
        f"{name} does not accept keyword arguments; pass a list of "
        "entries as one positional argument or varargs."
    )
```
And there is a dedicated test enforcing this in `tests/test_config/test_expand_helpers.py:231-255` (`test_litmus_sweeps_rejects_kwargs_form`).

All real-code examples (`examples/02-verify/tests/test_rail.py:37`, `examples/03-inline-limits/tests/test_sequence.py:27`, `examples/03-inline-limits/tests/test_rail.py:36`, every `tests/test_execution/test_class_step_containers.py` site) use the list-of-dicts form `litmus_sweeps([{"vin": [...]}])`.

Affected lines in the page (every inline Python example): 39, 57, 82-83, 102, 106, 157-158, 179-181, 198-200, 236-240, 259-262, 280-283, 295, 321, 323. Every one of them needs to change to `litmus_sweeps([{"vin": [...]}])`. The cheat sheet on line 280-283 is wrong in the inline column.

**CRITICAL — Conflict with `docs/reference/litmus-markers.md` (line 56).**
The reference says: `**Signature (inline):** @pytest.mark.litmus_sweeps(**by_axis) (single axis) or @pytest.mark.litmus_sweeps([entries]) (multiple axes).` — also wrong. Both docs claim the kwargs form is legal; the code rejects it. Either both docs need to change, or the code needs to grow that overload. Examples in the repo all use the list-of-dicts form, so the doc claim is the bug.

**CRITICAL — Line 67-73 error-message claim is wrong shape.**
The page shows:
```
litmus_sweeps zip requires all argvalues to have the same length;
got {'vin': 2, 'expected': 3}
```
But since the kwargs form is rejected up front, that specific case raises "litmus_sweeps does not accept keyword arguments" first, not the length-mismatch error. The actual length-mismatch error comes from `src/litmus/execution/vectors.py:130-133`:
```python
raise ValueError(f"Zip expansion requires equal-length lists. Got: {detail}")
```
…or from Pydantic's `SweepEntry` validator. The error string in the doc does not match either.

**CRITICAL — Line 102-111 class-level sweep example uses bare kwargs form.**
```python
@pytest.mark.litmus_sweeps(voltage=[1, 2, 3])
class TestPowerSequence:
    @pytest.mark.litmus_sweeps(current=[4, 5, 6])
```
The real version in `tests/test_execution/test_class_step_containers.py:199-204` is `litmus_sweeps([{"voltage": [1, 2, 3]}])` / `litmus_sweeps([{"current": [4, 5, 6]}])`. The page's form will fail at collection.

**CRITICAL — Line 240: `@pytest.mark.litmus_sweeps(channel=list(range(1, 17)))` claims "channels 1..16."**
`list(range(1, 17))` produces `[1, 2, ..., 16]`, comment is correct, but again the kwargs form will fail.

**WARNING — `freq=logspace(1, 6, 6)` comment "10 Hz to 1 MHz" (line 237).**
Verified — `numpy.logspace(1, 6, 6).tolist()` = `[10, 100, 1000, 10000, 100000, 1000000]`. Numerically correct, but the inline example writes `freq` while the surrounding context (line 244-247) suggests this is illustrative; reader might be confused that 6 points across 5 decades skips one decade per point. Consider clarifying that `num` is total point count, not points-per-decade.

**WARNING — `arange(0.0, 1.0, 0.1)` comment "0.0..0.9 step 0.1" (line 238).**
Technically correct (stop exclusive), but numpy's float arange is notorious for endpoint-rounding artifacts (you may get `0.7000000000000001`). Worth noting in this how-to, since hardware tests often want exact endpoints (in which case `linspace` is the right tool).

**WARNING — Line 16: `context.changed("temp")` "tells you when an outer loop just rolled over."**
That's the intent, but `context.changed(key)` (per `src/litmus/execution/harness.py:209-223`) returns `True` whenever the value differs from the **immediately preceding** vector — also `True` on every first iteration. The page's "when an outer loop just rolled over" is technically correct (every roll-over does flip the value), but doesn't mention the always-true-on-first-vector edge that's load-bearing for the `chamber.set_temperature(temp)` pattern (you DO want it to run the first time).

**SUGGESTION — The range expanders table (line 226-231) says `linspace(start, stop, num)` is "N evenly-spaced points, exact endpoints" but doesn't note `arange` is `stop`-exclusive while `linspace` is `stop`-inclusive.**
The docstrings in `src/litmus/expand.py` do call this out; the doc should too — it's an easy off-by-one trap.

---

## Gaps

**CRITICAL — No mention of the canonical `litmus_sweeps([{...}, {...}])` syntax until the cheat sheet on line 282.**
Given that the kwargs form does not work (see Accuracy), the list-of-dicts form is actually the *only* form. The page builds an entire mental model around the kwargs form before quietly introducing the real syntax in the cheat-sheet row labeled "Two stacked decorators." Readers will hit `UsageError` the moment they copy an example.

**WARNING — No mention of how `litmus_sweeps` interacts with `pytest.param(value, id="...")` IDs.**
The reference page (line 83) calls out that this is the reason to choose `@pytest.mark.parametrize`. The how-to never tells the reader how to get readable test IDs for `litmus_sweeps` runs, nor whether `pytest.param` is supported in the list-of-dicts payload.

**WARNING — No mention of `--collect-only` / how to preview the expanded matrix.**
A how-to about sweeps that doesn't show the user how to verify "I have 45 cases" before launching a 20-minute soak is missing a critical workflow step.

**WARNING — Sidecar YAML examples never show the surrounding structure.**
Every YAML snippet (`sweeps:` ... `- {vin: [...]}`) is shown in isolation. A new user will not know whether `sweeps:` is at file root, under a test ID, under a class. Compare `tests/test_config/test_*.yaml` real structure. The "writing-tests" how-to has the surrounding shape but this page does not link or restate it.

**WARNING — Self-loop mode section says nothing about teardown / failure semantics.**
What happens if one row in the `for v in vectors:` loop raises? Does the test fail at the first failure, or does the `vectors` iterator continue? `src/litmus/pytest_plugin/__init__.py:1140-1146` shows the iterator fails the test if zero rows consumed — but the doc never mentions partial-iteration semantics, which is the actual concern for streamed-measurement tests.

**SUGGESTION — `repeat(5.0, 100)` example (line 239) doesn't explain *why* you'd want 100 copies.**
Soak-stability / burn-in / repeat-measurements is a common hardware-test pattern. One sentence on the use case would land it.

**SUGGESTION — `expand_zip` vs `expand_product` (the underlying executor primitives) and the YAML `vectors: { expand: zip, ... }` recursive form (`src/litmus/execution/vectors.py:13-23`) are never mentioned.**
This is a how-to, so deep architecture isn't needed, but a one-line pointer to the YAML recursive composition would help users who outgrow the flat `sweeps:` list.

---

## Cross-links

**CRITICAL — Page never links to the `litmus_sweeps` reference at `docs/reference/litmus-markers.md`.**
The reference is the source of truth for the marker shape; how-to readers will hit "what's the actual signature again?" with no pointer. The reverse link (markers → vector-expansion) exists on line 83 / 228 of `litmus-markers.md` — this is one-way only.

**WARNING — Profiles cross-link (line 23) goes to `profiles.md`.**
Valid file, but the link text "profiles guide" is generic. Profiles are the third declared site for sweeps; a section anchor (e.g., `profiles.md#sweeps`) would land readers where the relevant content lives.

**WARNING — `context-architecture.md` (line 41, 75-77, 123 of that file) discusses the `context` fixture and shows `litmus_sweeps` examples.**
The vector-expansion page references `context.changed()` extensively but never links to `context-architecture.md`. Bidirectional link would help.

**WARNING — `spec-driven-testing.md` (line 92 of that file) explicitly mentions that "current vector's active parameters" drive spec band matching.**
Spec-driven readers benefit from understanding sweeps; vector readers benefit from knowing limits derive from active params. Link missing in both directions from this page.

**SUGGESTION — Tutorial `03-fixtures.md` lines 102-112 introduce `litmus_sweeps` for the first time and explicitly point to `litmus-markers.md`.**
A "first-time readers: see tutorial" cross-link at the top of this how-to would help readers who hit this page directly.

**SUGGESTION — `examples/03-inline-limits/tests/test_rail.py` and `examples/02-verify/tests/test_rail.py` are the canonical worked examples with `linspace` and `litmus_sweeps`.**
Linking to one of them at the end ("see also a complete worked example") gives the reader a real test file to copy.
