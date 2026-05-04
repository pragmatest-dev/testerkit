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

- **Pydantic everywhere** — Use Pydantic models for ALL configuration and data structures. NEVER pass raw dicts when a Pydantic model exists. `model_dump()` ONLY at actual write boundaries (YAML files, JSON API responses). Functions return and accept models, not dicts.
- **All YAML through `litmus/store.py`** — NEVER read/write YAML directly. All YAML I/O goes through the store layer which handles validation via Pydantic models.
- **Verify deps before specifying** — Always check PyPI (`uv run pip index versions <pkg>`) before writing version constraints. Never guess version numbers.
- Prefer YAML for human-editable configuration files
- Litmus does NOT provide instrument drivers — users bring their own (PyMeasure, PyVISA, vendor libs)
- All MCP tools should have equivalent HTTP API endpoints
- Operator UI uses NiceGUI with Tailwind CSS classes via `.classes()`
- **UI inputs:** Use dropdowns/autocomplete for fields with known value sets, even if dynamically populated from data
- API routes use FastAPI for JSON endpoints
- **UI consistency is a hard rule** — every page must use the same patterns:
  - **Layout primitives**: `page_layout()` shell, `page_header()` title, `data_table()` for any tabular list, `format_datetime()` for any timestamp. All from `litmus.ui.shared.components`.
  - **Data path**: pages read through the public Query API (`RunsQuery`, `StepsQuery`, `MeasurementsQuery`) — never directly from parquet, ContextVars, or in-process dicts.
  - **No admin leaks in operator pages**: `results_dir` and other infrastructure paths resolve from `ProjectConfig`; never expose them in filter rows or inputs.
  - **URL state**: pages with filters mirror state into the URL via `history.replaceState` so views are bookmarkable and shareable.
  - **Filters above content**: filter widgets always render above the data they filter — never below.
  - **Tabs subordinate to filters**: when a page has multiple analytical lenses (e.g. /metrics: Yield / Pareto / Cpk / Retest / Time loss / Assets), filters live above the tab strip.
  - **One-word sidebar labels**: "Metrics", "Measurements", "Channels", "Events", "Results" — no multi-word labels.
  - **Real empty states**: when a query returns 0 rows, render a card naming the cause and a concrete next step. Never "No data".
- **Top-level imports** — Prefer module-level imports. Only use lazy imports inside functions when needed to break circular imports or defer heavy optional dependencies (e.g., `import numpy`). Never use in-function imports just for convenience.

## Documentation Updates

- `docs/` has codebase descriptions and must be updated when implementing new features
- `examples/` has three-tier working examples (01-bringup / 02-station / 03-profiles)
- MCP tools and skills for AI workflows

Do NOT bloat CLAUDE.md with implementation details — the AI can discover those from code.

## Catalog YAML

Use `/catalog-from-datasheet` skill for all catalog work. Schema reference: `docs/capability-schema.md`. Models: `litmus/config/models.py`.

## Tool Usage Rules

- **Use Read/Write/Edit tools** for all file operations — NEVER use Bash `cat`, heredocs, `echo >`, or `sed` for reading or writing file content
- **No ad-hoc scripts** — NEVER generate throwaway Python/shell scripts to produce YAML or other output files. Write YAML directly via Edit/Write tools. If a script is needed, it must be an official maintained script in the repo.
- **Delete temp files** with `uv run python -c "from pathlib import Path; Path('...').unlink()"` — NEVER use `rm` (blocked by ask rules).

## Skill Adherence

When executing a skill (invoked via the Skill tool), follow the skill's workflow document **exactly as written, step by step**. Skills are procedures, not guidelines. Specific rules:

1. **Never skip steps** — execute every phase, every sub-step, in order
2. **Never ad-lib** — do not improvise, reorder, combine, or "optimize" the workflow
3. **Complete every loop** — if the workflow says "audit → fix → re-audit → repeat until clean", do ALL iterations. Never skip the re-audit after a fix.
4. **Read the workflow file** — the skill's `.md` file is the source of truth. Re-read it if unsure.
5. **Do not editorialize** — if the workflow says to spawn an agent, spawn it. Do not inline the work yourself unless the workflow explicitly says to.

## Planning Workflow

When planning a new feature or significant change for Litmus, always follow this workflow:

1. **Research & discuss** — Explore the codebase, read relevant files, discuss the approach with the user
2. **Shape the spec** — Once the approach is clear, invoke `/agent-os:shape-spec` to create a structured shape spec. This produces a `shape.md` in `agent-os/specs/`.
3. **Write the plan** — Create the implementation plan with tasks, files to create/modify, and verification steps
4. **Exit plan mode** — Present the plan for user approval

IMPORTANT: Always invoke `/agent-os:shape-spec` before finalizing the plan.

## Plan Adherence — NO DESIGN DEVIATIONS

Once a plan is approved, the plan is the contract. While executing:

1. **NO design deviations from plan.** Do not invent new fields, mechanisms, fixtures, or abstractions that the plan did not specify. If the plan says "add field X," add X — do not also add Y because it seems nice.
2. **If you discover a missing element, STOP and have a design discussion.** Bugs found during implementation (e.g., step_index collision, outcome propagation gap) are design questions, not auto-pilot fixes. Surface them, propose options, wait for the user's answer.
3. **NO auto-select.** Do not pick between design alternatives on the user's behalf. When the plan is silent on a choice, that's a signal to pause, not a license to decide.
4. **"Continue" is not a blanket authorization.** Treat each phase as its own decision point. Re-confirm the approach before starting, especially after compaction or a long gap.
5. **Over-implementation is worse than under-implementation.** Shipping extra scope without discussion is harder to undo than shipping only what was agreed.

## Compaction Recovery

After compaction, if the conversation mentions catalog processing: re-read `catalog/QUEUE.md`, then re-invoke `/catalog-from-datasheet` via the Skill tool.
