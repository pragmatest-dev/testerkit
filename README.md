# Litmus

Hardware test platform for the AI-assisted era.

## Installation

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- git (optional, for version control)

### From Source (Current)

```bash
git clone https://github.com/your-org/litmus
cd litmus
uv sync

# Make litmus CLI available
uv pip install -e .
```

### From PyPI (Coming Soon)

```bash
# Package name TBD - "litmus" is taken on PyPI
# uv tool install litmus-hw
# pipx install litmus-hw
```

## Quick Start

### 1. Create a New Project

```bash
litmus init my_project
cd my_project
```

This creates:
```
my_project/
  products/       # Product specifications
  stations/       # Station configurations
  fixtures/       # Test fixture definitions
  sequences/      # Test sequences
  tests/          # Test code
  results/        # Test output (gitignored)
  instruments/    # Custom instrument definitions
  conftest.py     # pytest fixtures
  litmus.yaml     # Project configuration
  pyproject.toml  # Python dependencies
  README.md       # Getting started guide
```

### 2. Install Dependencies

```bash
uv sync
```

For local litmus development, edit `pyproject.toml` to add the source path:
```toml
[tool.uv.sources]
litmus = { path = "../litmus", editable = true }
```

### 3. Define a Test Station

Create `stations/test_bench.yaml`:
```yaml
station:
  id: test_bench
  name: Test Bench

instruments:
  dmm:
    type: dmm
    resource: TCPIP::192.168.1.100::INSTR
    mock_config:
      voltage: 5.0
  psu:
    type: psu
    resource: TCPIP::192.168.1.101::INSTR
    mock_config:
      voltage: 12.0
      current: 0.1
```

### 4. Create a Product Spec

Create `products/my_widget/spec.yaml`:
```yaml
product:
  id: my_widget
  name: My Widget
  revision: "1.0"

specs:
  output_voltage:
    nominal: 5.0
    tolerance_pct: 5
    units: V
```

### 5. Write Tests

Create `tests/test_my_widget.py`:
```python
from litmus.execution import litmus_test

@litmus_test
def test_output_voltage(psu, dmm):
    """Measure output voltage under no load."""
    psu.set_voltage(12.0)
    psu.enable_output()
    return dmm.measure_dc_voltage()
```

Create `tests/config.yaml`:
```yaml
test_output_voltage:
  _mock:
    dmm.measure_dc_voltage: 5.0
  limits:
    test_output_voltage:
      low: 4.75
      high: 5.25
      nominal: 5.0
      units: V
      spec_ref: "output_voltage"
```

### 6. Run Tests

```bash
# With mock instruments (no hardware required)
pytest tests/ --station=test_bench --mock-instruments --dut-serial=TEST001

# With real instruments
pytest tests/ --station=test_bench --dut-serial=UNIT001
```

## CLI Reference

```bash
# Project management
litmus init <name>           # Create new project
litmus init <name> --no-git  # Skip git initialization

# Operator UI
litmus serve                 # Start web UI (http://localhost:8000)
litmus serve --port 8080     # Custom port
litmus serve --reload        # Auto-reload for development

# Results
litmus runs                  # List recent test runs
litmus show <run_id>         # Show test run details

# AI Integration
litmus mcp serve             # Start MCP server for AI agents
litmus setup claude-code     # Configure for Claude Code
litmus setup claude-desktop  # Configure for Claude Desktop
litmus setup cursor          # Configure for Cursor
litmus setup cline           # Configure for Cline (VS Code)
```

## AI Integration

Litmus exposes tools for AI agents via MCP (Model Context Protocol):

```bash
# Add to Claude Code
litmus setup claude-code

# Or manually
claude mcp add litmus -- litmus mcp serve
```

Available tools:
- `litmus` - CRUD operations (init, list, get, save, read)
- `litmus_discover` - Scan for VISA instruments
- `litmus_match` - Check product/station compatibility
- `litmus_run` - Execute tests
- `litmus_open` - Get browser URLs

## Documentation

- [Architecture](./litmus-architecture.md)
- [CLAUDE.md](./CLAUDE.md) - AI assistant instructions
- [docs/](./docs/) - Full documentation (tutorials, guides, reference)

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for architecture deep-dive, core abstractions, and development workflow.

## License

MIT
