# Page audit: docs/reference/cli.md

**Quadrant:** Reference (every `litmus <command>` and its flags — comprehensive CLI reference)
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 3 | 2 |
| Voice | 1 | 4 | 2 |
| Audience | 0 | 3 | 2 |
| Accuracy | 6 | 7 | 3 |
| Gaps | 5 | 6 | 2 |
| Cross-links | 1 | 3 | 3 |
| **Total** | **13** | **26** | **14** |

---

## Ordering

WARNING — "Getting Started (recommended order)" section sits between Setup commands and Common Workflows (lines 518–563). For a Reference page, a tutorial-style numbered walkthrough is out of place; it belongs in `docs/tutorial/00-quickstart.md` (which already exists). At minimum it interrupts the flow `Setup → MCP → … → reference tables` and pushes the comprehensive `Environment Variables` / `Exit Codes` tables to the very end where they're hard to find.

WARNING — Command grouping is inconsistent. Some top-level groups get an H2 (Yield / Manufacturing Metrics, Data management commands, Daemon commands, MCP Commands, Setup Commands) but the foundational commands (`init`, `serve`, `runs`, `show`) live under a single generic `## Commands` H2 with no sub-grouping. A reader scanning for "where do project-lifecycle commands live" has to recognise the four-command bucket implicitly.

WARNING — `## Test phase` (line 598) is a conceptual / pytest-flag explanation, not a CLI reference section. It documents `pytest --test-phase=<phase>` (not a `litmus` subcommand) and a resolution chain involving git status. Sitting between `Common Workflows` and `Environment Variables` it reads as a misplaced concepts page; either move to `concepts/` or fold the env-var part into the Environment Variables table.

SUGGESTION — The MCP tool table inside `### litmus mcp serve` (lines 313–326) duplicates the same list inside `### litmus setup show` (lines 503–516). For a Reference page that's fine, but readers see the same twelve rows twice within ~200 lines.

SUGGESTION — `## Common Workflows` (Development / CI/CD / AI-Assisted Development) is a How-to topic, not Reference; it duplicates content that belongs in `how-to/`. Either remove or move to a how-to page and link from here.

---

## Voice

CRITICAL — The page mixes Reference voice with Tutorial voice. The opening claim "**1. Create your project / 2. Define your product spec — What are you testing? / Most engineers start here…**" (lines 518–558) is pure tutorial walkthrough — second-person guidance, prose narrative, "see [From Mocks to Hardware] for the full transition guide." A CLI reference should describe what each command does and its flags; it should not coach the reader through a workflow.

WARNING — Several command descriptions use marketing / promotional voice rather than neutral reference: line 311 "Twelve tools, all prefixed `litmus_`. Full reference in …" — the count is presentational rather than spec; line 386 "Builds a `.mcpb` Desktop Extension bundle that can be double-clicked to install in Claude Desktop" reads as a product blurb.

WARNING — Tone shifts between commands. `litmus init` (lines 49–53) uses didactic explanatory prose ("When `--discover` is used … Duplicate types are numbered (dmm1, dmm2)") while `litmus metrics pareto` (lines 213–219) gets only a one-line summary. Reference pages should be uniformly terse-but-complete, not "fully described where the author cared, stub otherwise."

WARNING — Imperative second-person creeps in: line 179 "Create custom templates to match your organization's report format"; line 543 "Most engineers start here. Describe your DUT's characteristics and limits." These don't belong in a CLI reference.

WARNING — Inconsistent sentence-case in headings. "Yield / Manufacturing Metrics" (Title Case), "Data management commands" (Sentence case), "Daemon commands" (Sentence case), "MCP Commands" (Title Case), "Setup Commands" (Title Case), "Common Workflows" (Title Case), "Test phase" (Sentence case), "Environment Variables" / "Exit Codes" (Title Case). Pick one and apply throughout.

SUGGESTION — "What it does:" / "What it starts:" / "What it exposes:" sub-headings (lines 87, 309, 354, 386, 418, 452, 477) are good but inconsistent in punctuation and presence — some commands have them, some don't.

