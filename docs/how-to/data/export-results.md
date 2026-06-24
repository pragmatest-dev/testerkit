# Export results

Litmus has two CLI surfaces that take a run and produce a file:

- `litmus show <run_id> -f <fmt>` — generates a **report** (HTML
  for browsers, PDF for distribution, JSON / CSV for downstream
  tools). Driven by report templates.
- `litmus export <id> -f <fmt>` — **replays the event stream** for
  a run or session into a target format. The supported formats are
  the test-and-measurement interchange ones: CSV, JSON, STDF,
  HDF5, TDMS, MDF4.

Pick the command by what the receiver wants. A QA engineer wants
PDF reports → `litmus show -f pdf`. A semiconductor vendor wants
STDF → `litmus export -f stdf`.

## Prerequisites

- At least one completed run on disk
- A run ID or session ID (prefix match works for both — get one
  from `litmus runs` or the operator UI's
  [Results list](../../reference/operator-ui/results/list.md))

## Reports — `litmus show -f`

```bash
litmus show <run_id>              # text summary to the terminal
litmus show <run_id> -f html      # one HTML file
litmus show <run_id> -f pdf -o reports/   # PDF into reports/
litmus show <run_id> -f json      # one JSON file with the run's structured data
litmus show <run_id> -f csv       # tabular CSV (one row per measurement)
```

The HTML and PDF formats render via Jinja2 templates (default
template is `default`; switch with `-t <name>`). JSON and CSV are
fixed-format writers — the `-t` option doesn't apply to them.
Output path defaults to the current directory; override with `-o`.

| Format | Best for |
|---|---|
| html | Open in a browser, share via screenshot, embed in confluence |
| pdf | Archive or attach to a NCR / bug report |
| json | Programmatic consumption when you want structured run + step + measurement data |
| csv | Spreadsheet analysis when one row per measurement is what you want |

Reports run against the **denormalized parquet** for the run. They
include the run summary, steps, measurements, and (when captured)
environment snapshot.

## Interchange exports — `litmus export -f`

```bash
litmus export <id> -f csv                 # default output dir: exports/csv/
litmus export <id> -f json -o /tmp/out/   # explicit output dir
litmus export <id> -f stdf                # STDF v4 for semiconductor test floors
litmus export <id> -f hdf5                # HDF5 (scientific computing)
litmus export <id> -f tdms                # NI TDMS (LabVIEW ecosystem)
litmus export <id> -f mdf4                # ASAM MDF4 (automotive measurement data)
```

`<id>` accepts a run id OR a session id — the CLI auto-detects by
prefix-matching the events file. Output directory defaults to
`exports/<fmt>/` when `-o` isn't given.

| Format | Format details |
|---|---|
| csv | Flat CSV — one row per measurement |
| json | Structured JSON mirroring the event stream |
| stdf | Standard Test Data Format (v4) — semiconductor test floors / Spotfire / Examinator |
| hdf5 | Hierarchical Data Format — scientific computing |
| tdms | NI TDMS — LabVIEW / DIAdem |
| mdf4 | ASAM MDF4 — automotive measurement |

Mechanics differ from reports: `litmus export` reads the run's
events from the Arrow IPC store and replays them through a
subscriber registered for the requested format. This is what
makes the exporter set extensible — new formats register a
subscriber class with the `format_name` field and they show up in
the `-f` choices.

## Discoverability — what's installed

```bash
litmus export <run_id> -f bogus
# No subscriber registered for format 'bogus'.
# Available: csv, hdf5, json, mdf4, stdf, tdms
```

Asking for an unknown format prints the current list of installed
subscribers. The format set is fixed by the Litmus package — new
formats need to land as built-in subscribers in
`src/litmus/data/exporters/`; there is no plugin / entry-point
extension surface today.

## Common tasks

- **Daily PDF reports for the production line** — wrap
  `litmus show $RUN -f pdf -o daily-reports/` in your build /
  shift-end script.
- **Feed runs into a semi vendor's STDF analyzer** — `litmus
  export $RUN -f stdf -o stdf-out/`, ship the file.
- **Build a custom downstream tool that reads CSV** — `litmus
  export -f csv` to a known path, your tool picks it up.
- **Bulk-export an entire session** — pass the session ID instead
  of a run ID; the CLI replays every run in the session.

## See also

- [Lakehouse import](../../integration/data/lakehouse-import.md) — when the receiver wants parquet rows directly, skip export entirely
- [Grafana](../../integration/data/grafana.md) — when the receiver wants live dashboards instead of files
- [`litmus show`](../../reference/cli.md#cli-show) — CLI reference for the report path
- [`litmus export`](../../reference/cli.md#cli-export) — CLI reference for the interchange path
- [Data stores](../../concepts/data/data-stores.md) — where the parquet and event data live on disk
