# System Designer

**URL:** `/designer`

The System Designer is the visual surface for wiring a part's
DUT pins to a station's instrument channels. Pick a part, pick
a station, click a pin, click an instrument channel — the page
draws the wire and auto-saves a fixture YAML to disk.

The bottom of the page has three tabs (Connections, Station Type,
YAML Preview) that always reflect the current graph state.

## Top bar — selection

| Control | Purpose |
|---|---|
| Part | Dropdown of every part in the project's `parts/` directory. Picking one loads the part's pin map onto the design surface and, if a matching fixture YAML already exists for the auto-generated fixture ID, loads its connections too. |
| Station | Dropdown of every station in the project's `stations/` directory. Picking one loads the station's instruments + channels into the right-side pool of wiring targets. |
| Load Fixture | Opens a dialog listing every fixture in `fixtures/`. Pick one and the designer overlays its connections onto the current part. |
| Add Instrument | Opens a dialog to add an instrument to the current station definition. Fields: Instrument Type (optional dropdown from the project catalog — selecting one pre-fills channel names from the type's capability spec), Role Name (e.g. `dmm`, `psu`), Driver (e.g. `examples.drivers.DMM`), Channels (comma-separated). |

## ID bar

| Field | Purpose |
|---|---|
| System ID | Identifier the Station Type tab uses when rendering the station-type YAML preview. The designer does not write a station-type YAML on its own — the preview is for copy-paste or for matching against a hand-written `stations/<id>.yaml`. |
| Fixture ID | Identifier for the fixture YAML the designer writes on auto-save. |

**Auto-save** writes `fixtures/<fixture_id>.yaml` whenever both
IDs are set and the current graph has at least one connection.
Triggered on:

- Adding a wire by clicking a channel node while a pin is selected
- Auto-Match

Things that do **not** trigger auto-save and require a wire add to
flush:

- Clear All — the empty-connections short-circuit means the on-disk
  file is not rewritten to reflect the cleared state
- Drawer-initiated actions (Delete Connection, Delete Pin, Delete
  Instrument, edit a pin field)

There is no Save button anywhere on the page.

## Status bar

A row of counters and toggles, just above the design surface:

| Indicator | Meaning |
|---|---|
| `N / M pins wired` | How many of the part's pins are connected, and the total. |
| `N available` | How many pins are still unconnected. |
| `N instruments` | Instruments currently in the station. |
| Wiring: \<pin\> | When a pin is selected and waiting for a channel click, the status bar shows it in a blue chip. |
| Hide Unused checkbox | Filters the graph to only nodes with at least one connection. |
| Auto-Match button | Walks every unconnected pin that has a characteristic defined on it, asks the matching engine for a compatible channel from the current station, and creates the connection. Pins without a characteristic are skipped silently. |
| Clear All button | Wipes every connection on the current fixture in memory. Does not trigger an on-disk write (see auto-save notes above) — the cleared state lives in the page until you add the next wire. |

## Design Surface

An [ECharts](https://echarts.apache.org/) graph showing DUT pins on
the left and instrument channels on the right, with wires drawn
between connected nodes. Three interactions:

- **Click a pin** — selects it. Status bar shows "Wiring: \<pin\>".
- **Click a channel** while a pin is selected — creates the
  connection and clears the selection.
- **Click a wire** — opens the connection in the right-side
  properties drawer. The drawer shows the connection's fields
  (Point Name, DUT Pin, Net, Instrument, Channel, optional
  Terminal) as read-only inputs with a Delete Connection button —
  it's a remove-or-leave-alone affordance, not an edit form.

When no part is selected and no instruments are loaded, the
graph is replaced with a hint: "Select a part and load a station
to begin."

## Bottom tabs

Three tabs share the panel below the graph; they always reflect the
current graph state.

### Connections tab

A table of every current wire on the fixture.

| Column | What it shows |
|---|---|
| Point | The connection point name — auto-generated from pin + role at wire time and not user-editable |
| DUT Pin | The pin on the part side |
| Net | Net / signal name (optional, free-text) |
| Instrument | The station instrument the wire terminates on |
| Channel | The channel / terminal on that instrument |

Click a row to open the same read-only-with-Delete drawer that the
graph wires open.

When the fixture has no connections, the tab shows: "No connections
yet. Wire pins to instrument channels above."

### Station Type tab

A live YAML preview of a station-type document derived from the
current station's instruments. The structure mirrors what
`/stations` consumes — useful for copying out to a
`stations/<id>.yaml` file by hand. The designer does not save this
on its own; you write it to disk yourself.

When no instruments are loaded, the tab shows: "No instruments
added yet."

### YAML Preview tab

A live YAML preview of the **fixture** document — the file that
gets auto-saved to `fixtures/<fixture_id>.yaml`. Cross-check this
against the [Fixture YAML schema](../configuration.md#fixture-yaml)
to confirm the shape before you depend on the fixture in a run.

When the fixture has no connections, the tab shows: "No connections
to preview."

## Pre-fill via URL

The designer accepts three URL query parameters; the URL is also
kept in sync (via `history.replaceState`) as you make selections so
the current state is bookmarkable.

| Parameter | Pre-fills |
|---|---|
| `part` | The Part dropdown — `?part=<id>` |
| `station` | The Station dropdown — `?station=<id>` |
| `fixture` | Loads an existing fixture by ID — `?fixture=<id>` |

## Underlying data

- Parts come from `parts/`.
- Stations come from `stations/`.
- Fixtures (loaded and saved) live in `fixtures/`.
- Auto-Match uses the same
  [capability matching](../../concepts/configuration/capabilities.md) machinery
  used elsewhere to find a station-channel that satisfies a part
  pin's required signal direction.

## Common tasks

- **Wire a fresh fixture from scratch** — pick Part + Station,
  set Fixture ID, click pins → click channels. Save happens
  automatically.
- **Iterate on an existing fixture** — open the designer with
  `?fixture=<id>` (or pick it from Load Fixture), make changes,
  watch the YAML Preview tab confirm the shape.
- **Generate a draft fixture quickly** — load Part + Station,
  click Auto-Match, then refine the suggestions by clicking
  individual wires.

## See also

- [Fixtures](fixtures.md) — the entity reference for what gets saved
- [Configuration reference → Fixture YAML](../configuration.md#fixture-yaml) — the schema the designer writes
- [Concepts → Capabilities](../../concepts/configuration/capabilities.md) — how Auto-Match decides what fits
- [Stations](stations.md) — the station whose instruments populate the channel side
- [Parts](parts.md) — the part whose pins populate the DUT side
