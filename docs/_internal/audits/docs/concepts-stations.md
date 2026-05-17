# Page audit: docs/concepts/stations.md

**Quadrant:** Concepts / Explanation (stations — types vs instances, instrument roles, station YAML)
**Audited:** 2026-05-17

> Note: this environment did not expose the `Agent` tool used by the
> standard coordinator workflow. The six dimensions below were audited
> inline by the coordinator from the page contents + source-of-truth
> reads. Findings are still concrete and citable; flag for re-run with
> the dedicated sub-agents if a second pass is desired.

---

## Summary

| Dimension     | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering      | 0 | 3 | 2 |
| Voice         | 0 | 2 | 3 |
| Audience      | 1 | 2 | 2 |
| Accuracy      | 3 | 4 | 2 |
| Gaps          | 1 | 4 | 2 |
| Cross-links   | 0 | 3 | 3 |
| **Total**     | **5** | **18** | **14** |

---

## Ordering findings

**Premise.** A Concepts page should build the mental model top-down:
what is the thing → why does it exist → what are its parts → what
variations exist → how does it relate to neighbours. Reference-style
field tables and CLI snippets are downstream of that.

### WARNING — Two-level architecture is introduced *after* the
single-level config it generalises (lines 76–96 vs 5–25)

The page opens with a "Station Configuration" YAML example that is in
fact a **station instance**, then much later (line 76) introduces
"Station Types and Instances" as a distinct two-level architecture.
A Concepts page should introduce the type/instance split **first** —
that's the conceptual scaffold — and then show instances as the
concrete realisation. Today a reader meets concrete YAML before they
have the vocabulary to know what kind of file it is.

Suggested order:

1. What a Station is (current intro, good)
2. **Types vs Instances** (the conceptual two-level architecture)
3. Station instance YAML (concrete example)
4. Instrument roles inside a station
5. Mock mode (an attribute of an instance)
6. Multiple stations / phases / CI

### WARNING — "Instrument Configuration" table fragments the
shared-instruments paragraph (lines 27–36)

Lines 27–35 are a 3-row field table; line 36 then jams a dense
paragraph about shared instruments and `InstrumentServer` directly
onto the end of the table with no blank line and no heading. The
shared-instruments material is a distinct subtopic and belongs under
its own subheading (or — better — be deferred to the "Multiple
Stations" / multi-DUT section, since it only matters when there's
more than one slot).

### WARNING — "Mock Mode" appears before "Station Types and Instances"

Mock mode is a **runtime mode of an instance**, while station types
are a **structural concept**. Conceptually, structure precedes
runtime modes. Today the order is reversed: a reader sees mock
config (lines 48–74) before they've been told that stations even
come in two flavours (76+). This compounds the first finding above.

### SUGGESTION — "Using Stations in Tests" reads as how-to and could
move to the end (lines 124–145)

A Concepts page is improved if usage examples are last, framed as
"here's where to go next to actually do this." The current placement
between "Station Types and Instances" and "Supported Test Phases"
breaks the conceptual through-line.

### SUGGESTION — "Multiple Stations" directory listing belongs near
"Station Types and Instances"

The `stations/` directory layout (lines 170–177) is the physical
projection of the type/instance split. Pairing them helps the reader
see "ah, types go under `stations/types/`, instances at the root."

---

## Voice findings

**Premise.** Concepts pages are explanatory: full sentences, "the
station does X because Y", definitions before mechanics, "you" only
when motivating a concept. Reference-style telegraphic prose and
how-to imperatives are out of register.

### WARNING — Line 3 mixes definition + imperative + product framing
in one sentence

> "A **Station** is where you test — a physical bench with
> instruments. Station configs define what instruments are available
> and how to connect to them."

The first half is concept ("a station is..."), the second half
slides into reference ("station configs define..."). A cleaner
Concepts opening would be one paragraph that explains what a station
*is*, with the YAML mechanics deferred to the section that shows
them.

