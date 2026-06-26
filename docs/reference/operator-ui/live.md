# Live test monitor

**URL:** `/live/{run_id}`

The live monitor renders a streaming view of one in-progress test
run. It's the destination for the
[Launch Test](launch.md) form's Start Test button and the
ACTIVE TESTS sidebar block — when an operator clicks either, the
browser lands here.

The page is single-run: it doesn't list the run history. For that,
go to the [Results list](results/list.md). Once the run completes,
a "View Full Results →" link appears at the bottom that takes you
to `/results/{run_id}`.

## Status card

A pinned card at the top of the page with two rows:

- **Status** — current state pill. Starts at `Starting...`, then
  shifts to an uppercase status (`RUNNING`, `PASSED`, `FAILED`,
  `ERROR`) as the test runs. Colors track state (blue while active,
  green on pass, red on fail / error).
- **Run ID** — the full run UUID, monospaced.

Below the rows: a horizontal progress bar and the current step
label. Both update as the test progresses.

## Tab strip

| Tab | What it shows |
|---|---|
| Events | Streaming timeline of every event the run has emitted so far, in chronological order, with per-category filter chips (Session, Run, Test, Channel, File, Dialog, …) and expandable JSON rows. New rows append in real time as the run emits events. |
| Channels | Current channel readouts (per-channel last values + sparkline-style recent history). Channels are discovered from the run's events; their live values come from the channel data stream. |
| Streams | Live file streams the run is writing — name, format, `OPEN` → `DONE` status, size, and a link to the captured artifact. |
| Output | Tail of the test's captured output (stdout and stderr combined), last 100 lines, monospace on a dark background. |

The default tab is **Events**.

## Operator dialogs

When a test hits a [`litmus_prompts`](../pytest/markers.md#litmus_prompts)
marker, the prompt renders as a modal on this page. The same
session is also pushed to the
[ACTIVE TESTS sidebar block](../../how-to/overview/operator-ui-tour.md#active-tests-dynamic)
so an operator on any other page sees an amber row reading
"N dialog(s) waiting" and can click straight back here. The
modal is the dialog UI; the sidebar is the notifier.

For prompt-design guidance, see
[Design operator prompts](../../how-to/execution/operator-prompts.md).

## When the run finishes

- The Status pill turns green or red based on the runner's exit
  code.
- The progress bar fills to 100%.
- The Events tab keeps the full timeline of the completed run.
- A "View Full Results →" link appears at the bottom — click to
  jump to the [Results detail](results/detail.md) page for this
  run.

The page stops streaming when the run ends; it doesn't poll for
new runs.

## On stream errors

If the underlying runner connection fails (a connection or runtime
failure), the Status pill turns red and reads `ERROR`. A toast at
the bottom of the page
prints the exception type and message. The page does not
auto-retry — refresh to reconnect.

## Underlying data

- The status pill, progress bar, step label, and Output tail come
  from the runner's live status stream — a poll of the in-progress
  run.
- The Events, Channels, and Streams tabs stream from the run's event
  log as new events land; the Channels tab reads live sample values
  from the channel data stream.
- The captured output is held by the active runner; closing and
  re-opening this page reconnects to the live output without losing
  anything.

## Common tasks

- **Start a test and watch it run** — [Launch Test](launch.md) →
  Start Test → you land here.
- **Respond to an operator dialog** — when a test pauses, the
  amber sidebar row brings you back to this URL; the modal opens
  automatically.
- **Drill into the completed run** — once the Status pill turns
  green or red, click "View Full Results →" for the per-step
  detail view at [`/results/{run_id}`](results/detail.md).

## See also

- [Launch Test](launch.md) — the form that redirects here
- [Results detail](results/detail.md) — the post-run reference view
- [Design operator prompts](../../how-to/execution/operator-prompts.md) — the design guide for the dialogs this page surfaces
- [Tour of the Operator UI](../../how-to/overview/operator-ui-tour.md) — the ACTIVE TESTS sidebar block
