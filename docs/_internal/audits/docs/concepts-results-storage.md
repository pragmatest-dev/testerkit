# Page audit: docs/concepts/results-storage.md

**Quadrant:** Concepts / Explanation (where Litmus stores results, data-dir resolution, retention)
**Audited:** 2026-05-17

> Note: the coordinator's normal six-agent dispatch tool was not available in this environment, so the six dimensions were audited inline by the coordinator against the actual source code (`src/litmus/data/data_dir.py`, `src/litmus/data/_runs_duckdb_daemon.py`, `src/litmus/data/_duckdb_daemon.py`, `src/litmus/data/schemas.py`, `src/litmus/cli.py`, `src/litmus/models/project.py`) and against neighbouring docs. Findings remain dimension-scoped and unedited from the auditor's notes.

---

## Summary

| Dimension | CRITICAL | WARNING | SUGGESTION |
|---|---|---|---|
| Ordering | 0 | 2 | 2 |
| Voice | 0 | 1 | 3 |
| Audience | 0 | 2 | 2 |
| Accuracy | 3 | 3 | 2 |
| Gaps | 1 | 3 | 2 |
| Cross-links | 1 | 3 | 2 |
| **Total** | **5** | **14** | **13** |

---

## Ordering

**WARNING — "Resolution order" appears before the section that motivates it.**
The page opens with "Where results go", shows the default tree, then dives into resolution order *before* the reader understands that there is a project-vs-global tension to resolve. The "This means every project on the machine shares one results pool" sentence is the *why* of needing override at all. Lift that sentence to its own short heading ("Why one global pool") and put the resolution list immediately after the `data_dir: results` override snippet so the order is: default location → why it's shared → how to override → resolution chain.

**WARNING — "Parquet files and schema evolution" jumps two abstraction layers down without a transition.**
The page goes from "here is the directory" straight into a HARD-contract specification. A reader looking up "where do my results go" hits a 30-line contract before seeing anything actionable. Move the "HARD contract — additive evolution only" subsection to the *end* of the page (or to a dedicated "Schema stability" subpage) and keep the top-of-page section to a paragraph: "Each litmus version may add new columns. Older files lack them; DuckDB reads them as NULL with `union_by_name=true`."

**SUGGESTION — "The query index" sits between two schema-evolution discussions.**
"Parquet files and schema evolution" → "The query index" → "Mixed versions on one machine" reads as two halves of one thought (parquet contract + index behaviour) separated by an unrelated cache section. Either fold "The query index" into "Mixed versions" (it's the same topic — what happens across versions) or move it earlier as part of "Where results go" (it's a cache *inside* the directory tree).

**SUGGESTION — "When you might notice" reads as the punchline but sits at the very bottom.**
This subsection is the only operator-facing checklist on the page and answers the most common question ("what will I see after upgrading?"). Consider pulling it up to immediately after "Where results go", or at minimum cross-referencing it from the top of "Mixed versions on one machine".

---

## Voice

**WARNING — Voice slips from "Concepts" (explain *why*) into "Reference" (specify *what*).**
The HARD-contract subsection (lines 48–73) and the "Mixed versions on one machine" table read as a compliance spec, not as an explanation. Bullet phrasing like "**New columns only.** Every release may add columns. Existing column names, types, and semantics are stable across 0.x releases." is correct content but belongs in a reference page (`reference/parquet-schema.md` or a new `reference/schema-stability.md`). Concepts should narrate the *reasoning* — "we promise additive-only because once a parquet is on disk we can't migrate it; so consumers can write queries today that survive every 0.x release". Move the bullet list to reference; keep one explanatory paragraph here that links to it.

**SUGGESTION — "(Apache Parquet — the columnar storage format DuckDB and Spark both read natively)" is a parenthetical mini-glossary.**
The inline gloss is helpful for newcomers but interrupts the sentence's main claim. Either move it to a footnote-style line below, or drop it (the term is defined elsewhere and is now mainstream enough). The same applies to "Arrow IPC" appearing in the directory tree — concepts pages should define terms once and link.

**SUGGESTION — Use of bold for emphasis is inconsistent.**
`**HARD contract**`, `**The rule:**`, `**After upgrading litmus:**` all bold different kinds of things (a noun, a callout, a heading-substitute). Pick one pattern — typically bold for inline definitions, plain text or `###` headings for callouts.

**SUGGESTION — "This means every project on the machine shares one results pool" — the topic-shift cue ("This means") is good but the implication is buried.**
A concepts page can lean harder on the *consequence*: "All your projects show up in one `litmus runs` listing. That is the default because it makes cross-project comparisons trivial; override `data_dir` only if you need hard isolation." Right now the implication ("`litmus runs` … see everything") is stated as fact but not as a *design choice*.

