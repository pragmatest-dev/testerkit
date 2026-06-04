# Stations

**URLs:** `/stations` (list), `/stations/new`, `/stations/{id}`, `/stations/{id}/edit`

A station is a bench — the set of instruments at a physical location
that run tests against DUTs. The Stations entity pages are the
browser surface for configuring them. Test engineers usually author
station YAML directly in `stations/*.yaml`; the UI exists for quick
edits, bench operators who don't want to touch YAML, and a usage
overview that the YAML files don't give you on their own.

## List — `/stations`

A table with one row per station that is either configured in the project
(a YAML file exists) or has been observed in run history (no YAML file,
only referenced by past runs). Columns:

| Column | What it shows |
|---|---|
| Status | **Configured** chip (grey) — a YAML file exists. **Observed** chip (amber) — appears in run history but has no YAML file. |
| ID | Station identifier |
| Name | Human-readable station name (falls back to ID when blank) |
| Location | Physical location, when set |
| Instruments | Count of instruments configured on the station; `—` for Observed rows |
| Runs | Total runs that have used this station |
| Passed | Run count with outcome `passed` |
| Failed | Run count with outcome `failed` |
| Last Run | Most recent run start timestamp, browser-local time |

Above the table, a filter card with **All / Configured / Observed** buttons
narrows the view. The active filter is mirrored into the URL so the view is
bookmarkable.

A **New Station** button at the top right jumps to `/stations/new`.
Clicking a Configured row jumps to the detail view at `/stations/{id}`.
Observed rows are not clickable — no YAML exists to display.

When no stations are configured or observed, the table is replaced with an
empty-state card explaining the entity and offering a Create Station button.

## Detail — `/stations/{id}`

A header bar (Back + Edit buttons — no Delete), one Station
Information card with ID / name / description / location / supported
test phases, then a tab strip with three tabs:

| Tab | Content |
|---|---|
| Instruments | One row per instrument: Name, Driver, Resource, Manufacturer / Model, Serial, Cal Due, Status |
| Capabilities | A table of what this station's instruments can measure or source: Instrument, Capability, Function, Direction |
| Recent Runs | A run table scoped to this station (DUT, Project, Started, Outcome) |

When the URL points at a station that doesn't exist, the page shows
a "Station not found." card.

## Edit — `/stations/{id}/edit`

A header bar with the station name and a "Changes auto-saved" hint
(changes save as you type — there's no Save button). Below the
header, a tab strip with two tabs:

| Tab | Content |
|---|---|
| Info | Name, Location, Description. Station ID is read-only after creation. |
| Instruments | Per-instrument rows with Name, Driver, Resource. An "Add Instrument" button opens a dialog where you can pick a driver from the bundled library or type a Python import path. |

## New — `/stations/new`

A single form with the same Info + Instruments fields as Edit, plus
an editable Station ID at the top (renaming after creation means
delete + recreate). After creation, the page redirects to
`/stations/{id}`.

## Underlying data

Stations are stored as YAML in `stations/*.yaml` under the project
root. The Edit and New forms write to these files directly; the
List + Detail views read them. Changes outside the UI (e.g. you edit
the YAML in an IDE) are picked up on next page load.

For the YAML schema, see
[Configuration reference → Stations](../configuration.md#station-yaml).
For the concept, see [Concepts → Stations](../../concepts/configuration/stations.md).

## Common tasks

- **Add a new instrument to a station** — open `/stations/{id}/edit`,
  click Add Instrument, fill the dialog.
- **See yield by station** — the List view's Passed and Failed
  columns give a quick read; the [Metrics → Yield](metrics.md#yield)
  tab grouped by station gives the full picture.

## See also

- [Configuration reference → Stations](../configuration.md#station-yaml)
- [Concepts → Stations](../../concepts/configuration/stations.md)
- [Launch Test](launch.md) — pick a station and start a run
