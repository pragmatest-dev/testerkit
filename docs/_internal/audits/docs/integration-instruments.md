# Page audit: docs/integration/instruments.md

**Quadrant:** Integration (instrument integration — using vendor drivers, custom drivers)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 0 | 2 | 2 |
| Accuracy | 3 | 4 | 2 |
| Gaps | 1 | 3 | 2 |
| Cross-links | 0 | 3 | 3 |
| **Total** | **4** | **15** | **14** |

---

## Ordering

**WARNING — "Mock Instruments" lands before "Discovery", inverting the natural bring-up order**

The page begins with "Quick Start with PyVISA" (real hardware, real address), pivots to PyMeasure (real hardware, real address), then to Mock Instruments (no hardware), then to Discovery (find what hardware you have). For a reader bringing up a new bench, the natural order is discover → identify driver → wire into station → mock for CI. The current sequence asks the reader to type a fictional `TCPIP::192.168.1.100::INSTR` in section 1 before showing them how to find their real resource string in section 5.

Recommended order: PyVISA quick start → Discovery → PyMeasure drivers → Station Config → Mock Instruments → Traceability → Integration Patterns.

**WARNING — "With Station Config" subsection sits under "Quick Start with PyVISA" but introduces station YAML, the pytest plugin, and fixtures all at once**

Lines 38-60 introduce four new concepts (station YAML file, `driver:` field, plugin auto-instantiation, fixture availability) inside what reads as a PyVISA tutorial. The same content reappears in expanded form under "Using PyMeasure Drivers" (lines 70-96) and again under "Integration Patterns / pytest" (lines 193-201). Either pull station-config into its own section between PyVISA and PyMeasure, or defer all station-config examples to a single dedicated section.

**SUGGESTION — "Traceability" lives below "Integration Patterns" but is the page's most distinctive Litmus value-add**

Per-step instrument identity is what Litmus uniquely provides; everything above this section is "use PyVISA / PyMeasure exactly as you would without Litmus." Moving Traceability above Integration Patterns (or surfacing it in the lead-in) better signals the platform's contribution.

**SUGGESTION — "Standalone Script" under Integration Patterns is out of place**

The standalone script example (lines 220-246) doesn't integrate with Litmus at all — it's a hand-rolled `pyvisa` + `Mock` script with no logger, no context, no station. It belongs in `how-to/mock-mode.md` or `how-to/custom-drivers.md`, not in a section that opened by saying "Station roles become fixtures automatically."

---

## Voice

**WARNING — Page opens with a negative ("Litmus does NOT provide instrument drivers")**

Leading with what the product does NOT do is acceptable when the message is "stop searching for our driver catalog," but the present formulation reads as defensive. Reframe to the affirmative platform claim ("Litmus integrates with any Python instrument driver — PyMeasure, PyVISA, vendor SDKs — and adds discovery, identification, mocking, and traceability on top"). The "NOT" framing repeats CLAUDE.md's internal rule but doesn't need to bleed into user-facing docs.

**SUGGESTION — "Quick Start with PyVISA" but no "Quick Start with PyMeasure"**

Inconsistent heading style. Either both are quick starts or neither is.

**SUGGESTION — Section 2 is titled "Using PyMeasure Drivers" while section 1 is "Quick Start with PyVISA"**

"Using X" vs "Quick Start with X" is a small but jarring shift. Pick one verb (preferably "Using" since both sections do the same thing).

**SUGGESTION — "The pytest plugin will instantiate the driver" (line 53) leaks runner-specific framing into a page that should be runner-neutral**

Per the platform-not-plugin framing, an integration page about instruments should say "Litmus instantiates the driver" or "the runtime instantiates the driver from station config." The pytest plugin is one consumer; OpenHTF and the harness also consume station config.

---

## Audience

**WARNING — "InstrumentInfo(manufacturer=..., model=..., serial=...)" example (line 181) shows a model the reader has never been introduced to**

