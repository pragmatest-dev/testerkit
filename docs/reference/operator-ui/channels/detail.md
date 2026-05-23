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
the data type, units, the instrument role it belongs to (when set),
and the first-seen timestamp. Sample-shape fields beyond the basics
appear as well when present in the descriptor.

## Filters

A filter card sits above the chart and data table:

| Filter | What it does |
|---|---|
| Session | Restrict samples to one session. The dropdown is populated from sessions that actually wrote to this channel — no UUID typing required. |
| Since | Earliest sample timestamp |
| Until | Latest sample timestamp |

The dropdown defaults to `(any)`; the date pickers default to no
bounds (full history).

## Chart

Time-series chart of the filtered samples. Litmus decimates large
series to ~1,000 points using LTTB (Largest-Triangle-Three-Buckets)
so the chart stays responsive even for long-running captures. The
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
no decimation. Useful for copying specific (timestamp, value) pairs
into a notebook or for spotting outliers the chart's decimation
smoothed over.

## Bookmarkable URL state

| Parameter | Meaning |
|---|---|
| `session` | Session UUID (omitted for `(any)`) |
| `since` | Earliest sample timestamp |
| `until` | Latest sample timestamp |

Bookmark the URL to share a specific channel + filter combination.

## Underlying data

The page reads from the channel store directly. The chart's LTTB
decimation runs server-side (the chart asks for up to 1,000 points;
the raw data table sees the full filtered set).

For the storage layout, see
[Concepts → Three stores](../../../concepts/three-stores.md).

## See also

- [Channels list](list.md) — the table you came from
- [Concepts → Three stores](../../../concepts/three-stores.md)
