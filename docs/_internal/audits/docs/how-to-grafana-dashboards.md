# Page audit: docs/how-to/grafana-dashboards.md

**Quadrant:** How-to
**Audited:** 2026-05-17

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 1 | 2 |
| Voice | 0 | 0 | 2 |
| Audience | 0 | 1 | 2 |
| Accuracy | 1 | 2 | 2 |
| Gaps | 1 | 5 | 3 |
| Cross-links | 1 | 2 | 4 |
| **Total** | **3** | **11** | **15** |

---

## Ordering

| Severity | Location | Finding |
|---|---|---|
| ⚠️ WARNING | L89-105 | "Architecture" section sits between the dashboards list and the "SQL Tables" / "CLI Reference" sections. For a how-to, the architecture diagram is background that interrupts the task flow. A reader following the page top-to-bottom completes setup at L55, then encounters dashboard descriptions, then is pulled into architecture before getting to SQL tables (which they need to write their own panels) and CLI reference (which they need to operate the server). |
| 💡 SUGGESTION | "Quick Start" L17-55 | The three numbered steps work, but step 3 ("Set up dashboards") forks into "API-based" and "File-based" without telling the reader which to pick first. Lead with the recommendation (e.g., "Pick API-based if Grafana is in Docker or remote; pick File-based for a local install") before the two code blocks. |
| 💡 SUGGESTION | "Dashboards" L57-87 | The 10 dashboards are listed in no obvious order — not alphabetical, not by data store, not by task. Group them by data store (matches the Overview at L7-12: Parquet first, then Events, then Channels) or by audience task (yield → failure analysis → distribution → trend → traceability → events → channels → assets). |

---

## Voice

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| 💡 SUGGESTION | L13 | Passive voice | "implemented over DuckDB" — recasting as "the `pgwire` server (PostgreSQL wire protocol, what every PostgreSQL client speaks) runs on top of DuckDB" puts the actor first. |
| 💡 SUGGESTION | L103 | Passive voice (no clear actor) | "All timestamps are stored as UTC and converted to naive UTC timestamps at the pgwire layer for Grafana compatibility." Acceptable but the actor ("the pgwire server") could front the sentence. |

---

## Audience

| Severity | Location | Pattern | Offending text |
|---|---|---|---|
| ⚠️ WARNING | L13 | Programmer jargon for test engineers | "the PostgreSQL wire protocol — what every PostgreSQL client speaks — implemented over DuckDB" — a test engineer doesn't need to know about wire protocols. The relevant fact is "Grafana talks to it like a regular PostgreSQL server." |
| 💡 SUGGESTION | L7 | Programmer-y framing | "queries all three data stores" — "three data stores" is internal architecture language. A test engineer thinks "runs, events, and channels" or "test results, the event log, and channel data." |
| 💡 SUGGESTION | L96 | Programmer jargon | "Buena Vista + DuckDB" in the architecture diagram. Implementation detail that doesn't help the reader use the tool. Either drop it or move it to a footnote. |

---

## Accuracy

| Severity | Location | Claim | Actual (from source) | Source file:line |
|---|---|---|---|---|
| ❌ CRITICAL | L105 | "configurable with `--refresh`" | The actual CLI flag is `--refresh-seconds` (matches the CLI Reference at L123) | `src/litmus/grafana/cli.py:54` |
| ⚠️ WARNING | L120 | `litmus grafana serve` `--host` default `0.0.0.0` | True for the CLI option (default `0.0.0.0`), but the `serve()` function default is `127.0.0.1`. The CLI value is what users see, so the doc is correct for the CLI, but the implementation discrepancy is worth noting. The doc claim itself is accurate. | `src/litmus/grafana/cli.py:50` vs `src/litmus/grafana/server.py:184` |
| ⚠️ WARNING | L98-100 | Architecture diagram shows `read_parquet('results/runs/**/*.parquet')` and `Arrow IPC (results/events/**/*.arrow)` etc. | The actual code uses `{data_dir}/runs/`, `{data_dir}/events/`, `{data_dir}/channels/` — the data dir does NOT contain a `results/` subdirectory. The `results/` prefix in the diagram is misleading; the layout is `<data_dir>/runs/`, not `<data_dir>/results/runs/`. | `src/litmus/grafana/server.py:62-77`, `src/litmus/data/data_dir.py:58-61` |
| 💡 SUGGESTION | L31 | "exposes all Litmus data stores as SQL tables" | Strictly: `measurements` is a VIEW over Parquet, `runs` is a VIEW over `measurements`, `events` and `channels` are TABLES populated from Arrow IPC. The "SQL Tables" section at L107-115 mostly captures this, but the description here is loose. | `src/litmus/grafana/server.py:65-89, 158` |
| 💡 SUGGESTION | L13 | "Litmus includes a `pgwire` server" | The server is implemented via the `buenavista` package (an optional `grafana` extra). The word "includes" is right because the CLI ships with Litmus, but the runtime dep is bundled only when the user installs `litmus-test[grafana]`. | `src/litmus/grafana/server.py:171-178`, `pyproject.toml` `grafana` extra |
| ✅ VERIFIED | — | 15 claims verified against source: package name `litmus-test`, extra `[grafana]`, command group `litmus grafana`, subcommands `serve`/`setup`/`export`, all 10 dashboard files exist (`asset_utilization.json`, `channel_explorer.json`, `event_log.json`, `failure_pareto.json`, `measurement_distribution.json`, `measurement_trend.json`, `station_comparison.json`, `test_duration.json`, `unit_traceability.json`, `yield_overview.json`), `--port 5433` default, `--folder Litmus` default, datasource type `grafana-postgresql-datasource`, all timestamps wrapped with `AT TIME ZONE 'UTC'`, three data stores (runs/events/channels), background refresh thread, `.arrow` extension for IPC files. | — | — |