The Discovery section returns `InstrumentInfo(...)` instances but never says where this lives, what fields it has, or how to use one. A reader copying this code will hit an import they can't satisfy. Either import it explicitly in the snippet (`from litmus.models.instrument import InstrumentInfo`) or link to the reference.

**WARNING — "Mock supports three value types" (line 116) jumps from a single `Mock(Keithley2400, ...)` line to a typing protocol explanation**

The reader has just seen `Mock(Keithley2400, voltage=5.0, current=1.5e-6)` and accepts it as "set return values." The "three value types" section then introduces `Mock(object, ...)`, dict lookups keyed by first argument, and callables — three distinct mental models in 15 lines, with no transition explaining when you'd reach for each. Add a one-line "use simple values for properties, dict for SCPI command/response, callable for stateful behavior" framing.

**SUGGESTION — The audience for "Integration Patterns" is unclear**

A test author already knows fixtures; an integrator already knows custom overrides. Splitting "pytest (Recommended)" / "Custom Fixture Override" / "Standalone Script" as siblings suggests they're peer choices, but they target three different readers (test author, fixture maintainer, scripter outside any framework).

**SUGGESTION — `Mock(object, ...)` (lines 121, 125, 132, 229) is shown without explaining why `object` is acceptable**

A reader who just saw `Mock(Keithley2400, ...)` will wonder why `object` is suddenly a valid first argument. One line — "use `object` when you don't have a real class to inherit from" — fixes this.

---

## Accuracy

**CRITICAL — Lines 41-51: `driver: pyvisa.resources.MessageBasedResource` example is non-functional**

The station-loader code path in `src/litmus/instruments/lifecycle.py:147` instantiates the driver as `driver_class(record.resource)`. `MessageBasedResource` is not constructable this way — PyVISA resources are returned by `ResourceManager.open_resource()`, never instantiated directly. A reader who copies this YAML will get an obscure failure. The correct way to get a raw PyVISA resource from station config is to OMIT `driver:` entirely and rely on the `elif record.resource:` fallback in lifecycle.py:148-152, which calls `rm.open_resource(record.resource)`. Either delete this example or replace it with a station block that omits `driver:`.

**CRITICAL — Lines 280-288: Traceability example shows a `resources:` block at station root and `instruments:` as `dmm: keysight_dmm_001` (string), but `StationConfig` defines neither**

`src/litmus/models/station.py:50-70` declares `StationConfig` with `model_config = {"extra": "forbid"}`, `instruments: dict[str, StationInstrumentConfig]`, and no `resources:` field. Loading the YAML shown on lines 280-286 against `StationConfig` would raise a Pydantic validation error on the unknown `resources:` key and on the string-valued instrument entries.

That said, `src/litmus/pytest_plugin/__init__.py:718-722` advertises this exact "new format" in its docstring. Reality: either the model needs to learn that shape (and `extra="forbid"` needs to relax), or the docstring + doc page describe an unimplemented future. Until the disconnect is reconciled, this example will fail at load time. Verify against `load_station()` and either land the schema or remove the example.

**CRITICAL — Lines 252-264: instrument-array column names listed in the prose do not match `INSTRUMENT_ARRAY_KEYS`**

The doc shows five columns: `step_instruments_name`, `step_instruments_serial`, `step_instruments_model`, `step_instruments_firmware`, `step_instruments_resource`. The actual canonical tuple in `src/litmus/data/backends/_row_helpers.py:34-49` has fourteen columns: `name, id, driver, resource, protocol, manufacturer, model, serial, firmware, cal_due, cal_last, cal_certificate, cal_lab, mocked`. The doc undercounts and silently drops the calibration columns (`cal_due`, `cal_last`, `cal_certificate`, `cal_lab`) and the `mocked` flag — which is the entire point of the calibration section that follows. Update the bullet list to the real tuple (or generate it from the constant).

**WARNING — Line 49 path is wrong: `pyvisa.resources.MessageBasedResource` cannot be passed to `load_driver_class`**