---

## Audience

**WARNING — Mixed audience signals on the same page.**
Audience switches between operator ("first `litmus runs` may be slow"), platform integrator ("read with `union_by_name=true`"), and core-contributor ("PK stability. `(run_id, step_path, vector_index)` is the per-step identity"). A concepts page should pick a primary audience — likely "someone building on Litmus results" — and link out for the others. The PK-stability and `record_type` discriminator details are core-contributor concerns and belong in `reference/parquet-schema.md`, which already documents the schema.

**WARNING — "Until the 1.0 cut" language assumes the reader is tracking the release roadmap.**
Phrases like "Until the 1.0 cut, the following invariants hold" and "Schema rewrites and column removals are deferred to the 1.0 cut" presume the reader knows Litmus's versioning posture. Either link to a "pre-1.0 contract" page or rewrite as "Across all 0.x releases" / "When 1.0 ships, …". The link to `_internal/explorations/api-stability-and-versioning.md` (line 72) points into the internal tree, which is invisible to public docs readers — see the cross-links section.

**SUGGESTION — "rm ~/.local/share/litmus/data/runs/_index.duckdb*"**
For a concepts page, recommending a destructive `rm` glob to operators (without `litmus data reindex`, which exists in the CLI — `src/litmus/cli.py:2399`) skips the safe path. The `litmus data reindex` CLI command does exactly this — stops the daemon and removes the index. Recommend the named CLI command first; show the raw `rm` as a fallback only.

**SUGGESTION — Closed-enum jargon without grounding.**
"`(run_id, step_path, vector_index)` is the per-step identity in the materialized table" — these terms are not defined on this page. The reader either already knows them (in which case the bullet is redundant) or doesn't (in which case it's noise). Link `step_path` and `vector_index` to the step-hierarchy / parquet-schema reference.

---

## Accuracy

**CRITICAL — `sessions/` is listed as a top-level subdirectory but does not exist.**
The directory tree (lines 8–13) lists:

```
~/.local/share/litmus/data/
├── events/
├── channels/
├── runs/
└── sessions/    # Session index
```

But: (a) `src/litmus/data/data_dir.py` docstring lines 10–14 says "The dir holds three subsystems — `events/`, `runs/`, `channels/`" — explicitly three, not four. (b) `ls ~/.local/share/litmus/data/` on this machine returns only `channels events runs`. (c) No code under `src/litmus/` writes to a `sessions/` subdir or to `sessions.json` (grep finds nothing). The `docs/concepts/three-stores.md` storage-layout block also lists `sessions/sessions.json`, but that appears to be aspirational / out-of-date in both pages. Remove the `sessions/` row, or — if a session index *is* intended — point to where it actually lives (the events Arrow IPC files already carry `session_id`; `litmus sessions list` reads them via DuckDB, not a JSON sidecar).

**CRITICAL — The "query index schema version" claim contradicts the code.**
Lines 78–79 state: "When a newer litmus version starts, it checks the index schema version. If the index is older than the running code, litmus deletes it and rebuilds from parquet files automatically."

`src/litmus/data/_runs_duckdb_daemon.py` lines 70–76 explicitly say the *opposite*: "The schema itself is always idempotently aligned with the code via :func:`_ensure_schema` — **no version checks, no drop-and-recreate**." Lines 104–114 spell out the actual mechanism: `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN IF NOT EXISTS` — additive only, no rebuild. There is no `schema_version` field stored in `_index.duckdb`; the events daemon (`_duckdb_daemon.py:288`) likewise opens and ingests without a version probe. Rewrite to: "On startup the daemon idempotently aligns the index schema with the current code — new columns are added in place; rows ingested by older versions read NULL for those columns. No rebuild is required for schema upgrades."

**CRITICAL — The "older daemons are stopped automatically" / "whichever runs last wins the daemon" claims are unverified by code.**
The "Mixed versions" table (line 94) says "The newest version's daemon takes over. Older daemons are stopped automatically." The matching paragraph (line 102) says "whichever runs last 'wins' the daemon. The other project's next query triggers a brief daemon restart." Searching `_runs_duckdb_daemon.py` and `runs_duckdb_manager.py` finds `force_restart()` (called by `litmus data reindex`) but no automatic-takeover code path keyed on version. The daemon is per-`data_dir`, not per-version, and projects sharing the same `data_dir` simply share one daemon. Verify with the code owner whether (a) the takeover is real and I missed the code path, or (b) the doc should be rewritten to "projects sharing one data dir share one daemon; the daemon's running version is whoever started it first" with the `litmus data reindex` story for upgrades. The current wording overstates the safety/automation level.

