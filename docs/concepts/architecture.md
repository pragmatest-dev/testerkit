# Litmus Architecture

## How the Framework Works

> **Vocabulary primer.** This page drops a lot of names into one diagram. If you haven't seen them yet: **[product](products.md)** and **[station](stations.md)** are YAML definitions; **[sidecar](../reference/configuration.md)** is the per-test YAML carrying limits / sweeps / mocks; **`verify` / `context` / `logger`** are three of the 20 pytest fixtures Litmus adds — the common per-test entry points (see [reference/litmus-fixtures](../reference/litmus-fixtures.md)); **[characteristic](capabilities.md)** is a measurable property on a product; **[capability](capabilities.md)** is what an instrument can do.

```mermaid
flowchart LR
    subgraph Inputs
        P[Product spec<br/>products/*.yaml<br/>pins, chars, bands]
        S[Station YAML<br/>stations/*.yaml<br/>instruments, resources]
        SC[Sidecar YAML<br/>tests/test_*.yaml<br/>limits, sweeps, mocks, retry, prompts]
        T[Test code<br/>tests/test_*.py<br/>verify / context / logger]
    end

    subgraph Plugin[Litmus pytest plugin]
        L[Load specs] --> EX[Expand vectors]
        EX --> RUN[Run test code]
        RUN --> CHK[Check limits]
    end

    P --> Plugin
    S --> Plugin
    SC --> Plugin
    T --> Plugin

    Plugin --> O[results/*.parquet<br/>+ event log]
    O --> A[CLI / UI / Python API / MCP tools]
```

## Key Concepts

| Concept | What It Is | Example |
|---------|-----------|---------|
| **[Product](products.md)** | Spec defining what you're testing | TPS54302 DC-DC converter |
| **[Characteristic](capabilities.md)** | Measurable property of product | output_voltage: 3.3V ±5% |
| **[Station](stations.md)** | Physical test bench with instruments | Bench 1 with DMM, PSU, ELoad |
| **[Capability](capabilities.md)** | What an instrument can do | DMM: measure DC voltage |
| **[Sidecar](../reference/configuration.md)** | YAML alongside a test file declaring limits, sweeps, mocks, retry, prompts | `tests/test_power.yaml` |
| **[TestRun](../reference/models.md)** | One execution of a test file | Run abc123 on SN001 |
| **Measurement** | Single data point with pass/fail | VOUT = 3.31V PASS |

## System Overview

```mermaid
flowchart LR
    subgraph Definitions["DEFINITIONS (YAML)"]
        PS["Product spec<br/>products/*.yaml"]
        ST["Station type<br/>stations/*.yaml"]
        TC["Test code + sidecar<br/>tests/test_*.py + .yaml"]
    end

    subgraph Runtime["RUNTIME"]
        DUT["DUT<br/>(serial)"]
        SI["Station instance"]
        TR["Test run"]
    end

    subgraph Storage["STORAGE"]
        TRR["TestRun results"]
        MD["Measurement data"]
    end

    PS -- "instantiated as" --> DUT
    DUT -- "tested in" --> TRR
    ST -- "deployed as" --> SI
    SI -- "produces" --> MD
    TC -- "executed as" --> TR
    TR --> TRR
    TR --> MD
```

## Entity Relationships

The platform's data model splits cleanly into three concerns: **what you're testing** (products and their specs), **how you test it** (stations, fixtures, capabilities), and **what gets executed and recorded** (sidecar configuration and runs). Each diagram below covers one concern. For the full per-model schema with every field, see [reference/models](../reference/models.md) and [reference/catalog-schema](../reference/catalog-schema.md). Click any diagram to expand.

### 1. Products & Specs

What the DUT is, what its measurable characteristics are, and how spec bands attach.

```mermaid
erDiagram
    Product {
        id string PK
        name string
        revision string
        description string
    }
    Pin {
        name string PK
        net string
        role string
        description string
    }
    Characteristic {
        name string PK
        direction enum
        function enum
        units string
        signals dict
        conditions dict
        controls dict
        attributes dict
    }
    SpecBand {
        when dict
        value float
        accuracy AccuracySpec
        resolution ResolutionSpec
    }

    Product ||--o{ Pin : "pins[]"
    Product ||--o{ Characteristic : "characteristics[]"
    Characteristic ||--o{ SpecBand : "bands[]"
```

### 2. Stations, Fixtures & Capability Matching

The bench side: physical stations, the instruments they hold, the capabilities those instruments expose, and the optional fixture layer that routes instrument channels to DUT pins.

```mermaid
erDiagram
    StationType {
        id string PK
        description string
    }
    Station {
        id string PK
        station_type string FK
        location string
    }
    StationInstrumentConfig {
        type string
        driver string
        resource string
        catalog_ref string
        mock bool
        channels dict
        mock_config dict
    }
    Capability {
        function enum
        direction enum
        signals dict
        conditions dict
        controls dict
        attributes dict
    }
    Fixture {
        id string PK
        product_id string FK
    }
    FixtureConnection {
        name string PK
        instrument string FK
        instrument_channel string
        instrument_terminal string
        dut_pin string FK
        net string
        function string
        route SwitchRoute
    }
    Characteristic {
        name string PK
        direction enum
        function enum
    }
    Pin {
        name string PK
        net string
        role string
    }

    StationType ||--o{ Station : "deployed as"
    Station ||--o{ StationInstrumentConfig : "instruments{}"
    StationInstrumentConfig ||--o{ Capability : "capabilities[]"
    Fixture ||--o{ FixtureConnection : "connections[]"
    FixtureConnection }o--|| Pin : "dut_pin →"
    FixtureConnection }o--|| StationInstrumentConfig : "instrument →"
    Characteristic ||--|| Capability : "matches (direction-flipped)"
```

