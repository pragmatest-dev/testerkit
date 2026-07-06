# The solution arc

Litmus grows in stages, not versions. Every stage below is a complete,
runnable solution — a project doesn't "finish" by reaching stage 12; it
stops wherever the next layer isn't solving a real problem yet. This is
the spine other refs hang off — start here when unsure where a user
sits, then drop into the named ref for depth.

## Where is the user?

| Signal in the request / repo | Stage |
|---|---|
| "just run this against the bench", no `verify`, no `litmus.yaml` | 1 |
| wants pass/fail persisted; "where did that reading go" | 2 |
| limit is a literal inside the test body, wants it named/reusable | 3 |
| wants ops to tune limits without a code review | 4 |
| same limit typed into 3+ test files, or "per-DUT tolerance" | 5 |
| `conftest.py` has a growing `if mock_instruments: Mock(...)` per instrument | 6 |
| "dev limits are loose, production limits are tight" / phases | 7 |
| a `verify` needs the raw trace behind it (scope capture, rise time) | 8 |
| "watch this reading live", no pytest — a monitor script or notebook | 9 |
| a photo, vendor binary, PDF, or streaming log needs to ride with the run | 10 |
| "how do I query this after the fact" — dashboards, ETL, MCP tools | 11 |
| two or more UUTs on one bench, tested at the same time | 12 |

If the repo already has YAML the user didn't mention (a `parts/` dir, a
`profiles/` dir), that YAML — not the request — is the real stage. Don't
regress a project to a lower stage because one request is simple.

## The stages

### 1 — Vanilla pytest: real drivers, `assert`, a run record

No measurement code yet — a fail tells you nothing about the value read.

- **Adds:** nothing Litmus-specific — plain `conftest.py` fixtures.
- **Default:** mock in the fixture itself (`Mock(PSU, measure_voltage=5.0)`),
  branching on `mock_instruments`, so the test runs with or without a bench.
- **Deeper:** `test-writing` · **Example:** `examples/01-vanilla`

### 2 — `verify` + Parquet log

`assert` becomes `verify(name, value, limit=...)`; every call logs a
measurement row (value, unit, limit, outcome) — a fail is now searchable.

- **Adds:** one call — `verify` in place of `assert`.
- **Default:** inline `limit={...}` or a module constant; add `litmus_retry`
  only for a genuinely transient failure, never to paper over a flaky limit.
- **Deeper:** `verify` · **Example:** `examples/02-verify`

### 3 — Limits as a marker

The limit moves from a dict literal in the body to
`@pytest.mark.litmus_limits(v_rail={...})` — declarative, still in Python.

- **Adds:** `litmus_limits` decorator.
- **Default:** treat this as the on-ramp to stage 4, not an end state —
  skip straight to a sidecar once ops will tune the value.
- **Deeper:** `verify`, `tiers` · **Example:** `examples/03-inline-limits`

### 4 — Sidecar YAML

Limits (and sweeps, mocks, retry, prompts) move out of Python into a
sibling `<test_file>.yaml`. Ops tune a tolerance without a code review.

- **Adds:** a `<test_file>.yaml` sidecar next to the test module.
- **Default:** sidecar over inline once a value is something an
  *operator*, not the code author, should change. Group related tests
  under a class so the sidecar's class branch carries shared limits once.
- **Deeper:** `test-writing`, `verify` · **Example:** `examples/04-sidecar-markers`

### 5 — Part spec

`parts/<id>.yaml` declares each characteristic's nominal value once;
sidecar limits reference `{characteristic: rail_3v3, tolerance_pct: 2}`.

- **Adds:** a `parts/<id>.yaml` file + `characteristic:` in the sidecar entry.
- **Default:** graduate here only when a limit is duplicated across
  tests, or a non-developer needs to edit the spec value directly.
- **Deeper:** `part-specs` · **Example:** `examples/05-part-spec`

### 6 — Station + catalog + fixture

`conftest.py`'s hand-written instrument fixtures disappear. `stations/*.yaml`
declares the bench once; `fixtures/*.yaml` routes UUT pins to instrument
channels. Tests iterate `context.connections`, never naming a channel.

- **Adds:** `stations/<id>.yaml`, `fixtures/<id>.yaml` (+ optional `catalog/<id>.yaml`).
- **Default:** `--mock-instruments` still flips the rig from `mock_config:` —
  mock-first bringup carries forward; use `litmus_mocks` only for a
  per-test fault-path override, not the default return-value mechanism.