SUGGESTION — `litmus metrics summary` table header uses "First-pass yield: 85.0%" example output that doesn't match the doc's surrounding column listing ("Period / Product / Station / Runs / Pass / Fail / FPY / Final / Avg(s)" — see source `src/litmus/cli.py:2098`). Either show the actual table format or pick one consistent example shape.

---

## Audience

WARNING — Tier vocabulary leak. Line 32 describes `--tier` values as `bringup (Tier 0/1 …)` / `bench (Tier 2 starter)` / `factory (Tier 3/4 …)`. "Tier 0/1/2/3/4" is internal Litmus jargon never defined on this page; a test engineer reading the CLI reference cannot decode "Tier 3/4." Either spell them out inline or link to where tiers are defined.

WARNING — Pytest plugin / runtime concepts assumed without context. The "Test phase" section (line 600) talks about "tags every run with the maturity tier it was produced for" — a reader who lands here from `--help` output has no Litmus concept of "run" or "phase" yet. Even a Reference page should briefly define the term or link out on first use.

WARNING — Operator-facing identifier rule violation (`feedback_operator_facing_identifiers.md`). Lines 199–200:
```
| `--product` | *(none)* | Product ID filter |
| `--station` | *(none)* | Station ID filter |
```
The repo's universal rule is Product → `dut_part_number`, Station → `station_hostname` in operator-facing labels. `Product ID` / `Station ID` are programmer-internal terms.

SUGGESTION — Acronym handling is uneven. FPY / RTY get a parenthetical expansion (line 185) — good — but Cpk / Cp (line 222) are never defined, and "process capability" alone won't help a developer audience. The DUT acronym is used (lines 96, 132, 543) without expansion in this page.

SUGGESTION — Some commands assume readers know what subsystems exist. "Manage them with `litmus daemon`" (line 267) presumes the reader knows "events, runs, channels" are daemons. A one-line "What is a daemon?" tip or link to `concepts/three-stores.md` would orient newcomers.

---

## Accuracy

CRITICAL — Package install name is wrong. Line 169: `(requires weasyprint: pip install 'litmus[pdf]')`. The actual package name in `pyproject.toml` is `litmus-test` (`pyproject.toml:6 name = "litmus-test"`), and the extra is declared under `[project.optional-dependencies] pdf = ["weasyprint>=62.0"]` at line 71. Should be `pip install 'litmus-test[pdf]'`.

CRITICAL — `litmus runs` default `--data-dir` documented as `results` (line 106). Source `src/litmus/cli.py:558` has `default=None`, and resolution is via `_get_data_dir()` → `resolve_data_dir()` which chains `--data-dir arg → project litmus.yaml data_dir → LITMUS_HOME env → platformdirs.user_data_dir("litmus")/data` (`src/litmus/data/data_dir.py:32–61`). The doc's "default: `results`" is wrong for both `runs` and the `metrics summary` table (line 195).

CRITICAL — `litmus runs` example output (lines 110–117) shows columns `Run ID / DUT Serial / Station / Outcome`. Actual source `src/litmus/cli.py:586` prints `Run ID / DUT Serial / Project / Station / Outcome` (5 columns including Project). The example output is stale.

CRITICAL — Test-phase resolution chain (lines 604–609) is incomplete and partly wrong:
* Source `src/litmus/execution/profiles.py:587–612 resolve_test_phase()` adds a demotion not documented: `--mock-instruments` (mocks_active=True) **forces `development`** regardless of git status. The doc omits this entirely.
* "Profile YAML — a profile can set `test_phase: <phase>` for every test it runs" is misleading. There is no `test_phase:` field on profiles; profiles match via `facets: {test_phase: <value>}` and the **CLI flag / env var sets the value the facet matches against**. The profile does not "set" the phase.
* Resolution as documented (`pytest --test-phase` > env > profile > auto) doesn't match code: code resolves `--test-phase` OR `LITMUS_TEST_PHASE` (`src/litmus/pytest_plugin/hooks.py:324`) then passes that as `requested_phase` to `resolve_test_phase()`, which then applies git-clean / mock-active demotion. There is no "profile" step in the chain at the runner level.

