# Debug failures via MCP

The MCP tools turn an AI assistant into an investigation partner
that can pull run data, channel waveforms, and event timelines
without you leaving the chat. This recipe is the diagnostic
workflow — "the run failed, why?"

## Prerequisites

- [MCP server registered](mcp-integration.md) with your AI client
- A failing run in the data dir (this recipe assumes you already
  know which one)

## The investigative toolkit

| Tool | What it surfaces |
|---|---|
| `litmus_runs(action="get", run_id=...)` | Run-level summary — outcome, station, product, started / ended timestamps |
| `litmus_steps(run_id=..., action="list")` | Every step the run executed, in order, with outcome + measurement count |
| `litmus_steps(run_id=..., action="tree")` | Same data as a step_path hierarchy (better for cluster / parametrize layouts) |
| `litmus_events(session_id=..., event_type=..., role=..., since=..., limit=...)` | Events around the failure — dialogs, instrument connects, errors |
| `litmus_sessions()` | List of sessions; useful to map a run back to its `connect()` lifetime |
| `litmus_channels(channel_id=..., session_id=..., last_n=..., max_points=...)` | Time-series channel data — supply rails, temperatures, anything logged via `context.observe()` |
| `litmus_open(type="run", id=...)` | Returns a browser URL to the operator UI's Results detail — fallback when you need to see the rendered view |

## Recipe — "Why did this run fail?"

### 1. Get the lay of the land

> Show me run a4f8b201.

Assistant calls `litmus_runs(action="get", run_id="a4f8b201")`
and reports outcome, station, product, started time. If
outcome != `failed`, redirect — Litmus distinguishes `failed`
(measurement crossed a limit or assertion failed), `errored`
(exception during the step), `terminated` (operator or harness
graceful stop with cleanup), and `aborted` (no `RunEnded` was ever
emitted — the close-time fallback path, rig may be in unknown
state). Each implies a different next step.

### 2. Find the step that flipped

> Which step failed?

`litmus_steps(run_id="a4f8b201", action="list")` returns the flat
step list with outcomes. The assistant scans for the first
`failed` or `errored` row. From there:

- **failed**: a measurement crossed a limit. Drill into that step's
  measurements (see Recipe step 4 below).
- **errored**: an exception was raised. The error message lives in
  the event log — jump to step 3.

### 3. Pull the events around the failure

When the step errored, the exception is in the event log. Get the
run's session id from step 1, then:

`litmus_events(session_id="<session_id>", since="<step_started_at>",
limit=100)` returns the events in order. The assistant scans for
`test.step_ended` events with `outcome="errored"` (the canonical
step-error signal) and `diagnostic.error` events (the catch-all
for raised exceptions). The event body carries the exception type
and message.

When the step failed (limit violation), events are less useful
than the measurement table itself — skip to step 4.

### 4. Inspect the measurements

For a `failed` step, the measurement table is the source of truth.
The MCP tool surface doesn't have a direct "fetch measurements"
method, so the assistant either:

- Opens the run in the operator UI via `litmus_open(type="run",
  id="a4f8b201")` (returns the URL — paste it into a browser), or
- Drops to a parquet query (the assistant can call out to a shell)
  for programmatic comparison

For a programmatic measurement diff across runs, see the
[Compare two runs](compare-runs.md) recipe — that walks the
DuckDB join you'd run.

### 5. Cross-check environment with channels

If the measurement is wild but the DUT is fine, the cause is
usually environmental. Get the channel ids the run logged:

> Show me the supply-rail channels from session <session_id> over
> the last 5 minutes.

`litmus_channels(channel_id="<rail_name>", session_id="<session_id>",
last_n=300)` returns timestamped values. The assistant inspects for
brown-outs, glitches, or thermal drift coincident with the failure
window. `max_points` controls server-side downsampling
([LTTB](../how-to/querying-channels.md)) when the raw series is
too large to ship over the wire.

### 6. Hand off to a human if needed

When the assistant has narrowed the cause but the operator needs
to verify visually:

`litmus_open(type="run", id="a4f8b201")` returns the
`/results/<run_id>` URL — share it in the chat, the operator
opens it.

## Tips that compound

- **Prefix run IDs.** All run-id parameters accept the 8-char
  prefix Litmus uses in human-readable contexts. No need to
  copy/paste the full UUID.
- **Phase filter on metrics.** `litmus_metrics` excludes
  `development` runs by default. Pass `phase="production"` to be
  explicit, or `phase="all"` to include development noise when you
  want to see everything.
- **Channel queries return raw rows by default.** Setting
  `max_points` enables server-side LTTB decimation — useful when
  the raw waveform is too large to ship over the wire, skip it
  when you want pixel-accurate data.

## See also

- [Find flaky tests](find-flaky-tests.md) — the UI-first version of the same diagnostic, for the cases where you'd rather click than chat
- [Compare two runs](compare-runs.md) — when the question is "what changed between this run and a known-good one"
- [Query runs and metrics via MCP](mcp-query-runs.md) — the broader "ask Litmus questions" surface
- [MCP integration](mcp-integration.md) — server setup
- [API reference → MCP tools](../reference/api.md#tools) — full per-tool parameter list
