# Query runs and metrics via MCP

When you want to ask "how is the line doing" or "show me the last
five runs on station X" from a chat-style session with an AI
assistant, the MCP tools give you the same answers the operator
UI gives, scriptably, without leaving the chat. This recipe walks
the three tools you'll use most.

## Prerequisites

- [MCP server registered](../overview/mcp-integration.md) with your AI client
  (run `litmus setup <client>`, restart the client)
- A project with at least a few runs already in the data dir

## The three query tools

| Tool | Use it for |
|---|---|
| `litmus_runs(action="list")` | "show me recent runs" — returns the most recent N run summaries (the MCP tool accepts only `limit`; for per-product / per-station filtering, follow up with the assistant filtering the response client-side) |
| `litmus_runs(action="get", run_id=...)` | "tell me about run X" — one run's full summary, accepts 8-char prefix |
| `litmus_steps(run_id=...)` | "what did run X actually execute" — flat list (`action="list"`) or hierarchy (`action="tree"`) |
| `litmus_metrics(action=...)` | "is the line healthy" — aggregate analytics over a date range |

All three tools read the same parquet store the operator UI's
Results and Metrics pages read. Behavior is identical; what you
get back is JSON instead of pixels.

## Recipe 1 — "What ran recently?"

Ask your assistant:

> List the last 20 runs.

It calls `litmus_runs(action="list", limit=20)` and gets a JSON
array of run summaries (run_id, started, outcome, DUT serial,
station, product). Then you can ask follow-ups like "filter to
the ones that failed", and the assistant either re-queries or
filters the in-memory list.

## Recipe 2 — "What does this run look like?"

When the recent-runs list surfaces a suspect run id:

> Show me the steps for run a4f8b201.

The assistant calls `litmus_steps(run_id="a4f8b201", action="tree")`
(8-char prefix is fine — the tool resolves it) and gets the
step hierarchy with each step's outcome, vector index, and
measurement count. The `tree` action returns the step_path-derived
hierarchy; `list` returns flat ordered rows — pick whichever the
assistant needs for the question.

To drill further, `litmus_runs(action="get", run_id="a4f8b201")`
returns the run-level summary (project, phase, outcome, started /
ended timestamps, totals) — the same shape the Results detail
Overview tab renders.

## Recipe 3 — "Is the line healthy?"

The `litmus_metrics` tool exposes the analytical lenses behind the
[Metrics page](../../reference/operator-ui/metrics.md). The split
between MCP actions and UI tabs is not 1:1 — the UI's "Yield" tab
is `summary` + `trend` on the MCP side, and the UI's "Assets" tab
has no MCP equivalent yet:

| Action | Question it answers |
|---|---|
| `summary` | First-pass yield, final yield, run counts, duration stats |
| `pareto` | Top failure modes ranked by count |
| `cpk` | Per-measurement process capability (Cpk / Cp) |
| `trend` | Yield trend over time, bucketed by `period` (`day` / `week` / `month`) |
| `retest` | Retest rates per serial bucketed by period |
| `time_loss` | Time lost to failed / errored runs |

Filters available on every action: `product`, `station`, `phase`,
`since`, `until`. Plus per-action tuning: `top_n` (Pareto cutoff),
`min_samples` (Cpk minimum-N filter).

Common asks:

> Show me yield for the last 14 days, weekly.

Translates to `litmus_metrics(action="trend", period="week",
since="<two weeks ago>")`. The assistant fills in the ISO date
from "the last 14 days".

> What's the top failure mode on station prod-1?

`litmus_metrics(action="pareto", station="prod-1", top_n=10)`.

> Which measurements have Cpk below 1.33?

`litmus_metrics(action="cpk")` then filter on the response client-side.
(The tool doesn't take a Cpk threshold; the assistant inspects the
JSON.)

## Recipe 4 — "Walk me through the run history" (chained)

For a longer diagnostic, chain the tools:

> Look at the last week of runs, find a serial that failed at
> least twice on the same step, then show me what changed between
> the first failure and the latest one.

That's:

1. `litmus_runs(action="list", limit=200)` — fetch recent runs
2. Group client-side by `dut_serial`, find one with multiple
   failures on the same `step_path`
3. `litmus_steps(run_id=<first>, action="list")` and again for the
   second
4. Diff the measurement values

This is the conversational equivalent of the
[Compare two runs](compare-runs.md) recipe — same data, less
clicking.

## See also

- [MCP integration](../overview/mcp-integration.md) — server setup, client registration
- [Datasheet → tests](../catalog/datasheet-to-test.md) — end-to-end authoring flow
- [Debug failures via MCP](mcp-debug-failures.md) — investigation-focused recipe
- [API reference → MCP tools](../../reference/runtime/api.md#tools) — every tool's full parameter list and return shape
- [Operator UI → Results list](../../reference/operator-ui/results/list.md) — the UI surface that reads the same data
- [Operator UI → Metrics](../../reference/operator-ui/metrics.md) — the UI surface that reads the same metrics actions
