# Measurements

**URL:** `/explore`

A chart-based view of every measurement Litmus has recorded — filter,
group, and plot. The table-based
[Results detail Measurements tab](results/detail.md#measurements) is
scoped to one run; this view spans the whole index, lets you filter
on any facet, and renders the result as a chart instead of a table.

Use it to ask questions like "what did `output_voltage` look like
across all production runs on bench-3 last week?", "did this
measurement drift when we changed lots?", or "how is the limit
margin distributed for `iq` across all DUTs?".

## Filter section

![Measurements — filter facets](../../_assets/operator-ui/explore/filters.png)

A row of facet widgets, each populated from values actually present
in the index. Changing any facet re-fetches all the others' options
(so the Product list only shows products that exist under your other
current filters).

| Facet | Filters by | Widget |
|---|---|---|
| Run outcome | The run's overall outcome (`passed`, `failed`, ...) | Multi-select enum |
| Measurement outcome | The measurement row's own outcome | Multi-select enum |
| Limit comparator | How the measurement is checked (`GELE`, `LE`, ...) | Multi-select enum |
| Product | DUT part number | Multi-select |
| Station | Station hostname or identifier | Multi-select |
| Test phase | The free-form `test_phase` value stamped on the run. The conventional values are `validation`, `production`, and `characterization`, but any string a project chooses to use will appear here. | Multi-select |
| Step | The test/step name the measurement belongs to | Multi-select |
| Measurement | The named measurement (`vout`, `iq`, ...) | Multi-select |
| DUT serial | A specific unit | Multi-select |
| Date range | Run start date `Since` / `Until` | Date range |

A count below the row tells you how many measurement rows the
current filter set matches.

Before any measurements are recorded, the page shows a quick-start
card pointing at the `verify` and `logger.measure` calls that
populate the index.

## Plot controls

![Measurements — plot controls](../../_assets/operator-ui/explore/plot-controls.png)

The PLOT card sits above the chart and decides what gets drawn.

| Control | What it does |
|---|---|
| Y axis | Which numeric column to plot vertically. Required. Both fixed schema columns (`measurement_value`, `run_started_at`, ...) and dynamic input/output columns (`in_*`, `out_*` recorded by `context.configure()` and `context.observe()`) appear in the dropdown. |
| X axis | Which numeric column to plot horizontally. Same column choices as Y. Required for scatter / line / bar; ignored for histogram. |
| Chart | One of `scatter` (default), `line`, `bar`, `histogram`. |
| Group by | Optional. Plots one series per distinct value of the chosen column — e.g. group by `dut_serial` to see one line per DUT. |
| Bins | Histogram bin count. 2–200, default 30. |
| Limit | Maximum row count fetched. 10–100,000, default 5,000. The chart only ever shows up to this many rows. |
| Refresh | Force a re-fetch. Filter and control changes already auto-refresh; use this to pick up new runs that arrived while the page was open. |

When Y is unset (or X is unset for non-histogram charts), the chart
area shows a placeholder until you pick a column.

## Chart

![Measurements — chart](../../_assets/operator-ui/explore/chart.png)

Interactive chart of the filtered rows. The chart kind matches the
PLOT control:

- **Scatter** — one point per measurement row at `(x, y)`
- **Line** — points sorted by X and connected
- **Bar** — bars at each X with height Y
- **Histogram** — distribution of the Y value alone (X is ignored), bucketed by Bins

When the result set exceeds Limit, only the first Limit rows are
returned (ordered by run id, not by date). Tighten the filter set or
raise Limit if the chart looks truncated; very large limits make the
chart slow to render.

## Live updates

The view watches the event log for `run.ended` events and re-fetches
the active chart when a run finishes. No manual reload needed.

## Bookmarkable URL state

Every facet, axis, and chart setting lives in the URL, so a URL
captures the exact view:

| Parameter | Meaning |
|---|---|
| Per-facet | Each multi-value facet repeats its column name as a query key (e.g. `?product=PN-100&product=PN-200`) |
| `since`, `until` | Date range, `YYYY-MM-DD` (omitted when blank) |
| `y`, `x` | Selected axes (omitted when blank, and `x` is omitted on histogram charts where it's ignored) |
| `chart_type` | `scatter` (default — omitted), `line`, `bar`, or `histogram` |
| `group_by` | Selected group-by column (omitted when blank) |
| `bins` | Histogram bin count (omitted at default 30) |
| `limit` | Max row count fetched (omitted at default 5,000) |

Defaults are stripped from the URL to keep it short.

## Underlying data

The view reads from the same measurements index the
[Results detail](results/detail.md) page reads from, just unfiltered
across all runs. The CLI equivalents:

- `litmus measurements ...` for parametric queries (when wired)
- The Python query class `MeasurementsQuery` is what the page calls
  under the hood

Each measurement row carries the values shown in `vout`, `iq`, etc.
— see [Parquet schema](../parquet-schema.md) for the column list.

## Common tasks

- **Distribution of one measurement** — set the Measurement facet
  to one value, Y to `measurement_value`, Chart to `histogram`, and
  read the spread. Limit-bearing measurements show their pass/fail
  via the Measurement outcome facet.
- **One measurement across DUTs** — set the Measurement facet,
  X to `run_started_at`, Y to `measurement_value`, Group by
  `dut_serial`. One line per unit, time-series view.
- **Compare lots** — Group by something derived from `dut_serial`,
  or stack Lot + Date Range filters and re-query.

## See also

- [Results detail → Measurements tab](results/detail.md#measurements)
  — per-run measurement view
- [Metrics → Cpk](metrics.md#cpk) — process capability ranking
  across the same data
- [Parquet schema](../parquet-schema.md) — the measurement row
  columns the facets and axes pull from