### 3. Test Configuration & Execution

The sidecar YAML tree on the left, the runtime objects it produces on the right. `TestEntry` is a recursive node — file-scope, class-scope, method-scope all share the same shape; the recursion is described in the field list rather than drawn as a self-edge (Mermaid routes self-edges through neighbouring entities and the line reads as a phantom relationship).

```mermaid
erDiagram
    SidecarConfig {
        limits dict
        sweeps list
        mocks list
        characteristics list
        connections any
        retry RetryConfig
        prompts dict
        tests dict
    }
    TestEntry {
        limits dict
        sweeps list
        mocks list
        characteristics list
        connections any
        retry RetryConfig
        prompts dict
        runner string
        tests dict
    }
    DUT {
        serial string PK
        part_number string
    }
    TestRun {
        id uuid PK
        started_at datetime
        dut_serial string FK
        station_id string FK
        outcome enum
    }
    TestVector {
        id uuid PK
        index int
        params dict
        outcome enum
    }
    Measurement {
        name string
        value float
        units string
        outcome enum
    }
    Product {
        id string PK
    }
    Station {
        id string PK
    }

    SidecarConfig ||--o{ TestEntry : "tests{}"
    DUT }o--|| Product : "instance of"
    TestRun }o--|| DUT : "for DUT"
    TestRun }o--|| Station : "on station"
    TestRun ||--o{ TestVector : "vectors[]"
    TestVector ||--o{ Measurement : "measurements[]"
```

## Type vs Instance

| Concept | Type (YAML Definition) | Instance (Runtime) |
|---------|------------------------|-------------------|
| What to test | `Product` | `DUT` |
| Where to test | `StationType` | `StationConfig` |
| What to run | `SidecarConfig` (file scope) + pytest collection | `TestRun` |
| Single iteration | `TestEntry` (per-method scope) | `TestVector` |
| Expected value | `Limit` / `SpecBand` | `Measurement` |

## Core Flows

### 1. Spec → Config → Test Flow

**Limits can come from three places** — product spec, sidecar override, or inline in the test:

```mermaid
flowchart LR
    A["Product spec<br/>products/*.yaml<br/>characteristic.bands"]
    B["Sidecar override<br/>tests/test_*.yaml<br/>limits: {name: {...}}"]
    C["Inline limit<br/>logger.measure(name, v, limit=Limit(...))"]
    R["Limit resolution<br/>(per measurement)"]
    A --> R
    B --> R
    C --> R
```

Product-spec bands derive a production limit by applying any configured guardband (tightening the spec for manufacturing margin). For example: `3.3V ± 5%` (3.135–3.465) with a 10% guardband becomes `3.152–3.449`.

**Full flow with conditions:**

```mermaid
flowchart LR
    PS["Product spec<br/>products/tps54302.yaml<br/>characteristics.output_voltage.bands<br/>(N bands keyed by when:)"]
    SC["Sidecar<br/>tests/test_*.yaml<br/>sweeps: [{temp:[25,85], load:[.5,3]}]<br/>characteristics: [output_voltage]"]
    TC["Test code<br/>tests/test_*.py<br/>verify('output_voltage', dmm.measure())"]

    subgraph Runtime["Runtime (per vector)"]
        V["Vector params<br/>{temp:25, load:0.5} ..."]
        CR["Resolve limit<br/>spec.get_limit(name, when={temp, load})"]
        VR["verify / logger.measure<br/>checks + records measurement row"]
    end

    PS -- "matched per vector" --> CR
    SC -- "drives sweep" --> V
    TC -- "calls verify" --> VR
    V --> CR
    CR --> VR
    VR --> M["Measurement row<br/>(parquet)"]
```

### 2. Capability Matching

```mermaid
flowchart LR
    PC["Product characteristic<br/>direction: OUTPUT<br/>function: dc_voltage<br/>(DUT outputs voltage)"]
    REQ["Required capability<br/>direction: INPUT<br/>function: dc_voltage<br/>(need to measure)"]
    SI["Station instrument<br/>provides: INPUT<br/>function: dc_voltage<br/>(DMM can measure)"]
    PC -- "direction-flip" --> REQ
    REQ -- "matches" --> SI
```

### 3. Test Execution

```mermaid
flowchart LR
    SC["SidecarConfig + test code<br/>(definition)"]
    TR["TestRun<br/>(instance)"]
    TV["TestVector<br/>(iteration)"]
    M["Measurement<br/>(data point)"]
    PQ["Parquet<br/>(storage)"]
    SC --> TR --> TV --> M --> PQ
```

## File Locations

| Entity | Location |
|--------|----------|
| Product specs | `products/*.yaml` |
| Station configs | `stations/*.yaml` |
| Test code | `tests/test_*.py` |
| Test sidecars | `tests/test_*.yaml` |
| Fixtures | `fixtures/*.yaml` |
| Instrument catalog | `catalog/**/*.yaml` |
| Test results (Parquet) | `<data_dir>/runs/{date}/*.parquet` |
| Event logs (Arrow IPC) | `<data_dir>/events/{date}/{session_id}.arrow` |
| Channel data (Arrow IPC) | `<data_dir>/channels/{date}/{channel}_{session}.arrow` |

## Data Architecture

The storage layer uses three complementary stores:

| Store | Purpose | Format |
|-------|---------|--------|
| **EventStore** | All test activity as typed events | Arrow IPC + DuckDB via Flight |
| **ChannelStore** | Time-series instrument data | Arrow IPC segments |
| **ParquetBackend** | Denormalized test results | Parquet files |

Events are the source of truth. Parquet files are a materialized view produced by `materialize_run_to_parquet()`, called from the runs daemon on `RunEnded`. See [Three Stores Architecture](three-stores.md) and [Event Log Architecture](event-log.md) for details.
