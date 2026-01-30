# Using Litmus with Claude Desktop

This guide shows how to connect Litmus to Claude Desktop for AI-assisted test development.

## Prerequisites

- Claude Desktop installed
- Litmus installed (`uv sync` in repo root)
- Python 3.11+

## 1. Configure Claude Desktop

Add Litmus as an MCP server in your Claude Desktop configuration.

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "litmus": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/litmus", "litmus", "mcp", "serve"],
      "env": {}
    }
  }
}
```

Replace `/path/to/litmus` with the actual path to your Litmus repository.

## 2. Start the UI Server

The UI server provides visual editing and test execution. Start it in a terminal:

```bash
cd /path/to/litmus
uv run litmus serve
```

The UI will be available at http://localhost:8000

## 3. Restart Claude Desktop

After updating the config, restart Claude Desktop. You should see "litmus" in the available tools when Claude connects.

## 4. Start a Conversation

Open a new conversation in Claude Desktop and try:

```
I want to create tests for a TPS54302 DC-DC converter.
I have the datasheet here: [paste or attach datasheet]
```

Claude will:
1. Discover your connected instruments
2. Create a station configuration
3. Parse the datasheet for electrical specs
4. Create a product specification
5. Check instrument requirements
6. Generate test code and sequences
7. Execute tests and show results

## Available MCP Tools

### Station Discovery
- `discover_visa_resources` - Find connected VISA instruments
- `create_station` - Create station config from instruments
- `list_available_instrument_types` - See available instrument drivers

### Products
- `create_product_folder` - Start a new product workflow
- `save_product_spec_to_folder` - Save product spec
- `get_product_folder` - Check workflow status
- `list_product_folders` - List all products

### Capability Matching
- `derive_required_capabilities` - Get required instruments from product
- `find_compatible_stations` - Find stations that can test a product
- `check_station_compatibility` - Check specific station/product match

### Test Generation
- `save_test_file` - Save generated test code
- `save_test_sequence` - Save test sequence definition
- `get_test_templates` - Get example test patterns

### Execution
- `run_sequence` - Execute a test sequence
- `get_run_status` - Check test progress
- `dry_run_sequence` - Preview what tests would run

### UI Integration
- `get_editor_url` - Get URL to edit in the UI

## Troubleshooting

### "MCP server not found"
- Check the path in `claude_desktop_config.json`
- Ensure `uv` is in your PATH
- Try running `uv run litmus mcp serve` manually to check for errors

### "PyVISA not installed"
- Install PyVISA: `uv add pyvisa pyvisa-py`
- For real instruments, you may need NI-VISA or Keysight IO Libraries

### No instruments discovered
- Ensure instruments are connected and powered on
- Check VISA addresses with NI MAX or Keysight Connection Expert
- Try with simulated instruments first (use `simulated: true` in station config)
