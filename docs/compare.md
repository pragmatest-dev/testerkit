# Litmus vs. Other Python Hardware Test Approaches

How Litmus compares to other frameworks and tools for hardware/manufacturing test automation. Updated March 2026.

## Quick Comparison

| Capability | **Litmus** | **OpenHTF** | **pytest‑f3ts** | **HardPy** | **TestStand** | **QCoDeS** |
|---|---|---|---|---|---|---|
| Test runner | pytest (native) | Custom (phases) | pytest plugin | pytest plugin | Proprietary | Jupyter |
| Config model | Pydantic + YAML | conf module | config.yml | CouchDB/JSON | Sequence editor | Parameters |
| Instrument drivers | BYO (PyVISA, PyMeasure, etc.) | Plugs (DI) | BYO | BYO | NI drivers | Built-in |
| Data storage | Parquet + DuckDB | JSON/Protobuf | Vendor SaaS | CouchDB | Database | SQLite |
| Operator UI | NiceGUI (built-in) | Removed/3rd party | FixturFab GUI | Browser UI | Native UI | None |
| Parallel multi-DUT | Subprocess per slot + sync | Not supported | Not supported | Not supported | Parallel model | Not supported |
| Shared instruments | InstrumentServer (TCP RPC) | N/A | N/A | N/A | Native | N/A |
| Signal routing | RouteManager + RoutedProxy | N/A | N/A | N/A | Switch exec | N/A |
| AI / MCP tools | MCP server + HTTP API | None | None | None | None | None |
| License | MIT | Apache 2.0 | Proprietary | GPL-3.0 | Commercial | MIT |
| Status (Mar 2026) | Active | Maintenance | Active | Active | Active | Active |

## Detailed Comparisons

### OpenHTF (Google)

The most established open-source hardware test framework. ~640 stars, last release v1.6.0 (March 2025).

**Architecture:** Tests are sequences of *phases* (decorated functions). Instruments are *plugs* — dependency-injected singletons shared across phases. Measurements are declared on phases with validators. Produces structured *test records*.

**Where OpenHTF is stronger:**
- Larger community and production track record
- TofuPilot and Nominal Connect ecosystem for analytics
- Protobuf output for high-throughput pipelines
- Mature measurement validator system