---

## Gaps

| Severity | Location | Gap |
|---|---|---|
| ❌ CRITICAL | "Quick Start" L17-55 | **No prerequisite for Grafana itself.** The reader is told to install Litmus extras and run `litmus grafana setup --grafana-url http://localhost:3000`, but Grafana itself must already be installed and running. State this: "You need Grafana 10+ installed and running (Docker, package, or Grafana Cloud)." A first-time reader who only installs `litmus-test[grafana]` will be confused when `--grafana-url http://localhost:3000` returns connection refused. |
| ⚠️ WARNING | L26-31 | **How do I know it worked?** No example output for `litmus grafana serve`. The reader hits Ctrl-C wondering whether the server is up. Show the expected stdout line ("Litmus pgwire server listening on 0.0.0.0:5433" — which the server actually prints) and a quick way to verify (e.g., `psql -h localhost -p 5433 -U litmus -d litmus -c "SELECT 1"` or a Grafana datasource test). |
| ⚠️ WARNING | L35-55 | **Post-setup verification.** After `litmus grafana setup` succeeds, what should the reader see in Grafana? The CLI prints "Done! N dashboards imported to 'Litmus' folder", but the doc doesn't tell the reader where to look in Grafana (Dashboards → browse → "Litmus" folder). |
| ⚠️ WARNING | L17-55 | **No firewall / remote Grafana note.** The pgwire server defaults to `0.0.0.0:5433`, which means it's exposed to the network. For a how-to about connecting Grafana Cloud (mentioned at L35), no guidance on tunneling, firewall, or authentication. The hardcoded password "litmus" at `cli.py:186` is also worth a note. |
| ⚠️ WARNING | "CLI Reference" L116-137 | **No `litmus grafana export` example or "when to use this".** The export command is documented but the page never says why a reader would use it (manual provisioning into a Grafana managed by config management, air-gapped sites, etc.). |
| ⚠️ WARNING | L55 | **"imports all 10 dashboards" but what if a dashboard with the same name already exists?** The code uses `overwrite: True` — that means re-running setup clobbers user edits. Surface this: "Re-running `litmus grafana setup` overwrites any dashboard edits you made in the Litmus folder." |
| 💡 SUGGESTION | "SQL Tables" L107-115 | **No example query.** The whole point of mentioning the SQL tables is that a reader will want to build their own panels. One concrete query per table (e.g., "select recent runs", "select events for a session") would multiply the page's usefulness. |
| 💡 SUGGESTION | "Architecture" L89-101 | **No mention of retention or scale.** A reader managing a long-running line will want to know what happens when the parquet directory grows to 10k runs or the IPC files grow large. Even one sentence ("DuckDB can scan thousands of parquet files in seconds; if your data dir gets very large, set up data pruning via `litmus data prune`") would help. |
| 💡 SUGGESTION | L29 | **No mention of `--data-dir` defaults.** The reader who has multiple projects with different `litmus.yaml` files will wonder which one the server picks up. State: "uses the same `data_dir` resolution as `litmus serve` — project `litmus.yaml`, then `LITMUS_HOME`, then platformdirs default." |

---

## Cross-links

| Severity | Location | Issue |
|---|---|---|
| ❌ CRITICAL | (page-wide) | **No "See also" section.** Every how-to in `docs/how-to/` should link to the relevant reference and concept pages. Missing links to: `reference/cli.md` (for general CLI patterns), `reference/parquet-schema.md` (so readers writing panels know the column names), `concepts/three-stores.md` (which explains the parquet / events / channels split this page exposes), `how-to/querying-events.md`, `how-to/querying-channels.md`. |
| ⚠️ WARNING | L9 first use of "Parquet" | "Parquet (runs, measurements)" — first use of Parquet as a data store. Should link to `reference/parquet-schema.md` so readers can look up column names for writing panels. |
| ⚠️ WARNING | L10-11 first use of "Arrow IPC" | "Arrow IPC (events)" / "Arrow IPC (channels)" — first use. Should link to `concepts/event-log.md` and `concepts/three-stores.md` so readers know what these stores contain. |
| 💡 SUGGESTION | L13 | "DuckDB" — first use. Could link to its docs or to an internal concept page if one exists for the analytics stack. |
| 💡 SUGGESTION | L78 | "Unit Traceability ... Select by DUT serial" — could link to `how-to/traceability.md`. |
| 💡 SUGGESTION | L86-87 | "Asset Utilization ... calibration status ..." — could link to a concept page about instrument assets / calibration (`concepts/stations.md` or similar). |
| 💡 SUGGESTION | L96 | "Buena Vista" — first use of an external library. If the reader needs to understand it (which a how-to reader usually doesn't), link to the project. Otherwise drop the name. |
