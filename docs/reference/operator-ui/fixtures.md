# Fixtures

**URLs:** `/fixtures` (list), `/fixtures/new`, `/fixtures/{id}`, `/fixtures/{id}/edit`

A fixture is the wiring between a DUT's pins and a station's
instrument channels — which probe goes on which pin, which DMM
channel reads which voltage. A fixture is bound to a product (so it
gets used in the right runs) and is used at run time by stations
whose instruments cover the fixture's connections. The fixture's
connection rows themselves carry free-text DUT-pin and channel
fields — they're not validated against the product's pin map at
edit time.

## List — `/fixtures`

A table with one row per fixture. Columns:

| Column | What it shows |
|---|---|
| ID | Fixture identifier |
| Name | Human-readable name |
| Product | Product name (falls back to the bound product ID when the product name isn't resolvable) |
| Rev | Revision string (when set) |
| Connections | Count of pin → channel connections defined |
| Runs | Total runs that have used this fixture |
| Passed | Run count with outcome `passed` |
| Failed | Run count with outcome `failed` |
| Last Run | Most recent run start timestamp |

Click a row to open `/fixtures/{id}` (detail). When no fixtures
exist, the table is replaced with a card offering a Create Fixture
button.

## Detail — `/fixtures/{id}`

A header bar (Back + Edit buttons), one Fixture Information card
(ID, name, product binding, revision, description), then a tab
strip with three tabs:

| Tab | Content |
|---|---|
| Pin Mappings | The connections table — one row per (DUT pin → instrument channel) mapping. Columns: Connection (the mapping's name), DUT Pin, Net, Instrument, Channel, Description. |
| Compatible Stations | Stations whose instruments cover this fixture's required instruments and channels. |
| Diagram | A Mermaid-rendered diagram of the pin-to-instrument wiring. |

## Edit — `/fixtures/{id}/edit` and New — `/fixtures/new`

A form for the same fields the fixture YAML carries. The Product
binding is a dropdown — editable on both New and Edit (no read-only
restriction). The DUT Pin and Instrument Channel fields on each
connection row are **free-text inputs**, not dropdowns sourced from
the product pin map.

The Instrument dropdown on each connection row pulls its options
from the union of all stations' instruments in the project, so any
instrument role registered anywhere is selectable.

Connections are managed as rows — Add / Edit / Delete one at a time
via per-row buttons and an Add Connection dialog.

## Underlying data

Fixtures are stored as YAML in `fixtures/*.yaml` under the project
root.

For the YAML schema, see
[Configuration reference → Fixtures](../configuration.md#fixture-yaml).
For the concept, see [Concepts → Fixtures](../../concepts/fixtures.md).

## Common tasks

- **Wire a new probe to a pin** — open `/fixtures/{id}/edit`, add a
  connection row mapping pin → instrument-role + channel.
- **See where a fixture has been used** — the List view's Runs +
  Passed + Failed columns + Last Run; the [Results list](results/list.md)
  doesn't filter by fixture today, so use the Runs column here as
  the quick scoreboard.

## See also

- [Configuration reference → Fixtures](../configuration.md#fixture-yaml)
- [Concepts → Fixtures](../../concepts/fixtures.md)