### WARNING — Line 36 is parenthetical-stuffed (voice → reference)

> "When shared, the orchestrator connects them once and serves them
> to worker subprocesses via an `InstrumentServer` (an internal RPC
> server that lets multiple test workers share one physical
> instrument — TCP with per-resource locking)."

This is a tight, fact-dense sentence with three nested clauses. For
a Concepts page, prefer to *explain* the idea ("a shared instrument
is connected once and proxied to each worker; this preserves
serialised access while letting parallel workers run") and link out
for the implementation detail. The current form belongs in a
reference table.

### SUGGESTION — Line 149 parenthetical buries a definition

> "Stations can optionally declare which test phases (`test_phase`
> is a station-level setting selecting the workflow phase —
> development, validation, production — and gating mocks; see
> [how-to/profiles](../how-to/profiles.md)) they support:"

The parenthetical contains an actual definition of `test_phase`
*and* a factual claim ("gating mocks" — see Accuracy below).
Concepts pages should never define a key concept inside parentheses.
Either lift it to its own short paragraph or remove it.

### SUGGESTION — "Concrete stations that implement a type" (line 100)
should explain the *why*, not just the *what*

A Concepts page should say *why* you'd separate types from
instances (multiple benches of the same shape; profile/`station_type`
matching; CI vs production differing only in resources). Today it's
purely structural.

### SUGGESTION — Tables-everywhere style flattens the explanation

Five of the page's main sections lean on a table. Tables are great
for reference but Concepts benefits from prose that names
trade-offs. E.g. "When to Use Mock Mode" (lines 71–74) is two rows
that say "use mocks when you have no hardware, use hardware when
you have hardware" — that's reference filler, not explanation. Cut
the table and write a sentence about *why* mocks exist (development
flow, CI, no licence cost) and *why* you might still want real
hardware (calibration, real noise floor).

---

## Audience findings

**Premise.** Concepts pages are read by a test engineer learning
Litmus. They want a mental model. They are not yet writing config,
not yet debugging, and not yet plumbing CI. Tutorial-style command
snippets and reference-style field tables both fight that audience.

### CRITICAL — `### Via fixtures` snippet implicitly teaches that
`dmm` is a magic fixture name without saying so (lines 132–137)

```python
def test_voltage(dmm, logger):
    """Instrument roles from station config are auto-registered as fixtures."""
    logger.measure("voltage", dmm.measure_voltage())
```

A first-time Concepts reader has no model for "instrument roles
become fixtures." The docstring on the example is the *only* place
this critical platform behaviour is stated, and it's buried inside
a code block as a comment. This is a foundational concept for
station YAML — the role keys (`dmm:`, `psu:`) become the fixture
names tests request — and deserves a paragraph of its own.

(Source-confirmed: `src/litmus/pytest_plugin/hooks.py` lines 232–274
dynamically register one session-scoped fixture per role.)

### WARNING — Sidebar of `litmus` CLI commands (lines 139–145) is
audience-wrong

The "Via CLI" snippet shows `litmus serve` / `litmus runs` /
`litmus show <run_id>`. These commands aren't about *using
stations* — they're general-purpose ops commands. Including them
under "Using Stations in Tests" tells the audience "I am still
reading about stations" when they are not. Cut.

### WARNING — `--mock-instruments` is introduced as if the reader
already knows pytest CLI conventions (line 50)

> "For development without hardware, use `--mock-instruments`:"
> ```
> pytest tests/ --station=stations/bench_1.yaml --mock-instruments --dut-serial=SIM001
> ```

The Concepts page is the wrong place to teach the CLI flags. Link
to [how-to/mock-mode.md](../how-to/mock-mode.md) and explain *what
mock mode does to a station* in one sentence: "in mock mode, the
station's `mock_config:` values are returned instead of querying
real hardware."

### SUGGESTION — `pymeasure.instruments.keysight.Keysight34461A`
appears five times unchanged

