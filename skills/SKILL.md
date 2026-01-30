# Litmus Skills

Skills are markdown prompts that guide AI assistants (Claude, Copilot, etc.) through hardware test workflows. They work with any AI that can call MCP tools.

## Available Skills

### Workflows
- **[datasheet-to-test](workflow/datasheet-to-test.md)** - Full 6-step workflow from datasheet to test results

## How Skills Work

Skills are designed for **Claude Desktop + MCP** (for Max subscribers) or any AI with MCP tool access:

```
┌─────────────────────────────────────────┐
│ Claude Desktop / AI Assistant           │
│                                         │
│ User: "Help me test this power IC"      │
│                                         │
│ Claude: *reads skill prompt*            │
│         *calls MCP tools*               │
│         *presents results for approval* │
└────────────────┬────────────────────────┘
                 │ MCP Protocol
                 ▼
┌─────────────────────────────────────────┐
│ Litmus MCP Server                       │
│                                         │
│ Tools: list_products, create_product,   │
│        derive_requirements, run_tests   │
└─────────────────────────────────────────┘
```

## Using Skills

### With Claude Desktop (Max Subscription)

1. Start the Litmus MCP server:
   ```bash
   litmus mcp serve
   ```

2. Configure Claude Desktop to use the server (in settings)

3. Ask Claude:
   > "Help me create tests for the TPS54302. The datasheet is in demo/datasheets/tps54302.md"

4. Claude will guide you through each step, asking for approval before proceeding

### With Other AI Tools

Skills are just markdown - copy the relevant prompts into your AI tool's context. The MCP tools work with any client that supports the protocol.

## Creating New Skills

Skills follow this pattern:

1. **Goal** - What the skill accomplishes
2. **Tools** - Which MCP tools to use
3. **Steps** - Sequence of actions with decision points
4. **Examples** - Sample inputs/outputs for each step

Key principles:
- Always pause for human approval before major actions
- Show confidence levels when extracting information
- Offer the UI editor for complex changes
- Persist state in product folders for resume capability
