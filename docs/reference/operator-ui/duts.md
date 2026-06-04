# DUTs

**URLs:** `/duts`

A DUT (device under test) is the physical unit a test run exercises. DUTs
are never declared in YAML — they are identified at runtime by the serial
number the operator (or automation) supplies when starting a run. Every
distinct `dut_serial` that has ever appeared in run history gets one row
on this page.

## List — `/duts`

A table with one row per distinct DUT serial observed in run history.
Rows are ordered by most-recent run first.

| Column | What it shows |
|---|---|
| Serial | The `dut_serial` value recorded on the run |
| Part Number | The `dut_part_number` recorded on the most recent run for this serial |
| Lot | The `dut_lot_number` recorded on the most recent run for this serial |
| Runs | Total run count across all outcomes for this serial |
| Passed | Run count with outcome `passed` |
| Failed | Run count with outcome `failed` |
| Last Run | Most recent run start timestamp, browser-local time |

A badge in the page header shows the total count of observed DUTs.

The table has no filter row. All rows are observed-only — there is no
Configured / Observed distinction because DUTs have no backing YAML.

There is no detail page at `/duts/{serial}`. The list is the only view.

### Empty state

When no runs with a non-empty `dut_serial` are present, the table is
replaced with a card:

> No DUTs observed yet.
>
> Run a test against a station to populate this list. Every distinct DUT
> serial that appears in run history shows up here.

### Outcome columns

**Passed** and **Failed** count only runs whose outcome is exactly
`passed` or `failed`. Runs with other outcomes (for example `aborted`)
contribute to the **Runs** total but are not reflected in either column.

## Underlying data

The table is built from the run history parquet. Each column maps to a
field in the run record:

| Column | Run field |
|---|---|
| Serial | `dut_serial` |
| Part Number | `dut_part_number` |
| Lot | `dut_lot_number` |

For the full set of run fields, see
[Parquet schema → Run columns](../data/parquet-schema.md).

For the query surface the UI reads through, see
[Query API](../data/query-api.md).

## See also

- [Parquet schema](../data/parquet-schema.md) — the `dut_serial`,
  `dut_part_number`, and `dut_lot_number` run columns
- [Results](results/index.md) — per-run view; filter by serial to see
  the full history of one DUT
- [Launch Test](launch.md) — where the operator supplies the serial
  number that populates this page
