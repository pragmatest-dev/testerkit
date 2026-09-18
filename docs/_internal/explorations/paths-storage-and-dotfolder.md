# Local filesystem / paths convention — the `.testerkit` dotfolder

**Status: DESIGN DECISIONS ENCODED, NOT IMPLEMENTED.** This document records
decisions already made in design discussion (see the section headers below —
each states the decision, not a menu of options) plus a full source-verified
map of every place current code resolves a local path. It is the contract for
a *later, separate* implementation task (see "Sequencing"). No code changes
accompany this doc.

Every current-behavior claim below was verified by opening the cited file and
reading the cited function on `exp/testerkit-server` (2026-09-18). Where the
decisions above contradict something the source actually does, that is
flagged explicitly rather than silently smoothed over — see "Conflicts with
source" at the end of each major section and the rollup in Open Questions.

---

## 1. Motivation

- **Consolidate scattered local state under one dotfolder.** Today TesterKit
  writes to `platformdirs.user_data_dir("testerkit")` (data), never writes to
  `user_config_dir`/`user_cache_dir` at all (grep-verified — see §3), and
  additionally scatters project-local state across a plain `data/` folder,
  `testerkit.yaml`, and (once `forward` ships for real use) a bare
  `_forward_cursor.json` sitting inside the data dir itself. There is no single
  place a user or an agent can point to and say "this machine's TesterKit
  state."
- **Consistency with the sibling project lvkit**, which already ships this
  exact shape (`~/.lvkit/` global home, `.lvkit/` project-local, verified in
  §2) — one mental model across both tools a user runs together.
- **Secure credential handling.** There is currently no on-disk credential
  store on the framework side at all (verified in §3.4) — `testerkit forward`
  reads a bearer token from a bare env var and nothing else. A durable,
  correctly-permissioned home for that token is new work, not a relocation of
  existing behavior.
- **Fix multi-repo token sprawl.** Because there is no global credential file
  today, the only way to hand a token to `testerkit forward` across multiple
  project checkouts on one machine is to re-export the env var (or duplicate
  it into each project's shell/CI config) per repo. A single global
  `~/.testerkit/credentials` fixes this at the root.
- **Clean migration off the old litmus/XDG dirs.** This machine currently has
  real data in *two* legacy locations at once (see §3.1) — migration must not
  clobber either.

---

## 2. lvkit's actual convention (mirrored, not invented)

Read directly from `/home/ryanf/repos/lvkit/src/lvkit/project_store.py` and
`/home/ryanf/repos/lvkit/src/lvkit/cache_paths.py`:

- **`PROJECT_STORE_DIR = ".lvkit"`** (`project_store.py:34`) — the same name
  is used for both the global home and a project-local store; they are
  distinguished by location, not by name.
- **`global_home()`** (`project_store.py:37-46`): `Path.home() / ".lvkit"`.
  Docstring: *"the per-user lvkit home — `~/.lvkit` (branded, like
  `~/.claude`)"*. Single source of truth — the cache root and the
  project-store finder both call this function rather than re-deriving the
  path.
- **`find_project_store(start=None)`** (`project_store.py:100-127`): walks up
  from `start` (default CWD) through `current.parents`, looking for a
  `.lvkit/` directory; **stops at the first match, at a `.git` marker, or at
  filesystem root**. Explicitly guards against treating the *global* home as
  a project store: `store.resolve() != home` (`:121`) — a bare `~/.lvkit`
  found while walking up is never mistaken for a project's own store.
- **`init_project_store(root)`** (`:130-...`): idempotent — creates `.lvkit/`
  plus a README and empty per-category index files; does not overwrite an
  existing README.
- **Cache is a *subdirectory* of the global home, not a sibling**:
  `global_cache_root()` (`cache_paths.py:46-58`) returns
  `os.environ.get("LVKIT_CACHE_DIR")` if set, **else `global_home() / "cache"`**
  — i.e. `~/.lvkit/cache`. So lvkit's actual layout is:

  ```
  ~/.lvkit/
    cache/            ← global_cache_root(); overridable via $LVKIT_CACHE_DIR
      projects/<slug>/{extract,render,diff}/...
      shared/{vilib,userlib}/<slug>/...
      adhoc/...
      _dirs.json       (display-only slug→path reverse map)
      _layout_version  (cache schema-version marker)
    (other per-user state, if any, is a sibling of cache/ under the home)

  <project>/.lvkit/    ← project-local, found by find_project_store()
    README.md
    primitives.json
    vilib/ openg/ drivers/
  ```

- **Cache is strictly deletable / regenerable**: `cleanup_legacy_cache()`
  (`cache_paths.py:519-561`) freely `shutil.rmtree`s whole subtrees of
  `global_cache_root()` on a layout-version bump — the module's own comments
  state correctness never depends on this cleanup, only reclaiming disk. Every
  cache path is reconstructible from source content + a fingerprint
  (`kind_fingerprint`, `source_fingerprint`, `extraction_fingerprint`) — never
  from state that isn't re-derivable.
- **`$LVKIT_CACHE_DIR`** (`cache_paths.py:53-55`) is described in the module
  docstring as "test hook + power-user/CI override" — it overrides only the
  cache root, not the global home itself (there is no equivalent
  `LVKIT_HOME` override for `global_home()` in this file).

This is the shape TesterKit's `.testerkit` convention mirrors: same
global/project-local split, same "global home owns a `cache/` subdir that is
strictly deletable," same idempotent init, same "don't mistake the global home
for a project store while walking up" guard, same single-source-of-truth
accessor function pattern.

---

## 3. Current TesterKit path-resolution behavior (verified against source)

### 3.1 Real state on this machine right now

```
~/.local/share/testerkit/     967 files   (data/, locks/ subdirs — verified via `ls`)
~/.local/share/litmus/       8079 files   (pre-rename legacy store)
~/.config/testerkit/            — does not exist
~/.cache/testerkit/             — does not exist
$TESTERKIT_HOME                 — unset
```

Both legacy stores hold real data. Any migration design MUST NOT silently
merge or overwrite either — see §7.

### 3.2 `resolve_data_dir()` — the primary chokepoint

`src/testerkit/data/data_dir.py:32-61`, verbatim precedence (docstring
`:16-21` matches the code exactly):

1. Explicit `path` argument (`:38-41`) — creates it via `mkdir(parents=True,
   exist_ok=True)` and returns it as-is; no further resolution.
2. `testerkit.yaml` in CWD ancestors, `data_dir` field, via
   `testerkit.connect._find_project_config()` (`:44-55`) — if found and
   `project.data_dir` is set, returns `root / project.data_dir`
   (created). Exceptions from the lookup (`ImportError, AttributeError,
   FileNotFoundError`) are swallowed and fall through.
3. `TESTERKIT_HOME` environment variable (`:58`).
4. `platformdirs.user_data_dir("testerkit")` (`:58`, the `os.environ.get`
   default) — Linux: `~/.local/share/testerkit`.

Step 3/4 are actually one line: `home = Path(os.environ.get("TESTERKIT_HOME",
platformdirs.user_data_dir("testerkit")))`, then `d = home / "data"` (`:59`).
So the **current** global default is `~/.local/share/testerkit/data/`, not
`~/.local/share/testerkit/` itself — `data/` is a subdirectory of the
platformdirs root, which also holds `locks/` (see §3.3) as a sibling.

`_find_project_config()` (`src/testerkit/connect.py:580-596`) walks
`Path.cwd()` up through `current.parent` looking for `testerkit.yaml`
(**no `.git` stop, no dotfolder check** — unlike lvkit's `find_project_store`,
this walk has no early-exit at a repo boundary). Returns
`(project_root, ProjectConfig)` via `testerkit.store.load_project`.

`ProjectConfig.data_dir` (`src/testerkit/models/project.py:87`): `str | None
= None` — a single optional string field, resolved as `root / data_dir`.

**`resolve_data_dir()` is called from ~50 sites across the codebase** (full
list in §5) — it is genuinely the chokepoint for "where does data live,"
*except* for four call sites that bypass it entirely by re-deriving the
platformdirs fallback inline (§3.3). That duplication is itself a latent bug
surface worth fixing in the same pass as the dotfolder migration.

### 3.3 The four places that duplicate the platformdirs fallback instead of calling `resolve_data_dir()`

