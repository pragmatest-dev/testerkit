# Page audit: docs/how-to/custom-drivers.md

**Quadrant:** How-to (writing custom instrument drivers — non-VISA, vendor SDK, etc.)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 1 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 1 | 3 | 2 |
| Accuracy | 6 | 4 | 2 |
| Gaps | 4 | 4 | 3 |
| Cross-links | 2 | 3 | 4 |
| **Total** | **14** | **17** | **16** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ❌ CRITICAL | L136, L141, L198, L203, L281, L290, L297, L304 | The serial / DAQmx / USB examples call `self._ensure_connected()` but no preceding section defines, names, or references this method. It is introduced silently in the middle of example code with no explanation of where it lives, what it does, or where the reader should put its implementation. (See also: Accuracy — this method does not exist on the base class.) |
| ⚠️ WARNING | L311 ("Interface-Level Mocks") | The page introduces `Mock` / `as_mock` AFTER three full driver implementations and BEFORE the "Creating Custom Mocks" section, which then shows a hand-rolled `MockTempLogger` subclassing `Instrument`. The reader is bounced from "use Mock factory" to "subclass Instrument and reimplement everything" without a stated rule for when to pick which. Either move Mocks before the long examples (since most drivers won't need custom code in test) or merge the two mock sections with a clear decision rule. |
| ⚠️ WARNING | L416 ("Registering Custom Drivers") | "Registering Custom Drivers" arrives AFTER "Best Practices" — readers who skim to the end will hit the practices summary before they know how to wire their driver into a station or fixture. In a how-to, registration is part of the task; practices are reflection. Move Registering before Best Practices. |
| 💡 SUGGESTION | L27 ("VISA Instruments") | The page starts with "Recommended Path" but the page title is "Custom Instrument Drivers for non-VISA instruments" (per the intro sentence at L3). The VISA section is at odds with the page's stated scope. Either narrow the page (drop the VISA section, link out to it) or rewrite the intro to drop the "non-VISA" framing. |
| 💡 SUGGESTION | L363 ("Testing Custom Drivers") | The "Testing" section comes after mocks but before registration, which interrupts the build flow (write driver → register driver → test driver). Consider: write → register → test. |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L27 | Marketing-adjacent superlative in header | "VISA Instruments (Recommended Path)" — "Recommended Path" is editorial. State the rule instead: "Use VisaInstrument for any SCPI instrument." |
| 💡 SUGGESTION | L25 | Hedging/aspirational | "Real hardware paths and mock paths share one driver class." — fine, but the surrounding "are the universal simulation interface" overstates: `sim_config` is a VisaInstrument feature, not universal (the non-VISA examples reimplement it by hand). |
| 💡 SUGGESTION | L407 | Throat-clearing list header | "Best Practices" as a numbered list of imperative one-liners is a common pattern but reads as filler at the end of a how-to. Inline each rule where it applies, or drop it. |
| 💡 SUGGESTION | L408 | Hedging | "Always implement `simulate=True`" — this is stated as a "best practice" but is actually a hard requirement of the base ABC contract (every driver inherits `simulate`). Frame as a rule, not a recommendation. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ❌ CRITICAL | L23–24 | Cold drop of core Litmus concept | "what an instrument *can do* is declared in its catalog YAML (`catalog/*.yaml`), not in code" — first mention of "catalog" on the page, no link to the catalog reference (`docs/reference/catalog-schema.md` or `docs/concepts/capabilities.md`). A reader writing a custom driver needs to know the catalog is the missing piece, and where to learn its schema. |
| ⚠️ WARNING | L410 | Cold drop / jargon | "Declare capabilities in the catalog - `catalog/*.yaml` is the matcher's contract; capability mixins do not live in driver code" — "matcher", "matcher's contract", and "capability mixins" are all undefined on the page. Test engineer reading this page won't know what the matcher is or what mixin pattern is being warned against. |
| ⚠️ WARNING | L23 | Programmer jargon | "There are no capability mixins to inherit" — "mixin" is a Python-pattern term. Test engineers don't think in mixins. Rewrite as: "You don't declare what an instrument can do in Python code — that's the catalog YAML's job." |
| ⚠️ WARNING | L24 | Programmer jargon | "Drivers are not interchangeable code-side; the matcher works off catalog metadata." — "code-side", "matcher works off catalog metadata" is software-architect framing. The audience question is: "If I write `MyDMM`, how does Litmus pick it for the DMM role?" Answer that directly. |
| 💡 SUGGESTION | L457 | Wrong vocabulary | "per-role auto-fixtures" — fine for the reference link, but the page never defined "role" anywhere. A one-liner ("role = the dictionary key under `instruments:` in your station YAML, e.g. `dmm:`, `psu:`") would close the loop. |
| 💡 SUGGESTION | L20 | Diagram clarity | The ASCII tree shows `Instrument → VisaInstrument → Concrete drivers`. Add the parallel `Instrument → (your direct subclass) → Concrete drivers` branch so non-VISA users see themselves in the diagram. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L136, L141, L198, L203, L281, L290, L297, L304 | Driver examples call `self._ensure_connected()` | No such method exists on `Instrument` or `VisaInstrument`. `grep -rn "_ensure_connected" src/` returns zero hits. Following these examples raises `AttributeError`. | `src/litmus/instruments/base.py` (entire file, no `_ensure_connected`); `src/litmus/instruments/visa.py` (same) |
| ❌ CRITICAL | L38–44 | `_sim_responses = {"MEAS:VOLT:DC?": "voltage", "MEAS:CURR:DC?": "current"}` paired with `dmm = MyDMM(..., sim_config={"voltage": 5.0, "current": 0.1})` and `float(self.query("MEAS:VOLT:DC?"))`. Implies the literal string `"voltage"` is looked up in `sim_config` to resolve to `5.0`. | `_generate_sim_config` writes `_sim_responses` entries as **static** pyvisa-sim dialogues (`q: "MEAS:VOLT:DC?" r: "voltage"`). The response is the literal string `"voltage"`. `float(self.query("MEAS:VOLT:DC?"))` therefore raises `ValueError: could not convert string to float: 'voltage'`. The `voltage` key in `sim_config` is wired to the `MEAS:VOLT?` property block (no `:DC`), not to `MEAS:VOLT:DC?`. The example as written does not function. | `src/litmus/instruments/visa.py:177–273` (see lines 197, 229, 252–259) |
| ❌ CRITICAL | L425–434 | `def dmm(simulate): ... simulate=simulate` — assumes a fixture named `simulate` is provided by Litmus. | No fixture named `simulate` exists in the codebase. `grep -rn "def simulate\b\|@pytest.fixture[\s\S]*?def simulate" src/` returns zero hits. The actual flag-source fixture is `mock_instruments` (bool, session scope). The example fails at collection with `fixture 'simulate' not found`. | `src/litmus/pytest_plugin/__init__.py:559–568` (the `mock_instruments` fixture). No `simulate` fixture anywhere in `src/`. |
| ❌ CRITICAL | L444–451 | Station YAML shows: `dmm: type: dmm, driver: my_drivers.MyDMM, resource: ..., mock: true, mock_config: {voltage: 5.0}`. The field `mock:` is correct, but the surrounding doc consistently uses `simulate=` and `sim_config=` in the Python API. The driver constructor (e.g. `MyDMM.__init__`) takes `simulate=` / `sim_config=`. The station-config translation from `mock_config:` (YAML) to `sim_config=` (Python) is never mentioned, and the reader has no way to know mock_config flows to the driver. | `StationInstrumentConfig.mock_config: dict[str, Any]` (station.py:34) and the pytest-plugin instrument fixture in `__init__.py:705–820` show `mock_config` is consumed by the Mock factory (`Mock(cls, **mock_config)`), NOT passed as `sim_config=` to the real driver. So `mock: true` + `mock_config:` bypasses the driver's `simulate=True` path entirely and uses interface-level `Mock`. The doc conflates two distinct mocking strategies. | `src/litmus/models/station.py:22–47`; `src/litmus/pytest_plugin/__init__.py:695–820` |
| ❌ CRITICAL | L97 | `super().__init__(simulate=simulate, sim_config=sim_config)` for `SerialDMM`. The base `Instrument.__init__` signature is `(resource: str = "", simulate: bool = False, sim_config: dict | None = None)`. The SerialDMM subclass takes `port: str` but never passes `resource=` to the base. So `self.resource` is `""` after construction. | `Instrument.__init__` at base.py:34–50 — `resource` defaults to `""`. The serial driver should pass `resource=port` (or similar) for the base's identity bookkeeping to work. | `src/litmus/instruments/base.py:34–56` |
| ❌ CRITICAL | L395 | `@pytest.mark.hardware` is shown as if it's a Litmus-provided marker for skipping in CI. | `hardware` is NOT registered as a Litmus marker. `LITMUS_MARKER_NAMES` lists only: `litmus_limits`, `litmus_sweeps`, `litmus_mocks`, `litmus_characteristics`, `litmus_connections`, `litmus_retry`, `litmus_prompts`. With `--strict-markers` this raises an error; without it, the marker is a no-op (no auto-skip in mock mode). The reader will think this gives them "skip in CI" for free. | `src/litmus/pytest_plugin/markers.py:30–38` |
| ⚠️ WARNING | L4 | "Two base classes live in `src/litmus/instruments/`" | Three relevant base/wrapper modules live there: `base.py` (`Instrument`), `visa.py` (`VisaInstrument`), and `mocks.py` (`Mock` factory). The page treats Mock as a separate concept later; either say "two base classes plus the Mock factory" or list all three. | `src/litmus/instruments/__init__.py:5–8` |
| ⚠️ WARNING | L9 / L17 | `litmus.instruments.base.Instrument` and `litmus.instruments.visa.VisaInstrument` are documented as the import paths. | Both are correct, but `litmus.instruments` itself does not re-export them (the package `__init__.py` is doc-only). Imports must use the full submodule path. Worth a one-line note so readers don't try `from litmus.instruments import Instrument` (which fails). | `src/litmus/instruments/__init__.py:1–11` (no actual re-export, only documentation strings) |
| ⚠️ WARNING | L43–47 | Method signatures: `measure_voltage(self, signal_type=None) -> float` | The base `VisaInstrument` does not define `measure_voltage`, and there's no `SignalType` enum or contract anywhere in the live code (`grep -rn "SignalType" src/` returns one orphan docstring reference in visa.py:9 and nothing else). Showing `signal_type=None` as a parameter implies a Litmus convention that doesn't exist. Drop the parameter or explain that it's the author's choice. | `grep -rn "SignalType" src/litmus/` → 1 hit (orphan docstring in visa.py:9) |
| ⚠️ WARNING | L24 | "capability mixins do not live in driver code" | Correct in current code, but the live `base.py:13` docstring and `visa.py:8–14` docstring both still show a mixin pattern: `DMM(VisaInstrument, VoltageInput, CurrentInput, ResistanceInput)`. The codebase's own docstrings contradict this page. Either the codebase docstrings are stale or the doc is wrong; flag for reconciliation. | `src/litmus/instruments/base.py:13–14`, `src/litmus/instruments/visa.py:8–14` |
| 💡 SUGGESTION | L320 | `dmm = Mock(MyDMM, measure_voltage=5.0, measure_current=0.1)` | Verified: matches `Mock(cls: type[T], **values: Any) -> T` signature. But the doc never mentions the dict-lookup or callable forms (e.g. `query={"*IDN?": "..."}`), which are the killer feature for SCPI mocking. Worth a one-liner. | `src/litmus/instruments/mocks.py:80–95, 98–125` |
| 💡 SUGGESTION | L320 | `from litmus.instruments.mocks import Mock, as_mock` | Verified accurate. Worth noting that `Mock` and `as_mock` return types are designed so the mock keeps the driver's typed surface — useful context for typed test code. | `src/litmus/instruments/mocks.py:32–68, 98–220` |
| ✅ VERIFIED | — | 12 claims verified against source (base ABC structure, VisaInstrument constructor signature, `query`/`write` signatures, Mock factory signature, `as_mock` signature, `_sim_responses` class attribute, StationInstrumentConfig fields, mock_config field default, driver dot-path resolution via `rsplit('.', 1) + importlib`, `LITMUS_MARKER_NAMES` tuple contents, `mock_instruments` fixture signature, context-manager `__enter__/__exit__` on base). | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | L23–24 / whole page | The page asserts "the catalog entry is the contract" but never tells the driver author what a catalog entry for a custom driver looks like. A reader writing `SerialDMM` needs to know: where do I put my catalog YAML? What `function:` / `direction:` keys do I declare? What links the catalog entry to my `SerialDMM` class? Without this, the doc's central claim ("declare capabilities in the catalog") has no actionable next step on this page. |
| ❌ CRITICAL | L416–451 ("Registering Custom Drivers") | Two registration paths (conftest fixture vs. station YAML `driver:`) are shown, but the page never says when to use which. The station-YAML path is the production path; the conftest path is for ad-hoc local drivers. The reader cannot tell what they're choosing between. |
| ❌ CRITICAL | L438–451 | Station-YAML registration: no statement of how `mock_config:` flows to the driver. Given the audit-accuracy finding that `mock_config` triggers the `Mock(cls, **mock_config)` path (not `simulate=True` + `sim_config={...}`), the reader will assume their driver's `simulate=True` branch runs in mock mode — it does not. This is a silent behavior gap that breaks "test both modes" guidance later. |
| ❌ CRITICAL | Whole page | No mention of `--mock-instruments` / `LITMUS_MOCK_INSTRUMENTS=1` / the `mock_instruments` session fixture. A reader writing a custom driver and trying to "run my tests without hardware" needs to know which knob switches the platform into mock mode. The page documents driver-internal `simulate=True` but never connects it to the platform's mock-mode entry point. |
| ⚠️ WARNING | L83–142 (Serial) | What happens if `pyserial` is not installed? The example imports `serial` unconditionally at module top, unlike the DAQmx (L155–158) and USB (L218–222) examples which use a `HAS_X` flag. Either make the pattern consistent or call out why serial is different. |
| ⚠️ WARNING | L182, L248 | Error messages: `"nidaqmx not installed. Use simulate=True for testing."` and `"pyusb not installed. Use simulate=True for testing."` — but what should a CI pipeline do? The page never says "in CI, set `--mock-instruments`" or "your conftest should default `simulate=True` on the no-hardware host." Reader is left guessing how to wire this. |
| ⚠️ WARNING | L289–301 | `enable_output` / `disable_output` show `channel: str | None = None` but the parameter is ignored in all branches. Either show a multi-channel example or drop the parameter. As written, it implies the doc reader should support channels even when their device doesn't. |
| ⚠️ WARNING | L207 (DAQmx) | `configure_voltage_range` is a no-op (`pass`). The docstring says "For dynamic range changes, recreate the task" but no example of how. A reader who needs runtime range changes is stranded. |
| 💡 SUGGESTION | L329–361 ("Creating Custom Mocks") | No example shows how to use the new `MockTempLogger` from a test or station YAML. Add a 3-line consumer to close the loop. |
| 💡 SUGGESTION | L86–142 | Serial example never shows how identity (`self.manufacturer`, `self.model`, `self.serial`, `self.firmware`) is populated for non-VISA. The base class declares those fields (base.py:53–56); the VisaInstrument auto-populates them via `*IDN?` parsing. Non-VISA drivers need either a hand-rolled equivalent or a stated "leave None — non-VISA instruments are exempt from identity verification." Worth a paragraph. |
| 💡 SUGGESTION | L363–404 | The hardware test (L395–403) shows `@pytest.mark.hardware` and a sanity assertion `v > 0.0`. Add a one-line "how to run only hardware tests": `pytest -m hardware` (and remind the reader to register the marker in their `conftest.py` or `pyproject.toml`). |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ❌ CRITICAL | L23 ("declared in its catalog YAML (`catalog/*.yaml`)") | First mention of "catalog" on the page, no link. Should link to `../reference/catalog-schema.md` (or `../reference/catalog-cookbook.md`) and `../concepts/capabilities.md`. |
| ❌ CRITICAL | L24 ("the matcher works off catalog metadata") | "matcher" is undefined and unlinked. Should link to `../concepts/capabilities.md#capability-matching` (header exists at L113 of that file). |
| ⚠️ WARNING | L455 | Link `[Capability Interfaces](../concepts/capabilities.md) — Full list of capability protocols` — file exists, but the page does not contain "capability protocols" or a list of "capability interfaces." The closest is `## MeasurementFunction` (L152) and `## Direction Pairing` (L82). The link text misrepresents the target. Either rename to "Capabilities — the matcher's contract" or link to a section that actually lists capability primitives. |
| ⚠️ WARNING | L456 | Link `[Fixture Manager](../concepts/fixtures.md) — Pin-to-instrument routing` — file exists; "Fixture Manager" is not a header in that file (closest is `## Fixture and Station Relationship` at L136). The "Pin-to-instrument routing" framing is accurate to the page content, but "Fixture Manager" as a section name doesn't appear there. Rename the link text or link to `## Using the pins Fixture` (L56). |
| ⚠️ WARNING | "See also" section | Missing obvious related pages: `../how-to/mock-mode.md` (the canonical page on `--mock-instruments` and `mock_config`), `../how-to/configuring-stations.md` (the canonical station-YAML reference, which the doc's own example mirrors), and `../reference/catalog-schema.md` (since the page tells the reader "declare capabilities in the catalog"). |
| 💡 SUGGESTION | L7 | `litmus.instruments.base.Instrument` and `litmus.instruments.visa.VisaInstrument` — first mention of the package; no link to a reference page for the instrument package. If no such reference exists today, that's a separate gap; otherwise link it. |
| 💡 SUGGESTION | L317 | `from litmus.instruments.mocks import Mock, as_mock` — first use of `Mock` / `as_mock`. Could link to a reference entry or to the `mocks.py` docstring. |
| 💡 SUGGESTION | L426 | `def dmm(simulate)` — even setting aside the accuracy bug, the conftest example is the natural place to link `../reference/litmus-fixtures.md#instruments` and `../reference/litmus-fixtures.md#instrument` so readers see how their custom fixture fits the platform's per-role auto-fixture story. |
| 💡 SUGGESTION | L447 | `driver: my_drivers.MyDMM` — link to `../how-to/configuring-stations.md` where the `driver:` field is documented in full (multiple examples there). |