The reader sees this exact driver path on lines 18, 23, 62, 111,
115. For a Concepts page, a generic placeholder
(`<vendor>.<family>.<model>`) makes the *shape* of the field
clearer; the concrete driver path belongs in the how-to.

### SUGGESTION — The reader is not told *who* writes station files

Concepts pages are stronger when they name the role of the
authoring human. Station files are typically authored by a test
engineer or station owner once per bench. State that — it gives
the reader a hook for "is this me?".

---

## Accuracy findings

**Premise.** Every load-bearing claim in the page must match the
source of truth in `src/litmus/`. I read:
`src/litmus/models/station.py`,
`src/litmus/pytest_plugin/__init__.py` (instrument fixtures
section), `src/litmus/pytest_plugin/hooks.py` (auto-registration),
`src/litmus/execution/profiles.py::resolve_test_phase`,
`src/litmus/connect.py`, `src/litmus/execution/slot_runner.py`, and
representative station YAML in `examples/06-station-catalog/` and
`examples/07-profiles/`.

### CRITICAL — `test_phase` does **not** "gate mocks" (line 149)

> "`test_phase` is a station-level setting … and gating mocks"

Two errors in one parenthetical:

1. **`test_phase` is not a station-level setting.** `StationConfig`
   (lines 50–70 of `src/litmus/models/station.py`) has
   `supported_phases: list[str]` — a list of *which phases this
   bench is allowed to run*. `test_phase` itself is a **profile
   facet** / run-level data stamp set via `--test-phase=` and
   resolved by `litmus.execution.profiles.resolve_test_phase`.
2. **It does not gate mocks.** The docstring of
   `resolve_test_phase` (profiles.py:587–612) explicitly states the
   opposite: `--test-phase=production --mock-instruments` *still
   applies the production profile* — mocks only demote the
   resulting `test_phase` data-stamp to `'development'`. There is
   no "gating" relationship in either direction.

Fix: rewrite the parenthetical (or, per Voice finding, drop it)
to say "`supported_phases:` declares which run-level test phases
this station is allowed to serve, so a CI station won't
accidentally accept a production run."

### CRITICAL — `_base.yaml # Station type definitions` in the
`stations/` tree is wrong (line 172)

```
stations/
├── _base.yaml           # Station type definitions
├── bench_1.yaml         # Production bench 1
...
```

Source of truth (`src/litmus/store.py` lines 1322–1350,
`src/litmus/models/station.py` lines 4 + 85): station types live as
**one file per type under `stations/types/<id>.yaml`**, loaded by
`load_station_type`. There is no `_base.yaml` convention, and the
loader doesn't look for one. This conflicts directly with the
correct `stations/types/voltage_tester.yaml` example shown at line
85 of the same page.

Fix: replace the `_base.yaml` row with `├── types/` or
`├── types/voltage_tester.yaml`.

### CRITICAL — "Instrument Configuration" table is missing the
fields most users actually need (lines 27–36)

The table shows only `type`, `resource`, `mock_config`. The actual
`StationInstrumentConfig` model
(`src/litmus/models/station.py:22–47`) has:

| Source field | In doc table? |
|---|---|
| `type` | yes |
| `driver` | **no** |
| `resource` | yes |
| `catalog_ref` | **no** |
| `mock` | **no** |
| `channels` | **no** |
| `description` | **no** |
| `mock_config` | yes |

Omitting `driver`, `mock`, and `catalog_ref` is a critical
accuracy gap because the page itself later uses all three (lines
22, 62, 111, 191, 197) without ever introducing them. A reader
referring back to the table cannot map the examples to the schema.
There is also a model validator
(`resource_required_for_real_hardware`) that's user-visible: at
least one of `resource` / `driver` is required unless `mock: true`.

### WARNING — Station config `name:` is required but the page treats
it as optional (lines 9–14, 102–106)

