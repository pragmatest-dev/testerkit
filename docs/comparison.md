# Litmus vs. The Field: Hardware Test Framework Comparison

## Competitors

| | **Litmus** | **Nominal** | **OpenHTF** | **NI TestStand** | **OpenTAP / PathWave** | **htf (HILSTER)** | **HardPy** | **pytest-f3ts** | **pytest (plain)** |
|---|---|---|---|---|---|---|---|---|---|
| Language | Python | Python/Rust/MATLAB | Python | LabVIEW/.NET | .NET/C#/Python | Python | Python/TypeScript | Python | Python |
| License | Apache-2.0 | Commercial (SaaS + on-prem) | Apache-2.0 | Commercial | Open-source (OpenTAP) / Commercial (PathWave) | Commercial | GPL-3.0 | Proprietary | MIT |
| First release | 2025 | 2023 | 2016 | ~2000 | 2019 | ~2020 | ~2023 | ~2023 | 2004 |

## Feature Comparison

### Test Execution

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Test sequencing | ✅ YAML sequences | ✅ Connect procedures | ✅ Phase lists | ✅ Visual editor | ✅ Test plans | ✅ Code-defined | ✅ pytest | ✅ pytest | ✅ Collection |
| Parametric sweeps | ✅ Vector expansion (product, zip, range) | ❌ | ❌ Manual loops | ✅ Sweep/loop steps | ✅ Sweep operator | ❌ | ❌ | ❌ | ✅ `parametrize` |
| Retry on failure | ✅ Per-step config | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ Attempt markers | ❌ | ❌ (needs plugin) |
| Skip-on-dependency | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ✅ Dependency markers | ❌ | ❌ (needs plugin) |
| Parallel multi-DUT | ✅ Subprocess per slot + sync | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ `pytest-xdist` |
| Shared instruments | ✅ InstrumentServer (TCP RPC) | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Signal routing | ✅ RouteManager + RoutedProxy | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Measurement & Limits

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Typed measurements | ✅ Pydantic models | ✅ Typed channels | ✅ Measurement API | ✅ | ✅ | ✅ | ❌ | 🟡 Standardized vars | ❌ |
| Limit comparators | ✅ 10 ATML types (EQ, GELE, GTLT, etc.) | ✅ Checks system | ✅ Validators | ✅ | ✅ | ✅ | ❌ Assertions only | 🟡 min/max only | ❌ |
| Spec-driven limits | ✅ Product YAML → auto-derived | ❌ | ❌ Hardcoded | 🟡 External file | ❌ | ❌ | ❌ | 🟡 YAML config | ❌ |
| Guardband support | ✅ Per-spec configurable | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Signal path traceability | ✅ DUT pin → fixture → instrument per measurement | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Instrument Integration

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Driver approach | Use any Python driver directly | Connect SDK (Python/Rust) | Plug wrappers (boilerplate) | NI drivers + code modules | .NET instrument plugins | Custom drivers | Bring your own | Bring your own | Manual fixtures |
| Auto-discovery | ✅ VISA + LXI + NI SysCfg + serial scan | ❌ | ❌ | ✅ NI ecosystem | 🟡 Plugin-dependent | ❌ | ❌ | ❌ | ❌ |
| Mock instruments | ✅ Config-driven, zero code | ❌ | ❌ Manual | ✅ Simulation | ✅ | ❌ | ❌ | ❌ | Manual fixtures |
| Identity verification | ✅ *IDN? query + config match | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Calibration tracking | ✅ Due date, certificate, lab per asset | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Instrument catalog | ✅ Capability schema per model | ❌ | ❌ | ✅ NI MAX | ❌ | ❌ | ❌ | ❌ | ❌ |

### Configuration

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Config format | YAML (Pydantic-validated) | Code + platform config | Python code | XML/binary | XML | Python code | Python code | YAML | `conftest.py` |
| Station configs | ✅ Roles → instruments → resources | ✅ Asset-linked | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Product specs | ✅ Characteristics, limits, conditions | ❌ | ❌ | 🟡 External | ❌ | ❌ | ❌ | ❌ | ❌ |
| Fixture routing | ✅ DUT pins → fixture points → instruments | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Non-dev editable | ✅ YAML, no code changes | 🟡 Connect UI | ❌ Python only | ✅ GUI | ✅ GUI | ❌ | ❌ | 🟡 YAML limits | ❌ |

### Operator Experience

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Operator UI | ✅ Browser-based (NiceGUI) | ✅ Auto-generated HMI (Connect) | 🟡 Basic web GUI | ✅ Full operator interface | ✅ Editor GUI | ✅ Web UI | ✅ Browser panel | ✅ FixturFab Test Runner | ❌ |
| Live test status | ✅ Real-time via journal streaming | ✅ Real-time streaming | 🟡 | ✅ | ✅ | ✅ | ✅ CouchDB sync | ✅ | ❌ |
| Operator dialogs | ✅ Confirm, choice, input, image | ✅ | ❌ | ✅ | ✅ | ✅ Interactive steps | ✅ Text, checkbox, radio, image | ✅ Via Test Runner | ❌ |
| One-click test launch | ✅ UI with DUT serial, station selection | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |

### Data & Analytics

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Storage format | Parquet (columnar, open) | Cloud platform (proprietary) | Proto/JSON callbacks | Binary/SQL | CSV/XML/custom | SQLite/JSON | CouchDB (JSON) | FixturFab API | None |
| Query engine | ✅ DuckDB (SQL over Parquet) | ✅ Platform query + Python SDK | ❌ | ✅ Proprietary | ❌ | ❌ | 🟡 CouchDB views | ❌ | ❌ |
| FPY / Cpk / SPC | ✅ Built-in analysis module | 🟡 Custom checks, no built-in Cpk | ❌ (needs TofuPilot) | ✅ | ❌ | ❌ | ❌ (needs StandCloud) | ❌ (needs FixturFab) | ❌ |
| Trend analysis | ✅ | ✅ Cross-run trends | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Config snapshots per run | ✅ Station + product + fixture YAML | ❌ | ❌ | 🟡 | ❌ | ❌ | ❌ | ❌ | ❌ |

### Traceability & Compliance

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Git commit per run | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Environment SBOM | ✅ CycloneDX 1.6 export | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Env fingerprint (queryable) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Instrument identity per run | ✅ Manufacturer, model, serial, firmware | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Calibration status per run | ✅ Due date, certificate, lab | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Test phase enforcement | ✅ Dirty repo → forced development | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Medical device certification | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ ISO/TR 80002-2 | ❌ | ❌ | ❌ |

