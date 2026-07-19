# TesterKit - Hardware Test Framework

## Project Overview

TesterKit is a Python-native hardware test **platform** for the AI-assisted era. It provides infrastructure for hardware testing—configuration management, instrument drivers, data storage, AI tool integration—regardless of which test runner you use.

**Primary path:** pytest-native framework for new projects
**Migration path:** OpenHTF adapter for existing test suites
**Catch-all:** Simple results API for any source (LabVIEW, TestStand, custom scripts)

## Core Philosophy

1. **Platform over framework** — Value is in infrastructure (config, instruments, data, tool exposure), not test execution
2. **Integrate, don't reinvent** — Use popular libraries (pytest, Pydantic, FastAPI, PyVISA) that LLMs know deeply
3. **Configuration as source of truth** — Non-developers can modify test behavior without touching code
4. **AI-ready, not AI-dependent** — Expose MCP tools and HTTP APIs for external agents; platform does NOT call LLMs
5. **Starts simple, grows with you** — After install, `pytest` passes on any machine — no server, no account, no hardware needed to begin. Add what you need as you need it: measurement logging, station config, part specs, capability matching — in whatever order fits your project.

## Common Commands

```bash
uv sync                        # Install dependencies
pytest                         # Run tests
ruff check .                   # Lint
ruff format .                  # Format

testerkit serve                   # Operator UI (localhost:8000)
testerkit serve --reload          # Dev mode with auto-reload
testerkit runs                    # List recent test runs
testerkit show <run_id>           # Show run details (terminal)
testerkit show <run_id> -f html   # Generate report (html/pdf/json/csv)
testerkit discover                # Scan for instruments
testerkit mcp serve               # Start MCP server
```

## Folder Convention

Entity-aligned folders contain YAML configuration files. Code folders contain Python scripts.
- **YAML config**: `catalog/`, `instruments/`, `stations/`, `parts/`, `fixtures/`, `sequences/`
- **Python code**: `drivers/`, `tests/`

## Test Storage Convention

