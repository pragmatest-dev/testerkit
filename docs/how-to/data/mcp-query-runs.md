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
| `litmus_runs(action="list")` | "show me recent runs" — returns the most recent N run summaries (the MCP tool accepts only `limit`; for per-part / per-station filtering, follow up and the assistant filters the results for you) |
| `litmus_runs(action="get", run_id=...)` | "tell me about run X" — one run's full summary, accepts 8-char prefix |
| `litmus_steps(run_id=...)` | "what did run X actually execute" — flat list (`action="list"`) or hierarchy (`action="tree"`) |
| `litmus_metrics(action=...)` | "is the line healthy" — aggregate analytics over a date range |

These tools return the same numbers you see on the operator UI's
Results and Metrics pages — just in the chat instead of the browser.

## Recipe 1 — "What ran recently?"

Ask your assistant:

> List the last 20 runs.

It calls `litmus_runs(action="list", limit=20)` and gets back the
recent runs with each one's outcome, serial, station, and part.
Then you can ask follow-ups like "filter to the ones that failed",
and the assistant re-queries or narrows the results for you.

## Recipe 2 — "What does this run look like?"

When the recent-runs list surfaces a suspect run id:

> Show me the steps for run a4f8b201.

The assistant calls `litmus_steps(run_id="a4f8b201", action="tree")`
(8-char prefix is fine — the tool resolves it) and gets the
step hierarchy with each step's outcome, vector index, and
measurement count. `tree` gives you the nested step hierarchy;
`list` gives a flat ordered list — the assistant picks whichever
fits the question.

To drill further, `litmus_runs(action="get", run_id="a4f8b201")`
returns the run-level summary (project, phase, outcome, started /
ended timestamps, totals) — the same shape the Results detail
Overview tab renders.

## Recipe 3 — "Is the line healthy?"

`litmus_metrics` answers the line-health questions. Pick an action:

| Action | Question it answers |
|---|---|
| `summary` | First-pass yield, final yield, run counts, duration stats |
| `pareto` | Top failure modes ranked by count |
| `ppk` | Per-measurement process performance (Ppk / Pp) |
| `trend` | Yield trend over time, bucketed by `period` (`day` / `week` / `month`) |
| `retest` | Retest rates per serial bucketed by period |
| `time_loss` | Time lost to failed / errored runs |

See [Operator UI → Metrics](../../reference/operator-ui/metrics.md) for how these map to UI tabs.

Filters available on every action: `part`, `station`, `phase`,
`since`, `until`. Plus per-action tuning: `top_n` (Pareto cutoff),
`min_samples` (Ppk minimum-N filter).

Common asks:

> Show me yield for the last 14 days, weekly.

Translates to `litmus_metrics(action="trend", period="week",
since="<two weeks ago>")`. The assistant fills in the ISO date
from "the last 14 days".

> What's the top failure mode on station bench-3?

`litmus_metrics(action="pareto", station="bench-3", top_n=10)`.

> Which measurements have Ppk below 1.33?

Ask that and the assistant runs `litmus_metrics(action="ppk")` and
reads the threshold off the results for you.

## Recipe 4 — "Walk me through the run history" (chained)

For a longer diagnostic, chain the tools:

> Look at the last week of runs, find a serial that failed at
> least twice on the same step, then show me what changed between
> the first failure and the latest one.

That's:

1. `litmus_runs(action="list", limit=200)` — fetch recent runs
2. The assistant groups by serial, finds one with multiple failures
   on the same step
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
