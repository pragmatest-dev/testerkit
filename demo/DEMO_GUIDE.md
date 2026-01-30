# Litmus Demo Guide

This guide walks through demonstrating the Litmus hardware test platform.

## Demo Options

**AI-Assisted Workflow (Recommended)**
Use Claude Desktop to create tests from datasheets through conversation:
- [CLAUDE_DESKTOP_SETUP.md](./CLAUDE_DESKTOP_SETUP.md) - Connect Claude Desktop to Litmus
- [WORKFLOW_DEMO.md](./WORKFLOW_DEMO.md) - Full walkthrough from datasheet to tests

**UI-Only Workflow**
Use the web UI to browse and run existing tests (documented below).

---

## Prerequisites

```bash
# Install dependencies
uv sync

# Ensure you're in the litmus repo root
cd /path/to/litmus
```

## Quick Start

```bash
# Start the Litmus UI
uv run litmus serve

# Open browser to http://localhost:8000
```

---

## Demo Flow

### 1. Dashboard Overview

**URL:** http://localhost:8000

The dashboard shows:
- **Stations** - Available test stations with their status
- **Recent Runs** - Latest test results

Click on a station card to see details, or click "Start Test" to launch a test.

---

### 2. Browse Products

**URL:** http://localhost:8000/products

Shows all product specifications loaded from `products/` and `demo/products/`.

Each product card shows:
- Name and revision
- Number of characteristics (electrical specs)
- Number of test requirements

**Click "View Details"** to see the full product spec.

#### Product Detail Page

**URL:** http://localhost:8000/products/{product_id}

Tabs:
- **Pins** - Pin definitions (VIN, VOUT, GND, etc.)
- **Characteristics** - Electrical specifications with conditions
- **Requirements** - Test requirements derived from characteristics
- **Sequences** - Test sequences for this product + compatible stations

**Click "Edit"** to modify the product spec via form UI.

---

### 3. Browse Stations

**URL:** http://localhost:8000/stations

Shows all configured test stations from `stations/` and `demo/stations/`.

Each station card shows:
- Station name and location
- Online/offline status
- Configured instruments

#### Station Detail Page

**URL:** http://localhost:8000/stations/{station_id}

Tabs:
- **Instruments** - Configured instruments with VISA addresses
- **Sequences** - Compatible test sequences (based on capability matching)
- **Recent Runs** - Test history for this station

---

### 4. Browse Test Sequences

**URL:** http://localhost:8000/sequences

Shows all defined test sequences from `sequences/`.

Each sequence shows:
- Name and test phase (validation/characterization/production)
- Product family it tests
- Number of steps

#### Sequence Detail Page

**URL:** http://localhost:8000/sequences/{sequence_id}

Tabs:
- **Steps** - Test steps with expanded test order
- **Requirements** - Station/fixture requirements + required instrument capabilities
- **Dialogs** - Operator dialogs defined for this sequence
- **Recent Runs** - Test history for this sequence

---

### 5. Launch a Test

**URL:** http://localhost:8000/launch

Or click "Start Test" from any station/sequence card.

Fill in:
1. **DUT Serial Number** - e.g., `DPB001-0001`
2. **Station** - Select from dropdown
3. **Test Sequence** - Select which sequence to run
4. **Operator** (optional) - Your name

Click **"Start Test"** to begin.

---

### 6. Live Test Progress

**URL:** http://localhost:8000/live/{run_id}

Shows real-time test execution:
- Status indicator (Running → Passed/Failed)
- Progress bar
- Live pytest output log
- Operator dialogs appear as modals when tests request input

Wait for completion, then click **"View Full Results"**.

---

### 7. View Results

**URL:** http://localhost:8000/results

Table of all test runs with:
- Run ID, DUT, Station, Sequence
- Start time
- Step count (total / failed)
- Outcome (PASS/FAIL)

Click any row to see details.

#### Result Detail Page

**URL:** http://localhost:8000/results/{run_id}

Tabs:
- **Overview** - Pass/fail statistics
- **Measurements** - All recorded measurements with limits and outcomes
- **DUT History** - Other test runs for the same DUT

---

### 8. Browse Instruments

**URL:** http://localhost:8000/instruments

Shows the instrument library - all supported instrument types with their capabilities.

Each instrument shows:
- Name and type ID
- Description
- Capability badges (voltage_dc, current_dc, etc.)

---

## Demo Scenarios

### Scenario A: Happy Path (All Tests Pass)

1. Go to http://localhost:8000/launch
2. Enter DUT: `DEMO-PASS-001`
3. Select station: `bench_full`
4. Select sequence: `Power Board - Quick Smoke`
5. Click "Start Test"
6. Watch tests run and pass
7. View results

### Scenario B: Test Failure

1. Go to http://localhost:8000/launch
2. Enter DUT: `DEMO-FAIL-001`
3. Select station: `bench_full`
4. Select sequence: `Power Board - Full Test`
5. Click "Start Test"
6. Some tests will fail
7. View results, see measurements tab for details

### Scenario C: Operator Dialog

1. Go to http://localhost:8000/launch
2. Enter DUT: `DEMO-DIALOG-001`
3. Select station: `bench_full`
4. Select sequence: `Power Board - Full Test`
5. Click "Start Test"
6. When the load test step runs, a dialog appears:
   - "Connect the electronic load to the 5V output"
7. Click OK to continue
8. View results

### Scenario D: Capability Matching

1. Go to http://localhost:8000/products/power_board_v1
2. Click "Sequences" tab
3. See "Required Instrument Capabilities" table
4. See "Compatible Stations" - only stations with matching instruments appear
5. Click "Run" on a compatible station to launch directly

---

## Key Files

### Product Folders
```
demo/products/tps54302/           # TPS54302 DC-DC converter
  manifest.yaml                   # Workflow position
  datasheet.md                    # Source datasheet
  spec.yaml                       # Product specification
demo/products/power_board/        # Demo power board
  manifest.yaml
  spec.yaml
```

### Station Configs
```
demo/stations/bench_full.yaml     # Full bench with DMM, PSU, scope, load
```

### Test Sequences
```
demo/sequences/power_board_smoke.yaml  # Quick smoke test
demo/sequences/power_board_quick.yaml  # Quick validation
demo/sequences/power_board_full.yaml   # Full production test with dialogs
```

### Test Code
```
demo/tests/test_power_board.py    # Power board test functions
demo/tests/test_tps54302.py       # TPS54302 test functions
```

---

## CLI Commands

```bash
# Start UI server
uv run litmus serve

# Start with auto-reload (development)
uv run litmus serve --reload

# List recent test runs
uv run litmus runs

# Show details for a specific run
uv run litmus show <run_id>
```

---

## Troubleshooting

### "No stations configured"
- Check that `demo/stations/*.yaml` files exist
- Ensure YAML is valid

### "No compatible stations found"
- The product requires capabilities no station provides
- Check product characteristics vs station instrument capabilities

### Tests not found
- Ensure test paths in sequences point to correct location
- Tests should be in `demo/tests/` not `tests/`

### Dialogs not appearing
- Dialogs only appear during live test execution
- Check sequence YAML has dialogs defined and steps reference them
