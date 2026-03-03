# Litmus vs. The Field: Hardware Test Framework Comparison

## Competitors

| | **Litmus** | **OpenHTF** | **NI TestStand** | **OpenTAP / PathWave** | **htf (HILSTER)** | **HardPy** | **pytest-f3ts** | **pytest (plain)** |
|---|---|---|---|---|---|---|---|---|
| Language | Python | Python | LabVIEW/.NET | .NET/C#/Python | Python | Python/TypeScript | Python | Python |
| License | Apache-2.0 | Apache-2.0 | Commercial | Open-source (OpenTAP) / Commercial (PathWave) | Commercial | GPL-3.0 | Proprietary | MIT |
| First release | 2025 | 2016 | ~2000 | 2019 | ~2020 | ~2023 | ~2023 | 2004 |

## Feature Comparison

### Test Execution

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Test sequencing | ✅ YAML sequences | ✅ Phase lists | ✅ Visual editor | ✅ Test plans | ✅ Code-defined | ✅ pytest | ✅ pytest | ✅ Collection |
| Parametric sweeps | ✅ Vector expansion (product, zip, range) | ❌ Manual loops | ✅ Sweep/loop steps | ✅ Sweep operator | ❌ | ❌ | ❌ | ✅ `parametrize` |
| Retry on failure | ✅ Per-step config | ❌ | ✅ | ✅ | ✅ | ✅ Attempt markers | ❌ | ❌ (needs plugin) |
| Skip-on-dependency | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ Dependency markers | ❌ | ❌ (needs plugin) |
| Parallel execution | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ `pytest-xdist` |

### Measurement & Limits

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Typed measurements | ✅ Pydantic models | ✅ Measurement API | ✅ | ✅ | ✅ | ❌ | 🟡 Standardized vars | ❌ |
| Limit comparators | ✅ 10 ATML types (EQ, GELE, GTLT, etc.) | ✅ Validators | ✅ | ✅ | ✅ | ❌ Assertions only | 🟡 min/max only | ❌ |
| Spec-driven limits | ✅ Product YAML → auto-derived | ❌ Hardcoded | 🟡 External file | ❌ | ❌ | ❌ | 🟡 YAML config | ❌ |
| Guardband support | ✅ Per-spec configurable | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Signal path traceability | ✅ DUT pin → fixture → instrument per measurement | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Instrument Integration

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Driver approach | Use any Python driver directly | Plug wrappers (boilerplate) | NI drivers + code modules | .NET instrument plugins | Custom drivers | Bring your own | Bring your own | Manual fixtures |
| Auto-discovery | ✅ VISA + LXI + NI SysCfg + serial scan | ❌ | ✅ NI ecosystem | 🟡 Plugin-dependent | ❌ | ❌ | ❌ | ❌ |
| Mock instruments | ✅ Config-driven, zero code | ❌ Manual | ✅ Simulation | ✅ | ❌ | ❌ | ❌ | Manual fixtures |
| Identity verification | ✅ *IDN? query + config match | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Calibration tracking | ✅ Due date, certificate, lab per asset | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Instrument catalog | ✅ Capability schema per model | ❌ | ✅ NI MAX | ❌ | ❌ | ❌ | ❌ | ❌ |

### Configuration

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Config format | YAML (Pydantic-validated) | Python code | XML/binary | XML | Python code | Python code | YAML | `conftest.py` |
| Station configs | ✅ Roles → instruments → resources | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Product specs | ✅ Characteristics, limits, conditions | ❌ | 🟡 External | ❌ | ❌ | ❌ | ❌ | ❌ |
| Fixture routing | ✅ DUT pins → fixture points → instruments | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Non-dev editable | ✅ YAML, no code changes | ❌ Python only | ✅ GUI | ✅ GUI | ❌ | ❌ | 🟡 YAML limits | ❌ |

### Operator Experience

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Operator UI | ✅ Browser-based (NiceGUI) | 🟡 Basic web GUI | ✅ Full operator interface | ✅ Editor GUI | ✅ Web UI | ✅ Browser panel | ✅ FixturFab Test Runner | ❌ |
| Live test status | ✅ Real-time via journal streaming | 🟡 | ✅ | ✅ | ✅ | ✅ CouchDB sync | ✅ | ❌ |
| Operator dialogs | ✅ Confirm, choice, input, image | ❌ | ✅ | ✅ | ✅ Interactive steps | ✅ Text, checkbox, radio, image | ✅ Via Test Runner | ❌ |
| One-click test launch | ✅ UI with DUT serial, station selection | ❌ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |

### Data & Analytics

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Storage format | Parquet (columnar, open) | Proto/JSON callbacks | Binary/SQL | CSV/XML/custom | SQLite/JSON | CouchDB (JSON) | FixturFab API | None |
| Query engine | ✅ DuckDB (SQL over Parquet) | ❌ | ✅ Proprietary | ❌ | ❌ | 🟡 CouchDB views | ❌ | ❌ |
| FPY / Cpk / SPC | ✅ Built-in analysis module | ❌ (needs TofuPilot) | ✅ | ❌ | ❌ | ❌ (needs StandCloud) | ❌ (needs FixturFab) | ❌ |
| Trend analysis | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Config snapshots per run | ✅ Station + product + fixture YAML | ❌ | 🟡 | ❌ | ❌ | ❌ | ❌ | ❌ |

### Traceability & Compliance

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Git commit per run | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Environment SBOM | ✅ CycloneDX 1.6 export | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Env fingerprint (queryable) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Instrument identity per run | ✅ Manufacturer, model, serial, firmware | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Calibration status per run | ✅ Due date, certificate, lab | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Test phase enforcement | ✅ Dirty repo → forced development | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Medical device certification | ❌ | ❌ | ❌ | ❌ | ✅ ISO/TR 80002-2 | ❌ | ❌ | ❌ |