CRITICAL — `litmus daemon restart` and `litmus daemon stop` signatures wrong. The doc (lines 277–291) shows `litmus daemon restart` and `litmus daemon stop` with no arguments — i.e. they always operate on all daemons. Source (`src/litmus/cli.py:2517–2575`) takes `@click.argument("targets", nargs=-1)` and `@click.option("--all", "all_flag", …)`. So `litmus daemon restart events` is valid, as is `litmus daemon stop --all`. The doc misses this entirely and incorrectly implies restart always touches all daemons.

CRITICAL — `litmus daemon restart` description (line 279) "Stop and restart all daemons (clears their in-memory state; on-disk parquet remains)." conflates two things: the source comment says "next acquire spawns fresh"; restart `SIGTERM`s the pid; daemons are not respawned eagerly — they respawn lazily on next acquire. The doc's "and restart" is misleading.

WARNING — `setup show` "Available tools (all prefixed `litmus_`):" (lines 503–515) lists twelve `litmus_*` tools that match the MCP server. But source `src/litmus/cli.py:1431–1453 setup_show()` actually emits a different, **stale** list: `list_products / get_product_spec / list_stations / get_station_config / find_compatible_stations / check_station_compatibility / derive_required_capabilities / get_instrument_library / save_product_spec`. The doc's example output of `setup show` is fabricated / aspirational — the real command prints the old tool names.

WARNING — `--phase` description (line 197) "exclude `development`" — code at `_base_filters` passes `phase` through to `MeasurementsQuery.yield_summary()` without special-casing default. If you don't pass `--phase`, the value is `None` and no filter is applied at the SQL level; the "exclude development by default" behavior would be inside the query. Need to verify in `src/litmus/analysis/measurements_query.py` rather than asserting it from the option text alone — likely overstated.

WARNING — `litmus init` option table shows `--name` as a separate row (line 34) and `[NAME]` as a positional argument (line 22). In source (`src/litmus/cli.py:40, 64`) both exist (positional `name` plus `--name project_name`), but the table doesn't make it clear that the **flag is for an entirely different purpose** (override the auto-detected project name when scaffolding the CWD). Reader is likely to assume the flag duplicates the positional.

WARNING — `litmus serve` "Default: `false`" for `--reload` (line 69) — Click flags are presented as `True` / `False` in the `--no-git` row (line 29) but `false` (lowercase) for `--reload`. Inconsistent and not in source style.

WARNING — `litmus mcp serve` `--transport` option lists `stdio` or `sse` (line 307). Source `src/litmus/cli.py:1011–1014` only handles `stdio` and explicitly prints `Transport '{transport}' not yet supported. Use 'stdio'.` for anything else. So `sse` is documented as supported but is in fact not implemented.

WARNING — Environment variable table (lines 646–650) is wrong/incomplete:
* `LITMUS_HOME` description claims the resolution chain `--data-dir arg → project litmus.yaml data_dir → LITMUS_HOME → platformdirs.user_data_dir("litmus")`. The actual chain (`src/litmus/data/data_dir.py:32–61`) inserts `LITMUS_HOME` only as the fallback root for `platformdirs.user_data_dir("litmus")` — and the final path is `<home>/data`, not the raw home. The table conflates these.
* `LITMUS_MOCK_INSTRUMENTS` description "Set to `1` to enable mock mode without `--mock-instruments`" is correct, but the precedence (flag > env > false) documented elsewhere (`src/litmus/pytest_plugin/hooks.py:952`) is not shown here.
* Missing env vars used in code: `_LITMUS_SLOT_ID` (worker slot binding, `src/litmus/pytest_plugin/hooks.py:340`), `LITMUS_SKIP_DAEMON_NOTIFY` (test-conventions opt-out).

