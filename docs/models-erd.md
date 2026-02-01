# Litmus Pydantic Models - Entity Relationship Diagram

This document shows the relationships between all Pydantic models in the Litmus codebase.

## Complete Models ERD

```mermaid
erDiagram
    %% ============================================
    %% CAPABILITIES MODULE (Shared Enums & Types)
    %% ============================================

    Direction {
        string INPUT
        string OUTPUT
        string BIDIR
    }

    Domain {
        string VOLTAGE
        string CURRENT
        string RESISTANCE
        string POWER
        string FREQUENCY
        string TIME
        string LOGIC
        string TEMPERATURE
    }

    SignalType {
        string DC
        string AC
        string PULSED
        string TRANSIENT
    }

    Comparator {
        string EQ
        string NE
        string LT_LE_GT_GE
        string GELE_etc
    }

    Capability {
        Direction direction
        Domain domain
        list signal_types
        InstrumentChannelSpec channels
        RangeSpec range
        AccuracySpec accuracy
    }

    InstrumentChannelSpec {
        int count
        int simultaneous
        string coupling
        string naming
    }

    %% ============================================
    %% PRODUCTS MODULE
    %% ============================================

    Product {
        string id PK
        string name
        string description
        string revision
        string datasheet
    }

    Pin {
        string name PK
        string net
        PinType type
        string description
    }

    SignalGroup {
        string protocol
        list signals
        dict parameters
    }

    Characteristic {
        Direction direction
        Domain domain
        list signal_types
        string units
        list pins_refs FK
        string datasheet_ref
    }

    ConditionPoint {
        float nominal
        float tolerance_pct
        float limit_low
        float limit_high
        Comparator comparator
    }

    TestRequirement {
        string characteristic_ref FK
        dict conditions
        float guardband_pct
        string priority
    }

    %% ============================================
    %% CONFIG MODULE - Station & Instruments
    %% ============================================

    StationType {
        string id PK
        string description
        dict instruments
        list capabilities
    }

    InstrumentConfig {
        string type
        string driver
        string resource
        dict settings
    }

    StationInstance {
        string id PK
        string station_type FK
        string location
        string active_fixture FK
    }

    InstrumentInstance {
        string type
        string resource
        string model
        list capabilities
        int channels
    }

    %% ============================================
    %% CONFIG MODULE - Fixtures
    %% ============================================

    FixtureConfig {
        string id PK
        string name
        string product_id FK
        string product_family
        string product_revision
    }

    FixturePoint {
        string name PK
        string dut_pin FK
        string net
        string instrument FK
        string instrument_channel
    }

    %% ============================================
    %% CONFIG MODULE - Test Configuration
    %% ============================================

    TestSequenceConfig {
        string id PK
        string name
        string product_family FK
        string test_phase
        string required_fixture FK
        string required_station_type FK
        int timeout_seconds
    }

    TestStepConfig {
        string id PK
        string test
        string sequence
        string measurement_name
        Limit limit
        string limit_ref FK
        RetryConfig retry
    }

    TestConfig {
        string description
        VectorConfig vectors
        RetryConfig retry
        dict limits
    }

    VectorConfig {
        string expand
        list loops
    }

    RetryConfig {
        int max_attempts
        float delay_seconds
        string strategy
        string dialog_ref FK
    }

    DialogConfig {
        string id PK
        string message
        string dialog_type
        list choices
        int timeout_seconds
    }

    Limit {
        float low
        float high
        float nominal
        string units
        string spec_ref FK
        Comparator comparator
    }

    Specification {
        string id PK
        string description
        float nominal
        float tolerance_pct
        string units
    }

    %% ============================================
    %% DATA MODULE - Test Results
    %% ============================================

    TestRun {
        uuid id PK
        datetime started_at
        datetime ended_at
        DUT dut
        string station_id FK
        string test_sequence_id FK
        Outcome outcome
    }

    DUT {
        string serial PK
        string part_number
        string revision
        string lot_number
    }

    TestStep {
        uuid id PK
        string name
        datetime started_at
        Outcome outcome
        string error_message
    }

    TestVector {
        uuid id PK
        string test_step_id FK
        int index
        dict params
        int attempt
        Outcome outcome
    }

    Measurement {
        string name
        float value
        string units
        float low_limit
        float high_limit
        Outcome outcome
        string dut_pin
        string instrument_channel
    }

    Outcome {
        string PASS
        string FAIL
        string SKIP
        string ERROR
        string ABORTED
    }

    %% ============================================
    %% DIALOGS MODULE
    %% ============================================

    Dialog {
        uuid id PK
        DialogType type
        string title
        string message
        string run_id FK
        int timeout_seconds
    }

    DialogResponse {
        uuid dialog_id FK
        bool confirmed
        int choice
        string value
        bool timed_out
        bool cancelled
    }

    %% ============================================
    %% RELATIONSHIPS
    %% ============================================

    %% Product structure
    Product ||--o{ Pin : "has"
    Product ||--o{ SignalGroup : "has"
    Product ||--o{ Characteristic : "has"
    Product ||--o{ TestRequirement : "has"
    Characteristic ||--o{ ConditionPoint : "at"
    Characteristic }o--o{ Pin : "applies to"
    TestRequirement }o--|| Characteristic : "tests"

    %% Capability relationships
    Capability }o--|| Direction : "has"
    Capability }o--|| Domain : "has"
    Capability ||--o| InstrumentChannelSpec : "has"
    Characteristic }o--|| Direction : "has"
    Characteristic }o--|| Domain : "has"

    %% Station structure
    StationType ||--o{ InstrumentConfig : "requires"
    StationInstance }o--|| StationType : "based on"
    StationInstance ||--o{ InstrumentInstance : "has"
    InstrumentInstance ||--o{ Capability : "provides"

    %% Fixture structure
    FixtureConfig }o--o| Product : "for"
    FixtureConfig ||--o{ FixturePoint : "has"
    FixturePoint }o--o| Pin : "connects"
    FixturePoint }o--|| InstrumentInstance : "routes to"
    StationInstance }o--o| FixtureConfig : "uses"

    %% Test configuration
    TestSequenceConfig ||--o{ TestStepConfig : "contains"
    TestSequenceConfig ||--o{ DialogConfig : "defines"
    TestSequenceConfig }o--o| FixtureConfig : "requires"
    TestSequenceConfig }o--o| StationType : "requires"
    TestStepConfig ||--o| TestConfig : "has"
    TestStepConfig }o--o| Limit : "has"
    TestStepConfig }o--o| RetryConfig : "has"
    TestConfig ||--o| VectorConfig : "has"
    TestConfig ||--o{ Limit : "has limits"
    Limit }o--o| Specification : "from"

    %% Test execution results
    TestRun ||--|| DUT : "tests"
    TestRun }o--|| StationInstance : "on"
    TestRun }o--|| TestSequenceConfig : "runs"
    TestRun ||--o{ TestStep : "contains"
    TestStep ||--o{ TestVector : "contains"
    TestVector ||--o{ Measurement : "produces"
    Measurement }o--|| Outcome : "has"
    TestVector }o--|| Outcome : "has"
    TestStep }o--|| Outcome : "has"
    TestRun }o--|| Outcome : "has"

    %% Dialog relationships
    Dialog }o--o| TestRun : "for"
    DialogResponse }o--|| Dialog : "responds to"
```