### AI & Automation

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| MCP server (AI tool use) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| HTTP API | ✅ FastAPI | ❌ | ❌ | ✅ REST API | ❌ | ❌ | ✅ FixturFab API | ❌ |
| AI assistant integration | ✅ First-class (exposes tools, doesn't call LLMs) | ❌ | 🟡 "Nigel" AI advisor | 🟡 LLM plugin generation | ❌ | ❌ | ❌ | ❌ |
| Headless CI/CD | ✅ pytest runner | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### Reporting

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| HTML reports | ✅ Jinja2 templates | ❌ (write your own callback) | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ `pytest-html` |
| PDF reports | ✅ WeasyPrint | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| JSON/CSV export | ✅ | ✅ JSON | ✅ | ✅ | ✅ | ✅ JSON (CouchDB) | ❌ | ❌ |
| Auto-report after run | ✅ Configurable in `litmus.yaml` | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |

### Documentation & Onboarding

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Quickstart guide | ✅ | ❌ "Read the source" | ✅ (with training) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Step-by-step tutorial | ✅ 9-part series | ❌ | ✅ | ✅ | ✅ | 🟡 Examples | 🟡 Articles | ✅ |
| Working demo project | ✅ `demo/` with 30 tests | 🟡 Examples only | ✅ | ✅ | 🟡 | ✅ Examples folder | ❌ | ❌ |
| API reference docs | ✅ | 🟡 Minimal | ✅ | ✅ | ✅ | 🟡 | 🟡 | ✅ |
| LLM reference docs | ✅ Optimized for AI assistants | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Migration & Integration

| Feature | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Existing pytest tests | ✅ Drop-in plugin | N/A | ❌ | ❌ | ❌ | ✅ pytest-based | ✅ pytest plugin | N/A |
| OpenHTF migration | ✅ Adapter + guide | N/A | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| External results API | ✅ POST results from any source | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| LabVIEW/TestStand bridge | ✅ HTTP API | ❌ | N/A | ✅ | ❌ | ❌ | ❌ | ❌ |

## Cost

| | **Litmus** | **OpenHTF** | **TestStand** | **OpenTAP** | **htf** | **HardPy** | **f3ts** | **pytest** |
|---|---|---|---|---|---|---|---|---|
| Framework | Free (Apache-2.0) | Free (Apache-2.0) | ~$5,000+/seat | Free (MPL-2.0) | Commercial | Free (GPL-3.0) | Free (plugin) | Free (MIT) |
| Deployment | Free | Free | Per-station license | Free | Per-station | Free (self-host CouchDB) | Free | Free |
| Analytics | Free (built-in) | TofuPilot ($) | Included | ❌ | Included | StandCloud ($) | FixturFab ($) | ❌ |
| Instruments | Bring your own | Bring your own | NI preferred | Keysight preferred | Bring your own | Bring your own | Bring your own | Bring your own |

## Where Litmus Wins

1. **Traceability** — No other framework captures git commit + instrument identity + calibration status + environment SBOM + config snapshots per run. This is the full audit trail regulated industries need.

2. **Configuration as source of truth** — Product specs, station configs, and fixture routing in YAML mean test engineers and quality engineers can modify behavior without touching code. OpenHTF and pytest require code changes for every limit tweak.

3. **Analytics out of the box** — Parquet + DuckDB gives you FPY, Cpk, trend analysis, and Pareto charts without a separate analytics platform. OpenHTF users need TofuPilot for this. HardPy needs StandCloud.

4. **AI-native** — MCP server means Claude, Copilot, or any AI agent can discover instruments, run tests, and query results. No other framework exposes test infrastructure as AI tools.

5. **Zero-boilerplate instruments** — Use PyMeasure, PyVISA, or vendor drivers directly. OpenHTF requires wrapping every driver in a Plug class. TestStand requires NI code modules.

6. **Incremental adoption** — Start with `pip install litmus` and a single pytest test. Add config, instruments, UI, and analytics as needed. No big-bang migration required.

7. **No viral license** — Apache-2.0 is manufacturing-friendly. HardPy's GPL-3.0 requires distributing source code of any derivative work — a dealbreaker for most hardware companies.

## Where Litmus Doesn't Win (Yet)

- **Parallel execution** — TestStand and OpenTAP support parallel test steps. Litmus runs vectors sequentially.
- **Medical certification** — htf (HILSTER) has ISO/TR 80002-2. Litmus provides the traceability data but no formal certification.
- **Visual sequence editor** — TestStand's drag-and-drop sequence builder is unmatched. Litmus sequences are YAML.
- **Maturity** — TestStand has 25 years of production deployment. Litmus is new.

---

*Sources: [OpenHTF GitHub](https://github.com/google/openhtf), [OpenHTF Missing Tutorial](https://www.frdmtoplay.com/openhtf-the-missing-tutorial/), [NI TestStand 2025 Q3](https://forums.ni.com/t5/NI-TestStand/Announcing-TestStand-2025-Q3/td-p/4447022), [OpenTAP](https://opentap.io/), [htf HILSTER](https://docs.hilster.io/htf/latest/), [HardPy GitHub](https://github.com/everypinio/hardpy), [HardPy Docs](https://everypinio.github.io/hardpy/), [pytest-f3ts](https://www.fixturfab.com/articles/pytest-f3ts-hardware-testing), [pytest-f3ts Docs](https://docs.fixturfab.com/pytest-f3ts/), [TofuPilot](https://www.tofupilot.com/), [LAVA Forum](https://lavag.org/topic/22024-open-source-alternatives-to-teststand/), [awesome-hardware-test](https://github.com/sschaetz/awesome-hardware-test)*