`src/litmus/instruments/lifecycle.py:35` does `module_path, class_name = driver_path.rsplit(".", 1)`, then `importlib.import_module(module_path)`. For `pyvisa.resources.MessageBasedResource`, this imports `pyvisa.resources` and looks up `MessageBasedResource` — which does exist, so loading succeeds, but instantiation at line 147 (`driver_class(record.resource)`) then fails because `MessageBasedResource.__init__` does not accept a single resource string. See first CRITICAL.

**WARNING — Lines 156-161: `mock_config: { measure_voltage: 5.0, measure_current: 0.25 }` doesn't pair with any code in the page**

The PSU test code earlier (line 90-95) does `psu.voltage = 5.0`, `psu.output_enabled = True`, `dmm.voltage_dc` — none of which touch `measure_voltage`/`measure_current`. The mock_config keys must match the *attribute or method name the test code actually accesses* on the mock. As written, the mock_config would never be exercised. Either align with `voltage`/`output_enabled`/`current` properties or change the test code to call `.measure_voltage()` / `.measure_current()`. Repo's `examples/01-vanilla/conftest.py:34` shows the canonical pairing: `Mock(DMM, measure_dc_voltage=3.31, measure_dc_current=0.042)` paired with `dmm.measure_dc_voltage()` in tests.

**WARNING — Line 165: `--dut-serial=TEST001` flag — verify it exists**

The other CLI flags in the example (`--station=`, `--mock-instruments`) are real (`src/litmus/pytest_plugin/hooks.py:923, 947`). Confirm `--dut-serial` is registered; if not, drop it or replace with the real flag.

**WARNING — Lines 78-79, 83-84: `pymeasure.instruments.keysight.Keysight34461A` and `KeysightE36312A`**

`load_driver_class` uses dotted rsplit — `pymeasure.instruments.keysight` must be a real module exporting `Keysight34461A`/`KeysightE36312A` at the top level. The repo uses these driver paths in `examples/01-vanilla` and several tutorial pages, so they're load-tested elsewhere, but pyMeasure's actual public surface is `pymeasure.instruments.keysight:Keysight34461A` (module attribute) — this is likely fine, but worth confirming with `uv run python -c "from pymeasure.instruments.keysight import Keysight34461A, KeysightE36312A"` (pymeasure was not installed in the audit environment).

**SUGGESTION — Line 56-60: `def test_voltage(dmm, logger)` shows logger but page hasn't introduced it**

Minor — readers from the tutorial will recognize `logger`, but this page lands at the top of the integration section and a first-time reader sees `logger` with no context. Either link to `concepts/event-log.md` / `reference/litmus-fixtures.md` or briefly note that `logger` is provided by the pytest plugin.

**SUGGESTION — Line 49 (`type: dmm`) is shown but `type:` semantics are not explained**

`StationInstrumentConfig.type` is a free-form string per `src/litmus/models/station.py:27`, used for capability matching against `StationType` templates. The doc shows `type: dmm` everywhere as if it's reserved vocabulary. Add a sentence or link to `concepts/stations.md` explaining what `type:` is for.

---

## Gaps

**CRITICAL — No coverage of how to choose between PyVISA, PyMeasure, and vendor SDK**

The page lists three driver sources in the lead and then immediately dives into PyVISA. A reader hitting this page has the actual question: "I have a Keysight DMM — which path should I take?" Add a one-paragraph decision aid: PyMeasure when the instrument is in PyMeasure's catalog (fastest path, high-level API); raw PyVISA when you want SCPI directly or your instrument is uncommon; vendor SDK for non-VISA (DAQmx, proprietary). Without it, the page is a tour of options with no guidance.

**WARNING — No worked example of `catalog_ref:` in a station YAML**

Line 148 shows `catalog_ref: generic_dmm` inside a mock block, but the page never shows a real-hardware station instrument with `catalog_ref:` driving driver selection — which is the catalog architecture's core purpose. The path `catalog_ref → catalog entry → driver field` is implemented in `src/litmus/instruments/pool.py:285-295`. Show one example.

