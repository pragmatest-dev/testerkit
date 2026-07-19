# Build a custom operator UI page

TesterKit ships its operator UI (`/results`, `/channels`, `/metrics`, ...) as NiceGUI pages. You can add your own page — a bring-up panel, a fixture-specific control screen, a live dashboard for one station — using the same layout primitives and live-update pattern the built-in pages use, without touching TesterKit internals.

```python
from nicegui import ui
from testerkit.ui import page_layout, page_header, data_table, format_datetime

@ui.page("/my-bringup")
def my_panel():
    with page_layout():
        page_header("Bringup", icon="build")
        data_table(
            columns=[
                {"name": "name", "label": "Channel", "field": "name"},
                {"name": "seen", "label": "Last seen", "field": "seen"},
            ],
            rows=[{"name": "dmm.voltage", "seen": format_datetime(last_seen)}],
            row_key="name",
        )
```

> **Prerequisites.** A working TesterKit project (`testerkit.yaml`, a station YAML if you're connecting to hardware) and NiceGUI installed (it ships with `testerkit`). A custom page is a plain Python script — `@ui.page` registers a route the moment the module runs, no `testerkit serve` plugin hook required.

## Step 1: Lay out the page with the shared primitives

`testerkit.ui` re-exports the primitives the built-in pages are built from, so a custom page matches the site's look without reimplementing it:

- `page_layout()` — the viewport-bound flex-column shell every page with a scrolling table sits in.
- `page_header(title, *, icon=None, badge=None, actions=None)` — the icon + title strip at the top of the page, with optional badge and right-aligned action buttons.
- `data_table(columns, rows, *, row_key, on_row_click=None, time_columns=None)` — the canonical Quasar table: sticky header, virtual scroll for large row counts, and (via `time_columns`) automatic browser-local-time rendering for any column whose cells came from `format_datetime`.
- `format_datetime(dt)` — renders a UTC timestamp as an HTML span that converts to the browser's local time on load. Stored data stays UTC; only display localizes.

```python
from testerkit.ui import page_layout, page_header, data_table, format_datetime
```

Import from `testerkit.ui`, not the deeper `testerkit.ui.shared.components` path — the deep path is the form the built-in pages themselves use and can move; `testerkit.ui` is the stable custom-UI surface.

## Step 2: Show live channel data safely

TesterKit's live data (channel samples, session/instrument events) arrives on background gRPC Flight reader threads, not on NiceGUI's event loop. NiceGUI elements may only be touched from the event loop, so the rule for any live view is:

> **Only the event loop touches a NiceGUI element.**

`testerkit.ui.channel_data(channel_id)` gives you a subscribable handle that already does this marshaling for you — `bind_channel_store` (called once, at startup) bridges the station's `ChannelStore` onto a per-channel [NiceGUI `Event`](https://nicegui.io/documentation/generic_events), and every sample delivered through it lands on the UI event loop. That's safe enough to mutate a single element directly inside the callback:

```python
from testerkit.ui import channel_data

reading = ui.label("No reading")

def _on_sample(sample):
    reading.text = f"{sample.value:.4f} {sample.unit or ''}"

channel_data("dmm.voltage").subscribe(_on_sample)
```

This is the pattern `examples/interactive_station.py` uses for its instrument readback cards — one channel, one label, set directly in the callback.

Once you're painting more than a single element per sample — a table of channels, a chart, anything where a sample flood would mean a flood of DOM updates — write the reading into a plain holder instead and let one `ui.timer` do all the painting:

```python
from dataclasses import dataclass
from nicegui import ui
from testerkit.ui import channel_data

@dataclass
class _Row:
    label: ui.label
    value: object = None
    dirty: bool = False

rows: dict[str, _Row] = {}   # populated with one _Row per channel as you discover them

def _on_sample(sample):
    row = rows[sample.channel_id]
    row.value = sample.value
    row.dirty = True

def _render():
    for row in rows.values():
        if row.dirty:
            row.dirty = False
            row.label.set_text(f"{row.value:.4f}")

ui.timer(0.25, _render)
```

The callback never touches an element; the timer is the only renderer. This is the shape `src/testerkit/ui/components/channel_values.py` uses for the operator UI's live channel-values panel — reach for it as the reference implementation once your page has more than one live element.

Don't reach for `bind_text_from(data, "latest", ...)` — the channel `Event` handle has no `.latest` attribute to bind to; it's a pure pub/sub emitter, not a value holder. Subscribe with `.subscribe(callback)` as shown above.

## Step 3: Wire up a station for live data and control

`testerkit.connect(station_id)` returns a `StationConnection` — the same connection object pytest uses under the hood, usable directly in a NiceGUI app:

```python
import testerkit
from testerkit.ui import bind_channel_store

station = testerkit.connect("bench_01", mock=True)
station.start()
if station.channel_store:
    bind_channel_store(station.channel_store)   # once, at startup
```

Call `bind_channel_store` once, at app startup (`app.on_startup` in NiceGUI), before any page subscribes with `channel_data`. From there:

- `station.instrument(role)` returns the driver instance for that role — call its methods directly (`station.instrument("psu").set_voltage(5.0)`) to command an instrument from a button handler.
- `station.channel_store` / `station.event_store` are the stores `channel_data` and `ui_subscribe` read from.
- `station.session_id` and `station.event_store` let you show cross-process activity — e.g. `ui_subscribe(station.event_store, callback)` to react to instrument-connect/disconnect events from other sessions, the same way the built-in `InstrumentToggle` component shows another session's instrument as in-use.

Stop the station on shutdown (`app.on_shutdown` → `station.stop()`) so the session closes cleanly.

## Reading channel data outside a page context

`testerkit.ui.channel_data` requires `bind_channel_store` to have bridged the store first, and its subscription is UI-loop-scoped. For a script, notebook, or any code path that isn't a NiceGUI page, use the store-direct functions in `testerkit.channels` instead — `channels.latest`, `channels.live`, `channels.window` (see [Stream continuous instrument data](../data/stream-live-channel.md)). Their callbacks deliver on the raw background reader thread with no marshaling, so if you call them from inside a page, treat the callback as background work: write a holder, paint from a `ui.timer`, exactly as in Step 2 — never set an element directly from one of their callbacks.

## The full worked example

`examples/interactive_station.py` is the complete station-monitor-and-control page: instrument cards with live readback + set-and-apply controls, a scope waveform card, an instrument-activity log, and a session table — all built from `testerkit.connect`, `bind_channel_store`, `channel_data`, and the shared components above. Run it directly:

```bash
cd examples && uv run python interactive_station.py
```

Then, in another terminal, run `pytest` in the same directory to watch its events and channel data appear on the page live.

## See also

- [Reference → Query API](../../reference/data/query-api.md) and [Reference → API](../../reference/runtime/api.md) — read historical run/measurement data into a custom page (as opposed to the live channel data covered here)
- [Stream continuous instrument data into a live channel](../data/stream-live-channel.md) — the store-direct `testerkit.channels` verbs from a script, outside any UI page
- [Query channel data](../data/querying-channels.md) — time-range and session-scoped channel queries
- [Managing sessions](managing-sessions.md) — what `connect()`/`StationConnection` sessions are and how they're scoped
- [Tour of the operator UI](../overview/operator-ui-tour.md) — the built-in pages this page's primitives are shared with