Grep for `platformdirs.user_data_dir` across `src/` returns exactly these
occurrences (all read and confirmed identical in form:
`Path(os.environ.get("TESTERKIT_HOME", platformdirs.user_data_dir("testerkit")))`):

| File:line | Purpose | Confirmed behavior |
|---|---|---|
| `src/testerkit/data/data_dir.py:58` | The canonical resolver itself | Base case — this one is correct by definition |
| `src/testerkit/store.py:464` | `find_station_config()` machine-global fallback | Docstring literally says `~/.local/share/testerkit/stations/{id}.yaml` (`:453`); appends `stations/` to the home, **not** `data/` — a *different* subtree of the same home than `resolve_data_dir()` produces |
| `src/testerkit/instruments/locks.py:27-28` (`_testerkit_home()`) | Cross-process instrument lock files | Module docstring: *"Lock files live under `TESTERKIT_HOME/locks/`"* (`:4`) — appends `locks/`, a third distinct subtree |
| `src/testerkit/cli/data_cmd.py:167-179` (`_global_data_dir()`) | `testerkit data promote` target | Docstring literally says *"Mirrors the fallback in testerkit.data.data_dir.resolve_data_dir but ignores any project override"* (`:170-171`) — appends `data/`, matching `resolve_data_dir()`'s subtree, but via an independent re-implementation rather than a shared helper |

So today's actual global home (`~/.local/share/testerkit/` per platformdirs)
has at least three known subtrees hung off it by three independently-written
functions: `data/`, `stations/`, `locks/`. There is no single `home()`
accessor analogous to lvkit's `global_home()` — every call site re-derives
`Path(os.environ.get("TESTERKIT_HOME", platformdirs.user_data_dir("testerkit")))`
from scratch. This is the exact anti-pattern lvkit's `global_home()` avoids.
**Any dotfolder migration must introduce a single `home()`-style accessor and
repoint all four of these.**

`docs/reference/runtime/connect.md:112,131` and
`docs/concepts/data/data-stores.md:106,130` and
`docs/tutorial/09-production.md:354` currently document the
`~/.local/share/testerkit/...` paths directly (verified by reading each) —
these are user-facing docs that encode the current, pre-migration behavior
and will need updating alongside any implementation.

### 3.4 Endpoint + token config — current state (`src/testerkit/cli/forward_cmd.py`)

Read in full. Verified:

- `_TOKEN_ENV = "TESTERKIT_FORWARD_TOKEN"` (`:58`)
- `_URL_ENV = "TESTERKIT_FORWARD_URL"` (`:59`)
- The `forward` command (`:454-477`): `server = url or
  os.environ.get(_URL_ENV)`; `token = os.environs.get(_TOKEN_ENV)` — **no
  `--token` CLI flag exists**, no `testerkit.yaml` field for either is read
  here, and **there is no on-disk credential file of any kind** — the token
  lives only in the process environment. If both are missing, `forward` raises
  `click.ClickException` immediately (`:474-477`).
- `--url` is a Click option (`:432`) with `default=None`; there is no
  `--token` option at all.
- The module docstring (`:21-23`) itself already flags this: *"Auth is a
  per-bench machine token in `TESTERKIT_FORWARD_TOKEN`; the server URL is
  `--url` or `TESTERKIT_FORWARD_URL`."*
- `data-dir` for forward (`:479`) goes through `resolve_data_dir()` correctly
  — forward is data-dir-conventional even though it is env-var-only for
  auth.