### AI & Automation

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| MCP server (AI tool use) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| HTTP API | ✅ FastAPI | ✅ Platform API + Python SDK | ❌ | ❌ | ✅ REST API | ❌ | ❌ | ✅ FixturFab API | ❌ |
| AI assistant integration | ✅ First-class (exposes tools, doesn't call LLMs) | ❌ | ❌ | 🟡 "Nigel" AI advisor | 🟡 LLM plugin generation | ❌ | ❌ | ❌ | ❌ |
| Headless CI/CD | ✅ pytest runner | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### Reporting

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| HTML reports | ✅ Jinja2 templates | ❌ | ❌ (write your own callback) | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ `pytest-html` |
| PDF reports | ✅ WeasyPrint | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| JSON/CSV export | ✅ | ✅ | ✅ JSON | ✅ | ✅ | ✅ | ✅ JSON (CouchDB) | ❌ | ❌ |
| Auto-report after run | ✅ Configurable in `litmus.yaml` | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |

### Multi-Modal Data & Telemetry

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Time-series telemetry ingestion | ❌ | ✅ CSV, Parquet, Avro, MCAP, TDMS, JSONL | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Video capture & sync | ❌ | ✅ MP4/MKV/AVI time-aligned to channels | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Log ingestion | ❌ | ✅ Structured JSONL logs | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Multi-source time alignment | ❌ | ✅ Workbooks sync video + telemetry + logs | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Interactive data exploration | ❌ | ✅ Workbooks (zoom, pan, cross-channel compute) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Streaming data connections | ❌ | ✅ Real-time channel write streams | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Automated Checks & Data Reviews

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Post-hoc validation checks | ❌ | ✅ Checklists applied to any run/asset | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Streaming checks (live) | ❌ | ✅ Continuous monitoring on assets | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Check violation tracking | ❌ | ✅ Violation events with timestamps | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Formal data review workflow | ❌ | ✅ DataReview with checklists + approvals | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Reusable check templates | ❌ | ✅ Checklists shareable across runs/assets | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Asset & Fleet Management

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Physical asset registry | ❌ | ✅ Assets with properties, runs, data scopes | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Asset-linked test runs | ❌ | ✅ Runs tied to assets, searchable | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Cross-run analysis per asset | ❌ | ✅ Workbook templates across asset history | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Fleet-wide monitoring | ❌ | ✅ Streaming checklists across asset groups | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Operational (post-deployment) data | ❌ | ✅ Same platform for test + field ops | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Collaboration & Team Workflows

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Multi-user workspaces | ❌ | ✅ Workspace-based access control | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Shared analysis templates | ❌ | ✅ Workbook templates | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Event annotations | ❌ | ✅ Events with timestamps + metadata | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Attachments on runs/assets | ❌ | ✅ File attachments with versioning | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| User management | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Documentation & Onboarding

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Quickstart guide | ✅ | ✅ | ❌ "Read the source" | ✅ (with training) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Step-by-step tutorial | ✅ 9-part series | ✅ | ❌ | ✅ | ✅ | ✅ | 🟡 Examples | 🟡 Articles | ✅ |
| Working demo project | ✅ `demo/` with 30 tests | ❌ | 🟡 Examples only | ✅ | ✅ | 🟡 | ✅ Examples folder | ❌ | ❌ |
| API reference docs | ✅ | ✅ | 🟡 Minimal | ✅ | ✅ | ✅ | 🟡 | 🟡 | ✅ |
| LLM reference docs | ✅ Optimized for AI assistants | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Migration & Integration

| Feature | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Existing pytest tests | ✅ Drop-in plugin | ❌ | N/A | ❌ | ❌ | ❌ | ✅ pytest-based | ✅ pytest plugin | N/A |
| OpenHTF migration | ✅ Adapter + guide | ❌ | N/A | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| External results API | ✅ POST results from any source | ✅ SDK upload | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| LabVIEW/TestStand bridge | ✅ HTTP API | 🟡 Via SDK upload | ❌ | N/A | ✅ | ❌ | ❌ | ❌ | ❌ |

## Cost

| | **Litmus** | **Nominal** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|---|---|---|---|---|---|---|---|---|
| Framework | Free (Apache-2.0) | Commercial SaaS (SDK is MIT) | Free (Apache-2.0) | ~$5,000+/seat | Free (MPL-2.0) | Commercial | Free (GPL-3.0) | Free (plugin) | Free (MIT) |
| Deployment | Free | Cloud / on-prem / air-gapped | Free | Per-station license | Free | Per-station | Free (self-host CouchDB) | Free | Free |
| Analytics | Free (built-in) | Included (platform) | TofuPilot ($) | Included | ❌ | Included | StandCloud ($) | FixturFab ($) | ❌ |
| Instruments | Bring your own | Bring your own | Bring your own | NI preferred | Keysight preferred | Bring your own | Bring your own | Bring your own | Bring your own |

## Where Litmus Wins

1. **Traceability** — No other framework captures git commit + instrument identity + calibration status + environment SBOM + config snapshots per run. This is the full audit trail regulated industries need.

2. **Configuration as source of truth** — Product specs, station configs, and fixture routing in YAML mean test engineers and quality engineers can modify behavior without touching code. OpenHTF and pytest require code changes for every limit tweak.

3. **Analytics out of the box** — Parquet + DuckDB gives you FPY, Cpk, trend analysis, and Pareto charts without a separate analytics platform. OpenHTF users need TofuPilot for this. HardPy needs StandCloud.

4. **Parallel multi-DUT** — Subprocess-per-slot execution with cross-process sync points, shared instrument server (TCP RPC, per-resource locking, lock-lease timeout), and signal routing (RouteManager with break-before-make). No other open-source Python framework supports parallel multi-DUT testing. OpenHTF issue #61 has been open since 2016.

5. **AI-native** — MCP server means Claude, Copilot, or any AI agent can discover instruments, run tests, and query results. No other framework exposes test infrastructure as AI tools.

6. **Zero-boilerplate instruments** — Use PyMeasure, PyVISA, or vendor drivers directly. OpenHTF requires wrapping every driver in a Plug class. TestStand requires NI code modules.

7. **Incremental adoption** — Start with `pip install litmus` and a single pytest test. Add config, instruments, UI, and analytics as needed. No big-bang migration required.

8. **No viral license** — Apache-2.0 is manufacturing-friendly. HardPy's GPL-3.0 requires distributing source code of any derivative work — a dealbreaker for most hardware companies.

## Where Litmus Doesn't Win (Yet)

- **Multi-modal data platform** — Nominal ingests telemetry, video, logs, and simulation data into a unified time-aligned workspace. Litmus stores structured measurement results but has no video, log ingestion, or interactive data exploration.
- **Post-hoc analysis & fleet monitoring** — Nominal's checks, data reviews, and streaming checklists enable continuous validation across assets and operations. Litmus validates during test execution only.
- **Asset lifecycle tracking** — Nominal tracks physical assets from test through field operations with cross-run analysis. Litmus tracks DUT serial numbers per run but has no asset registry or fleet view.
- **Team collaboration** — Nominal provides multi-user workspaces, shared analysis templates, and event annotations. Litmus is single-station, file-based.
- **Medical certification** — htf (HILSTER) has ISO/TR 80002-2. Litmus provides the traceability data but no formal certification.
- **Visual sequence editor** — TestStand's drag-and-drop sequence builder is unmatched. Litmus sequences are YAML.
- **Maturity** — TestStand has 25 years of production deployment. Litmus is new.

---

*Updated March 2026. Sources: [OpenHTF GitHub](https://github.com/google/openhtf) (v1.6.0, Mar 2025), [NI TestStand 2026 Q1](https://forums.ni.com/t5/NI-TestStand/Announcing-TestStand-2026-Q1/td-p/4468199), [OpenTAP](https://opentap.io/), [htf HILSTER](https://docs.hilster.io/htf/latest/), [HardPy GitHub](https://github.com/everypinio/hardpy) (v0.22.1, Feb 2026), [pytest-f3ts](https://www.fixturfab.com/articles/pytest-f3ts-hardware-testing) (v1.1.4, Mar 2026), [TofuPilot](https://www.tofupilot.com/), [QCoDeS](https://github.com/microsoft/Qcodes) (v0.55.0, Feb 2026), [tm_devices](https://github.com/tektronix/tm_devices) (v3.5.0, Dec 2025), [awesome-hardware-test](https://github.com/sschaetz/awesome-hardware-test)*
