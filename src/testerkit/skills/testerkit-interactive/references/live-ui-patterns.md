# Live-UI patterns — what to pick, what to avoid

AI-judgment layer only: which pattern to reach for and why, plus the
anti-pattern catalog. User-facing build steps (imports, prerequisites, full
walkthrough) live in the shipped `testerkit docs show
how-to/execution/custom-operator-ui` — don't duplicate that prose here; this
file exists to make the *choice* correctly on the first pass.

## The one rule everything else follows

NiceGUI elements may only be mutated from the event loop NiceGUI itself runs
on. TesterKit's live data — `ChannelStore` samples, `EventStore` events — is
produced on background gRPC Flight reader threads. Every TesterKit-provided
binding exists to cross that thread boundary safely; nothing else does it
for you.

## Decision table — which subscribe surface, and does it marshal for you?

| Surface | Where it lives | Delivers on | Safe to mutate a widget directly in the callback? |
|---|---|---|---|
| `testerkit.ui.channel_data(id).subscribe(cb)` | `testerkit.ui` (re-export of `testerkit.ui.shared.event_binding.ui_channel_data`) | NiceGUI event loop (`_on_ui_loop` hops it there) | **Yes** — one element, one sample, one line |
| `testerkit.ui.subscribe(store, cb, ...)` (`ui_subscribe`) | same module, for `EventStore` events (session/instrument activity) | NiceGUI event loop | **Yes**, same reason |
| `testerkit.channels.latest(name, cb)` | `testerkit.channels` (store-direct, no NiceGUI dependency) | raw Flight reader thread | **No** |
| `testerkit.channels.live(name, cb, max_hz=...)` | `testerkit.channels` | raw Flight reader thread | **No** |
| `testerkit.channels.window(name, cb, dur=...)` | `testerkit.channels` | raw Flight reader thread (history prefill runs inline, then the same raw thread for the live tail) | **No** |

The first two are the ones to reach for inside a NiceGUI page — they exist
specifically so page code doesn't have to think about threads. The
`testerkit.channels` verbs are for scripts, notebooks, and anything with no
event loop to hop onto; if you use them *inside* a page anyway (e.g. because
you need `window`'s history-then-live seam and there's no `channel_data`
equivalent for it yet), you own the marshaling — see the holder+timer recipe
below, and never write to a widget from inside `live`/`window`/`latest`'s
callback itself.

## The holder + `ui.timer` recipe (reference implementation)

Read `src/testerkit/ui/components/channel_values.py` before writing a new
multi-element live view — it is the shipped canonical case, not a toy:

1. One dataclass per row/element holds the latest values plus a `dirty` bool.
   No `ui.label`/`ui.*` mutation happens in step 2.
2. The sample callback (`channel_data(id).subscribe(...)`) writes only into
   the holder and flips `dirty = True`. Even though this callback is already
   loop-marshaled and *could* mutate directly, `channel_values.py` still
   batches through a holder — at sample-flood rates, one DOM write per
   sample is the wrong trade even when it's technically safe.
3. A single `ui.timer(interval, render_fn)` is the *only* code that calls
   `.set_text()` / `.update()` / etc., and only for rows where `dirty` is
   true (then clears the flag).
4. Structural changes (a new channel appearing, a channel closing) are
   discovery events off `EventStore` (`channel.started` / `channel.ended`),
   delivered via `ui_subscribe` — already loop-safe — and are the only place
   new elements get created.

Use this whenever a page shows more than one live element, or a single
element updates faster than is worth a DOM write per sample. Note
`interactive_station.py`'s `_on_waveform` handler mutates the ECharts option
and calls `chart.update()` directly inside the sample callback, one
acquisition per sample, with no holder — that's the same "single element,
direct mutation" shape as the one-label case above, just for a chart instead
of a label; it is not the holder+timer shape. If a waveform channel in your
project fires faster than one DOM update per sample is worth, throttle it
through a holder + timer the same way `channel_values.py` does.

## The broken example, and why

`testerkit/ui/__init__.py`'s own module docstring (not this skill) shows:

```python
data = channel_data("oven.temp_c")
ui.label().bind_text_from(data, "latest", lambda v: f"{v:.1f} C")
```

This raises. `channel_data(id)` returns `nicegui.Event` — read
`testerkit/ui/shared/event_binding.py`: `_channel_signals: dict[str, Event]`,
and `ui_channel_data` does nothing but `Event()` and cache it. `nicegui.Event`
is a pure pub/sub emitter (`.subscribe(cb)` / `.emit(value)`); it carries no
stored value and has no `.latest` attribute for `bind_text_from` (which
needs an object with a gettable attribute) to read. There is no fixed
`"latest"`-style binding surface in TesterKit today — every live view is
`.subscribe(callback)` plus either direct mutation (single element) or the
holder+timer recipe (multiple elements). Do not present the docstring's
example as working code, and do not invent a `.latest` shim to make it work
— that isn't a decision this skill or a single doc fix owns.

## Other things not to teach as the default path

- **`utc_datetime_input`** exists in the shared UI components but has no
  current call site — don't reach for it as the "how to take a timestamp
  input" answer without checking it's still what you want; it may be dead
  code.
- **`InstrumentToggle`** (`testerkit.ui.shared.components`) is the shipped
  connect/disconnect button used by `examples/interactive_station.py` — it
  already handles the "another session has this instrument" cross-process
  state via `ui_subscribe`. Reach for it instead of hand-rolling a
  connect/disconnect button that re-derives that logic.
- **`station.configure(role, method, **params)`** on `StationConnection`
  only emits an `InstrumentConfigure` event for the activity log — it never
  calls the driver. Pair it with an actual driver call
  (`toggle.driver.start_continuous(...)`) when the UI action does something;
  don't expect `configure` alone to change instrument state.
