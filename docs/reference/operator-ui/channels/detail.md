# Channels — single-channel detail

**URL:** `/channels/{channel_id}`

The drill-down view for a single channel. Shows the channel's
descriptor, a full time-series chart (decimated for large series),
and a raw-data table — all filterable by session and date range.

You reach this page by clicking a row in [/channels](list.md).

## Header

A page header showing the channel ID + a Back button (returns to the
channels list).

If the channel ID in the URL isn't registered, the page shows a
"Channel `<id>` is not registered." card with a link back to
`/channels`.

## Descriptor card

Below the header, a card summarises the channel's static metadata:
the data type, units, the instrument role and resource it belongs to
(when set), and the first-seen timestamp. Any recorded attributes
appear below when present.

## Filters

A filter card sits above the chart and data table with **Since** /
**Until** date pickers (default to no bounds — full history) and an
**X-axis toggle** between absolute time and elapsed offset.

Session scoping isn't a picker here: when you arrive from a run's
detail page with a session in the URL, a banner shows it with a Clear
button. To see one session's samples, reach this page from that run.

## Chart

Time-series chart of the filtered samples. TesterKit decimates large
series to ~1,000 points (LTTB) so the chart stays responsive even for
long-running captures. The
visible decimation preserves visual shape: peaks and troughs at
their original positions, intermediate points dropped.

For **array / waveform channels** (e.g. scope captures), the chart
switches mode automatically: the 10 most recent captures overlay in
fading blue (newest darkest), and older captures collapse into a
gray min/max envelope behind them. This gives you an eye-diagram-
style view — the latest capture stands out, the historical envelope
shows drift over time.

## Data table

Below the chart, a paginated table of the same data — same filters,
no decimation. Columns: Received, Value, Source, and Session (shown as
the UUT serial + run start, not a UUID). Useful for copying specific
(timestamp, value) pairs into a notebook or for spotting outliers the
chart's decimation smoothed over.

## Bookmarkable URL state

| Parameter | Meaning |
|---|---|
| `session_id` | Session scope (set by a deep-link from a run) |
| `since` | Earliest sample timestamp |
| `until` | Latest sample timestamp |
| `x_mode` | X axis: `time` (absolute) or `offset` (elapsed) |

Bookmark the URL to share a specific channel + filter combination.

## Underlying data

The samples shown here come from the channel store. The chart is
decimated to ~1,000 points; the raw-data table is not.

For the storage layout, see
[Concepts → Data stores](../../../concepts/data/data-stores.md).

## See also

- [Channels list](list.md) — the table you came from
- [Concepts → Data stores](../../../concepts/data/data-stores.md)
