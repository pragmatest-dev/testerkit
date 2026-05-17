# Page audit: docs/concepts/why-pytest.md

**Quadrant:** Concepts / Explanation
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 1 | 2 |
| Audience | 0 | 2 | 2 |
| Accuracy | 2 | 2 | 1 |
| Gaps | 0 | 2 | 3 |
| Cross-links | 0 | 1 | 3 |
| **Total** | **2** | **9** | **13** |

---

## Ordering findings

**WARNING — "What Litmus adds" table mixes layers of abstraction.**
The "What Litmus adds on top" table interleaves data-model concerns (persistence, spec-driven limits), API surface (`context.*`, fixtures), configuration mechanisms (sidecar, station YAML, profiles), and CLI flags. Reader has to re-orient at each row. Group by kind (data/persistence rows together, fixtures/markers together, CLI flags together) or order by which thing appears earliest in a typical test author's flow (markers first → fixtures → CLI → sidecar). The current order looks list-of-features rather than story-of-why.

**SUGGESTION — Lede paragraph buries the page's actual subject.**
The first paragraph is dense: it (a) re-states what Litmus is, (b) lists the bundled plugin's surface (20 fixtures / 7 markers / sidecar), and (c) names alternative runners. The page's purpose ("you explain *why* pytest is the primary path") only surfaces in paragraph 2. Move the "This page explains what you get for free" sentence to the top, push the inventory of the plugin's surface (20 fixtures, etc.) to the dedicated section below.

**SUGGESTION — "Why this matters for AI assistants" placement.**
This is one of the strongest arguments and currently sits near the bottom, after the feature tables. In a Concepts/Why page, the AI argument is part of the "why pytest" thesis — consider promoting it to immediately follow "You already know the basics" so the AI rationale colors how the reader reads the tables that follow.

---

## Voice findings

**WARNING — "What you get for free" framing is marketing-tinged for a Concepts page.**
"What pytest handles for you (free)" reads as a sales bullet list. Concepts pages explain *why*; marketing-style "free" wording deflects from the explanation. Tone-down to descriptive ("pytest already provides:" / "out of pytest, you get:") and let the depth of the list make the argument.

**SUGGESTION — "What pytest handles for you" vs "What Litmus adds on top" parallel framing.**
"Free" vs "on top" is asymmetric. Consider symmetric framing: "What pytest provides" / "What Litmus layers on" — keeps the page's structural claim (Litmus is additive) consistent across both headings.

**SUGGESTION — Hedging in the first paragraph.**
"(of which `context`, `verify`, and `logger` are the three you hit every test)" — interrupts the sentence with a parenthetical that should be its own beat. Either commit to it as the canonical "the three core fixtures" framing in a separate sentence, or drop it from the lede and let the dedicated section handle it.

---

## Audience findings

**WARNING — Mixes audiences without signaling which is which.**
The page targets at least two readers: (a) someone evaluating Litmus who needs the "why pytest" argument, and (b) someone already using Litmus who wants the inventory of fixtures/markers. The opening paragraph blends both ("explains what you get for free" + "[20 fixtures] of which `context`, `verify`, `logger` are the three you hit every test"). For an evaluator, the fixture roll-call is noise; for a user, the alternative-runner aside is noise. Either split into two sections with clear signposts, or commit to one audience (evaluator-first feels right for Concepts).

**WARNING — Assumes pytest fluency in a "why pytest" page.**
"`pytest -k`, `-m`, node IDs, `--lf`/`--ff`", "`@pytest.mark.parametrize`", "yield-based setup/teardown, scope resolution, automatic composition" — these terms are unexplained. A reader who needs convincing that pytest is the right choice probably does not yet read these as benefits. Either add one-line "what this means" gloss per bullet, or link out to pytest docs at each bullet (not just the page-end link).

**SUGGESTION — "Why this matters for AI assistants" assumes the reader cares about AI.**
The argument is strong, but the section header asserts the conclusion. For readers who don't yet care, lead with the observation ("LLMs already know pytest deeply") and let the AI-assistant angle land.

**SUGGESTION — Operator vs developer vs test-engineer not distinguished.**
Litmus repeatedly distinguishes these audiences (per CLAUDE.md). The page implicitly addresses the developer (markers, fixtures, parametrize) but never names that the operator and test engineer also benefit (operator: pytest runs on any machine; test engineer: sidecar YAML is editable without code). One sentence per audience would tie the page to Litmus's three-audience model.

---

## Accuracy findings

**CRITICAL — Contradiction: "Retries… are ecosystem plugins, not Litmus additions."**
Line 41 says retries are not a Litmus addition and points to `@pytest.mark.flaky` directly. But:
- `src/litmus/pytest_plugin/markers.py:36` lists `litmus_retry` among the seven `LITMUS_MARKER_NAMES`.
- `src/litmus/pytest_plugin/retry.py` is `retry_config_to_flaky_kwargs` — Litmus translates its own `RetryConfig` model to `pytest-rerunfailures` kwargs.
- Line 45 of this same page lists `litmus_retry` among the seven Litmus markers the AI must learn.
- The markers reference (`docs/reference/litmus-markers.md:153`) documents `@pytest.mark.litmus_retry(max_retries=, delay=, on=)`.

The accurate statement is: "Litmus's `litmus_retry` marker translates to `pytest-rerunfailures` under the hood — the wire format is `@pytest.mark.flaky`, the Litmus-native form is `litmus_retry`."

