# Litmus vs. The Field: Hardware Test Framework Comparison

Updated March 2026.

## Landscape

| | **Litmus** | **OpenHTF** | **NI TestStand** | **OpenTAP / PathWave** | **htf (HILSTER)** | **HardPy** | **pytest-f3ts** | **pytest (plain)** |
|---|---|---|---|---|---|---|---|---|
| Language | Python | Python | LabVIEW/.NET | .NET/C#/Python | Python | Python/TypeScript | Python | Python |
| License | Apache-2.0 | Apache-2.0 | Commercial | Open-source (OpenTAP) / Commercial (PathWave) | Commercial | GPL-3.0 | Proprietary | MIT |
| First release | 2025 | 2016 | ~2000 | 2019 | ~2020 | ~2023 | ~2023 | 2004 |
| Status (Mar 2026) | Active (single developer) | Maintenance (~640 stars) | Active (commercial) | Active (Keysight) | Active (commercial) | Active (~56 stars) | Active (FixturFab) | Active (ecosystem) |
| Production deployments | None yet | Hundreds of teams | Thousands of factories | Growing | Industrial/medical | Small | Small (FixturFab customers) | Many (ad-hoc) |

## Feature Comparison

### Test Execution

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Test runner | pytest (native) | Custom (phases) | Proprietary sequencer | .NET test plans | Code-defined | pytest | pytest | pytest |
| Parametric sweeps | ✅ Vector expansion | ❌ Manual loops | ✅ Sweep/loop steps | ✅ Sweep operator | ❌ | ❌ | ❌ | ✅ `parametrize` |
| Retry on failure | ✅ Per-step config | ❌ | ✅ | ✅ | ✅ | ✅ Attempt markers | ❌ | ❌ (needs plugin) |
| Parallel multi-DUT | ✅ Subprocess per slot + sync | ❌ (issue #61 since 2016) | ✅ Parallel model | ✅ | ❌ | ❌ | ❌ | 🟡 `pytest-xdist` (no DUT awareness) |
| Shared instruments | ✅ InstrumentServer (TCP RPC) | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Signal routing | ✅ RouteManager + RoutedProxy | ❌ | ✅ Switch exec | ❌ | ❌ | ❌ | ❌ | ❌ |
| Cross-slot sync points | ✅ SyncPoint/SyncCoordinator | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |

### Measurement & Limits

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Typed measurements | ✅ Pydantic models | ✅ Measurement API | ✅ | ✅ | ✅ | ❌ | 🟡 Standardized vars | ❌ |
| Limit comparators | ✅ 10 ATML types | ✅ Validators | ✅ | ✅ | ✅ | ❌ Assertions only | 🟡 min/max | ❌ |
| Spec-driven limits | ✅ Product YAML → auto-derived | ❌ Hardcoded in decorators | 🟡 External file | ❌ | ❌ | ❌ | 🟡 YAML config | ❌ |
| Signal path traceability | ✅ DUT pin → fixture → instrument | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Instrument Integration

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Driver approach | BYO (PyVISA, PyMeasure, vendor) | Plug wrappers (boilerplate) | NI drivers + code modules | .NET plugins | Custom drivers | BYO | BYO | Manual fixtures |
| Auto-discovery | ✅ VISA + LXI + serial | ❌ | ✅ NI ecosystem | 🟡 Plugin-dependent | ❌ | ❌ | ❌ | ❌ |
| Observer proxy | ✅ Auto-detect 30+ libraries | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Mock instruments | ✅ Config-driven, zero code | ❌ Manual | ✅ Simulation | ✅ | ❌ | ❌ | ❌ | Manual fixtures |
| Identity verification | ✅ *IDN? + config match | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Calibration tracking | ✅ Due date, certificate, lab | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Configuration

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Config format | YAML (Pydantic-validated) | Python code | XML/binary | XML | Python code | Python code | YAML | `conftest.py` |
| Station configs | ✅ Roles → instruments → resources | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Fixture/routing config | ✅ Multi-slot, DUT pins, signal paths | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Non-dev editable | ✅ YAML, no code changes | ❌ Python only | ✅ GUI | ✅ GUI | ❌ | ❌ | 🟡 YAML limits | ❌ |

### Data & Results

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Storage format | Parquet (columnar, open) | JSON/Protobuf callbacks | Binary/SQL | CSV/XML/custom | SQLite/JSON | CouchDB (JSON) | FixturFab API | None |
| Query engine | ✅ DuckDB (SQL) | ❌ | ✅ Proprietary | ❌ | ❌ | 🟡 CouchDB views | ❌ | ❌ |
| Event log | ✅ Arrow IPC + typed events | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| FPY / Cpk / analytics | ✅ Built-in | ❌ (needs TofuPilot) | ✅ | ❌ | ❌ | ❌ (needs StandCloud) | ❌ (needs FixturFab) | ❌ |
| Config snapshots per run | ✅ Station + product + fixture | ❌ | 🟡 | ❌ | ❌ | ❌ | ❌ | ❌ |

### Traceability

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Git commit per run | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Environment SBOM | ✅ CycloneDX 1.6 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Instrument identity per run | ✅ Mfr, model, serial, firmware | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Calibration status per run | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Test phase enforcement | ✅ Dirty repo → development | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Medical certification | ❌ | ❌ | ❌ | ❌ | ✅ ISO/TR 80002-2 | ❌ | ❌ | ❌ |

### Operator Experience

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Operator UI | ✅ Browser (NiceGUI) | 🟡 Basic web GUI | ✅ Full operator interface | ✅ Editor GUI | ✅ Web UI | ✅ Browser panel | ✅ FixturFab Test Runner | ❌ |
| Operator dialogs | ✅ Confirm, choice, input, image | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Live event timeline | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ CouchDB sync | ✅ | ❌ |
| HTML/PDF reports | ✅ Jinja2 + WeasyPrint | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ `pytest-html` |

### AI & Automation

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| MCP server (AI tool use) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| HTTP API | ✅ FastAPI | ❌ | ❌ | ✅ REST | ❌ | ❌ | ✅ FixturFab API | ❌ |
| LLM-optimized docs | ✅ Skills refs for AI assistants | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Headless CI/CD | ✅ pytest runner | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

## Cost

| | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|---|---|---|---|---|---|---|---|
| Framework | Free (Apache-2.0) | Free (Apache-2.0) | ~$5,000+/seat | Free (MPL-2.0) | Commercial | Free (GPL-3.0) | Free (plugin) | Free (MIT) |
| Analytics | Free (built-in) | TofuPilot ($) | Included | ❌ | Included | StandCloud ($) | FixturFab ($) | ❌ |

## Where Litmus Wins

1. **pytest-native** — Tests are normal pytest functions. No custom executor, no phase decorators, no new abstractions to learn. Full IDE support, debugging, plugin ecosystem.

2. **Configuration as source of truth** — Product specs, station configs, fixture routing, and multi-slot definitions all in Pydantic-validated YAML. Non-developers can edit test behavior without touching code. OpenHTF requires code changes for every limit tweak.

3. **Analytics without SaaS** — Parquet + DuckDB gives you SQL-queryable results, FPY, Cpk, and trend analysis locally. No cloud service required. OpenHTF needs TofuPilot. HardPy needs StandCloud.

4. **Parallel multi-DUT** — Subprocess-per-slot with cross-process sync points, shared instrument server (TCP RPC, per-resource locking), and signal routing with break-before-make. No other open-source Python framework has this. OpenHTF issue #61 has been open since 2016.

5. **Event-driven architecture** — Typed event log (Arrow IPC) with subscriber system enables live observability, crash recovery, and decoupled data pipelines. Events flow from pytest → Arrow → Parquet → DuckDB without coupling.

6. **AI-ready** — MCP server exposes instruments, events, and results as tools for AI agents. No other test framework has MCP integration.

7. **Traceability** — Git commit + instrument identity + calibration status + environment SBOM + config snapshots per run. The full audit trail regulated industries need.

8. **Zero-boilerplate instruments** — Observer proxy auto-detects PyVISA, PyMeasure, tm_devices, QCoDeS, and 25+ other driver libraries. Every instrument interaction is logged without code changes. OpenHTF requires wrapping each driver in a Plug class.

## Where Litmus Doesn't Win

- **Maturity** — TestStand has 25 years of production deployment. OpenHTF is used by hundreds of teams. Litmus has zero production deployments and a single developer.
- **Visual sequence editor** — TestStand's drag-and-drop builder is unmatched for non-programmers. Litmus sequences are YAML.
- **Medical certification** — htf has ISO/TR 80002-2. Litmus provides traceability data but no formal certification.
- **Multi-modal data** — Platforms like Nominal ingest telemetry, video, logs into time-aligned workspaces. Litmus stores structured measurements only. These are complementary tools, not competitors.
- **Team collaboration** — Litmus is single-station, file-based. No multi-user workspaces, no shared analysis. Suitable for single-station or small-team use.
- **Ecosystem** — OpenHTF has TofuPilot, Nominal Connect, Spintop. Litmus has no third-party integrations yet.

## Complementary Tools (Not Competitors)

These serve different roles and can work alongside Litmus:

| Tool | What it does | Relationship to Litmus |
|---|---|---|
| **Nominal** | SaaS data platform: telemetry ingestion, video sync, fleet monitoring, data reviews | Analytics layer — could consume Litmus Parquet output |
| **TofuPilot** | SaaS analytics for OpenHTF/pytest: FPY, Cpk, control charts | Analytics layer — Litmus has built-in equivalents |
| **PyMeasure** | Instrument driver library + experiment procedures | Driver library — used directly by Litmus |
| **tm_devices** | Tektronix instrument drivers | Driver library — auto-detected by Litmus observer |
| **QCoDeS** | Research instrument control + SQLite data (Microsoft) | Research tool — different use case than production test |
| **Bluesky/Ophyd** | Scientific experiment orchestration (synchrotrons) | Facility science — different domain entirely |

---

*Updated March 2026. Sources: [OpenHTF](https://github.com/google/openhtf) (v1.6.0, Mar 2025), [TestStand 2026 Q1](https://forums.ni.com/t5/NI-TestStand/Announcing-TestStand-2026-Q1/td-p/4468199), [OpenTAP](https://opentap.io/), [htf](https://docs.hilster.io/htf/latest/), [HardPy](https://github.com/everypinio/hardpy) (v0.22.1), [pytest-f3ts](https://www.fixturfab.com/articles/pytest-f3ts-hardware-testing) (v1.1.4), [TofuPilot](https://www.tofupilot.com/), [QCoDeS](https://github.com/microsoft/Qcodes) (v0.55.0), [tm_devices](https://github.com/tektronix/tm_devices) (v3.5.0), [awesome-hardware-test](https://github.com/sschaetz/awesome-hardware-test)*
