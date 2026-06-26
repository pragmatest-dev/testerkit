# Instruments

**URLs:** `/instruments` (list), `/instruments/new`, `/instruments/{id}` (detail), `/instruments/{id}/edit`

The Instruments page has two tabs: **Catalog** (instrument types
— the templates that describe what an instrument can do) and
**Inventory** (physical assets — individual units with serial
numbers, calibration dates, manufacturer / model).

## List — `/instruments`

### Catalog tab (default)

A table with one row per instrument type defined in the catalog.

| Column | What it shows |
|---|---|
| Type | Catalog type id (e.g. `keysight_e3631a`) |
| Name | Human-readable name |
| Description | Description, when set |
| Capabilities | Count of capability entries (function + direction pairs the instrument supports) |

Click a row to open `/instruments/{type}` — the catalog-type detail
view with the full capability list. When the catalog is empty, the
table is replaced with a card offering a Create Instrument button.

### Inventory tab

A table with one row per physical instrument that is either configured
in the project (an asset YAML file exists) or has been observed in run
history (no YAML file, only referenced by past runs). Columns:

| Column | What it shows |
|---|---|
| Status | **Configured** chip (grey) — an asset YAML file exists. **Observed** chip (amber) — appears in run history but has no asset YAML file. |
| ID | Asset identifier |
| Driver | Driver identifier from the asset YAML |
| Manufacturer / Model | Identity stamp from the asset YAML |
| Serial | Hardware serial number |
| Cal Due | Calibration due date, ISO 8601 |
| Cal Lab | Calibration lab name |
| Runs | Total runs that have used this instrument |
| Last Run | Most recent run start timestamp, browser-local time |

Above the table, a filter card with **All / Configured / Observed** buttons
narrows the view. The active filter is mirrored into the URL so the view is
bookmarkable.

Clicking a Configured row jumps to the asset detail view at `/instruments/{id}`.
Observed rows are not clickable — no YAML file exists to display.

When no instrument assets are configured or observed, the tab shows a blue
hint card pointing at `litmus station init` to discover instruments and create
asset files.

## Detail — `/instruments/{id}`

The same URL resolves either a catalog type OR a physical asset
depending on what `{id}` matches; the page picks the right shape
automatically:

- **Catalog type** — info card followed by a tab strip with three
  tabs:

  | Tab | Content |
  |---|---|
  | Capabilities | The full capability list — function, direction, signals, specs, and parameters per entry |
  | SCPI Commands | The instrument's SCPI command vocabulary (when set) |
  | Simulation | Simulation-mode response definitions (when set) |

- **Asset** — info card followed by **stacked cards** (no tabs):
  Calibration card (last cal date, due date, lab) and Linked
  Stations card (the stations whose YAML references this asset).

## Edit — `/instruments/{id}/edit`

Only resolves for catalog types; assets are edited by hand-editing
the `instruments/<id>.yaml` file directly.

A tab strip with four tabs:

| Tab | Content |
|---|---|
| Info | Name, description, manufacturer / model |
| Capabilities | Add / edit / delete capability entries |
| SCPI Commands | Edit the SCPI command vocabulary |
| Simulation | Edit simulation-mode responses |

## New — `/instruments/new`

A form for creating a new catalog type. Sets type id, name,
description, and an initial capability list. After creation, the
page redirects to `/instruments/{id}` so further capability /
SCPI / simulation editing happens via the four-tab edit surface.

## Underlying data

- Catalog types come from the project's `catalog/` directory plus
  the bundled generic catalog (`litmus.catalog.generic`)
- Inventory assets come from `instruments/*.yaml`

For the YAML schemas, see [Catalog schema](../catalog/schema.md) for
catalog types and [Models → `InstrumentAssetFile`](../data/models.md#model-instrumentassetfile)
for asset files.

## Common tasks

- **Add a new instrument type to the project's catalog** — open
  `/instruments/new`, fill the type form.
- **Discover instruments on the bench** — run `litmus station init`
  from the project root; it walks the VISA bus and writes one asset
  YAML per found device.
- **Check calibration status** — the Inventory tab's Cal Due
  column, sorted ascending, surfaces what's about to expire.

## See also

- [Configuration reference → Catalog](../catalog/schema.md)
- [Concepts → Capabilities](../../concepts/configuration/capabilities.md) — how
  catalog capabilities feed station ↔ part matching
- [`litmus station init`](../cli.md#cli-station-init) — discover
  instruments and create asset YAML