`StationConfig` (line 56 of station.py) declares `name: str` —
required. The bench_1 example on lines 102–117 has no `name:`
field, which would fail to validate. The example on lines 9–14
*does* include `name:`. Either source of truth is wrong; the model
is the truth, so the example at 102–117 should add `name:`.

### WARNING — "Instrument roles from station config are
auto-registered as fixtures" needs explicit mention of
session-scope (line 135 docstring)

The page implies the auto-registration but doesn't note the
*scope*. In source
(`src/litmus/pytest_plugin/hooks.py:264–272`) every role-fixture
is `@pytest.fixture(scope="session")`. This matters because a
reader writing per-function fixtures around them needs to know
about scope mismatches.

### WARNING — `mock_config: voltage: 3.31` does not necessarily
match how mocks resolve method calls (lines 64–67, 191, 198)

The page implies the YAML key is the *measurement name*. Looking
at real station YAML in `examples/07-profiles/stations/bench_01.yaml`,
the keys are **method names on the driver**:
`measure_voltage`, `measure_dc_voltage`, `set_voltage`,
`set_current`, etc. Calling `dmm.measure_voltage()` resolves to
the `measure_voltage:` value, not `voltage:`. The page's `voltage:`
key is likely a fabricated simplification that would not work
against a Keysight 34461A driver.

Recommend: change `voltage: 3.31` → `measure_voltage: 3.31`,
matching the in-tree example.

### WARNING — `InstrumentServer` is described as TCP-only (line 36)

The description "TCP with per-resource locking" is correct *as a
description of what's in `instruments/server.py`*, but for a
Concepts page the TCP detail is an implementation footnote the
reader does not need. (Voice/Audience finding above already
flagged the parenthetical; flagging here as accuracy because the
"TCP" wording will become a maintenance trap if the transport ever
changes — and the source comment in `slot_runner.py:90` already
just says "instrument server address" without naming the
transport.)

### SUGGESTION — `Common Instrument Types` table conflates
*role keys* with *types* (lines 40–46)

Source of truth: there is no enum of valid `type:` values in the
model — it's a free `str`. The page presents `dmm`, `scope`, etc.
as if they were defined types. Accurate framing: "common
conventional role keys / types" — they're conventional, not
constrained.

### SUGGESTION — `hostname:` field is documented nowhere on the
page but is operationally important

`StationConfig.hostname` (station.py:62–66) is "load-bearing": the
session-start resolver auto-matches it against
`socket.gethostname()` so operators don't need to pass
`--station=<id>` on the matching machine. This is exactly the
kind of explanation Concepts should give. (Also flagged as a Gap
below.)

---

## Gaps findings

**Premise.** Concept gaps are unexplained but load-bearing
mechanisms a reader needs to form the model.

### CRITICAL — Page does not explain the **role → fixture**
mechanism in prose

This is the single most important station concept: the keys you
write under `instruments:` in station YAML *become pytest fixture
names*. The page demonstrates it (line 134) but never explains it.
A Concepts page must say it explicitly: "Every role key under a
station's `instruments:` becomes a session-scoped pytest fixture
of the same name. A station with `dmm:` and `psu:` makes `dmm`
and `psu` fixtures available; a station with `top_dmm:` and
`bot_dmm:` makes those."

(Source: `src/litmus/pytest_plugin/hooks.py:232–274`.)

### WARNING — `hostname:` auto-matching is undocumented

The `hostname:` field on `StationConfig` (station.py:62–66) lets
the bench machine pick its own station file without `--station=`.
This is a fundamental piece of the "stations as deployments"
story and should be explained where station instances are
introduced.

### WARNING — `catalog_ref:` is used in the CI example (line 191)
without being introduced

The reader has not been told what `catalog_ref:` is or what it
points at. They land on the example, see a new key, and have to
guess. Either introduce it in the field table (Accuracy finding)
or remove from the example.