Tests in this repo write to the project-local data dir
(`<repo>/data/`, scoped by the repo's `testerkit.yaml`). Per-test
isolation is by **identifier** (uuid4 `run_id`, `session_id`,
unique `uut_serial` / `part_id`), NEVER by `tmp_path` for any
constructor that spawns a daemon.

**Forbidden** (each spawns a per-test daemon, ~100 gRPC threads;
the suite hits WSL's pids cgroup at ~30 such tests):
- `RunStore(_data_dir=tmp_path)` / `EventStore(_data_dir=tmp_path)`
- `ChannelStore(tmp_path, ..., serve=True)`
- `StationConnection(..., data_dir=tmp_path)`
- `--data-dir=<tmp_path>` to a pytester subprocess
- Hardcoded `platformdirs.user_data_dir("testerkit")` (bypasses the project's `testerkit.yaml`)

**Required:**
```python
from testerkit.data.data_dir import resolve_data_dir
canonical = resolve_data_dir()
store = RunStore()              # no _data_dir → canonical
backend = ParquetBackend(data_dir=canonical)
```

The forbidden patterns are enforced by `tests/test_conventions.py`,
which fails the suite if anyone reintroduces them. `ParquetBackend(data_dir=tmp_path)`
is fine because `TESTERKIT_SKIP_DAEMON_NOTIFY=1` (set in conftest)
suppresses its daemon-notify hop.

## Development Guidelines

- **Pydantic everywhere** — Use Pydantic models for ALL configuration and data structures. NEVER pass raw dicts when a Pydantic model exists. `model_dump()` ONLY at actual write boundaries (YAML files, JSON API responses). Functions return and accept models, not dicts.
- **All YAML through `testerkit/store.py`** — NEVER read/write YAML directly. All YAML I/O goes through the store layer which handles validation via Pydantic models.
- **Verify deps before specifying** — Always check PyPI (`uv run pip index versions <pkg>`) before writing version constraints. Never guess version numbers.
- Prefer YAML for human-editable configuration files
- TesterKit does NOT provide instrument drivers — users bring their own (PyMeasure, PyVISA, vendor libs)
- All MCP tools should have equivalent HTTP API endpoints
- Operator UI uses NiceGUI with Tailwind CSS classes via `.classes()`
- **UI inputs:** Use dropdowns/autocomplete for fields with known value sets, even if dynamically populated from data
- API routes use FastAPI for JSON endpoints
- **UI consistency is a hard rule** — every page must use the same patterns:
  - **Layout primitives**: `page_layout()` shell, `page_header()` title, `data_table()` for any tabular list, `format_datetime()` for any timestamp. All from `testerkit.ui.shared.components`.
  - **Data path**: pages read through the public Query API (`RunsQuery`, `StepsQuery`, `MeasurementsQuery`) — never directly from parquet, ContextVars, or in-process dicts.
  - **No admin leaks in operator pages**: `data_dir` and other infrastructure paths resolve from `ProjectConfig`; never expose them in filter rows or inputs.
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
- **Verify every claim against source before writing.** Open the file, read the function, then write the page. Pattern-matching from "things that look like this usually do that" produces plausible-sounding docs that don't survive an audit. Confirmed-against-source claims are the baseline, not the goal.
- **No framework internals in user-facing pages** (anything under `docs/` not `docs/_internal/`). No file:line citations in published prose, no private attribute names (`_foo`, `_active_*_var`), no internal class names users don't construct (`MockClass`, `ConnectionIterator`, `MeasurementLimitConfig` when "the raw limit entry" reads cleaner), no implementation-chain narration. Verification artifacts belong in commit messages and audit reports, not the page. Pydantic class names users actually reference in YAML (e.g. `InstrumentCatalogEntry`, `SpecBand`, `MeasurementFunction`) stay — those are API surface.
- **Audit-driven fixes are per-page.** When applying audit findings across multiple docs pages: fix ONE page, re-audit, confirm 0 critical, THEN move to the next. Batch-fixing across pages propagates the same misreading into every fix; the only loop that converges is per-page fix → re-audit → next. Scrub passes ("remove internals") count as rewrites and must re-audit.
- **The 5 reference pages under `docs/reference/` that contain generator markers** (`event-types.md`, `models.md`, `configuration.md`, `api.md`, `cli.md`, `pytest-native.md`, `query-api.md`) — never hand-edit the content between `<!-- GENERATED:...:start -->` / `<!-- GENERATED:...:end -->` markers. Run `uv run python scripts/generate_reference_docs.py --all` to regenerate. The pre-commit `reference-docs-drift` hook fails the commit on drift.

Do NOT bloat CLAUDE.md with implementation details — the AI can discover those from code.

## Catalog YAML

Use the `testerkit-datasheets` skill (its catalog pipeline) for all catalog work. Schema reference: `docs/reference/catalog/schema.md`. Models: `src/testerkit/models/capability.py`.

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

When planning a new feature or significant change for TesterKit, always follow this workflow:

1. **Research & discuss** — Explore the codebase, read relevant files, discuss the approach with the user
2. **Shape the spec** — Once the approach is clear, invoke `/agent-os:shape-spec` to create a structured shape spec. This produces a `shape.md` in `agent-os/specs/`.
3. **Write the plan** — Create the implementation plan with tasks, files to create/modify, and verification steps
4. **Exit plan mode** — Present the plan for user approval

**Shaping style (preferred):** shape plans as an *interactive teaching session* — start high-level, then go one level deeper per topic, a few paragraphs at a time, pausing for questions before advancing — rather than dumping a long plan doc for review. Build the plan file incrementally as decisions settle; reserve plan-mode exit for when the model is fully walked and the decisions are locked. Approved plans are encoded as a living **execution diary** (design contract + progress log) committed under `docs/_internal/explorations/` for cross-session execution. (Plans are shaped via direct discussion + plan mode; the agent-os step above is no longer used.)

## Plan Adherence — NO DESIGN DEVIATIONS

Once a plan is approved, the plan is the contract. While executing:

1. **NO design deviations from plan.** Do not invent new fields, mechanisms, fixtures, or abstractions that the plan did not specify. If the plan says "add field X," add X — do not also add Y because it seems nice.
2. **If you discover a missing element, STOP and have a design discussion.** Bugs found during implementation (e.g., step_index collision, outcome propagation gap) are design questions, not auto-pilot fixes. Surface them, propose options, wait for the user's answer.
3. **NO auto-select.** Do not pick between design alternatives on the user's behalf. When the plan is silent on a choice, that's a signal to pause, not a license to decide.
4. **"Continue" is not a blanket authorization.** Treat each phase as its own decision point. Re-confirm the approach before starting, especially after compaction or a long gap.
5. **Over-implementation is worse than under-implementation.** Shipping extra scope without discussion is harder to undo than shipping only what was agreed.

## Compaction Recovery

After compaction, if the conversation mentions catalog processing: re-read `catalog/QUEUE.md`, then re-invoke the `testerkit-datasheets` skill (its catalog pipeline) via the Skill tool.
