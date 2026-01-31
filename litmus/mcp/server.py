"""MCP server for AI-assisted test generation workflows.

This server exposes 5 tools:
- litmus: Unified CRUD operations (init, list, get, save, read)
- litmus_discover: Scan for VISA instruments
- litmus_match: Check compatibility between products/stations/fixtures
- litmus_run: Execute tests and return results
- litmus_open: Get URL to view/edit in browser

The platform does NOT call LLMs - it exposes these tools so that AI agents
(Claude Code, etc.) can orchestrate the full datasheet-to-test workflow.
"""

from typing import Any

from fastmcp import FastMCP

from litmus.mcp.tools import (
    discover_tool,
    litmus_tool,
    match_tool,
    open_tool,
    run_tool,
)


def create_mcp_server() -> FastMCP:
    """Create and configure the Litmus MCP server."""
    mcp = FastMCP(
        "Litmus",
        instructions="""Litmus is a hardware test platform. The UI and tools are driven by
project folders. Understanding these folders is essential.

## Project Folders (UI-Driven)

Each folder has a corresponding UI page. Create the appropriate folder/files to populate the UI.

```
my-project/
├── products/                    # WHAT you're testing
│   └── {product_id}/
│       └── spec.yaml            # Product specification
├── stations/                    # WHERE you test (instruments + addresses)
│   └── {station_id}.yaml        # Station configuration
├── fixtures/                    # HOW pins connect to instruments
│   └── {fixture_id}.yaml        # Pin-to-channel mappings
├── instruments/                 # Custom instrument drivers
│   └── {instrument_id}.yaml     # YAML driver definition
│   └── {instrument_id}.py       # Python driver (optional)
├── sequences/                   # Test execution order
│   └── {sequence_id}.yaml       # Ordered list of tests
├── tests/                       # Test code + configuration
│   ├── test_{product_id}.py     # Test functions
│   ├── config.yaml              # CONDITIONS (vectors) + LIMITS
│   └── conftest.py              # pytest fixtures
└── results/                     # Output (gitignored)
    └── measurements/            # Parquet files
```

## Folder Details

### products/ - Product Specifications
**Purpose:** Define WHAT you're testing - electrical characteristics, limits, test conditions.
**UI Page:** /products
**File Pattern:** `products/{product_id}/spec.yaml`

```yaml
# products/power_board/spec.yaml
product:
  id: power_board
  name: "5V to 3.3V Buck Converter"

characteristics:
  output_voltage:
    nominal: 3.3
    tolerance_pct: 5
    unit: V

test_conditions:
  default_vin: 5.0
  default_vout: 3.3
```

**CRUD Operations:**
- `litmus(action="list", type="product")` - List all products
- `litmus(action="get", type="product", id="power_board")` - Get spec details
- `litmus(action="save", type="product", id="power_board", content={...})` - Create/update

### stations/ - Test Stations
**Purpose:** Define WHERE you test - which instruments at which addresses.
**UI Page:** /stations
**File Pattern:** `stations/{station_id}.yaml`

```yaml
# stations/bench_001.yaml
station:
  id: bench_001
  name: "Main Test Bench"

instruments:
  psu:
    type: psu
    resource: "TCPIP::192.168.1.101::INSTR"
    simulate: true
    sim_config:
      voltage: 5.0
      current: 0.5
  dmm:
    type: dmm
    resource: "TCPIP::192.168.1.102::INSTR"
    simulate: true
    sim_config:
      voltage: 3.31
```

**CRUD Operations:**
- `litmus(action="list", type="station")` - List all stations
- `litmus(action="get", type="station", id="bench_001")` - Get station details
- `litmus(action="save", type="station", id="bench_001", content={...})` - Create/update
- `litmus_discover()` - Scan for connected VISA instruments

### fixtures/ - Pin Mappings
**Purpose:** Define HOW product pins connect to station instruments.
**UI Page:** /fixtures
**File Pattern:** `fixtures/{fixture_id}.yaml`

```yaml
# fixtures/power_board_fixture.yaml
fixture:
  id: power_board_fixture
  product: power_board

channels:
  VIN:
    instrument: psu
    channel: 1
    type: power
  VOUT:
    instrument: dmm
    channel: 1
    type: measure
  GND:
    instrument: psu
    channel: GND
    type: ground
```

**CRUD Operations:**
- `litmus(action="list", type="fixture")` - List all fixtures
- `litmus(action="get", type="fixture", id="power_board_fixture")` - Get fixture
- `litmus(action="save", type="fixture", id="power_board_fixture", content={...})`

### instruments/ - Custom Drivers
**Purpose:** Define custom instrument drivers for non-standard equipment.
**UI Page:** /instruments
**File Pattern:** `instruments/{instrument_id}.yaml` or `.py`

```yaml
# instruments/custom_dmm.yaml
instrument:
  id: custom_dmm
  name: "Custom Multimeter"
  type: dmm

capabilities:
  - domain: voltage
    direction: measure
    range: [0, 1000]
    resolution: 0.001

commands:
  measure_dc_voltage: "MEAS:VOLT:DC?"
  set_range: "VOLT:RANG {value}"
```

**CRUD Operations:**
- `litmus(action="list", type="instrument")` - List instrument library
- `litmus(action="get", type="instrument", id="custom_dmm")` - Get driver
- `litmus(action="save", type="instrument", id="custom_dmm", content={...})`
- `litmus(action="read", path="template:instrument")` - Get Python template
- `litmus(action="read", path="template:instrument_yaml")` - Get YAML template
- `litmus(action="read", path="template:capabilities")` - See capability interfaces

### sequences/ - Test Sequences
**Purpose:** Define test execution order and grouping.
**UI Page:** /sequences
**File Pattern:** `sequences/{sequence_id}.yaml`

```yaml
# sequences/full_validation.yaml
sequence:
  id: full_validation
  name: "Full Product Validation"
  product: power_board

steps:
  - test: test_output_voltage_no_load
    required: true
  - test: test_output_voltage_full_load
    required: true
  - test: test_efficiency
    required: false
```

**CRUD Operations:**
- `litmus(action="list", type="sequence")` - List sequences
- `litmus(action="get", type="sequence", id="full_validation")` - Get sequence
- `litmus(action="save", type="sequence", id="full_validation", content={...})`

### tests/ - Test Code
**Purpose:** Test functions + configuration (vectors and limits).
**Files:**
- `test_{product}.py` - Test functions using @litmus_test
- `config.yaml` - Test CONDITIONS (vectors) and LIMITS
- `conftest.py` - pytest fixture definitions

**CRUD Operations:**
- `litmus(action="list", type="test")` - List test files
- `litmus(action="read", path="template:test")` - Get test template
- `litmus(action="save", type="test", id="tests/test_x.py", content={"code": "..."})`

### results/ - Test Output
**Purpose:** Parquet files with test measurements (gitignored).
**UI Page:** /runs (via litmus_open)

**CRUD Operations:**
- `litmus(action="list", type="run")` - List test runs
- `litmus(action="get", type="run", id="{run_id}")` - Get run details

## Tools

1. **litmus** - Unified CRUD operations
   - `litmus(action="init", path="~/projects/my-project")` - Initialize project
   - `litmus(action="list", type="product|station|fixture|instrument|sequence|test|run")`
   - `litmus(action="get", type="...", id="...")`
   - `litmus(action="save", type="...", id="...", content={...})`
   - `litmus(action="read", path="...")`

2. **litmus_discover** - Scan for connected VISA instruments

3. **litmus_match** - Check compatibility
   - `litmus_match(product_id="x")` - Find compatible stations
   - `litmus_match(product_id="x", station_id="y")` - Detailed check

4. **litmus_run** - Execute tests
   - `litmus_run(test="tests/test_x.py", station="bench_001", serial="SN001")`

5. **litmus_open** - Get browser URL
   - `litmus_open(type="product|station|fixture|instrument|sequence|run", id="x")`

## Test Code Pattern

ALWAYS read the template first: `litmus(action="read", path="template:test")`

### Two Files Required

1. **test_xxx.py** - Test functions
2. **config.yaml** - CONDITIONS (vectors) + LIMITS

### Test Function Pattern

```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(vector, psu, dmm):
    # 1. Get conditions FROM VECTOR (not hardcoded!)
    vin = vector.get("vin", 5.0)  # From config.yaml vectors

    # 2. SET UP stimulus
    psu.set_voltage(vin)
    psu.enable_output()

    # 3. MEASURE and RETURN - framework checks limits
    return dmm.measure_dc_voltage()
```

### config.yaml Pattern

```yaml
test_output_voltage:
  vectors:
    - vin: 5.0  # Test condition from spec
  limits:
    test_output_voltage:
      low: 3.135   # 3.3V - 5% (from spec)
      high: 3.465  # 3.3V + 5% (from spec)
      nominal: 3.3
      units: V
```

### Key Rules

1. **NO HARDCODED VALUES** - Get everything from spec or config.yaml
2. **Tests SET UP conditions** - psu.set_voltage(), eload.set_current()
3. **Tests MEASURE results** - dmm.measure_dc_voltage()
4. **Tests RETURN values** - Framework checks limits from config.yaml
5. **Limits in config.yaml** - Derived from product spec.yaml

## Workflow

### Step 0: Initialize Project
```
litmus(action="init", path="~/projects/my-project")
```

### Step 1: Create Product Spec
```
litmus(action="save", type="product", id="power_board", content={
    "product": {"id": "power_board", "name": "5V to 3.3V Converter"},
    "characteristics": {"output_voltage": {"nominal": 3.3, "tolerance_pct": 5}},
    "test_conditions": {"default_vin": 5.0, "default_vout": 3.3}
})
```

### Step 2: Create Station
```
litmus(action="save", type="station", id="bench_001", content={
    "station": {"id": "bench_001", "name": "Test Bench"},
    "instruments": {
        "psu": {"type": "psu", "resource": "TCPIP::192.168.1.101::INSTR", "simulate": true},
        "dmm": {"type": "dmm", "resource": "TCPIP::192.168.1.102::INSTR", "simulate": true}
    }
})
```

### Step 3: Generate Tests
```
litmus(action="read", path="template:test")  # See the pattern
litmus(action="get", type="product", id="power_board")  # Get spec values

# Save test file
litmus(action="save", type="test", id="tests/test_power_board.py", content={"code": "..."})

# Save config with vectors + limits
litmus(action="save", type="test", id="tests/config.yaml", content={"code": "..."})
```

### Step 4: Run Tests
```
litmus_run(test="tests/test_power_board.py", station="bench_001", serial="SN001")
```

## Checklist

Before generating test code:
☐ Read product spec: `litmus(action="get", type="product", id="...")`
☐ Read template: `litmus(action="read", path="template:test")`
☐ Test uses `vector.get()` for conditions (not hardcoded)
☐ Test uses `@litmus_test` decorator
☐ Test RETURNS measurement value
☐ config.yaml has vectors AND limits
☐ Limits derived from spec (with optional guardband)
""",
    )

    # -------------------------------------------------------------------------
    # Tool 1: litmus (unified CRUD)
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus")
    def litmus(
        action: str,
        type: str | None = None,
        id: str | None = None,
        path: str | None = None,
        content: dict[str, Any] | None = None,
        create: bool = True,
        scaffold: bool = True,
    ) -> dict[str, Any]:
        """Unified Litmus operations: init, list, get, save, read.

        Actions:
        - init: Initialize/switch project directory
          litmus(action="init", path="~/my-project")

        - list: List entities of a type
          litmus(action="list", type="product")

        - get: Get entity details
          litmus(action="get", type="product", id="tps54302")

        - save: Create/update entity
          litmus(action="save", type="product", id="tps54302", content={...})

        - read: Read project file or template
          litmus(action="read", path="products/x/spec.yaml")
          litmus(action="read", path="template:test")

        Args:
            action: One of: init, list, get, save, read
            type: Entity type for list/get/save (product, station, fixture, sequence, instrument, run, test)
            id: Entity ID for get/save
            path: Path for init/read actions
            content: Content dict for save action
            create: For init - create directory if missing (default True)
            scaffold: For init - create folder structure (default True)

        Returns:
            Action-specific results.
        """
        return litmus_tool(action, type, id, path, content, create, scaffold)

    # -------------------------------------------------------------------------
    # Tool 2: litmus_discover
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_discover")
    def discover() -> dict[str, Any]:
        """Scan for connected VISA instruments.

        Discovers available VISA resources on this computer. Returns a list of
        instruments with their addresses, connection types, and identification.

        Returns:
            List of discovered resources with addresses and suggested types.
        """
        return discover_tool()

    # -------------------------------------------------------------------------
    # Tool 3: litmus_match
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_match")
    def match(
        product_id: str | None = None,
        station_id: str | None = None,
        fixture_id: str | None = None,
    ) -> dict[str, Any]:
        """Check compatibility between products, stations, and fixtures.

        Usage patterns:
        - match(product_id="...") → Find compatible stations, derive requirements
        - match(product_id="...", station_id="...") → Detailed compatibility check
        - match(fixture_id="...") → Find stations with required instruments

        Args:
            product_id: Product ID to check compatibility for
            station_id: Station ID for detailed check (requires product_id)
            fixture_id: Fixture ID to find compatible stations

        Returns:
            Compatibility results with requirements and matches.
        """
        return match_tool(product_id, station_id, fixture_id)

    # -------------------------------------------------------------------------
    # Tool 4: litmus_run
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_run")
    def run(test: str, station: str, serial: str) -> dict[str, Any]:
        """Execute tests and return results.

        Runs pytest with the specified test path and waits for completion.
        Returns full results including pass/fail status and measurements.

        Args:
            test: Test file or directory (e.g., "products/x/tests/test_x.py")
            station: Station ID to run on
            serial: DUT serial number

        Returns:
            Run results with outcome, measurements, and any errors.
        """
        return run_tool(test, station, serial)

    # -------------------------------------------------------------------------
    # Tool 5: litmus_open
    # -------------------------------------------------------------------------

    @mcp.tool(name="litmus_open")
    def open_ui(
        type: str, id: str, base_url: str = "http://localhost:8000"
    ) -> dict[str, Any]:
        """Get URL to view/edit an entity in the browser UI.

        Use this when detailed viewing or visual editing is needed.

        Args:
            type: Entity type (product, station, run, fixture, sequence)
            id: Entity ID
            base_url: UI server URL (default: http://localhost:8000)

        Returns:
            URL to open in browser.
        """
        return open_tool(type, id, base_url)

    return mcp


def run_mcp_server():
    """Run the MCP server (for CLI entry point)."""
    mcp = create_mcp_server()
    mcp.run()