- **Deeper:** `instruments`, `mocks` · **Example:** `examples/06-station-catalog`

### 7 — Profiles

Scenarios (dev / production / characterization) split into
`profiles/*.yaml` with `extends:` chains; one CLI facet selects limits,
station type, and fixture together — the test body never changes.

- **Adds:** `profiles/*.yaml` + a `facets:` block per scenario.
- **Default:** split into a profile only for a **recurring lab
  condition** — a single knob set once is a CLI flag, not a profile.
  Bind `station_type`, never a concrete station id.
- **Deeper:** `profiles` · **Example:** `examples/07-profiles`

### 8 — Waveform evidence

`observe` captures the raw trace behind a `verify`'d scalar — one click
from a failing measurement row to the capture that produced it.

- **Adds:** `observe(name, waveform)` right before the derived `verify` calls.
- **Default:** route array/time-series data through `observe` — it lands
  in ChannelStore by shape; never hand-roll a separate logging path.
- **Deeper:** `streaming`, `observe` · **Example:** `examples/08-waveform-evidence`

### 9 — Continuous instrument streaming

Outside pytest entirely — a monitor script or notebook opens a streaming
sink and pushes samples continuously; the operator UI updates live.

- **Adds:** a standalone script using deep imports (`litmus.channels`,
  `litmus.connect`) — not test-author fixtures.
- **Default:** pick the consumer verb by who's watching — `channels.latest`
  for a script reacting to the newest sample, `channels.live` for rate.
- **Deeper:** `streaming` · **Example:** `examples/09-instrument-streaming`

### 10 — Artifacts and byte streams

A UUT photo, vendor blob, structured report, or streaming log routes to
FileStore through the same `observe` call, by value shape.

- **Adds:** `observe(name, value)` where `value` is `bytes`, `PIL.Image`,
  or `BaseModel`; or `litmus.files.stream(name, format=...)` for a record stream.
- **Default:** let shape pick the store — typed array + sample interval
  → ChannelStore (stage 8); blob/image/document → FileStore.
- **Deeper:** `artifacts`, `observe` · **Example:** `examples/10-artifacts-and-byte-streams`

### 11 — Querying data

Analysts, dashboards, and MCP tools reach runs through the public Query
API (`RunsQuery`, `MeasurementsQuery`, `EventStore`) — the same
primitives the operator UI's `/runs`, `/metrics`, `/events` pages read.

- **Adds:** nothing to test code — a script using `from litmus.queries import RunsQuery`.
- **Default:** query through the public API, never parquet or daemon
  state directly; `litmus metrics summary | pareto | ppk` covers common
  analytics without writing a script.
- **Deeper:** `analytics` · **Example:** `examples/11-querying-data`

### 12 — Parallel sites

Two or more UUT positions on one bench, tested at the same time. A
fixture with 2+ `sites:` is multi-site; a bare `pytest` spawns one
worker per site. Test code never mentions a site, channel, or index.

- **Adds:** `sites:` (2+) in the fixture YAML — nothing else changes.
- **Default:** name sites (`left`/`right`, not bare indices); `--site
  <name> --uut-serial <sn>` (singular) debugs one position in isolation.
- **Deeper:** `multi-site` · **Example:** `examples/12-parallel-sites`

## Cross-cutting best practices

- **Smallest thing first.** Every stage above is a complete, shippable
  test. Climb only when the request demands the next layer.
- **Sidecar over inline once operators tune.** A marker a non-author
  needs to edit belongs in `<test>.yaml`, not `@pytest.mark.litmus_*`.
- **Mock-first bringup.** Every stage that touches an instrument should
  still pass under `--mock-instruments` with no bench attached.
- **Outer sweeps by default.** `@pytest.mark.parametrize` / sidecar
  `sweeps:` is the default shape for a swept stimulus — one vector per
  pytest item. Reach for the `vectors` fixture (inner loop) only to
  amortize expensive setup or collapse a sweep into one analytics row.
- **Limit lives at the layer that owns it.** Inline (code-owned) →
  sidecar (operator-tuned) → part spec (a fact about the DUT) → profile
  (varies by phase). Stop at the lowest layer that answers the request.
- **`measure` → `verify` is a one-word graduation.** They share a
  signature — write `measure` while characterizing, flip the one word
  to `verify` the moment a spec lands.

Front door for "which tool, which verb": `litmus refs show routing`.
Tier-by-tier scaffold detail: `litmus refs show tiers`.