- Server-side confirmation (adjacent repo, read for cross-reference only —
  not this repo's code): `testerkit-server/testerkit_server/tokens.py` mints
  `tk_`-prefixed opaque bearer tokens (`new_token()`, `:69-71`), stores only a
  SHA-256 hash (`_hash`, `:65-66`; `TokenStore.mint`, `:98-112`), and the
  plaintext is **shown once and never persisted server-side either**. This
  confirms: a lost token cannot be "looked up" anywhere, client or server —
  the only remedy is re-mint (see §6).
- `testerkit-server/docs/32-testerkit-connect-machine-enrollment.md` (dated
  2026-09-18, same day as this doc — read in full) is the server-side
  discussion draft this doc's credential-lifecycle section cross-references.
  It **explicitly labels itself "DISCUSSION DRAFT... Do not treat any choice
  here as decided"** and lists "does re-running `connect` on the same bench
  mint a second token or rotate the existing one?" as **open question #3**,
  and "keyring vs `0600` file... env-var-wins?" as **open question #8**. The
  rotate-on-reconnect behavior described in §6 below is that doc's
  *recommendation*, not a shipped or locked decision — flagged again in Open
  Questions.
- That same server doc independently confirms (its own §4 "Storage on the
  machine," `:293-306`) the same fact verified above: *"there is no on-disk
  credential store on the framework side"* and *"the framework does not
  currently use `platformdirs.user_config_dir` anywhere"* — corroborating
  the grep results in §3.3.

### 3.5 `.gitignore` — current content (verified, full file read)

```gitignore
.venv/
__pycache__/
*.pyc
*.egg-info/
dist/
/data/
/examples/*/data/
.ruff_cache/
.pytest_cache/
.coverage
.claude/settings.local.json
.claude/scheduled_tasks.lock
.tmp/
examples/uv.lock
examples/*/uv.lock
dist/
.playwright-mcp/
report_*.html
*.mp4
*.pdf

# Generated skill refs (built from docs/ and models.py)
testerkit/skills/refs/

# JSON schemas — regenerated via `testerkit schema export` / `testerkit init`
# / `testerkit schema refresh`. The source of truth is the live Pydantic
# models in src/testerkit/models/. Don't commit snapshots.
/schemas/
.benchmarks/

# Transient logs (daemon, serve, stream, uicheck)
*.log

# Local databases and env files
*.duckdb
*.duckdb.wal
.env
.env.local
.env.*.local
```

Two entries are the ones a `.testerkit/` dotfolder is meant to replace:
`/data/` and `/examples/*/data/` (the repo's own top-level `data/`, which is
this repo's project-local override per `testerkit.yaml`'s `data_dir: data`
convention — not yet verified in this repo's own `testerkit.yaml`, but the
`init.py` scaffold below confirms this is the standard shape for any project
that sets a local override).

### 3.6 `testerkit init` project scaffold (`src/testerkit/init.py`, read in full)

- `init_project()` (`:41-452`) creates a plain `.gitignore` (`:260-290`) with
  a **hardcoded** `data/` and `reports/` block (`:270-275`) — this literal
  string is a *second* place (besides the checked-in top-level `.gitignore`)
  that would need to change to `.testerkit/` under the new convention.
- The starter tier (`tier="bench"` / `starter=True`) sets `data_dir: "data"`
  in the generated `testerkit.yaml` (`:241-248`) specifically so starter
  learning runs don't pollute the platformdirs global store
  (comment `:236-240`); `testerkit data promote` later removes that override
  (`README.md` template, `:319-333`).
- The generated `README.md` (`:292-336`) hardcodes
  `~/.local/share/testerkit/data/` in its prose (`:331`) — a third place
  documenting the current global path literally.
- No `.env`/credential scaffolding of any kind exists in `init_project()` —
  confirmed by reading the full function; there is nothing to migrate here
  for the credential file, only new work.
- **No existing `.testerkit`/dotfolder collision**: grepped `\.testerkit\b`
  across `src/` and `docs/` — every hit is a CSS class name
  (`.testerkit-time`, `.testerkit-page`, `.testerkit-sticky-table`,
  `.testerkit-data-table`, `.testerkit-date-utc` in
  `src/testerkit/ui/shared/components.py`, `layout.py`,
  `static/global.css`) or a pytest marker mention
  (`@pytest.mark.testerkit` in `docs/integration/runtime/pytest-existing.md`).
  Nothing currently reads or writes a directory literally named `.testerkit`.
  The name is free to adopt.

---

## 4. The `.testerkit` dotfolder — target design

### 4.1 Global `~/.testerkit/` (mirrors lvkit's `~/.lvkit/`)

```
~/.testerkit/
  cache/            ← STRICTLY DELETABLE (regenerable only — mirrors lvkit's cache/)
  data/             ← durable results/data, the global fallback store (name settled — see below)
  credentials       ← 0600 file (or OS keyring); the one machine token. SECRET.
```

- `cache/` holds anything that is a pure derived artifact of durable state —
  the DuckDB index files (`_index.<fingerprint>.duckdb`,
  `_index.duckdb` per `docs/concepts/data/data-stores.md:88,92,138` — "always
  a disposable cache... delete it and it rebuilds"), and any future
  lvkit-style render/derived cache TesterKit grows. Same guarantee lvkit's
  cache carries: delete the whole subtree, lose nothing but rebuild time.
- `data/` — **name settled, not open**: `data/` is the durable store, both
  globally and project-locally. This matches today's `resolve_data_dir()`
  internals exactly (`d = home / "data"`, `data_dir.py:59` — the function
  already names its subtree `data`, not `results`) and continues a prior
  deliberate rename in this same codebase away from `results` terminology
  (the project's own history: an earlier `results_dir` field was renamed to
  `data_dir` — see `docs/_internal/project-history-narrative.md:144,267`,
  which lists `results_dir → data_dir` explicitly among the completed
  renames). `data/` is what currently lives directly under the platformdirs
  root. This is durable and must never be swept by a cache-clean.
- `credentials` is a `0600` file (or OS keyring where available) holding the
  single machine token for `testerkit forward` / `testerkit connect`. It is
  **kept outside `cache/`** specifically so that wiping the deletable cache
  can never de-auth the machine. It is **one file per machine** — this is the
  multi-repo sprawl fix from §1: global, not duplicated per project checkout.
- **Lock files** (currently `TESTERKIT_HOME/locks/`, `instruments/locks.py`)
  and **station YAML machine-global fallback** (currently
  `TESTERKIT_HOME/stations/`, `store.py:453`) are existing global-tier
  subtrees that move under the same `~/.testerkit/` root once the single
  `home()` accessor lands (§3.3) — whether they sit in `data/`, directly
  under `~/.testerkit/`, or get their own named subdir is an implementation
  detail for the later task, not re-litigated here; the point settled by this
  doc is that they stop being independently-derived platformdirs paths and
  start being subtrees of one accessor.

### 4.1a Internal layout of `data/` — by store kind (verified against source + live disk)

`resolve_data_dir()` does **not** return the home directory itself — it
returns `<home>/data` (`d = home / "data"`, `data_dir.py:59`). Every store
kind is then a sibling subdirectory hung directly off that returned path,
each appended by its own call site rather than by a shared per-kind
constant:

- `.../data/runs` — `src/testerkit/cli/daemon.py:29`
  (`Path(resolve_data_dir()) / "runs"`), `src/testerkit/data/run_store.py:61`
  (`self._runs_dir = data_dir / "runs"`), `src/testerkit/api/app.py:99`
  (`runs_dir = results / "runs"`).
- `.../data/events` — `src/testerkit/cli/daemon.py:28`,
  `src/testerkit/data/event_store.py:172`
  (`self._events_dir = self._data_dir / "events"`),
  `src/testerkit/api/app.py:100`.
- `.../data/channels` — `src/testerkit/cli/daemon.py:30`,
  `src/testerkit/channels.py:257` (`resolve_data_dir() / "channels"`),
  `src/testerkit/api/app.py:101`.
- `.../data/files` — `src/testerkit/data/files/store.py:81`
  (`self._files_dir = self._data_dir / "files"`),
  `src/testerkit/api/app.py:102`.
- `testerkit forward`'s three per-store cursor files (§4.2) confirm the same
  siblings again: `events_dir = resolved / "events"`,
  `channels_dir = resolved / "channels"`, `files_dir = resolved / "files"`
  (`forward_cmd.py:480,482,484`).

**Confirmed on the live machine** (`ls -a
~/.local/share/testerkit/data/{runs,events}/`, run during this research):

```
~/.local/share/testerkit/           ← platformdirs.user_data_dir("testerkit") today
├── data/                           ← resolve_data_dir()'s return value (data_dir.py:59)
│   ├── runs/
│   │   ├── runs/                   ← the actual date-partitioned parquet tree
│   │   │                             (data_cmd.py:238 confirms this double
│   │   │                             "runs/runs" nesting: src_runs_root =
│   │   │                             src_data / "runs" / "runs")
│   │   ├── _daemon.log
│   │   ├── _epochs.json
│   │   ├── _index.927a7b67dc38.duckdb   ← content-addressed query index
│   │   └── _runs_duckdb.lock
│   └── events/
│       ├── 2026-07-25/ ... 2026-09-18/  ← date-partitioned event WAL segments
│       ├── _daemon.log
│       ├── _epochs.json
│       ├── _index.5060fe6df3b6.duckdb   ← same content-addressed pattern as runs
│       └── _duckdb.lock
│   (no channels/ or files/ subdir exists yet on this machine — this
│    project's global store has never had a channel/file write; both are
│    real siblings per the file:line citations above, just empty-to-date here)
└── locks/                          ← SIBLING of data/, not inside it —
                                        instruments/locks.py:34-37 anchors
                                        _lock_dir() at _testerkit_home() / "locks",
                                        i.e. off the platformdirs root directly,
                                        not off resolve_data_dir()'s "data" subtree
```

Both `_index.<fingerprint>.duckdb` files match the "content-addressed query
index" contract already documented in
`docs/concepts/data/data-stores.md:88,92,138` ("always a disposable cache —
delete it and it rebuilds"). `locks/` sitting as a **sibling of `data/`**
rather than inside it (confirmed both on disk and in
`instruments/locks.py:27-37`, which anchors `_lock_dir()` off
`_testerkit_home()` — the bare platformdirs root — not off
`resolve_data_dir()`) is exactly the discrepancy §3.3 already flagged: locks
and the station-YAML global fallback are subtrees of the *home*, while
runs/events/channels/files are subtrees of the home's `data/` subtree — two
different roots, reached by four independently-written functions.

**Target layout under `.testerkit`** — the by-kind structure inside `data/`
is unchanged (same four store kinds, same index-file convention); only the
enclosing root moves, and `locks/` + the station fallback become siblings of
`data/` under the new root exactly as they are siblings of it today:

```
~/.testerkit/
  data/
    runs/
      runs/{date}/...            (unchanged shape)
      _index.<fingerprint>.duckdb
      _epochs.json  _daemon.log  _runs_duckdb.lock
    events/
      {date}/...                 (unchanged shape)
      _index.<fingerprint>.duckdb
      _epochs.json  _daemon.log  _duckdb.lock
    channels/                    (same sibling shape, per channels.py:257)
    files/                       (same sibling shape, per files/store.py:81)
  cache/                         ← NEW — strictly deletable (§4.1)
  credentials                    ← NEW — 0600, secret, NEVER inside cache/
  locks/                         ← moves here as a sibling of data/, matching
                                     its CURRENT sibling relationship to the
                                     platformdirs root (instruments/locks.py:34-37)
  stations/                      ← the machine-global station fallback
                                     (store.py:453-465) — also currently a
                                     sibling of data/ under the platformdirs
                                     root; moves the same way

<project>/.testerkit/
  data/
    runs/  events/  channels/  files/   ← identical by-kind shape, project-scoped
  cache/
  (forward cursor files — §4.2)
```

Whether the on-disk index files (`_index.*.duckdb`) move into the new
`cache/` tier (they are, by the docs' own "disposable, rebuilds from source"
definition, cache-shaped) or stay physically alongside the data they index
(today's behavior, and simpler — no cross-directory pointer needed) is an
implementation call for the later task, not decided here; either choice is
consistent with the tier-discipline table in §5.3, since "disposable" is the
property that matters, not the physical directory.

### 4.2 Project-local `.testerkit/` (nearest-ancestor, GITIGNORED)

```
<project>/.testerkit/
  data/             ← same name as global, see §4.1
  cache/
  <forward WAL cursor>   ← currently a bare _forward_cursor.json inside data_dir/events/
  <local overrides>
```

- Discovered by nearest-ancestor walk from CWD, mirroring
  `find_project_store()`'s shape (§2) — **not** `_find_project_config()`'s
  current CWD-to-filesystem-root walk with no `.git` stop (§3.2); the new
  walk should adopt lvkit's stop-at-`.git` behavior since that is the
  documented, deliberate lvkit convention this doc is mirroring.
- Holds the same `data/` + `cache/` split as the global home, scoped to this
  project.
- Holds the `forward` command's cursor files, currently
  `<data_dir>/events/_forward_cursor.json`,
  `<data_dir>/channels/_forward_cursor.json`,
  `<data_dir>/files/_forward_cursor.json` (`forward_cmd.py:481,483,485`) —
  these are WAL replication state, not test-result data, so they arguably
  belong in `.testerkit/` rather than inside the data store proper. (Not
  re-litigating exactly where; flagging that these three cursor files are a
  concrete "local overrides"-shaped thing that exists today and needs a new
  home.)
- **Does NOT hold the token.** The credential is global-only — a project
  checkout is not a security boundary for a machine identity, so a
  project-local credential would just be another copy to leak or go stale.

### 4.3 `testerkit.yaml` (top-level, COMMITTED) — the committed/ignored split

- `testerkit.yaml` stays exactly where it is today (project root, committed)
  and keeps its role as shareable project config — `name`, `data_dir`,
  `default_station`, `channels`/`files`/`session`/`stream` tuning,
  `multi_site`, etc. (`src/testerkit/models/project.py:81-106`, full field
  list read).
- It gains one new field for the endpoint: `server.url` (see §5) — a
  shareable, non-secret value, correctly committed.
- `.testerkit/` (the whole dotfolder) is gitignored in full.
- **Hard rule made explicit**: no secrets in the committed half. The token
  never has a `testerkit.yaml` field — see §5.

**Conflicts with source:** none — `testerkit.yaml` today has no `server`
namespace at all (confirmed by reading the full `ProjectConfig` model); adding
one is new, additive schema work, not a rename of an existing field.

---

## 5. Path resolution + tiers

### 5.1 Data-dir precedence — current vs. target

**Current** (verbatim from `data_dir.py`, restated for contrast):
1. Explicit `path=` argument
2. `testerkit.yaml` `data_dir:` (project, found via unbounded CWD-to-root walk)
3. `TESTERKIT_HOME` env var
4. `platformdirs.user_data_dir("testerkit")` + `/data`

**Target:**
1. Explicit `path=`/`--data-dir` argument (unchanged — still the escape hatch
   for tests/migration scripts)
2. Project `.testerkit/` / `testerkit.yaml` `data_dir:` (found via a
   `.git`-bounded nearest-ancestor walk, mirroring lvkit's
   `find_project_store`)
3. `TESTERKIT_HOME` environment variable (unchanged — still the full-home
   override, distinct from the new cache-only override below)
4. Global `~/.testerkit/data/`

The structural change from current to target is step 4's target directory
(`~/.local/share/testerkit/data/` → `~/.testerkit/data/`) and step 2's walk
boundary (unbounded → `.git`-stopped) — the *shape* of the precedence chain
(explicit → project → env → global default) is unchanged and does not need
re-justifying.

### 5.2 New cache-root override: `TESTERKIT_CACHE_DIR`

Mirrors lvkit's `$LVKIT_CACHE_DIR` (`cache_paths.py:53-55`) exactly: an env
var that overrides only the cache root, independent of `TESTERKIT_HOME`
(which continues to override the whole home, data included). No such variable
exists in TesterKit today (grep-confirmed — the only cache-adjacent code
found is the DuckDB `_index.*.duckdb` file convention documented in
`docs/concepts/data/data-stores.md`, which today lives *alongside* the data
it indexes, not under a separate cache root — moving those index files under
`~/.testerkit/cache/` vs. leaving them next to their data is an implementation
call for the later task, not decided here).

### 5.3 Tier discipline

| Tier | Contents | Deletable? | Location |
|---|---|---|---|
| Config | `testerkit.yaml` (project), station/part/fixture YAML | No — hand-authored, committed | project root (unchanged) |
| Data | Parquet runs, event WAL, channel segments, file blobs | No — durable record | `~/.testerkit/data/` (global) or `.testerkit/data/` (project-local) |
| Cache | DuckDB query indexes, any future derived/render cache | Yes — always rebuildable | `~/.testerkit/cache/` (global) or `.testerkit/cache/` (project-local) |
| Credential | The one machine token | No — but never a cache; see below | `~/.testerkit/credentials` (global only) |

**Hard rule**: the credential must never be cache-tier. This is the same
principle lvkit demonstrates structurally by putting `cache/` *inside*
`global_home()` as a clearly-scoped, freely-`rmtree`'d subtree rather than
letting cache-cleanup logic touch the home root itself
(`cleanup_legacy_cache()` only ever removes named subtrees under
`global_cache_root()`, never the home directory or anything outside `cache/`)
— TesterKit's `credentials` file sits as a sibling of `cache/`, not inside
it, for the identical reason: a "clear my cache" operation must be safe to
run without re-authenticating.

---

## 6. Endpoint + token config

### 6.1 URL

- Precedence: `--url` → `TESTERKIT_URL` → `testerkit.yaml` `server.url` →
  default (no default exists today — `forward` currently raises if unset;
  whether the target design ships a hardcoded default cloud URL is out of
  scope for this doc).
- Committable — not a secret, may live in `testerkit.yaml`.
- **Rename**: `TESTERKIT_FORWARD_URL` → `TESTERKIT_URL`
  (`src/testerkit/cli/forward_cmd.py:59`, and its two docstring mentions at
  `:23` and `:432`'s help string). Confirmed **free to rename**: `forward` and
  the not-yet-built `connect`/other cloud commands are unreleased, so this is
  not a breaking change to any shipped surface — `git log`/docs show no other
  consumer of `TESTERKIT_FORWARD_URL` (grep-confirmed: the only occurrences
  in the whole repo are inside `forward_cmd.py` itself).

### 6.2 Token

- Precedence: `TESTERKIT_TOKEN` env → global `~/.testerkit/credentials`.
- **Never** in `testerkit.yaml`, never committed.
- **Rename**: `TESTERKIT_FORWARD_TOKEN` → `TESTERKIT_TOKEN`
  (`forward_cmd.py:58`, `:22` docstring, and the `raise
  click.ClickException` message at `:477`). Same free-rename justification as
  the URL var — grep-confirmed single-file usage.
- **Generalize off the `FORWARD_` prefix**: both env vars currently carry a
  `forward`-specific name even though the underlying credential/endpoint pair
  is meant to be shared by `testerkit forward`, the not-yet-built `testerkit
  connect`, and any future cloud command — the rename doubles as removing an
  implicit assumption that only `forward` will ever need these.

**Conflicts with source:** none identified — this is purely additive/rename
work on a module that is itself marked `REVIEW NEEDED` in its own docstring
(`forward_cmd.py:25-31`, the server-side ingest endpoints it POSTs to don't
exist yet), so there is no shipped external consumer to break.

---

## 7. Credential lifecycle (rotate-on-reconnect)

**This section states the current *recommendation* from the adjacent
server-side discussion draft, not a locked decision** — see the explicit
caveats quoted in §3.4. Recorded here because it is the design this doc's
credential file (§4.1, §6.2) is built to hold, but the mechanics below are
still open on the server side per that doc's own open-questions list.

- Tokens are **hashed at rest server-side**
  (`testerkit-server/testerkit_server/tokens.py:65-71`, `_hash` = SHA-256;
  `TokenStore.mint`, `:98-112`, persists only `{hash: scope}` and returns the
  plaintext exactly once). This is server-side code, cited for context only —
  not something this repo implements or changes.
- Because only the hash is ever stored, **the plaintext is unrecoverable**: a
  lost token has no "retrieve" path anywhere in the system, client or server —
  the only remedy is to mint a new one (`tokens.py`'s own docstring, `:11`:
  *"Rotatable — mint issues a new token; revoke removes one..."*).
  `TokenStore` today has **no expiry field** at all
  (`TokenScope`, `:33-49` — `created_at` only, no `expires_at`); tokens are
  long-lived until explicitly revoked.
- **Re-`connect` mints a NEW token.** With a stable machine identity, the
  server is expected to **rotate**: mint a new token, then revoke the old one
  for that same `(org, agent_id)` — `revoke_agent(org, agent_id)`
  (`tokens.py:114-134`) already exists and is exactly the primitive this
  rotation would call, org-scoped so a same-named agent in a different org is
  never touched. **Without a recoverable stable identity**, the server instead
  mints a token for what looks like a *new* machine and should flag the old
  one as stale (a same-name/host "supersede?" prompt) rather than silently
  leaving two live tokens for what is really one bench.
- **No token TTL exists today**; rotate-on-reconnect is scoped to solving
  *lost-token cleanup* (an operator re-running `connect` after losing/rotating
  a token shouldn't accumulate orphaned live tokens). A TTL is explicitly
  called out in the server doc as later defense-in-depth, not a v1
  requirement (`docs/32-...md` §4 "Expiry / rotation" and open question #4).
- **What "stable machine identity" resolves to is an open question** — the
  server doc's open question #3 asks this directly (*"Is a 'machine' 1:1 with
  a physical bench, with a station config, with an `agent_id` string? Does
  re-running `connect` on the same bench mint a second token or rotate the
  existing one?"*). This doc does not answer it; flagged again in §8.
- Cross-reference: `testerkit-server/docs/32-testerkit-connect-machine-enrollment.md`
  (full doc read for this exploration) is the source of the two-path
  enrollment design (interactive device-flow vs. admin-issued enrollment key)
  that `testerkit connect` would eventually implement; this doc's credential
  *storage* section (§4.1/§6.2) is the framework-side counterpart to that
  server doc's §4 "Storage on the machine," which independently arrived at
  "keyring-preferred, `0600` fallback" as its own recommendation.

---

## 8. Open questions / decisions to confirm

**Settled, not open:** the durable-store subdirectory is named `data/` —
both globally (`~/.testerkit/data/`) and project-locally
(`.testerkit/data/`). This matches `resolve_data_dir()`'s own internal
naming (`d = home / "data"`, `data_dir.py:59`) and continues a prior
deliberate rename already made in this codebase away from `results`
terminology (`results_dir → data_dir`, per
`docs/_internal/project-history-narrative.md:144,267`). Not re-litigated
below.

1. **Cache override env var name.** This doc assumes `TESTERKIT_CACHE_DIR`
   (mirroring `LVKIT_CACHE_DIR`) but that exact name is not yet locked
   anywhere in code or a prior decision record — confirm before implementing.
2. **Auto-created vs. opt-in project-local store.** lvkit's project store is
   explicitly opt-in (`init_project_store()` is a separate call from normal
   resolution; `find_project_store()` returns `None` and lvkit falls back to
   its bundled data when no `.lvkit/` exists). Whether TesterKit's
   project-local `.testerkit/` should be **auto-created on first write** (more
   convenient, matches how a project's top-level `data/` already gets
   auto-`mkdir`'d today per `resolve_data_dir()`'s `d.mkdir(parents=True,
   exist_ok=True)`) or **opt-in via `testerkit init`/`connect`** (more
   explicit, matches lvkit) is not decided.
3. **Exact machine-identity source for rotate.** Ties directly to the
   server-side open question #3 quoted in §7 — this doc cannot resolve it
   unilaterally since the identity source (hostname? a generated machine UUID
   persisted in `~/.testerkit/credentials` itself? the station config id?)
   determines what the framework side needs to read/write at `connect` time.
4. **Whether the on-disk credential prefers OS keyring or goes straight to a
   `0600` file.** The server-side doc's own open question #8 states this
   plainly as unresolved ("OS keyring vs `0600` file... and does
   stored-credential or env var win?"). This doc's §4.1/§6.2 describe the
   file as the concrete artifact but do not mandate file-over-keyring as the
   final answer — only that whichever is chosen, it sits outside `cache/`.
5. **Source-review findings that constrain implementation** (not
   contradictions of the decisions above, but real facts the implementer must
   account for): (a) the platformdirs fallback is independently re-derived in
   four places today (§3.3) and must be consolidated into one `home()`-style
   accessor as part of this work, not left as four call sites each rewritten
   individually; (b) `_find_project_config()`'s CWD walk has no `.git`
   boundary today (§3.2) — adopting lvkit's bounded walk is a **behavior
   change**, not a pure rename, and should be called out to reviewers as such;
   (c) three per-store `_forward_cursor.json` files already exist inside the
   data dir (`forward_cmd.py:481,483,485`) and are a concrete migration
   target for "local overrides" in `.testerkit/` (§4.2).

---

## 9. Migration (careful-not-destructive, not greenfield)

The real work here is **not clobbering** — this machine alone has two legacy
stores with real data in them simultaneously (§3.1: 967 files at
`~/.local/share/testerkit`, 8079 at `~/.local/share/litmus`). lvkit, by
contrast, ships its dotfolder convention on a project with existing *users*
but no comparable "two legacy stores on one machine" hazard, so lvkit's own
code has no analogous migration guard to copy from — this section is new
design, informed by lvkit's shape but not copied from lvkit's migration logic
(lvkit's only migration logic, `cleanup_legacy_cache()` in §2, is a
same-generation cache-layout bump, not a cross-brand data migration; it
freely deletes because cache is by definition regenerable — the opposite
risk profile from durable run data).

**Global migration, one-time, on first resolution under the new scheme:**

- Detect old `~/.local/share/litmus` (pre-rename brand) and old XDG
  `~/.local/share/testerkit` (+ `~/.config/testerkit`, `~/.cache/testerkit`
  variants, though neither exists on this machine today per §3.1) →
  **move into `~/.testerkit/` only when the target is empty.** If
  `~/.testerkit/` already has content, **log and leave both legacy dirs in
  place** — never silently merge two stores. Given this machine has *two*
  non-empty legacy stores at once, the guard must handle "found more than one
  legacy candidate" explicitly (log all found candidates and require manual
  resolution) rather than picking one arbitrarily.
- **Project migration**: top-level `data/` (the `ProjectConfig.data_dir:
  "data"` convention from `init.py`) → `.testerkit/data/`, same don't-clobber
  rule (only auto-move when the destination doesn't already exist).
- **One-time, log once** — not a check-on-every-invocation. (Exact mechanism
  — a marker file, a version stamp analogous to lvkit's `_layout_version`
  string in `cleanup_legacy_cache()` — is an implementation detail for the
  later task.)
- lvkit's `_layout_version` marker pattern (`cache_paths.py:509-516,549-561`:
  a plain string file compared on every cache-root touch, triggering a
  one-time cleanup on mismatch) is the closest verified precedent for "how do
  we know migration already ran," even though lvkit's version is scoped to
  cache (freely destructive) and TesterKit's would gate a durable-data move
  (must be additive/non-destructive) — the *mechanism* transfers, the
  *destructiveness* does not.

---

## 10. Gitignore

- Replace the current two scattered entries — `/data/` and
  `/examples/*/data/` (`.gitignore:6-7`, verified) — with a single
  `.testerkit/` entry.
- `testerkit init` (`src/testerkit/init.py:260-290`) and the future
  `testerkit connect` should **auto-ensure** `.testerkit/` is present in the
  project `.gitignore` (append if missing, same idempotent-merge spirit
  `init.py` already uses for `.vscode/settings.json` — see the merge-not-skip
  comment at `:354-359`) so results/cursors/credentials can never be
  accidentally committed. Note this repo's own `init.py` scaffold currently
  hardcodes the gitignore *body* rather than merging into an existing file
  (`:261-262`: `if not gitignore_path.exists()` — full skip when one already
  exists) — unlike the `.vscode/settings.json` path, a pre-existing
  `.gitignore` today is **left completely untouched**, which means
  auto-ensuring `.testerkit/` needs new merge logic, not a copy of the
  existing gitignore-writing code path.

---

## 11. Affected code — the full touchpoint map

### 11.1 The resolution chokepoint itself

| File:line | What it does today | What changes |
|---|---|---|
| `src/testerkit/data/data_dir.py:32-61` (`resolve_data_dir`) | 4-step precedence chain, global default `platformdirs.user_data_dir("testerkit")/data` | Step 4 target becomes `~/.testerkit/data/`; step 2's project walk gains a `.git` boundary |

### 11.2 The three duplicated platformdirs fallbacks (§3.3) — need a shared `home()` accessor

| File:line | Subtree |
|---|---|
| `src/testerkit/store.py:464` (`find_station_config`) | `stations/` |
| `src/testerkit/instruments/locks.py:27-28` (`_testerkit_home`) | `locks/` |
| `src/testerkit/cli/data_cmd.py:167-179` (`_global_data_dir`) | `data/` (promote target) |

### 11.3 Env var rename sites

| File:line | Current | New |
|---|---|---|
| `src/testerkit/cli/forward_cmd.py:58` | `_TOKEN_ENV = "TESTERKIT_FORWARD_TOKEN"` | `"TESTERKIT_TOKEN"` |
| `src/testerkit/cli/forward_cmd.py:59` | `_URL_ENV = "TESTERKIT_FORWARD_URL"` | `"TESTERKIT_URL"` |
| `src/testerkit/cli/forward_cmd.py:21-23` (module docstring) | mentions both old names | update prose |
| `src/testerkit/cli/forward_cmd.py:432` (`--url` help string) | `f"...(or ${_URL_ENV})"` | inherits from constant rename |
| `src/testerkit/cli/forward_cmd.py:474-477` (`click.ClickException` messages) | reference `${_URL_ENV}` / `${_TOKEN_ENV}` | inherits from constant rename |

### 11.4 Gitignore + scaffold sites

| File:line | Current | Change |
|---|---|---|
| `.gitignore:6-7` | `/data/`, `/examples/*/data/` | replace with `.testerkit/` |
| `src/testerkit/init.py:260-290` (`init_project`, gitignore block) | hardcoded `data/`/`reports/` body, full-skip if file exists | new content; needs merge-if-exists logic (currently none) |
| `src/testerkit/init.py:236-248` | starter `data_dir: "data"` override in generated `testerkit.yaml` | becomes `.testerkit/data` (or drops the override entirely if project-local is auto-created — ties to Open Question 2) |
| `src/testerkit/init.py:292-336` (README template) | hardcodes `~/.local/share/testerkit/data/` in prose (`:331`) and a `data/` row in the folder table (`:308`) | update both |

### 11.5 `testerkit data promote` (`src/testerkit/cli/data_cmd.py`)

- `_global_data_dir()` (`:167-179`) — the promote target; see §11.2.
- The `data_promote` command (`:196-236`) resolves `project.data_dir` via
  `_find_project_config()` and compares `src_data == dst_data`
  (`:225-236`) — this whole flow's *meaning* changes once the project-local
  default becomes `.testerkit/data/` rather than an opt-in `data_dir:`
  override; whether "promote" still makes sense as a concept once every
  project has a project-local store by convention is a design question for
  the implementation task, not answered here.

### 11.6 Documentation sites with hardcoded current paths (verified by reading each)

- `docs/concepts/data/data-stores.md:106,130` — resolution-order list +
  a `read_parquet` example path.
- `docs/reference/runtime/connect.md:112,131` — lock-file location + station
  machine-global fallback path.
- `docs/tutorial/09-production.md:354` — promote-target prose.
- `docs/_internal/explorations/docs-corpus-review.md:597` — mentions a
  `~/.config/testerkit/config.yaml` global file as something a prior doc pass
  flagged; **this file does not exist in code today** (grep-confirmed no
  `user_config_dir` call anywhere in `src/`) — it appears to be either a
  planned-but-unbuilt idea or a doc error caught by that prior review; noted
  here so this doc's `.testerkit/` design doesn't get confused with it. If a
  future global YAML config is ever built, `~/.testerkit/config.yaml` would
  be its natural home under this convention, but that is out of scope here.

### 11.7 The ~50 `resolve_data_dir()` call sites (chokepoint consumers — no individual change needed once §11.1 lands)

Grep-enumerated in full during research (representative spread, not
exhaustive re-listing here since each is a pure pass-through): `client.py:391`,
`channels.py:257`, `grafana/server.py:282`, `grafana/cli.py:75`,
`api/runner.py:37,221`, `api/app.py:98,383`,
`analysis/runs_query.py:144`, `analysis/measurements_query.py:888`,
`analysis/steps_query.py:167`, `pytest_plugin/hooks.py:298,361`,
`pytest_plugin/__init__.py:299`, `cli/forward_cmd.py:479`, `cli/daemon.py:28-30`,
`cli/_common.py:113`, `data/files/store.py:80`, `data/event_store.py:165,171`,
`data/run_store.py:59`, `data/backends/parquet.py:178`,
`reports/core.py:94`, `ui/pages/metrics_page.py:83,494,689`,
`ui/pages/files/list.py:182`, `ui/pages/channels/list.py:283`,
`ui/pages/channels/detail.py:116`, `ui/pages/explore.py:247,706`,
`ui/pages/results/list.py:110`, `ui/pages/results/detail.py:322`,
`ui/shared/services.py:1571-1574,1677-1680,1695-1699`,
`mcp/tools.py:394,502,1128,1240-1253,1370,1406,1430,1458,1534,1650,1656,1672,1689,1708,1734,1837`.
None of these need to change *code* when §11.1 lands — that is the point of
having a single chokepoint — but every one of them is a place where the
*effective* data location shifts the moment `resolve_data_dir()`'s global
fallback changes, so they are listed here as the verification surface for
"did the migration actually take effect everywhere," not as edit targets.

### 11.8 Touchpoint count

- **Direct path-resolution functions/constants**: 4 (`resolve_data_dir` +
  the 3 duplicated fallbacks in §11.2).
- **Env var definition + every reference to rename**: 5 lines across 1 file
  (`forward_cmd.py`).
- **Gitignore-related code/config sites**: 4 (`.gitignore` itself +
  3 spots inside `init.py`).
- **User-facing docs hardcoding the current global path**: 4 files.
- **Pure `resolve_data_dir()` consumers** (no edit needed, verification
  surface only): ~50 call sites across ~30 files, grep-enumerated in §11.7.
- **Total distinct file:line touchpoints identified**: **17** requiring an
  actual code/config/doc edit, plus the ~50-site consumer surface that
  inherits the change for free through the chokepoint.

---

## 12. Affected documentation — every reference to the OLD convention

Full-repo grep across `docs/`, `examples/`, `README*.md`, and `CLAUDE.md` for
every reference to the conventions this migration replaces: the hardcoded
global path, XDG/`platformdirs` mentions, `TESTERKIT_HOME`'s described
resolution target, the `TESTERKIT_FORWARD_*` env vars, and any lingering
`litmus` reference. Each needs updating when the implementation task lands;
none are edited by this doc-only pass.

### 12.1 Hardcoded old global path (`~/.local/share/testerkit`)

| File:line | Content | Generated? |
|---|---|---|
| `docs/reference/runtime/connect.md:112` | "Lock files live in `~/.local/share/testerkit/locks/`..." | No — hand-editable |
| `docs/reference/runtime/connect.md:131` | "`~/.local/share/testerkit/stations/cell-7.yaml` (machine-global)" | No |
| `docs/tutorial/09-production.md:354` | "Copies the rest into the global store (`~/.local/share/testerkit/data/` on Linux...)" | No |
| `docs/concepts/data/data-stores.md:106` | Resolution-order item 4: `` `~/.local/share/testerkit/data/` (platform default via `platformdirs`) `` | No |
| `docs/concepts/data/data-stores.md:130` | `read_parquet('~/.local/share/testerkit/data/runs/**/*.parquet', ...)` SQL example | No |
| `examples/scripts/demo_queries.sql:9,18,32,42,55,72` | Six `FROM '~/.local/share/testerkit/data/runs/**/*.parquet'` example queries (one commented-out `SET VARIABLE` at `:9`, five live `FROM` clauses) | No — plain example script, not under `docs/reference/` |

### 12.2 `TESTERKIT_HOME` resolution-chain description (wording changes from "platformdirs default" to "`~/.testerkit/`")

| File:line | Content | Generated? |
|---|---|---|
| `docs/how-to/execution/multi-uut-testing.md:126` | "...resolved from `--data-dir` → project `testerkit.yaml` → `TESTERKIT_HOME` → platform default." | No |
| `docs/reference/data/query-api.md:13` | "...resolution is `_data_dir=<path>` arg → project `testerkit.yaml` `data_dir:` → `TESTERKIT_HOME` env var → platform default." | **query-api.md is one of the 5 generator-marker pages** (per this repo's CLAUDE.md) — but line 13 sits **outside** its `<!-- GENERATED:query-api-classes:start -->`/`:end` block (that block spans lines 28-232, confirmed by reading the file) — so this specific line is ordinary hand-editable prose, not protected content. Edit directly; no regen needed for this line. |
| `docs/reference/runtime/connect.md:21` | Table row: "Resolution: explicit arg → `testerkit.yaml` `data_dir:` → `TESTERKIT_HOME` → the platform default user-data directory." | No — `connect.md` is not one of the 5 generator-marker pages |
| `docs/reference/cli.md:645` | Environment-variables table: "`TESTERKIT_HOME` \| Default data directory. Resolution: ... → `platformdirs.user_data_dir("testerkit")`." | **cli.md is one of the 5 generator-marker pages**, but line 645 sits **outside** the `<!-- GENERATED:cli-commands:start -->`/`:end` block (confirmed: that block spans lines 20-593, ending well before 645) — the "Environment variables" section is hand-written. Edit directly. |
| `docs/concepts/data/data-stores.md:105` | Resolution-order item 3 header line, immediately preceding the item 4 hardcoded path already listed in §12.1 | No |
| `docs/reference/configuration.md:27` | `` data_dir: data  # optional — runs/, events/, channels/ subtree (default: ./data) `` | **configuration.md is one of the 5 generator-marker pages**, but this line (27) is **outside** its `<!-- GENERATED:configuration-file-index:start -->`/`:end` block (spans lines 9-19, confirmed) — hand-editable. Lower priority: describes the *project-local override* default, not the global path; only needs a touch if the field's example/behavior changes, which this doc does not propose. |
| `docs/_internal/explorations/instrument-access-model.md:11` | "...under `TESTERKIT_HOME/locks/`, robust to process death..." | No — internal exploration doc, lower priority |
| `docs/_internal/explorations/instrument-reservation.md:113,420,424,428` | Four mentions of `TESTERKIT_HOME/locks/` as the machine-global lock location | No — internal, lower priority |

### 12.3 `TESTERKIT_FORWARD_URL` / `TESTERKIT_FORWARD_TOKEN` env var docs

| File:line | Content | Generated? |
|---|---|---|
| `docs/reference/cli.md:210` | `` `--url` \| `text` \| Server ingest base URL (or $TESTERKIT_FORWARD_URL) `` | **Inside** the `<!-- GENERATED:cli-commands:start -->` block (lines 20-593) — this table row is generated directly from `forward_cmd.py`'s Click option help string (`` help=f"Server ingest base URL (or ${_URL_ENV})" ``, `forward_cmd.py:432`). **Do not hand-edit.** Once `_URL_ENV`'s value is renamed to `"TESTERKIT_URL"` in source (§6.1), re-run `uv run python scripts/generate_reference_docs.py --all` and this row updates automatically. The pre-commit `reference-docs-drift` hook would otherwise fail the commit on the resulting drift. |

No `TESTERKIT_FORWARD_TOKEN` mention was found anywhere in `docs/reference/cli.md` (grep-confirmed) — the token is read via a bare `os.environ.get` with no corresponding Click option, so there is no generated table row for it to update; only the source-side rename (§6.2, `forward_cmd.py:58,22,477`) is needed, no docs follow-up for the token specifically.

### 12.4 Credential/token storage location

No documentation anywhere (`docs/`, `examples/`, `README*.md`, `CLAUDE.md`)
currently describes where a machine token lives on disk, because — as
verified in §3.4 — no such on-disk location exists yet. This is net-new
documentation the implementation task must add (naturally alongside whatever
`testerkit connect` reference page eventually documents the enrollment flow
from `testerkit-server/docs/32-...md`), not an update to an existing claim.

### 12.5 `litmus` references — checked, no action needed

Grepped `docs/`, `examples/`, `README*.md`, `CLAUDE.md` for `litmus`/`.litmus`.
Findings:
- No hits in any tracked `docs/` or `README*.md` or `CLAUDE.md` content.
- The only hits are (a) gitignored runtime log files under
  `examples/*/.uicheck.log` and `examples/*/data/*/_daemon.log`, which embed
  a stale absolute path (`/home/ryanf/repos/litmus/.venv/...`) left over from
  before this checkout was renamed — these are build artifacts excluded by
  `.gitignore`'s `*.log` rule, not repo content, and will simply stop
  appearing once regenerated from the current `testerkit` checkout path; and
  (b) `examples/01-vanilla/uv.lock:24` (`name =
  "litmus-example-01-vanilla"`), a stale lockfile snapshot — the
  corresponding `pyproject.toml` files are already correctly renamed to
  `testerkit-example-*` across all 12 examples (grep-confirmed), and
  `examples/*/uv.lock` is itself gitignored (`.gitignore:15`). **No tracked
  file needs a litmus-reference fix**; both hits are non-committed,
  regenerable artifacts.

---

## 13. Sequencing

This document is the design contract. Implementation — the resolution
precedence change, the migration helper, the env-var rename, and the
`.gitignore` auto-ensure logic — is **separate, later work**, tracked
independently of this doc. It does **not** block the `exp/testerkit-server →
main` cloud merge; this is framework-side local-filesystem work, orthogonal
to the server integration currently in flight on this branch.

---

## 14. Multi-version coexistence & concurrency (shared global store)

**Future-work guidance, not something to implement now** — recorded here
because it directly constrains how any *new* global artifact this doc
proposes (`machine_id`, `credentials`) should be shaped, and because it is
the same care lvkit takes with its own shared global home (§2).

**The situation:** multiple repos on one machine each pin their **own**
TesterKit version (one venv per checkout — the normal `uv`/editable-install
pattern this repo itself uses), but all of them **share** the global
`~/.testerkit/` store (§4.1, §5.1 step 4). Different package versions
therefore read and write the same on-disk store concurrently — an older
checkout's daemon and a newer checkout's daemon can both be pointed at
`~/.testerkit/data/runs/` at the same time. This is not a new problem this
doc introduces: it is the status quo today under
`~/.local/share/testerkit/data/`, and the codebase already has three
purpose-built mechanisms for it, verified below. They are described here as
**existing, working infrastructure to build on**, not as gaps.

### 14.1 Content-addressed derived index — coexistence, not clobbering

`src/testerkit/data/_index_epoch.py`:

- `index_file_name(fingerprint)` (`:33-40`) names the on-disk index
  `` f"_index.{fingerprint[:12]}.duckdb" `` — a 12-hex-char prefix of a full
  64-char content fingerprint. This is exactly what was observed live on
  disk in §4.1a (`_index.927a7b67dc38.duckdb` for runs,
  `_index.5060fe6df3b6.duckdb` for events).
- The **filename** is keyed on the fingerprint alone — two package builds
  that produce a *different* projection (schema DDL, adapter registry;
  see the module's own description of what feeds the fingerprint,
  `:160-164`) get **different files**, so an older and a newer version
  sharing one global store never overwrite each other's index; each simply
  opens its own content-addressed file (or builds it fresh the first time).
  Two versions that happen to produce the *identical* projection collapse
  onto the **same** file and share it — "the sharing collapse," named
  explicitly in `stamp_epochs_ledger`'s docstring (`:239-241`,
  ``"a behaviorally-identical projection can be, and often is, opened by
  several package versions"``).
- The **`(testerkit_version, schema_version, fingerprint)` triple** is
  stamped as in-file *provenance*, not baked into the filename:
  `stamp_index_meta` (`:53-85`) writes all three into an `_index_meta` table
  inside the opened DuckDB file (`:74-80`) — this is how a human or
  `testerkit data index list` can tell *which* versions built or touched a
  given content-addressed file, and it's why the module doc frames content-
  addressing as "the filename is the gate, the in-file `_index_meta` is
  provenance" (module docstring, `:4-6`).
- The **`_epochs.json`** ledger (`stamp_epochs_ledger`, `:226-281`;
  `read_epochs_ledger`, `:283-313`) is the cross-version visibility layer:
  every distinct `testerkit_version` that has ever opened a given
  fingerprint's index gets appended to that entry's `seen_by` set
  (`:235-236,271`) — this is precisely the bookkeeping a shared, multi-repo,
  multi-version store needs to answer "which package versions have touched
  this index," and it already exists, on disk, today (confirmed present:
  `~/.local/share/testerkit/data/{runs,events}/_epochs.json`, §4.1a).
- Corruption/incomplete-build self-heal (`open_index`, `:134-223`) discards
  and rebuilds **only the index file itself** — never parquet — so a crash
  in one version's process can never destroy another version's ability to
  read the same durable data; only the disposable derived cache is at risk,
  and it self-heals.

### 14.2 Schema-version whitelist — refuse-and-regenerate, not misread

`src/testerkit/data/schema_versions.py`:

- Every durable artifact (`SchemaStore.RUNS`, `EVENTS_ENVELOPE`,
  `EVENT_CATALOG`, `CHANNELS`, `FILES`, `:41-58`) carries a schema-version
  stamp; `CURRENT_SCHEMA_VERSION` (`:64-70`) is the one home for "what a
  freshly-written artifact of this kind looks like today" — currently `"0.1"`
  for every store (module comment `:14-15`: deliberately decoupled from the
  package version — schema `0.1` at package `0.3.0` is not a mismatch).
- `KNOWN_SCHEMA_VERSIONS` (`:82-85`) is the **whitelist-dispatch** set each
  store's reader checks a stamp against — `CURRENT_SCHEMA_VERSION` ∪ any
  `_LEGACY_READABLE` versions that store still ships an adapter for
  (`_LEGACY_READABLE` is empty today for every store, `:76`, since nothing
  has yet reached a second schema epoch). The module docstring states the
  refusal behavior explicitly: *"Unstamped artifacts are unsupported by
  design (regenerate)"* (`:33`) and *"Anything not in this set is refused at
  read time (\"unsupported schema version\"); an absent stamp is refused as
  unstamped/pre-baseline (\"regenerate\")"* (`:79-81`).
- This is the deliberate opposite failure mode from silent misreading: an
  **older** TesterKit version that encounters a **newer** store's schema
  stamp (once a future epoch bump ships) refuses cleanly rather than
  attempting to parse a shape it doesn't understand — the module frames this
  as "coexist-always + optional-migrate" (`:11`, cross-referencing
  `docs/_internal/explorations/schema-versioning-migration.md`).

### 14.3 Cross-process file locks — the concurrency primitive, with its known limit

`src/testerkit/instruments/locks.py` (full file read in §3.3 already; cited
again here for the concurrency angle specifically):

- Uses `filelock` (`FileLock`, imported `:22`) — OS-level `fcntl.flock()` on
  Linux/macOS — so a lock **auto-releases on process death, including
  `SIGKILL`** (module docstring `:1-4`), which is exactly the property a
  shared store touched by independently-versioned, independently-crashable
  processes needs: a killed daemon from one repo's venv can never leave a
  stale lock that blocks a different repo's venv.
- `_lock_dir()` (`:34-37`) anchors these lock files under
  `_testerkit_home() / "locks"` — already global, already shared across
  every version and every project checkout on the machine (this is the
  existing behavior §3.3/§4.1a's target layout carries forward unchanged
  into `~/.testerkit/locks/`).
- **Documented limitation, not silently glossed over**: the module docstring
  states it plainly — *"`filelock` uses `fcntl.flock()` on Linux/macOS, which
  only works on a single machine. Cross-machine coordination is future
  work"* (`:8-9`). Multi-**version**, single-machine concurrency is covered;
  multi-**machine** concurrency (e.g. two benches both trying to reach the
  same instrument over a network) is explicitly out of scope for this
  primitive today.

### 14.4 Requirements this imposes on the new global artifacts

Given the three mechanisms above already handle "many versions, one shared
store" for the *existing* global state (derived indexes, durable parquet/WAL,
instrument locks), the **new** global artifacts this doc introduces
(`~/.testerkit/machine_id`-or-equivalent identity state, and
`~/.testerkit/credentials`, §4.1/§6.2/§7) should be held to the same bar:

1. **New global state must be version-tolerant/stamped.** Neither
   `machine_id` nor `credentials` has a format defined yet (both are, today,
   simple opaque strings — a token, an identity value) — low risk *today*,
   but the schema-versioning module's own lesson applies: stamp a version
   field into whatever structured shape these files eventually take (even a
   trivial `{"v": 1, ...}` envelope), so a future format change doesn't lock
   an older repo's TesterKit out of a file it needs to at least *recognize*,
   the same way `schema_versions.py` lets a reader say "I don't understand
   this stamp" instead of misparsing silently.
2. **Favor coexistence over in-place format bumps for shared DURABLE
   state.** The index's content-addressed-by-fingerprint approach (§14.1) is
   the model: different versions get different files instead of one file
   different versions fight over. "Refuse unsupported → regenerate"
   (§14.2's behavior) is the right call for *derived* state a single repo
   owns, but it is a real UX cliff for something living in the **shared**
   global store — an older repo cannot safely "regenerate" the machine's
   global credential/identity file out from under a newer repo that is
   concurrently relying on it. Project-local `.testerkit/data/` (owned by
   one repo, one version at a time in practice) can afford to be stricter;
   anything in the **global** shared tier should default to coexistence.
3. **Migration must not strand a concurrently-running older version.** The
   don't-clobber migration guard in §9 already carries this spirit for the
   litmus/XDG → `.testerkit/` move; the same principle extends forward to
   any later shared-global-store schema change: prefer versioned/additive
   artifacts (a new file, a new key, a new content-address) over destructive
   in-place rewrites for anything under `~/.testerkit/` that more than one
   installed version might touch concurrently.
4. **Cache stays strictly deletable.** `cache/` (§4.1, §5.3) carries no
   cross-version compatibility burden at all by construction — any format
   change there is safe by definition, the same guarantee lvkit's
   `cleanup_legacy_cache()` (§2) already relies on to freely `rmtree` whole
   cache subtrees on its own layout-version bumps. This is the one tier
   where "just delete and rebuild" is always the correct answer regardless
   of how many versions are touching the store.

None of the above is a call to build anything today — `machine_id` and
`credentials` don't exist yet (§3.4, §7), and the mechanisms in §14.1-14.3
are cited as prior art to follow, not gaps to fill. This section exists so
that whoever implements §4.1/§6.2/§7 inherits this constraint deliberately
rather than reinventing (or worse, under-thinking) it.

---

## Appendix — verification log (files opened, functions read)

- `src/testerkit/data/data_dir.py` (full)
- `src/testerkit/store.py` (`find_station_config`, lines 444-476, plus the
  platformdirs line at 464)
- `src/testerkit/channels.py` (module docstring + `write`/`_resolve_store`,
  lines 1-100; `resolve_data_dir() / "channels"` confirmed at line 257 via
  grep)
- `src/testerkit/client.py` (`TesterKitClient.__init__` and docstring,
  lines 370-412)
- `src/testerkit/init.py` (full, 725 lines)
- `src/testerkit/cli/forward_cmd.py` (full, 532 lines)
- `.gitignore` (full)
- `/home/ryanf/repos/lvkit/src/lvkit/cache_paths.py` (full, 562 lines)
- `/home/ryanf/repos/lvkit/src/lvkit/project_store.py` (lines 1-150)
- `src/testerkit/instruments/locks.py` (full, 179 lines)
- `src/testerkit/data/_index_epoch.py` (full, 343 lines — §14.1)
- `src/testerkit/data/schema_versions.py` (full, 86 lines — §14.2)
- `src/testerkit/cli/data_cmd.py` (lines 140-239)
- `src/testerkit/connect.py` (`_find_project_config`, `connect`,
  `_default_station_id`, lines 575-634)
- `src/testerkit/models/project.py` (`ProjectConfig`, lines 75-114)
- `/home/ryanf/repos/testerkit-server/testerkit_server/tokens.py` (full,
  165 lines — server-side, read for cross-reference only)
- `/home/ryanf/repos/testerkit-server/docs/32-testerkit-connect-machine-enrollment.md`
  (full, 482 lines — server-side, read for cross-reference only)
- `src/testerkit/ui/shared/services.py` (lines 1560-1680)
- `src/testerkit/mcp/tools.py` (lines 1235-1260)
- `src/testerkit/cli/_common.py` (lines 95-114)
- `src/testerkit/data/event_store.py` (lines 155-174)
- `src/testerkit/cli/daemon.py` (lines 1-40)
- `docs/concepts/data/data-stores.md` (lines 85-139)
- `docs/reference/runtime/connect.md` (lines 100-139)
- `tests/test_conventions.py` (full guard logic, lines 1-120) — confirmed the
  `_PLATFORMDIRS_HARDCODE` convention test scans only `tests/*.py`, not
  `src/`, so it does not currently guard any of the four `src/` occurrences
  identified in §3.3.
- Shell verification: `find ~/.local/share/testerkit -type f | wc -l` → 967;
  `find ~/.local/share/litmus -type f | wc -l` → 8079; `env | grep -i
  testerkit` → empty (`TESTERKIT_HOME` unset); `ls ~/.local/share/testerkit`
  → `data`, `locks`; `~/.config/testerkit` and `~/.cache/testerkit` do not
  exist.
- Grep sweeps across `src/`: `resolve_data_dir`, `platformdirs`,
  `user_data_dir|user_config_dir|user_cache_dir`, `TESTERKIT_HOME`,
  `TESTERKIT_FORWARD`, `\.local/share|\.config/testerkit|\.cache/testerkit`,
  `keyring|0o600|0600`, `TESTERKIT_TOKEN|TESTERKIT_URL`, `\.testerkit\b`,
  `credentials`.
