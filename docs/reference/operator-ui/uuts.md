# UUTs

**URLs:** `/uuts`

A UUT (device under test) is the physical unit a test run exercises. UUTs
are never declared in YAML — they are identified at runtime by the serial
number the operator (or automation) supplies when starting a run. Every
distinct `uut_serial` that has ever appeared in run history gets one row
on this page.

## List — `/uuts`

A table with one row per distinct UUT serial observed in run history.
Rows are ordered by most-recent run first.

| Column | What it shows |
|---|---|
| Serial | The `uut_serial` value recorded on the run |
| Part Number | The `uut_part_number` recorded on the most recent run for this serial |
| Lot | The `uut_lot_number` recorded on the most recent run for this serial |
| Runs | Total run count across all outcomes for this serial |
| Passed | Run count with outcome `passed` |
| Failed | Run count with outcome `failed` |
| Last Run | Most recent run start timestamp, browser-local time |

A badge in the page header shows the total count of observed UUTs.

The table has no filter row. All rows are observed-only — there is no
Configured / Observed distinction because UUTs have no backing YAML.

There is no detail page at `/uuts/{serial}`. The list is the only view.

### Empty state

When no runs with a non-empty `uut_serial` are present, the table is
replaced with a card:

> No UUTs observed yet.
>
> Run a test against a station to populate this list. Every distinct UUT
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
| Serial | `uut_serial` |
| Part Number | `uut_part_number` |
| Lot | `uut_lot_number` |

For the full set of run fields, see
[Parquet schema → Run columns](../data/parquet-schema.md).

For the query surface the UI reads through, see
[Query API](../data/query-api.md).

## See also

- [Parquet schema](../data/parquet-schema.md) — the `uut_serial`,
  `uut_part_number`, and `uut_lot_number` run columns
- [Results](results/list.md) — per-run view; filter by serial to see
  the full history of one UUT
- [Launch Test](launch.md) — where the operator supplies the serial
  number that populates this page
