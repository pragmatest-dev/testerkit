# Export results

TesterKit has two CLI surfaces that take a run and produce a file:

- `testerkit show <run_id> -f <fmt>` — generates a **report** (HTML
  for browsers, PDF for distribution, JSON / CSV for downstream
  tools). Driven by report templates.
- `testerkit export <id> -f <fmt>` — writes a run or session out in a
  test-and-measurement interchange format: CSV, JSON, STDF, HDF5,
  TDMS, MDF4.

Pick the command by what the receiver wants. A QA engineer wants
PDF reports → `testerkit show -f pdf`. A semiconductor vendor wants
STDF → `testerkit export -f stdf`.

## Prerequisites

- At least one completed run on disk
- A run ID or session ID (prefix match works for both — get one
  from `testerkit runs` or the operator UI's
  [Results list](../../reference/operator-ui/results/list.md))

## Reports — `testerkit show -f`

```bash
testerkit show <run_id>              # text summary to the terminal
testerkit show <run_id> -f html      # one HTML file
testerkit show <run_id> -f pdf -o reports/   # PDF into reports/
testerkit show <run_id> -f json      # one JSON file with the run's structured data
testerkit show <run_id> -f csv       # tabular CSV (one row per measurement)
```

HTML and PDF use a report template (default is `default`; switch
with `-t <name>`). JSON and CSV have a fixed layout, so `-t`
doesn't apply. Output path defaults to the current directory;
override with `-o`.

PDF output needs an extra: `pip install 'testerkit[pdf]'`.

| Format | Best for |
|---|---|
| html | Open in a browser, share via screenshot, embed in confluence |
| pdf | Archive or attach to a NCR / bug report |
| json | Programmatic consumption when you want structured run + step + measurement data |
| csv | Spreadsheet analysis when one row per measurement is what you want |

Reports include the run summary, steps, measurements, and (when
captured) the environment snapshot.

## Interchange exports — `testerkit export -f`

```bash
testerkit export <id> -f csv                 # default output dir: exports/csv/
testerkit export <id> -f json -o /tmp/out/   # explicit output dir
testerkit export <id> -f stdf                # STDF v4 for semiconductor test floors
testerkit export <id> -f hdf5                # HDF5 (scientific computing)
testerkit export <id> -f tdms                # NI TDMS (LabVIEW ecosystem)
testerkit export <id> -f mdf4                # ASAM MDF4 (automotive measurement data)
```

`<id>` accepts a run id OR a session id — the CLI auto-detects
whether you gave a run id or a session id. Output directory
defaults to `exports/<fmt>/` when `-o` isn't given.

| Format | Format details |
|---|---|
| csv | Flat CSV — one row per measurement |
| json | Structured JSON mirroring the event stream |
| stdf | Standard Test Data Format (v4) — semiconductor test floors / Spotfire / Examinator |
| hdf5 | Hierarchical Data Format — scientific computing |
| tdms | NI TDMS — LabVIEW / DIAdem |
| mdf4 | ASAM MDF4 — automotive measurement |

## Discoverability — what's installed

```bash
testerkit export <run_id> -f bogus
# No subscriber registered for format 'bogus'.
# Available: csv, hdf5, json, mdf4, stdf, tdms
```

Asking for an unknown format prints the list of installed formats.
The format set ships with TesterKit; there is no plugin surface to
add your own today.

## Common tasks

- **Daily PDF reports for the production line** — wrap
  `testerkit show $RUN -f pdf -o daily-reports/` in your build /
  shift-end script.
- **Feed runs into a semi vendor's STDF analyzer** — `testerkit
  export $RUN -f stdf -o stdf-out/`, ship the file.
- **Build a custom downstream tool that reads CSV** — `testerkit
  export -f csv` to a known path, your tool picks it up.
- **Bulk-export an entire session** — pass the session ID instead
  of a run ID; the CLI replays every run in the session.

## See also

- [Lakehouse import](../../integration/data/lakehouse-import.md) — when the receiver wants parquet rows directly, skip export entirely
- [Grafana](../../integration/data/grafana.md) — when the receiver wants live dashboards instead of files
- [`testerkit show`](../../reference/cli.md#cli-show) — CLI reference for the report path
- [`testerkit export`](../../reference/cli.md#cli-export) — CLI reference for the interchange path
- [Data stores](../../concepts/data/data-stores.md) — where the parquet and event data live on disk