**CRITICAL — "Mock mode" row claims `pytest-mock` is part of Litmus's mock support.**
The "What Litmus adds on top" table row reads: `Mock mode | --mock-instruments, sidecar mocks:, pytest-mock`. But:
- `pytest-mock` is not a Litmus dependency (`pyproject.toml` — only `pytest-rerunfailures` is in dev deps; no `pytest-mock` anywhere).
- `src/litmus/execution/mocks.py:47` imports `from unittest.mock import patch` directly.
- `src/litmus/models/test_config.py:77` says payload "is forwarded verbatim to `unittest.mock.patch.object`".
- No source file imports `pytest_mock` or `from pytest_mock`.

Replace `pytest-mock` with `litmus_mocks marker (unittest.mock.patch.object under the hood)` or drop the third item entirely.

**WARNING — "Plugin ecosystem" bullet lists ecosystem plugins as if they ship with Litmus.**
Line 24 lists `pytest-xdist`, `pytest-timeout`, `pytest-rerunfailures`, `pytest-dependency`, `pytest-html`. Only `pytest-rerunfailures` is in Litmus's dev dependencies (`pyproject.toml:112`); the others must be installed separately. Under "What pytest handles for you (free)" the reader will reasonably infer all of these are available once Litmus is installed. Reword as "compatible with the pytest plugin ecosystem (`pytest-xdist`, `pytest-timeout`, … — install as needed)".

**WARNING — "20 fixtures" claim verified, but "of which `context`, `verify`, `logger` are the three you hit every test" is opinion presented as fact.**
The reference page (`docs/reference/litmus-fixtures.md`) says "most tests need `verify` and nothing else from this group" and groups fixtures by intent — it does not claim `context`/`verify`/`logger` are the per-test trio. The AI-assistants section (line 45) gives a different "most often" set: `context, verify, logger, pins, instruments`. Two different "top fixtures" lists on the same page. Pick one and use it consistently, or attribute as "common picks" rather than a definitive trio.

**SUGGESTION — `context` is in the example signature but never used in the body.**
The first code sample takes `context` as a parameter (`def test_voltage(self, context, dmm, verify)`) but the body only uses `verify`. Either drop `context` from the signature (cleaner minimal example), or add a one-liner showing what `context` does in this test (e.g. `context.observe("temperature", room_temp)`).

---

## Gaps findings

**WARNING — No "what pytest doesn't give you, that Litmus must add" framing.**
The page enumerates what pytest provides and what Litmus adds, but never explicitly says *why* the additions are necessary — i.e., what hardware-test workflows specifically require that vanilla pytest can't express. For a Concepts page that argues "pytest is the primary path", you need the dual: "here's what pytest is great at; here's what hardware testing needs that pytest doesn't ship". Without this, the additions look arbitrary.

**WARNING — No mention of the *cost* of choosing pytest.**
A genuine "Why X" Concepts page acknowledges tradeoffs. The page is unilaterally positive about pytest. What does Litmus *give up* by riding on pytest? (Examples: session-scoped fixtures don't restart per-DUT cleanly; pytest's CLI surface is not operator-friendly; collection is filesystem-driven, not config-driven; `--collect-only` doesn't show sweep expansion until generate-tests runs.) A short "Trade-offs" section would make the choice feel deliberate rather than assumed.

**SUGGESTION — No mention of `pytest -p no:litmus` or how to opt out.**
A platform-as-pytest-plugin user will at some point ask "how do I disable Litmus for a particular test file?". A one-liner on opt-out would close the loop on "it's a stock pytest install".

**SUGGESTION — No mention of OpenHTF / results-API parity.**
The lede says OpenHTF and the results API are alternatives, but the page never circles back to clarify "what's available on pytest that's not on the others" (or vice versa). A reader picking the runner would benefit from one line per alternative: "OpenHTF gets the same data model but uses phases instead of pytest fixtures."

**SUGGESTION — No "how to migrate from raw pytest" pointer.**
Adjacent to the "you already know the basics" code sample, a sentence like "if you have an existing pytest suite, see [pytest-existing.md](../integration/pytest-existing.md) for the incremental adoption path" would close a real gap for the largest target audience.

---

## Cross-links findings

**WARNING — Inbound link surface is thin (one inbound link from concepts/index.md).**
A `grep -rn "why-pytest" docs/` shows the only inbound reference is `docs/concepts/index.md:7`. The page is essentially orphaned outside the Concepts index. Candidates that should link inbound:
- `docs/reference/pytest-native.md` — currently links forward to fixtures/markers but not to this Why page. Add: "for the rationale, see [Why pytest](../concepts/why-pytest.md)."
- `docs/integration/pytest-existing.md` — natural "why this path" pointer.
- `README.md` and `docs/index.md` if either pitches pytest-first.

**SUGGESTION — Forward link to `pytest-native.md` is duplicative of links above it.**
"Next steps" links to `pytest-native.md` after the body already implicitly relies on it. Consider promoting `pytest-native.md` into the body ("see [pytest-native reference](../reference/pytest-native.md) for the full inventory of what's pytest vs. what's Litmus") rather than the Next-steps coda.

**SUGGESTION — Missing link from the "AI assistants" section to MCP / AI docs.**
"Why this matters for AI assistants" is a strong claim but doesn't link to the AI/MCP integration story (`docs/how-to/mcp-integration.md` exists). Reader who is convinced should be able to follow the thread.

**SUGGESTION — "Litmus fixtures" / "Litmus markers" linked four times each.**
Lines 3, 45, 50, 51 all cross-link to the same two reference pages with slightly different framing. Consider compressing: one canonical link per page in the lede, the rest as bare names. Reduces visual noise.