**WARNING — Calibration / identity verification behavior is not described**

`src/litmus/instruments/lifecycle.py:45-84` (check_calibration, verify_instrument_identity) does meaningful work: warnings on overdue cal, warnings 30 days before due, identity mismatch warnings against the instrument asset's `info:`. The page mentions calibration data is stored but does not say "Litmus will warn you 30 days before cal expires" — which is the operational reason to maintain the asset file. Add 2-3 lines.

**WARNING — `InstrumentProxy` and event emission are entirely absent**

`src/litmus/instruments/proxy.py` wraps each driver in a proxy that emits `InstrumentRead` / `InstrumentSet` / `InstrumentConfigure` events to the event log (channels classified by `ChannelKind` in `src/litmus/models/instrument.py:17-30`). This is a major Litmus-specific behavior on top of every "bring your own driver" — and reads in particular are how `instr_*` columns get populated automatically. The page never mentions it. Add a section "What Litmus adds when it owns the driver lifecycle" linking to `concepts/event-log.md`.

**SUGGESTION — No mention of `litmus.instruments.base.Instrument` or `litmus.instruments.visa.VisaInstrument`**

`docs/how-to/custom-drivers.md` introduces these base classes as the recommended path for custom code. This integration page never names them — a reader who wants "PyVISA + structure" will not discover them. Briefly reference them in the "Next Steps" or in a "When you outgrow raw PyVISA" subsection.

**SUGGESTION — Mock-mode interaction with `--mock-instruments` vs `mock: true` is unclear**

The page shows both `--mock-instruments` (CLI) and `mock: true` (YAML) without saying how they combine. The actual rule in `src/litmus/pytest_plugin/__init__.py:749` is `use_mock = mock_instruments or (inline_config.mock if inline_config else False)` — i.e., either flag turns mocking on per instrument. Document the precedence.

---

## Cross-links

**WARNING — "Next Steps" is thin and missing the most relevant peers**

Three links: custom-drivers, mock-mode, stations. Missing:
- `reference/litmus-fixtures.md` (the `instruments` / `instrument` / `dmm` / `psu` fixtures shown on the page have a reference entry)
- `concepts/event-log.md` (instrument reads/sets are the headline event types)
- `how-to/configuring-stations.md` (the page shows station YAML repeatedly)
- `reference/parquet-schema.md` or `concepts/results-storage.md` (the `step_instruments_*` columns documented in Traceability)

**WARNING — Inline mentions of `logger`, `context`, `verify` have no link to where they're defined**

The page uses `logger.measure(...)` (line 60, 93), `context.get_param(...)` (line 199), and the autouse fixture pattern without linking to `reference/litmus-fixtures.md`. First mention of each should link.

**WARNING — `pyvisa-py` is mentioned (line 12) but PyVISA backend selection has no further pointer**

A reader on Linux without NI-VISA installed will land here, see "pure-Python backend (no NI-VISA or Keysight IO Libraries required)" and have no link to follow when `pyvisa-py` doesn't see their USB device (very common). Link to PyVISA's backend docs or to a how-to.

**SUGGESTION — `concepts/stations.md` is linked in Next Steps but the body shows three station YAML examples with no inline link**

When showing station YAML for the first time (line 41), link to `concepts/stations.md`.

**SUGGESTION — Traceability section should link to `concepts/results-storage.md` and `reference/parquet-schema.md`**

The `step_instruments_*` column convention is part of the parquet schema and the results-storage model; without those links a reader has no way to learn what `INSTRUMENT_ARRAY_KEYS` actually contains.

**SUGGESTION — Cross-link from `concepts/stations.md` and `how-to/configuring-stations.md` back to this page**

Inbound link health: a grep shows only `docs/integration/index.md` and `docs/integration/pytest-existing.md` link here. The stations concept page and the configuring-stations how-to both discuss instruments in station YAML and should point to this page for the bring-your-own-driver story.
