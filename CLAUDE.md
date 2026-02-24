# Litmus - Hardware Test Framework

## Project Overview

Litmus is a Python-native hardware test **platform** for the AI-assisted era. It provides infrastructure for hardware testing—configuration management, instrument drivers, data storage, AI tool integration—regardless of which test runner you use.

**Primary path:** pytest-native framework for new projects
**Migration path:** OpenHTF adapter for existing test suites
**Catch-all:** Simple results API for any source (LabVIEW, TestStand, custom scripts)

## Core Philosophy

1. **Platform over framework** — Value is in infrastructure (config, instruments, data, tool exposure), not test execution
2. **Integrate, don't reinvent** — Use popular libraries (pytest, Pydantic, FastAPI, PyVISA) that LLMs know deeply
3. **Configuration as source of truth** — Non-developers can modify test behavior without touching code
4. **AI-ready, not AI-dependent** — Expose MCP tools and HTTP APIs for external agents; platform does NOT call LLMs
5. **Incremental adoption** — Start with results API, add config system, add instruments, add AI tools

## Common Commands

```bash
uv sync                        # Install dependencies
pytest                         # Run tests
ruff check .                   # Lint
ruff format .                  # Format

litmus serve                   # Operator UI (localhost:8000)
litmus serve --reload          # Dev mode with auto-reload
litmus runs                    # List recent test runs
litmus show <run_id>           # Show run details (terminal)
litmus show <run_id> -f html   # Generate report (html/pdf/json/csv)
litmus discover                # Scan for instruments
litmus mcp serve               # Start MCP server
```

## Folder Convention

Entity-aligned folders contain YAML configuration files. Code folders contain Python scripts.
- **YAML config**: `catalog/`, `instruments/`, `stations/`, `products/`, `fixtures/`, `sequences/`
- **Python code**: `drivers/`, `tests/`

## Development Guidelines

- Use Pydantic models for all configuration and data structures
- Prefer YAML for human-editable configuration files
- Litmus does NOT provide instrument drivers — users bring their own (PyMeasure, PyVISA, vendor libs)
- All MCP tools should have equivalent HTTP API endpoints
- Operator UI uses NiceGUI with Tailwind CSS classes via `.classes()`
- **UI inputs:** Use dropdowns/autocomplete for fields with known value sets, even if dynamically populated from data
- API routes use FastAPI for JSON endpoints

## Documentation Updates

- `docs/` has codebase descriptions and must be updated when implementing new features
- `demo/` has working examples
- MCP tools and skills for AI workflows

Do NOT bloat CLAUDE.md with implementation details — the AI can discover those from code.

## Capability Schema

**Read `docs/capability-schema.md` before writing ANY catalog YAML.** It defines the full capability model: signals (range + accuracy + resolution + SpecBands), conditions, controls, attributes, channel topology. Every datasheet spec must map to a schema field — no spec data in comments.

Key models: `litmus/config/models.py` (Capability, Signal, SpecBand, Condition, Control, Attribute, ResolutionSpec, AccuracySpec)
Key enums: `litmus/config/models.py` (MeasurementFunction, ConnectorType, TerminalRole, GroundTopology)

## Catalog YAML Processing Rules

When writing or rewriting catalog YAML files from datasheets:

1. **PDF is the ONLY source of truth** — never copy from existing catalog/ YAMLs or guess
2. **Read EVERY page of the PDF, 2-4 pages at a time** — NO SKIPPING
3. **Every datasheet spec → structured schema field** — use signals, conditions, controls, attributes, SpecBands. NO spec data left as comments. See `docs/capability-schema.md` for the decision tree
4. **Use ALL applicable MeasurementFunction values** — scopes need `waveform` + `dc_voltage` + `ac_voltage` + `frequency` + `rise_time` + `fall_time` + `pulse_width` + `duty_cycle` + `phase`. Use `heater_power` not `dc_voltage` for heaters. Use `excitation_current`, `trigger`, `reference_clock` where they apply
5. **Resolution on every signal** — `resolution: {digits: 6.5}` or `{bits: 16}` or `{value: 0.001, units: V}`
6. **SpecBands for condition-dependent accuracy** — if accuracy varies by frequency, range, V/div, NPLC, or any control/condition, encode it as `specs:` entries
7. **Compute accuracy from full equations** — e.g. NI GainError = ResidualGain + GainTempco×ΔT + RefTempco×ΔT
8. **Use compact channel range syntax** — `"ai[0:7]"` not `["ai0", "ai1", ...]`
9. **All channels in topology dict** — every channel in capabilities MUST exist in `catalog_entry.channels`
10. **Comments: 3-line header max** (instrument name, PDF source, model variants). No spec data in comments
11. **Verify each file loads clean** via `load_catalog_entry()` before moving on
12. **No instrument features** — no UI, math, FFT, protocol decode, mask test

## Planning Workflow

When planning a new feature or significant change for Litmus, always follow this workflow:

1. **Research & discuss** — Explore the codebase, read relevant files, discuss the approach with the user
2. **Shape the spec** — Once the approach is clear, invoke `/agent-os:shape-spec` to create a structured shape spec. This produces a `shape.md` in `agent-os/specs/`.
3. **Write the plan** — Create the implementation plan with tasks, files to create/modify, and verification steps
4. **Exit plan mode** — Present the plan for user approval

IMPORTANT: Always invoke `/agent-os:shape-spec` before finalizing the plan.

## Compaction Recovery

After compaction, if the conversation summary mentions catalog processing, `/process-catalog`, or `/catalog-from-datasheet`:
1. **Re-read `catalog/QUEUE.md`** to find the current pending entry
2. **Re-invoke `/catalog-from-datasheet` via the Skill tool** — NEVER write catalog YAML manually
3. **Re-read `litmus/skills/workflow/datasheet-to-catalog.md`** for the full extraction workflow
4. The skill spawns section-processor (sonnet) and catalog-reviewer (opus) subagents — follow that workflow exactly
