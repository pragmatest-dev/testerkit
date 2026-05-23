# Channels — list view

**URL:** `/channels`

Every channel Litmus has seen — streaming numeric / array signals
captured during test runs (scope traces, PSU readback, sensor logs).
Each row shows a live sparkline of the last 50 samples and the
latest value; values update in place as new samples arrive. Click a
row to drill into the [single-channel chart](detail.md).

## Table

![Channels — table](../../../_assets/operator-ui/channels/table.png)

A count above the table tells you how many channels are registered.

| Column | What it shows |
|---|---|
| Channel ID | The channel's identifier — typically `<instrument-role>.<signal>` or `<channel-name>` |
| Latest | The most recent sample, with units appended when known. `—` when no samples yet. |
| History | An inline sparkline of the last 50 samples (or `—` for series with fewer than 2 samples). |
| Type | Sample data type (`float`, `array`, ...) from the channel descriptor |
| Instrument | The instrument role this channel belongs to, when known |
| Last updated | When the most recent sample arrived, in browser-local time |

The table is dense — rows are about 30 pixels tall; sparklines
render inside the row at 80×24 pixels.

## Live updates

The view watches the event log for `instrument.read` and
`instrument.set` events and refreshes the table when a new sample
arrives — no manual reload needed. Cells re-render in place;
unchanged rows don't repaint, so the view stays calm during quiet
periods. If the event log isn't running, the page falls back to a
2-second polling loop.

## Empty state

When no channels are registered, the table is replaced with a card
explaining how channels get populated: "Channels appear once a test
writes to the channel store (e.g. `context.observe('scope', value)`
or instrument observers)."

## Underlying data

Channels live in the channel store — separate from the run / event /
measurement stores because the sample shape (numeric streams, arrays,
images) doesn't fit the row-per-measurement model the other stores use.

The page reads from the channel store directly. Programmatic access
goes through `litmus.data.ChannelStore`; there is no first-class CLI
equivalent today.

## Common tasks

- **Watch a sensor live during a test** — open `/channels`, find the
  row, watch the Latest column and sparkline update.
- **Drill into one channel's full history** — click any row to open
  the [single-channel detail](detail.md) with a full chart + raw data
  table + session/date filters.

## See also

- [Channel detail](detail.md) — the per-channel view you reach by
  clicking a row
- [Concepts → Three stores](../../../concepts/three-stores.md) — why
  ChannelStore is separate from EventStore and the parquet runs index
