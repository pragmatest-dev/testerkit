---
name: testerkit-data
description: Use when a user wants to read, query, or export existing TesterKit test data — runs, steps, measurements, channels, files, or events — or understand the data stores and how to access them (models, CLI, Query API, MCP). Not computing a statistic across runs (that's testerkit-analysis) and not diagnosing why one run failed (that's testerkit-debug).
---

# Reading TesterKit data

The data already exists on disk. This skill covers the **stores, the model,
and every read surface** — models, CLI, Query API, MCP, UI — so a question
turns into the right access call.

## The one rule

**Never read `data/**/*.parquet` or `data/*/_index.*.duckdb` directly.** That
layout is internal and content-addressed — the fingerprint in an index
filename is a hash of the current schema, so it renames on any version bump and
a hand-rolled reader breaks silently on upgrade. Every question below has a
stable, purpose-built surface. If none exposes the shape you need, extend the
Query API — don't reach into the files.

## 1. The stores

TesterKit keeps four data stores under the project `data/` dir. All are derived
from the event log:

| Store | Holds | Read via |
|---|---|---|
| runs / measurements | tabular run → step → measurement records | CLI (`testerkit runs`/`show`), Query API, `testerkit_runs`/`testerkit_steps` |
| channels | streaming / waveform samples | `testerkit.channels` module, `testerkit_channels`, UI |
| files | blobs & growing artifacts (photos, logs, vendor captures) | `testerkit.files` module, `testerkit_files`, UI |
| events | the append-only log every store is derived from | `testerkit_events` / `testerkit_sessions`, UI |

Tabular runs data has a first-class **CLI**; channels, files, and events read
through the UI, MCP, HTTP, or the Python modules — there is no `testerkit
channels`/`files`/`events` subcommand.

## 2. The model

A run carries its step tree; each step carries three role-tagged
`list<struct>` lanes — `inputs`, `outputs`, `measurements`. Reference a value
by **role + name**:

```python
from testerkit.queries import FieldRef
FieldRef.measurement("v_rail")    # a judged measurement (has a limit)
FieldRef.output("capture_length") # a recorded output reading
FieldRef.input("vin")             # a stimulus input
```

Only `measurements` carry limits; `inputs`/`outputs` are record-only. Full
shape: `reference/data/models` and `reference/data/parquet-schema`.

## 3. CLI (tabular data)

```bash
testerkit runs --station bench_01 --since 7d --json   # list / filter runs
testerkit show <run_id>                               # one run: outcome, steps, measurements
testerkit show <run_id> -f json                       # machine-readable (also html|pdf|csv)
testerkit show <run_id> --env                          # captured environment snapshot
testerkit export <run_id> -f stdf -o exports/          # STDF and other event-shaped formats
testerkit sbom <run_id> -o sbom.json                   # CycloneDX 1.6
```

`show`/`export`'s id is a run_id or session_id prefix, auto-detected. Prefer
`--json` for tool use — stable across schema changes and token-efficient.
`testerkit show` prints measurements only; it doesn't list outputs or artifact URIs.

## 4. Query API (scripted)

```python
from testerkit.queries import RunsQuery, StepsQuery, MeasurementsQuery, FieldRef

with RunsQuery() as q:
    for r in q.list_recent(limit=20, outcome="failed"):
        print(r.uut_serial_number, r.station_hostname, r.outcome)
    q.get(run_id)                 # one run
    q.list_for_session(session_id)

with StepsQuery() as s:
    s.list_for_run(run_id)        # flat step list
    s.tree_for_run(run_id)        # nested step tree

with MeasurementsQuery() as m:
    m.parametric(y=FieldRef.measurement("v_rail"), x=FieldRef.input("vin"))
    m.histogram(field=FieldRef.output("capture_length"))
```

Opening a query class with no args reads the active project's data dir; always
close it (or use `with`). These are the same classes `testerkit serve` renders, so
anything scriptable here is clickable there. Computing yield/Ppk/Pareto/trend
over these rows is `testerkit-analysis`.

## 5. Channels & files readback

| Data | Surface |
|---|---|
| Channel, one-shot pull | `testerkit.channels.query(name, last_n=..., max_points=...)` |
| Channel, live gauge/chart | `testerkit.channels.latest(name, cb)` / `.live(name, cb, max_hz=...)` |
| Channel, agent/remote | MCP `testerkit_channels(channel_id, session_id=, last_n=, max_points=)` |
| Channel, human | `testerkit serve` → `/channels/<channel_id>` |
| Artifact catalog (metadata, not bytes) | MCP `testerkit_files(uri=, session_id=, run_id=)` |
| Artifact, human | `testerkit serve` → `/files` |

Filter artifact readback by `session_id`, not `run_id`, to catch every artifact
from a run — `observe`-routed blobs carry no `run_id`. Writing channels/files in
the first place is `testerkit-capture`.

## 6. Events

The event log is the source every other store derives from — read it for the
raw ordered stream, or when a projection looks wrong:

```
MCP testerkit_events(session_id=, event_type=, role=, since=, limit=)
MCP testerkit_sessions(project=)          # session index
```

## 7. MCP read tools

| MCP tool | Mirrors |
|---|---|
| `testerkit_runs(action="list"\|"get", run_id=...)` | `testerkit runs` / `RunsQuery` |
| `testerkit_steps(run_id=..., action="list"\|"tree")` | `StepsQuery.list_for_run` / `.tree_for_run` |
| `testerkit_channels` / `testerkit_files` / `testerkit_events` / `testerkit_sessions` | channel / file / event / session reads |

## Deeper
Read the docs:
```bash
testerkit docs show concepts/data/data-stores
testerkit docs show reference/data/models
testerkit docs show reference/data/query-api
testerkit docs show reference/data/parquet-schema
testerkit docs show how-to/data/mcp-query-runs
testerkit docs show how-to/data/export-results
```
Sibling skills: `testerkit-analysis` (compute a statistic over these records),
`testerkit-capture` (write channels/files in the first place), `testerkit-debug`
(why one run failed).