### WARNING — `station_type` validation behaviour is unexplained

`validate_station_against_type`
(`src/litmus/models/station.py:100–140`) is exactly the kind of
behaviour a Concepts page should name: "When a station declares
`station_type:`, the resolver checks that the station's
instruments cover every role the type requires, with a matching
`type:`. Mismatches surface at session start, not at test time."
Today the page says "implement a type" without explaining what
the check is.

### WARNING — `description:` and `channels:` fields on instruments
are completely undocumented

Both are real, user-authored fields on `StationInstrumentConfig`.
Even a one-line "you can attach a freeform `description:` and a
`channels:` mapping for ChannelStore wiring" would close the gap.

### SUGGESTION — Page does not explain how mock-vs-real is chosen
**per instrument**

`StationInstrumentConfig.mock: bool` lets a station declare a
specific instrument as mocked while the rest are real (used in
the CI example, line 191). The interaction with `--mock-instruments`
(global) is not explained, but it's the kind of question every
reader will have within a week of writing their first multi-bench
config.

### SUGGESTION — Calibration and identity verification are not
mentioned

The `instruments` fixture
(`src/litmus/pytest_plugin/__init__.py:701–703`) connects at
session start, **verifies identity against configuration, and
checks calibration status**. This is a load-bearing station
behaviour worth one sentence in Concepts because it's how
station YAML becomes a source of truth at run time, not just a
config file.

---

## Cross-links findings

**Premise.** Cross-links should land on the right *kind* of page
(concepts → concepts/reference; how-to → how-to), use exact
anchors, and not duplicate prose that already exists at the
target.

### WARNING — `pytest --station=stations/bench_1.yaml ...` cross-link
to `mock-mode.md` is missing (line 50–53)

The page introduces `--mock-instruments` then *defines mock mode
in place* (lines 48–74) rather than linking to
[how-to/mock-mode.md](../how-to/mock-mode.md), which already
covers it in depth. Concepts page should explain *what mock mode
means for stations* in one sentence and link out.

### WARNING — `[Capabilities](capabilities.md)` link description is
misleading (line 210)

> "Capabilities — Understanding what stations can do"

`docs/concepts/capabilities.md` is about catalog capabilities (a
discoverable property of instruments), not about "what stations
can do" in the runtime sense. Either re-describe the link
("Catalog capability declarations on station types") or replace
it with `capability-model.md` if that's the intended target.

### WARNING — `[Fixtures](fixtures.md)` Next-Step link description
is also misleading (line 211)

> "Fixtures — Mapping DUT pins to instruments"

A fixture is more than pin mapping; the linked concept page also
covers slots, routing, and shared instruments. Use the same
framing the target page uses ("how DUTs connect to a station") or
adopt the target's own subtitle.

### SUGGESTION — Inline `[Writing Tests]` cross-link (line 121) is
to a how-to and should point to a specific anchor

> "See [Writing Tests](../how-to/writing-tests.md) for details."

The target has 29+ sections. A bare link forces the reader to
hunt. The relevant section is `## Sidecar YAML` (line 236 of
writing-tests.md) and possibly `## Litmus markers` (line 163).
Either link those anchors directly or — better — link to
`docs/reference/litmus-markers.md` since the claim is about
*marker fields*, which is exactly that page's job.

### SUGGESTION — Forward link to `multi-dut-testing.md` is missing

The shared-instruments paragraph (line 36) links to
`configuring-stations.md#shared-instruments-multi-dut` but never
mentions [how-to/multi-dut-testing.md](../how-to/multi-dut-testing.md),
which is the deeper coverage of the *DUT-slot* topology that
makes sharing relevant. Add it.

### SUGGESTION — `[Configuration Reference](../reference/configuration.md)`
link is correct but ungenerous (line 212)

Anchor it: `../reference/configuration.md#station-configuration`
(verified to exist — line 97 of configuration.md). Saves the
reader a search-in-page.
