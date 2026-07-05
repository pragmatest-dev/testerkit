---
name: litmus-skills
description: AI workflow prompts for hardware test generation with Litmus
---

# Litmus Skills

Skills are markdown prompts that guide AI assistants (Claude, Copilot, etc.) through hardware test workflows. They work with any AI that can call MCP tools.

## Available Skills

### Workflows
- **[first-test](workflow/first-test.md)** - Start here: write a first test from scratch (no datasheet), zero config → adopt advanced pieces as needed
- **[datasheet-to-test](workflow/datasheet-to-test.md)** - Full workflow from part datasheet to running tests
- **[datasheet-to-catalog](workflow/datasheet-to-catalog.md)** - Generate catalog YAML from instrument datasheet PDF (thorough, for accuracy specs)
- **[catalog-scaffold](catalog-scaffold.md)** - Quick catalog entry from Claude's knowledge (fast, for common instruments)

### Agents
Subagent prompt templates spawned by workflows via the Task tool. Not invoked directly.
- **[section-splitter](agents/section-splitter.md)** - Splits a datasheet PDF into sections for parallel processing
- **[section-extractor](agents/section-extractor.md)** - Extracts capability data from one section into structured form
- **[section-writer](agents/section-writer.md)** - Writes catalog YAML for one section
- **[section-reviewer](agents/section-reviewer.md)** - Audits catalog YAML against the PDF; reports gaps and schema violations
- **[scaffold-writer](agents/scaffold-writer.md)** - Generates a quick catalog scaffold from Claude's knowledge of common instruments

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
│ Tools: list_parts, create_part,   │
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
   > "Help me create tests for the TPS54302. The datasheet is in examples/parts/tps54302/datasheet.md"

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
- **Always use `ask_user_input_v0` for approval gates** — never print text menus like `[A]pprove [E]dit`. This ensures Claude Desktop renders interactive buttons instead of confusing text options
- Always pause for human approval before major actions
- Show confidence levels when extracting information
- Offer the UI editor for complex changes
- Persist state in part folders for resume capability