## Module Organization

| Module | Purpose | Key Models |
|--------|---------|------------|
| `litmus/capabilities/models.py` | Shared enums & capability specs | Direction, Domain, SignalType, Capability |
| `litmus/products/models.py` | Product specifications | Product, Pin, Characteristic, ConditionPoint |
| `litmus/config/models.py` | Configuration definitions | StationType, FixtureConfig, TestSequenceConfig, Limit |
| `litmus/data/models.py` | Test execution results | TestRun, TestStep, TestVector, Measurement |
| `litmus/dialogs/models.py` | Operator dialogs | Dialog, DialogResponse |

## Type vs Instance Models

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            TYPES (Definitions)                               │
│                        What CAN be done / What EXISTS                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │   Product   │  │ StationType │  │ FixtureConf │  │TestSequence │        │
│  │   ───────   │  │   ───────   │  │   ───────   │  │   Config    │        │
│  │ id: str     │  │ id: str     │  │ id: str     │  │ id: str     │        │
│  │ revision    │  │ instruments │  │ product_id  │  │ steps       │        │
│  │ pins        │  │ capabilities│  │ points      │  │ required_*  │        │
│  │ charact.    │  │             │  │             │  │             │        │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │
│        │                │                │                │                 │
│        │                │                │                │                 │
│        ▼                ▼                ▼                ▼                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                          INSTANCES (Runtime)                                 │
│                      What IS happening / What EXISTS NOW                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │     DUT     │  │  Station    │  │  (Fixture   │  │   TestRun   │        │
│  │   ───────   │  │  Instance   │  │  Instance)  │  │   ───────   │        │
│  │ serial: str │  │ id: str     │  │   active    │  │ id: uuid    │        │
│  │ part_number │  │ station_type│  │   on the    │  │ dut         │        │
│  │ revision    │  │ instruments │  │   station   │  │ station_id  │        │
│  │             │  │ location    │  │             │  │ outcome     │        │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │
│                                                                             │
│        Specific         Physical          Currently         One test        │
│        device           bench with        installed         execution       │
│        being            real              fixture           with results    │
│        tested           instruments                                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          SPEC → RUNTIME FLOW                                 │
└─────────────────────────────────────────────────────────────────────────────┘

    Product.yaml                    Station.yaml              Fixture.yaml
    ────────────                    ────────────              ────────────
    ┌──────────┐                   ┌──────────────┐          ┌──────────────┐
    │ Product  │                   │StationInstance│          │ FixtureConfig│
    │ ─ pins   │◄──────────────────│ ─ instruments │◄─────────│ ─ points     │
    │ ─ chars  │      maps to      │ ─ location   │  routes   │ ─ product_id │
    └──────────┘                   └──────────────┘          └──────────────┘
         │                               │                          │
         │ defines                       │ has                      │ connects
         ▼                               ▼                          ▼
    ┌──────────┐                   ┌──────────────┐          ┌──────────────┐
    │Charactr- │                   │InstrumentInst│          │ FixturePoint │
    │ istics   │───────────────────│ ─ type       │◄─────────│ ─ dut_pin    │
    │ ─ limits │  requires caps    │ ─ resource   │  maps to │ ─ instrument │
    └──────────┘                   └──────────────┘          └──────────────┘
         │
         │ to_capability_requirement()
         ▼
    ┌──────────┐                                    TEST EXECUTION
    │Capability│                                    ──────────────
    │ ─ domain │                                   ┌──────────────┐
    │ ─ direct │                                   │   TestRun    │
    └──────────┘                                   │ ─ id: uuid   │
                                                   │ ─ dut        │
                                                   │ ─ outcome    │
    TestSequence.yaml                              └──────┬───────┘
    ─────────────────                                     │
    ┌──────────────────┐                                  │ contains
    │TestSequenceConfig│                                  ▼
    │ ─ steps          │────────────────────────►   ┌──────────────┐
    │ ─ required_*     │     executes as            │  TestStep    │
    └──────────────────┘                            │ ─ vectors    │
         │                                          │ ─ outcome    │
         │ contains                                 └──────┬───────┘
         ▼                                                 │
    ┌──────────────────┐                                   │ contains
    │  TestStepConfig  │                                   ▼
    │ ─ test           │                            ┌──────────────┐
    │ ─ limit          │                            │  TestVector  │
    └──────────────────┘                            │ ─ params     │
         │                                          │ ─ measurements│
         │ has                                      └──────┬───────┘
         ▼                                                 │
    ┌──────────────────┐                                   │ produces
    │      Limit       │                                   ▼
    │ ─ low/high       │────────────────────────►   ┌──────────────┐
    │ ─ units          │     checks against         │ Measurement  │
    └──────────────────┘                            │ ─ value      │
                                                    │ ─ outcome    │
                                                    └──────────────┘
```

## Capability Matching

The system uses capability matching to ensure stations can test products:

```python
# Product defines what it needs tested
product.characteristics["output_voltage"]
    → direction: OUTPUT (DUT provides voltage)
    → domain: VOLTAGE
    → signal_types: [DC]

# Characteristic converts to capability requirement
cap_req = char.to_capability_requirement()
    → direction: INPUT (flip! instrument must MEASURE)
    → domain: VOLTAGE
    → signal_types: [DC]

# Station instruments provide capabilities
station.instruments["dmm_main"]
    → capabilities: [
        Capability(direction=INPUT, domain=VOLTAGE, signal_types=[DC])
      ]

# Match: DMM can measure DC voltage ✓
```