**WARNING — DuckDB does not expand `~` in `read_parquet(...)` literals.**
Line 42:

```sql
FROM read_parquet('~/.local/share/litmus/data/runs/**/*.parquet',
                  union_by_name=true)
```

This will fail with "No files found" because DuckDB's filesystem layer does not perform tilde expansion. Either use a concrete absolute path (e.g. `'/home/you/.local/share/litmus/data/runs/**/*.parquet'`), use the shell to substitute it before invoking DuckDB, or — more representative of the platform — show `read_parquet('results/runs/**/*.parquet', ...)` from inside a project directory (matches the rest of the docs; `reference/parquet-schema.md` uses exactly this form). Equivalent pattern already passes in `docs/reference/parquet-schema.md` lines 131, 225, 362.

**WARNING — `data_dir: results` example does not match how the resolver works.**
Lines 28–31:

```yaml
name: my-project
data_dir: results    # writes to ./results/ instead of global
```

`src/litmus/data/data_dir.py` lines 50–52 resolves `data_dir` *relative to the directory containing `litmus.yaml`*: `d = root / project.data_dir`. The comment "writes to ./results/" is correct only when `litmus.yaml` is in the CWD; if it's in a parent (`_find_project_config()` walks ancestors), the override lands in that parent's `results/`. State this explicitly: "`data_dir` is resolved relative to the directory containing `litmus.yaml`, regardless of the current working directory." Otherwise users running tests from a subdirectory will be surprised when results don't appear in `./results/`.

**WARNING — `LITMUS_HOME` vs final data directory: subtle but real.**
The resolution list (line 24) says step 4 is "`~/.local/share/litmus/data/` (platform default)". The code (data_dir.py:58–60) does `home = LITMUS_HOME or platformdirs.user_data_dir("litmus")` then appends `home / "data"`. So:
- `LITMUS_HOME=/foo` ⇒ data dir is `/foo/data`, **not** `/foo`.
- `platformdirs.user_data_dir("litmus")` ⇒ `~/.local/share/litmus`, then `/data` appended ⇒ `~/.local/share/litmus/data`. Correct.

The page should call out that `LITMUS_HOME` is the *home* dir (one level above), not the data dir, otherwise users will set `LITMUS_HOME=~/.local/share/litmus/data` thinking they're matching the default — and end up with `.../data/data/`.

**SUGGESTION — "Each Parquet … file is one run" is true but understates the row model.**
A reader landing on this concepts page often wants to know what's *in* a parquet. Add one sentence: "Each parquet file is one run; rows within it are either `record_type='step'` or `record_type='measurement'` — see [parquet schema](../reference/parquet-schema.md) for the row contract." That gives the reader a hook into the actual schema reference.

**SUGGESTION — `litmus serve` / `litmus runs` mention assumes name continuity.**
`litmus runs` and `litmus serve` are accurate (see `cli.py`), but a brief parenthetical "the operator UI / CLI runs listing" is cheap and would let a reader unfamiliar with the CLI vocabulary keep reading.

---

## Gaps

**CRITICAL — Retention story is completely absent.**
The page is the natural home for "how long does Litmus keep my results?", but there is no mention of `litmus data prune` (which exists — `cli.py:2358`) or of the default-unlimited retention policy that the project memory notes (`Retention: Default to unlimited (keep everything). Opt-in via global config or project config.`). A reader landing on "Results Storage" expecting to find disk-usage / retention guidance will leave empty-handed. Add a "Retention" section: default policy, `litmus data prune --older-than 30d`, the project memory's "no surprise data loss" stance, and what gets pruned per data type (events vs channels vs runs — note that `data_prune` defaults to `("channels", "events")` and excludes `runs` by default).

**WARNING — No coverage of channel and event subdirectories beyond names.**
The directory tree lists `events/`, `channels/`, `runs/` but the page never tells the reader what file *names* / partitioning conventions to expect (date-partitioned, session-keyed). This information exists in `three-stores.md` and is essential for anyone trying to find a specific run's events on disk. Either inline a one-paragraph layout summary or link explicitly: "For the on-disk layout of each subdirectory, see [Three Stores → Storage Layout](three-stores.md#storage-layout)."

**WARNING — No mention of cross-machine / shared-NAS scenarios.**
A real-world question for hardware test fleets: can multiple stations write to a shared NFS `data_dir`? The code's locking story (`instruments/locks.py` uses `LITMUS_HOME/locks/`) and the per-`data_dir` daemon model both have implications here. Even a single sentence ("Litmus's daemon assumes single-host access to a `data_dir`; mounting a shared NAS across multiple stations is not supported in 0.x") would close the gap. Today the silence reads as "you can probably do this".

