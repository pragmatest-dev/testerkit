---
name: litmus-data
description: Use when a user wants to read, query, or export existing Litmus test data — runs, steps, measurements, channels, files, or events — or understand the data stores and how to access them (models, CLI, Query API, MCP). Not computing a statistic across runs (that's litmus-analysis) and not diagnosing why one run failed (that's litmus-debug).
---

# Reading Litmus data

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

Litmus keeps four data stores under the project `data/` dir. All are derived
from the event log:

| Store | Holds | Read via |
|---|---|---|
| runs / measurements | tabular run → step → measurement records | CLI (`litmus runs`/`show`), Query API, `litmus_runs`/`litmus_steps` |
| channels | streaming / waveform samples | `litmus.channels` module, `litmus_channels`, UI |
| files | blobs & growing artifacts (photos, logs, vendor captures) | `litmus.files` module, `litmus_files`, UI |
| events | the append-only log every store is derived from | `litmus_events` / `litmus_sessions`, UI |

Tabular runs data has a first-class **CLI**; channels, files, and events read
through the UI, MCP, HTTP, or the Python modules — there is no `litmus
channels`/`files`/`events` subcommand.

## 2. The model

A run carries its step tree; each step carries three role-tagged
`list<struct>` lanes — `inputs`, `outputs`, `measurements`. Reference a value
by **role + name**:

```python
from litmus.queries import FieldRef
FieldRef.measurement("v_rail")    # a judged measurement (has a limit)
FieldRef.output("capture_length") # a recorded output reading
FieldRef.input("vin")             # a stimulus input
```

Only `measurements` carry limits; `inputs`/`outputs` are record-only. Full
shape: `reference/data/models` and `reference/data/parquet-schema`.

## 3. CLI (tabular data)

```bash
litmus runs --station bench_01 --since 7d --json   # list / filter runs
litmus show <run_id>                               # one run: outcome, steps, measurements
litmus show <run_id> -f json                       # machine-readable (also html|pdf|csv)
litmus show <run_id> --env                          # captured environment snapshot
litmus export <run_id> -f stdf -o exports/          # STDF and other event-shaped formats
litmus sbom <run_id> -o sbom.json                   # CycloneDX 1.6
```

`show`/`export`'s id is a run_id or session_id prefix, auto-detected. Prefer
`--json` for tool use — stable across schema changes and token-efficient.
`litmus show` prints measurements only; it doesn't list outputs or artifact URIs.

## 4. Query API (scripted)

```python
from litmus.queries import RunsQuery, StepsQuery, MeasurementsQuery, FieldRef

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
close it (or use `with`). These are the same classes `litmus serve` renders, so
anything scriptable here is clickable there. Computing yield/Ppk/Pareto/trend
over these rows is `litmus-analysis`.

## 5. Channels & files readback

| Data | Surface |
|---|---|
| Channel, one-shot pull | `litmus.channels.query(name, last_n=..., max_points=...)` |
| Channel, live gauge/chart | `litmus.channels.latest(name, cb)` / `.live(name, cb, max_hz=...)` |
| Channel, agent/remote | MCP `litmus_channels(channel_id, session_id=, last_n=, max_points=)` |
| Channel, human | `litmus serve` → `/channels/<channel_id>` |
| Artifact catalog (metadata, not bytes) | MCP `litmus_files(uri=, session_id=, run_id=)` |
| Artifact, human | `litmus serve` → `/files` |

Filter artifact readback by `session_id`, not `run_id`, to catch every artifact
from a run — `observe`-routed blobs carry no `run_id`. Writing channels/files in
the first place is `litmus-capture`.

## 6. Events

The event log is the source every other store derives from — read it for the
raw ordered stream, or when a projection looks wrong:

```
MCP litmus_events(session_id=, event_type=, role=, since=, limit=)
MCP litmus_sessions(project=)          # session index
```

## 7. MCP read tools

| MCP tool | Mirrors |
|---|---|
| `litmus_runs(action="list"\|"get", run_id=...)` | `litmus runs` / `RunsQuery` |
| `litmus_steps(run_id=..., action="list"\|"tree")` | `StepsQuery.list_for_run` / `.tree_for_run` |
| `litmus_channels` / `litmus_files` / `litmus_events` / `litmus_sessions` | channel / file / event / session reads |

## Deeper
Read the docs:
```bash
litmus docs show concepts/data/data-stores
litmus docs show reference/data/models
litmus docs show reference/data/query-api
litmus docs show reference/data/parquet-schema
litmus docs show how-to/data/mcp-query-runs
litmus docs show how-to/data/export-results
```
Sibling skills: `litmus-analysis` (compute a statistic over these records),
`litmus-capture` (write channels/files in the first place), `litmus-debug`
(why one run failed).
