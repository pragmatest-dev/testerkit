# Live UI pattern (NiceGUI)

How every live view in the operator UI updates. One rule, two jobs, all
documented NiceGUI primitives. Internal contributor note — not user-facing.

## The rule

> **Only the event loop ever touches a NiceGUI element.**

NiceGUI is single-event-loop. Our live data, however, arrives on **background
gRPC Flight reader threads** (`EventStore`/`ChannelStore` deliver on their own
threads) — and the NiceGUI docs give *no* guidance on updating the UI from a
background thread. So we never do. Every element mutation happens on the loop;
background work reaches the UI only through a plain Python holder or an awaited
result.

## The two jobs

| Shape | Pattern | Primitive(s) |
|---|---|---|
| **One-shot blocking call** — a history fetch, any sync API call inside a handler | `async` handler → `await run.io_bound(fn, …)` → update elements directly after the await (you resume on the loop) | async handlers + `run.io_bound` |
| **Continuous push** — a live sample/event stream | the delivery callback writes only a plain holder (deque / dataclass / cell); a single `ui.timer` reads the holder and paints | `ui.timer` + holder |

Supporting cast, unchanged:
- `@ui.refreshable` — **structural** rebuilds only (a section appears/disappears),
  driven by a dirty flag the timer checks. Never for per-sample updates.
- `bind_text_from` / automatic sync — trivial single-value reflections.

## Why this and not "marshal the callback onto the loop"

NiceGUI *can* marshal a background callback onto the loop
(`core.loop.call_soon_threadsafe`, which `event_binding._on_ui_loop` does), and
that is loop-safe. But it isn't a documented NiceGUI pattern, it's hard to teach,
and it breeds the stale-callback-on-rebuild bug (a marshalled callback firing
after `refresh()` has replaced the element it targets). Holder + `ui.timer`
structurally avoids both: the timer always paints the *current* elements, and
nothing off-loop touches UI.

## Canonical examples

- **Customer template** — `examples/09-instrument-streaming/scripts/live_monitor_ui.py`.
  Background producer + `channels.latest`/`channels.window` callbacks write
  `_STATE` (a dict + `deque`); one `ui.timer(0.1, redraw)` paints. This is the
  shape we teach.
- **Operator detail page** — `src/litmus/ui/pages/channels/detail.py`. `refresh()`
  is `async` and runs `query_channel` via `run.io_bound`; live samples land in a
  page-level deque painted by a `ui.timer`; the `LiveBadge` is fed by
  `channel.started`/`channel.closed` lifecycle events plus sample activity.
- **Operator values panel** — `src/litmus/ui/components/channel_values.py`. The
  sample callback records a per-row holder (`_ChannelRow`); a `ui.timer` paints
  the rows that changed. Row add/close are structural and run on the loop via
  `ui_subscribe`.

## Anti-patterns

- Mutating an element inside a sample/event delivery callback (even a marshalled
  one). Write the holder; let the timer paint.
- Calling a blocking query synchronously inside a handler or page build — it
  freezes the page. Use `await run.io_bound(...)`.
- `@ui.refreshable` for per-sample updates — it deletes and rebuilds the subtree
  every sample.

## Status

Converged: the channel detail page, the channel values panel, and `LiveBadge`.

Not yet converged (follow-up): the other event-driven panels still mutate
elements inside their `ui_subscribe` callbacks via `event_binding`'s loop
marshalling — `event_timeline`, `instrument_activity`, `session_table`,
`file_streams`, `results/detail`, `metrics`, `explore`, `results/list`. The
tree-wide simplification (drop the render-path marshalling once every panel uses
holder + timer) is deliberately deferred to avoid changing all live panels at
once.