**WARNING — `data_dir: results` example doesn't show absolute paths.**
Only a *relative* path is shown. Hardware test bays often want an absolute path on a specific drive (e.g. `D:\litmus-data` on Windows). Show both forms and note that relative paths resolve from `litmus.yaml`'s directory while absolute paths are taken as-is.

**SUGGESTION — Backup / archival guidance is missing.**
"How do I back this up?" is the natural follow-on to "Where do my results go?". One sentence: "All four artefacts (events, channels, runs, index) are safe to copy when the daemon is stopped; the index is regeneratable from parquets if needed." If the answer is "stop the daemon, rsync, restart" or "snapshot the filesystem", say so.

**SUGGESTION — The page doesn't mention the project-local convention used in this repo.**
Per CLAUDE.md: "Tests in this repo write to the project-local data dir (`<repo>/data/`, scoped by the repo's `litmus.yaml`)." The page's `data_dir: results` example uses `results` rather than `data`. Reusing `data/` (or noting both choices) would match the convention readers will see in the repo and in tests.

---

## Cross-links

**CRITICAL — Public page links into `_internal/`.**
Line 72:

```
[API stability framing](../_internal/explorations/api-stability-and-versioning.md)
```

`docs/_internal/` is by convention not published / not navigable for end users (compare `docs/_internal/audits/`, which is clearly an internal artefact). Either (a) promote the relevant content to a public reference page (`reference/schema-stability.md` or a section in `reference/parquet-schema.md`) and link there, or (b) drop the link and inline the one sentence needed. Same comment at the bottom of `event-log.md:186`. Linking from public docs into internal explorations leaks the internal tree and breaks for anyone reading the published site.

**WARNING — Missing forward link to `reference/parquet-schema.md`.**
The page talks about "Parquet files and schema evolution", PK tuples, `record_type` discriminator, and `union_by_name=true` — every one of which is documented in detail in `docs/reference/parquet-schema.md` (lines 26, 34, 131, 195). Not linking it is a missed opportunity and forces concepts-page maintenance to track schema additions. Add a "See also: [Parquet schema reference](../reference/parquet-schema.md)" at the top of the schema-evolution section or at the page footer.

**WARNING — Missing forward link to `reference/cli.md#litmus-data-prune` and `litmus-data-reindex`.**
The page mentions `litmus runs`, `litmus serve`, recommends `rm …_index.duckdb*` for rebuild, but never links to the actual CLI commands (`litmus data reindex` and `litmus data prune` both exist; cli.md documents them at lines 249 and 257). Add inline links.

**WARNING — Missing forward link to `reference/configuration.md`.**
`reference/configuration.md:385` documents the `data_dir:` field in `litmus.yaml`. The page's YAML snippet should link to it so the reader can see the full schema (name, default_station, mock_instruments, etc.) — currently `data_dir:` appears as a bare key with no link to the surrounding config.

**SUGGESTION — Missing "See also" footer.**
Most concepts pages (e.g. `three-stores.md`, `event-log.md`, `architecture.md`) end with a `## See Also` section listing 3–5 related pages. This page abruptly ends after "When you might notice". Add:

```
## See Also

- [Three Stores Architecture](three-stores.md) — what events/, channels/, runs/ each hold
- [Parquet schema reference](../reference/parquet-schema.md) — full row contract
- [CLI: data commands](../reference/cli.md#litmus-data) — prune / reindex
- [Configuration: `data_dir`](../reference/configuration.md) — overriding the default
```

**SUGGESTION — Inbound link from `tutorial/03-fixtures.md` is good; mirror it back.**
`tutorial/03-fixtures.md:5` links here to explain "the row Litmus writes per test in parquet". The page itself doesn't link back to the tutorial or to any how-to. Once the reader has read "where results go", they likely want "how do I query them" → link to `how-to/querying-events.md`, `how-to/multi-dut-testing.md` (which already has a `read_parquet` example), or `how-to/traceability.md`.

---

## Coordinator notes

- The accuracy dimension produced three CRITICALs that touch the same theme: the page describes a more sophisticated versioning / multi-daemon story than the code implements. Recommend the doc owner reconcile by reading `src/litmus/data/_runs_duckdb_daemon.py` lines 60–115 and `src/litmus/data/data_dir.py` end-to-end, then rewriting the "query index" and "mixed versions" sections to match what the code actually does (idempotent ADD COLUMN; one daemon per `data_dir`).
- Both the page and `docs/concepts/three-stores.md` list a `sessions/` subdirectory that does not exist on disk and is not written by any code path. Whichever page becomes canonical for the layout, fix both together.
- The cross-link CRITICAL (public → `_internal/`) also exists in `docs/concepts/event-log.md:186`. Worth a follow-up sweep across `docs/concepts/` for any other `_internal/` links.