WARNING — Exit codes table (lines 654–658) says `2 = Command not found`. Click's standard exit codes are `0` success, `1` ClickException / SystemExit(1), `2` UsageError (invalid CLI args). The doc swaps these — `1` is described as "General error (invalid options, missing files)" but Click uses 2 for invalid options. Source has many `raise SystemExit(1)` for runtime errors and many `raise click.ClickException` (also exits 1), but `raise click.UsageError(...)` (e.g. `src/litmus/pytest_plugin/hooks.py:359`) and Click's own bad-option handling exit 2. Table needs reconciliation with actual Click behavior.

SUGGESTION — `litmus show` "Without `-f`, prints a terminal summary." (line 142) — true, but the source also has a `--env` flag (`src/litmus/cli.py:611`) that switches the terminal output to an environment snapshot. Not documented at all.

SUGGESTION — `litmus runs` source has a `--json` flag (`src/litmus/cli.py:560`) that emits JSON instead of the table. Not documented.

SUGGESTION — `litmus discover` source has a `--no-identify` flag and protocol flags `--visa / --ni / --serial / --lxi / --json` (`src/litmus/cli.py:1462–1467`). None are documented; `litmus discover` is only ever mentioned obliquely in the Getting Started walkthrough.

---

## Gaps

CRITICAL — Many commands defined in `src/litmus/cli.py` are not documented at all on this "comprehensive CLI reference":
* `litmus validate` (`src/litmus/cli.py:430`)
* `litmus new-test` (`src/litmus/cli.py:223`)
* `litmus discover` (`src/litmus/cli.py:1468`) — referenced in the workflow but no flag table
* `litmus export` (`src/litmus/cli.py:791`)
* `litmus sbom` (`src/litmus/cli.py:867`)
* `litmus schema export` and `litmus schema refresh` (`src/litmus/cli.py:917, 942`)
* `litmus catalog datasheet` (`src/litmus/cli.py:1567`)
* `litmus station init / validate / update` (`src/litmus/cli.py:1591, 1697, 1809`)
* `litmus instrument list / show / cal` (`src/litmus/cli.py:1882, 1929, 1994`)
* `litmus grafana serve / setup / export` (`src/litmus/grafana/cli.py:49, 80, 143`)
* `litmus metrics retest` (`src/litmus/cli.py:2279`) — present in source, missing in doc body (only summary, pareto, cpk, trend, time-loss appear).

For a page whose stated purpose is "every `litmus <command>` and its flags," this is the largest single gap.

CRITICAL — `litmus init` is missing its key behaviour: there is no documentation that `--ai` will trigger an interactive AI-tool-detection prompt when omitted (`src/litmus/cli.py:169–190`). The flag is only described in the table as "Set up AI tool integration" without mentioning that omitting it auto-detects installed tools and prompts.

CRITICAL — `litmus init` Examples block (lines 38–47) shows no example with `--tier`, `--ai`, or `--starter` despite all three being documented options. Reader has no canonical usage.

CRITICAL — `litmus metrics pareto` lacks the `--group-by` flag in its option table (line 217 just says `[--top N] [filter options...]`). Source `src/litmus/cli.py:2122` adds `--group-by [product|step|measurement]` with detailed semantics. This is one of the most important flags on the page and it's omitted.

CRITICAL — `litmus data prune` lacks the `--type` option (line 253). Source `src/litmus/cli.py:2361–2365` accepts `--type` (multiple, e.g. `channels`, `events`). The doc shows only `--older-than` and `--dry-run`.

WARNING — `litmus runs` example output is only 4 columns, missing the JSON output mode entirely (no `--json` example).

WARNING — Each `metrics ...` subcommand other than `summary` has only a one-line description and a stub usage line — no flag tables, no example output, no semantics. For Reference, every subcommand deserves the same treatment as `summary`.

