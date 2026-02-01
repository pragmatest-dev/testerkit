# Litmus Architecture

## How the Framework Works

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TEST EXECUTION FLOW                                │
└─────────────────────────────────────────────────────────────────────────────┘

  1. SPEC                 2. CONFIG                3. CODE               4. RUN
  ───────                 ────────                 ──────                ──────

  products/*/spec.yaml            tests/config.yaml        tests/test_*.py       pytest
  ┌───────────┐          ┌────────────┐           ┌────────────┐       ┌───────┐
  │ Product   │          │ vectors    │           │ @litmus_   │       │ CLI   │
  │ - pins    │          │ - sweep    │           │   test     │       │  or   │
  │ - chars   │          │ - params   │           │            │       │  UI   │
  │ - limits  │          │ limits     │           │ measure()  │       │       │
  └───────────┘          │ - per-test │           │ return val │       └───────┘
                         │ retry      │           └────────────┘
  stations/*.yaml        │ - attempts │
  ┌───────────┐          │ dialogs    │
  │ Station   │          │ - prompts  │
  │ - instrs  │          └────────────┘
  │ - resource│
  └───────────┘

        │                      │                        │                  │
        ▼                      ▼                        ▼                  ▼
  ┌───────────────────────────────────────────────────────────────────────────┐
  │                      LITMUS PYTEST PLUGIN                                  │
  │                                                                           │
  │   Loads specs ──► Expands vectors ──► Runs test code ──► Checks limits   │
  │                                                                           │
  └───────────────────────────────────────────────────────────────────────────┘
                                         │
                                         ▼
                               5. STORE & ANALYZE
                               ─────────────────
                               ┌─────────────────┐
                               │ results/*.parq  │
                               │ litmus CLI      │
                               │ Python API      │
                               │ MCP tools       │
                               └─────────────────┘
```

## Key Concepts

| Concept | What It Is | Example |
|---------|-----------|---------|
| **Product** | Spec defining what you're testing | TPS54302 DC-DC converter |
| **Characteristic** | Measurable property of product | output_voltage: 3.3V ±5% |
| **Station** | Physical test bench with instruments | Bench 1 with DMM, PSU, ELoad |
| **Capability** | What an instrument can do | DMM: measure DC voltage |
| **TestSequence** | Ordered list of test steps | production_test.yaml |
| **TestRun** | One execution of a sequence | Run abc123 on SN001 |
| **Measurement** | Single data point with pass/fail | VOUT = 3.31V PASS |

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              LITMUS PLATFORM                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   DEFINITIONS (YAML)              RUNTIME                    STORAGE        │
│   ──────────────────              ───────                    ───────        │
│                                                                             │
│   ┌──────────┐                   ┌──────────┐              ┌──────────┐    │
│   │ Product  │──────────────────►│   DUT    │──────────────►│ TestRun  │    │
│   │  Spec    │  instantiated as  │ (serial) │   tested in  │ Results  │    │
│   └──────────┘                   └──────────┘              └──────────┘    │
│                                                                             │
│   ┌──────────┐                   ┌──────────┐              ┌──────────┐    │
│   │ Station  │──────────────────►│ Station  │──────────────►│Measuremt │    │
│   │  Type    │  deployed as      │ Instance │   produces   │  Data    │    │
│   └──────────┘                   └──────────┘              └──────────┘    │
│                                                                             │
│   ┌──────────┐                   ┌──────────┐                              │
│   │ Test     │──────────────────►│ Test     │                              │
│   │ Sequence │  executed as      │  Run     │                              │
│   └──────────┘                   └──────────┘                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## ERD Diagram

```mermaid
erDiagram
    %% ============================================
    %% PRODUCT DEFINITIONS
    %% ============================================

    Product {
        string id PK
        string name
        string revision
        string description
    }

    Pin {
        string name PK
        string net
        string type
    }

    Characteristic {
        string name PK
        enum direction
        enum domain
        list signal_types
        string units
    }

    Condition {
        float nominal
        float tolerance_pct
        float min
        float max
    }

    TestRequirement {
        string name PK
        string characteristic_ref FK
        float guardband_pct
    }

    %% ============================================
    %% STATION & INSTRUMENTS
    %% ============================================

    StationType {
        string id PK
        string description
    }

    Station {
        string id PK
        string station_type FK
        string location
    }

    Instrument {
        string name PK
        string type
        string resource
        bool simulate
    }

    Capability {
        enum direction
        enum domain
        list signal_types
    }

    %% ============================================
    %% TEST CONFIGURATION
    %% ============================================

    TestSequence {
        string id PK
        string name
        string product_family FK
    }

    TestStep {
        string name PK
        string test_file
        Limit limit
    }

    Limit {
        float low
        float high
        string units
    }

    %% ============================================
    %% FIXTURES (Optional Bridge)
    %% ============================================

    Fixture {
        string id PK
        string product_id FK
    }

    FixturePoint {
        string name PK
        string dut_pin FK
        string instrument FK
    }

    %% ============================================
    %% TEST EXECUTION (Runtime)
    %% ============================================

    DUT {
        string serial PK
        string part_number
    }

    TestRun {
        uuid id PK
        datetime started_at
        string dut_serial FK
        string station_id FK
        enum outcome
    }

    TestVector {
        uuid id PK
        int index
        dict params
        enum outcome
    }

    Measurement {
        string name
        float value
        string units
        enum outcome
    }

    %% ============================================
    %% RELATIONSHIPS
    %% ============================================

    %% Product structure
    Product ||--o{ Pin : has
    Product ||--o{ Characteristic : has
    Product ||--o{ TestRequirement : has
    Characteristic ||--o{ Condition : "at conditions"
    TestRequirement }o--|| Characteristic : tests

    %% Station structure
    StationType ||--o{ Station : "deployed as"
    Station ||--o{ Instrument : has
    Instrument ||--o{ Capability : provides

    %% Test configuration
    TestSequence ||--o{ TestStep : contains
    TestSequence }o--|| Product : "for"
    TestStep ||--o| Limit : has

    %% Fixture (optional)
    Fixture }o--|| Product : "for"
    Fixture ||--o{ FixturePoint : has
    FixturePoint }o--|| Pin : connects
    FixturePoint }o--|| Instrument : "routes to"

    %% Capability matching
    Characteristic ||--|| Capability : "requires (direction flipped)"

    %% Test execution
    DUT }o--|| Product : "instance of"
    TestRun }o--|| DUT : tests
    TestRun }o--|| Station : "on"
    TestRun }o--|| TestSequence : runs
    TestRun ||--o{ TestVector : contains
    TestVector ||--o{ Measurement : produces
```

## Type vs Instance

| Concept | Type (YAML Definition) | Instance (Runtime) |
|---------|------------------------|-------------------|
| What to test | `Product` | `DUT` |
| Where to test | `StationType` | `Station` |
| What to run | `TestSequence` | `TestRun` |
| Single iteration | `TestStep` | `TestVector` |
| Expected value | `Limit` / `Condition` | `Measurement` |

## Core Flows

### 1. Spec → Config → Test Flow

**Limits can come from three places:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        WHERE DO LIMITS COME FROM?                            │
└─────────────────────────────────────────────────────────────────────────────┘

  OPTION A: Product Spec                OPTION B: Test Config        OPTION C: Inline
  (Derived from datasheet)              (Per-test overrides)         (In test code)

  products/product/spec.yaml                    tests/config.yaml            test_*.py
  ┌─────────────────────┐              ┌─────────────────────┐      ┌─────────────┐
  │ characteristics:    │              │ test_output:        │      │ harness.    │
  │   output_voltage:   │              │   limits:           │      │   measure(  │
  │     conditions:     │              │     output_voltage: │      │     "vout", │
  │       - nominal: 3.3│              │       low: 3.2      │      │     value,  │
  │         tolerance: 5%              │       high: 3.4     │      │     low=3.2,│
  │         temp: 25    │              │       units: V      │      │     high=3.4│
  │         load: 1.0   │              └─────────────────────┘      │   )         │
  │                     │                                           └─────────────┘
  │ test_requirements:  │
  │   verify_output:    │
  │     characteristic: │
  │       output_voltage│
  │     guardband: 10%  │◄─── Tightens limits for manufacturing margin
  └─────────────────────┘

        │
        │  Spec + guardband = production limit
        ▼
  ┌─────────────────────┐
  │ 3.3V ± 5% = 3.135   │
  │ With 10% guardband: │
  │   3.135 + 0.0165    │
  │   3.465 - 0.0165    │
  │ = 3.152 to 3.449    │
  └─────────────────────┘
```

**Full flow with conditions:**

```
Product Spec (YAML)              Test Config (YAML)           Test Code (Python)
────────────────────             ──────────────────           ──────────────────

products/tps54302/spec.yaml              tests/config.yaml            tests/test_*.py
┌────────────────────┐           ┌────────────────────┐       ┌────────────────┐
│ characteristics:   │           │ test_output:       │       │ @litmus_test   │
│   output_voltage:  │           │   vectors:         │       │ def test_output│
│     conditions:    │           │     expand: product│       │  (context, dmm):│
│       - temp: 25   │──────────►│     temp: [25, 85] │──────►│                │
│         load: 0.5  │  lookup   │     load: [0.5,3.0]│ sweep │  # context has  │
│         nominal:3.3│  limit    │                    │       │  # temp & load │
│         tol: 1%    │  for      │   limits:          │       │                │
│       - temp: 25   │  condition│     ref: specs.    │       │  return dmm.   │
│         load: 3.0  │           │       output_volt  │       │    measure()   │
│         nominal:3.3│           │     guardband: 10% │       └────────────────┘
│         tol: 1%    │           └────────────────────┘
│       - temp: 85   │
│         load: 1.0  │
│         nominal:3.3│
│         tol: 2%    │◄─── Different tolerance at high temp
└────────────────────┘

        │                              │                             │
        │ conditions define            │ vectors sweep               │ code runs
        │ valid operating points       │ across conditions           │ per vector
        ▼                              ▼                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         RUNTIME (TestHarness)                                │
│                                                                             │
│   for vector in vectors:  # {temp:25, load:0.5}, {temp:25, load:3.0}, ...  │
│       limit = spec.get_limit("output_voltage", **vector.params)             │
│       value = test_func(vector, dmm)                                        │
│       result = check(value, limit)  # PASS/FAIL                            │
│       store(Measurement(value, limit, result))                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2. Capability Matching
```
Product.characteristic      →  Required Capability  →  Station.instrument
direction: OUTPUT              direction: INPUT        provides: INPUT
domain: VOLTAGE                domain: VOLTAGE         domain: VOLTAGE
(DUT outputs voltage)          (need to measure)       (DMM can measure)
```

### 3. Test Execution
```
TestSequence  →  TestRun   →  TestVector  →  Measurement  →  Parquet
(definition)     (instance)    (iteration)    (data point)    (storage)
```

## File Locations

| Entity | Location |
|--------|----------|
| Product specs | `products/*/spec.yaml` or `products/*/spec.yaml` |
| Station configs | `stations/*.yaml` |
| Test sequences | `sequences/*.yaml` |
| Fixtures | `fixtures/*.yaml` |
| Instrument library | `litmus/instruments/library/*.yaml` |
| Test results | `results/**/*.parquet` |