**Where Litmus is stronger:**
- **pytest-native** — no custom runner, no phase decorators, works with existing pytest tests, plugins, and IDE integration. OpenHTF requires learning a distinct execution model.
- **Configuration as code** — Pydantic models with validation, type checking, IDE autocomplete. OpenHTF's `conf` module is untyped string-based config.
- **Structured data** — Parquet + DuckDB for SQL-queryable results out of the box. OpenHTF outputs JSON by default; structured analytics requires third-party services.
- **Parallel multi-DUT** — subprocess-per-slot with cross-process sync points, shared instrument server, signal routing. OpenHTF has no multi-DUT support (GitHub issue #61, open since 2016).
- **AI integration** — MCP server exposes events, measurements, instruments as tools. OpenHTF has no AI story.
- **Built-in operator UI** — NiceGUI dashboard with live event timeline, result detail, execution Gantt. OpenHTF's web UI was removed; ecosystem options are fragmented.
- **Event-driven architecture** — typed event log (Arrow IPC) with subscriber system enables live observability, crash recovery, and decoupled data pipelines. OpenHTF uses synchronous output callbacks.

**Migration path:** Litmus provides an OpenHTF adapter for incrementally migrating existing test suites.

### pytest-f3ts (FixturFab)

A pytest plugin for PCBA functional test, tightly coupled to the FixturFab ecosystem. v1.1.4 (March 2026).

**Architecture:** Provides `test_config` fixture from `config.yml`, `log_vars()` for recording measurements, operator dialog support. Tests run in pytest but results flow to FixturFab's SaaS analytics.

**Where pytest-f3ts is similar:** Both are pytest-native and let you write tests as normal pytest functions.

**Where Litmus differs:**
- **Self-contained** — results stored locally in Parquet, queryable with DuckDB. No vendor SaaS dependency.
- **Full data model** — station configs, fixture configs, product specs, instrument calibration tracking. f3ts has minimal config.
- **Parallel testing, signal routing, shared instruments** — not available in f3ts.
- **Open source** — f3ts is tied to the FixturFab commercial ecosystem.

### HardPy (everypinio)

A pytest-based hardware test bench framework with browser UI. ~56 stars, GPL-3.0, actively developed.

**Architecture:** pytest plugin with CouchDB for results, TypeScript browser UI. Deliberately excludes instrument drivers — users bring InstrumentKit, PyMeasure, QCoDeS, etc.

**Where HardPy is similar:** pytest-native, BYO instrument drivers, browser-based UI.

**Where Litmus differs:**
- **License** — MIT vs GPL-3.0 (important for commercial/proprietary test systems)
- **Data storage** — Parquet + DuckDB (SQL-queryable, columnar analytics) vs CouchDB (document store, requires separate deployment)
- **Configuration** — Pydantic-validated YAML vs ad-hoc
- **Parallel multi-DUT, shared instruments, signal routing** — not available in HardPy
- **AI/MCP integration** — not available in HardPy
- **No external database dependency** — Litmus uses embedded DuckDB; HardPy requires CouchDB

### NI TestStand

The industry standard for production test sequencing. Commercial, Windows-first (Blazor UI in early access for cross-platform).

**Architecture:** Proprietary sequence editor with step types (Python, LabVIEW, C/.NET). Rich parallel model, database results, UUT tracking, process models.

**Where TestStand is stronger:**
- Decades of production deployment at scale
- Native parallel model with sophisticated UUT handling
- Rich reporting and database integration
- Regulatory compliance features (21 CFR Part 11)
- Professional support and training

**Where Litmus is stronger:**
- **Free and open source** — TestStand licenses cost thousands per seat
- **Python-first** — tests are native pytest, not Python called from a proprietary sequencer. TestStand's Python step types marshal data across a language boundary.
- **Cross-platform** — Linux, macOS, Windows. TestStand is historically Windows-only.
- **AI-native** — MCP tools for agent integration. TestStand has no AI story.
- **Modern data stack** — Parquet + DuckDB vs proprietary database schemas
- **Version control friendly** — YAML configs + Python code vs binary sequence files
- **No vendor lock-in** — switch test runners, instrument libraries, or analytics tools without rewriting

### QCoDeS (Microsoft)

Research-grade instrument control and data management for quantum computing. ~420 stars, very actively maintained.

**Architecture:** Parameter-centric model — instruments expose typed Parameters with get/set/validate. Data stored in SQLite DataSets grouped by Experiments. Jupyter-first workflow.

**Where QCoDeS is stronger:**
- Microsoft-backed with strong maintenance
- Excellent data management (typed parameters, SQLite with full provenance)
- Rich instrument driver ecosystem for research equipment

**Where Litmus is stronger:**
- **Manufacturing-oriented** — pass/fail outcomes, limits checking, operator UI, station management. QCoDeS has none of these.
- **Production throughput** — Parquet columnar format for high-volume analytics vs SQLite row store
- **Parallel multi-DUT** — QCoDeS is single-experiment, single-instrument
- **Configuration management** — station/fixture/product YAML hierarchy. QCoDeS has no station concept.

QCoDeS is the right choice for research labs. Litmus is built for production test.

### Bluesky / Ophyd

Scientific experiment orchestration for synchrotron beamlines and physics labs. Very active async rewrite (ophyd-async v0.16+).

**Architecture:** Three layers: ophyd (hardware abstraction via EPICS/Tango), Bluesky RunEngine (experiment plans + callbacks), Databroker (document-oriented data).

**Not comparable for manufacturing test** — Bluesky is designed for facility science with EPICS control systems. No pass/fail, no limits, no operator UI, no station management. Included here because it's sometimes mentioned in "Python instrument control" discussions.

### Fixate (PyFixate)

Small Australian framework (~28 stars) with a unique virtual multiplexer abstraction for signal routing. Stable but maintenance is inactive.

**Where Fixate pioneered:** First-class signal switching/multiplexer modeling — directly represents jig routing topology. Litmus's RouteManager and RoutedProxy serve a similar role with a more modern implementation (break-before-make sequencing, per-resource locking, remote instrument access).

### Instrument Driver Libraries

These complement Litmus rather than compete with it:

| Library | Scope | Litmus integration |
|---|---|---|
| **PyVISA** | VISA protocol layer | Primary instrument communication |
| **PyMeasure** (~720 stars) | Drivers + experiment procedures | Driver library, auto-detected by observer proxy |
| **tm_devices** (Tektronix) | Tektronix instrument drivers | Auto-detected by observer proxy |
| **InstrumentKit** | Research instrument drivers | Compatible via PyVISA |
| **pyvisa-py** | Pure-Python VISA backend | Zero-install instrument communication |

Litmus's observer-based instrument proxy auto-detects 30+ driver libraries and transparently emits typed events for every instrument interaction — no code changes required regardless of which driver library is used.

### Analytics Platforms

| Platform | Approach | Litmus compatibility |
|---|---|---|
| **TofuPilot** | SaaS analytics for OpenHTF/pytest | Could integrate via output subscriber |
| **Nominal Connect** | Real-time testing platform (Rust) | Could integrate via event stream |
| **StandCloud** | Analytics for HardPy | Not compatible |
| **FixturFab** | SaaS for pytest-f3ts | Not compatible (proprietary) |

Litmus includes built-in analytics (yield, Cpk, Pareto, trend) via `litmus yield` CLI and NiceGUI dashboard, but can also export to external platforms.

## What Makes Litmus Unique

No other framework combines all of these:

1. **pytest-native** — tests are normal pytest functions, not a custom DSL
2. **Pydantic configuration** — type-safe, validated, IDE-friendly YAML configs
3. **Parquet + DuckDB** — SQL-queryable columnar results, no external database
4. **Parallel multi-DUT** — subprocess isolation, cross-process sync, shared instruments
5. **Signal routing** — RouteManager with break-before-make, RoutedProxy for transparent measurement
6. **AI/MCP integration** — expose events, measurements, instruments as MCP tools for AI agents
7. **Built-in operator UI** — NiceGUI dashboard with live events, results, Gantt chart
8. **Observer instrument proxy** — auto-detect 30+ driver libraries, emit typed events with zero code changes
9. **Event-driven architecture** — Arrow IPC event log with subscriber system for live observability and crash recovery
10. **Incremental adoption** — start with results API (any source), add config, add instruments, add AI tools