WARNING — `litmus serve` lacks documentation of the `--reload` development-mode behaviour: source uses `uvicorn` with `reload_dirs=[litmus_pkg]` and `reload_includes=["*.py", "*.yaml"]` (`src/litmus/cli.py:524–536`). Operators will hit confusing behaviour if they don't know which files trigger reload.

WARNING — No documentation of how to discover commands: `litmus --help` is shown in the install snippet but no explanation that all subgroups (`litmus daemon --help`, `litmus metrics --help`, etc.) print their own subcommand list.

WARNING — `litmus show` `-f` formats `html`, `pdf`, `json`, `csv` are listed, but the page doesn't say what additional formats `litmus export` supports (stdf, hdf5, tdms, mdf4, atml — see `src/litmus/cli.py:787`). Readers comparing "export" vs "show -f" have no map.

WARNING — `litmus setup claude-code` "What it does" omits the `--print-only` behaviour (skips registration, prints config to stdout) even though the flag is listed in the option table without describing the print path.

WARNING — Daemon section never explains what each daemon does. "events / runs / channels" appear as labels in `daemon status` but the doc never says "events is the WAL, runs is the per-run parquet index, channels is the time-series stream." Cross-link to `concepts/three-stores.md` would close this.

SUGGESTION — No documentation of environment variables consumed by subcommands beyond `LITMUS_HOME` / `LITMUS_TEST_PHASE` / `LITMUS_MOCK_INSTRUMENTS`. Code references `WSL_DISTRO_NAME`, `USERNAME`, `USER`, `APPDATA` in `setup claude-desktop` (`src/litmus/cli.py:1203–1213`) — relevant for users running on Windows / WSL.

SUGGESTION — No "global options" table. Click's `--version` and `--help` are mentioned once in the install snippet but never enumerated as common options that apply to the root and every subcommand.

---

## Cross-links

CRITICAL — `litmus station init` referenced at line 536 in the Getting Started walkthrough — but `litmus station init` is not documented on this page nor linked to anywhere. Reader cannot find what flags it accepts. Same for `litmus discover` (line 535) and `litmus new-test output_voltage` (line 542).

WARNING — `## Test phase` section (line 598) talks about `pytest --test-phase=<phase>` but does not link to `docs/reference/pytest-native.md` (which exists at `/home/ryanf/repos/litmus/docs/reference/pytest-native.md`) or to `docs/how-to/profiles.md`. Readers cannot find more on phases or the pytest-plugin flag set.

WARNING — Single "See Also" section at the bottom lists only two links (Platform Architecture, MCP Tools). A reference page of this scope should link to:
* `docs/reference/api.md` for HTTP endpoints (referenced once inline at line 311 but not in See Also)
* `docs/reference/configuration.md` for `litmus.yaml` (relevant to every `--data-dir` discussion)
* `docs/concepts/three-stores.md` for daemon semantics
* `docs/reference/pytest-native.md` for the pytest side of phase / mocks
* `docs/how-to/grafana-dashboards.md` (since `litmus grafana` exists, even though undocumented on this page)

WARNING — `litmus setup` subcommands reference Claude Code / Claude Desktop / Copilot / Cursor / Cline configuration outcomes but no cross-link to `docs/how-to/mcp-integration.md`, which is the natural follow-up for each.

SUGGESTION — `litmus show -f html` (line 167) mentions "self-contained, print-friendly" but does not link to `docs/reference/outputs.md` or any report-template documentation, leaving the `-t` / `--template` option (line 140) under-explained.

SUGGESTION — `litmus init --discover` paragraph (lines 51–52) says "Litmus scans for VISA instruments, looks up each in the catalog…" but never links to the catalog reference (`docs/reference/catalog-schema.md` exists). Reader learning what "catalog" means here has no path.

SUGGESTION — `litmus sbom` (undocumented on this page) generates CycloneDX 1.6 JSON (`src/litmus/cli.py:868`). If/when documented, it should link to `docs/concepts/results-storage.md` or a traceability doc — currently nothing about provenance / SBOM appears on this reference page.
