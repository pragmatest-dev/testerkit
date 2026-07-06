---
name: litmus-interactive
description: Use when a user wants to pause a test for operator input (confirm/choice/input dialogs), build a custom live operator screen or bench-monitor page, or live-monitor a channel outside a test. Distinct from litmus-tests (which verb to call) and litmus-capture (channel/file store mechanics) — this skill owns the human-in-the-loop and live-UI surface.
---

# Interactive tests & live operator UIs

Three jobs: pause a test for the operator, build a custom NiceGUI screen that
updates live, and drive a station interactively outside pytest entirely.

## 1. Operator prompts in a test

Declare each prompt with the `litmus_prompts` marker, keyed by name; read the
answer with the `prompt` fixture:

```python
import pytest

@pytest.mark.litmus_prompts(
    insert_uut={"message": "Insert UUT, then click Confirm.", "prompt_type": "confirm"},
    pick_bench={"message": "Which bench?", "prompt_type": "choice",
                "choices": ["bench_01", "bench_02"]},
    chamber_temp={"message": "Set chamber temperature (C):", "prompt_type": "input",
                  "timeout_seconds": 120},
)
def test_setup(prompt):
    prompt("insert_uut")           # confirm -> True
    bench = prompt("pick_bench")   # choice  -> selected str
    temp = prompt("chamber_temp")  # input   -> typed str
```

`prompt_type` is `confirm` (default), `choice` (needs `choices`), or `input`;
`timeout_seconds` caps the wait and raises `PromptUnavailableError` on
expiry — it does not auto-answer. `prompt()` with no key works only when
exactly one `litmus_prompts` entry is in scope for that test; pass the key
whenever more than one prompt is declared at file, class, or test level.

Routing is automatic and never your test's concern: an installed UI dialog
handler wins, then `LITMUS_AUTO_CONFIRM=1`, then a tty fallback, else
`PromptUnavailableError`. **Set `LITMUS_AUTO_CONFIRM=1` for CI** so prompts
auto-resolve instead of hanging the run — check that a `choice` prompt's
first option is a sane default, since auto-confirm picks index 0.

A sidecar `<test>.yaml` can declare the same shape under `prompts:` — it
cascades into the marker like any other Litmus field (right-sizing lives in
`litmus-tests`).

**Session-level prompts are a different thing.** `required_inputs:` in
`litmus.yaml` asks once, at session start, before any test runs — for a
serial number or operator badge every test in the session needs:

```yaml
required_inputs:
  serial_number: {message: "Scan or type the UUT serial:", prompt_type: input}
```

Resolution order per key: CLI flag (`--serial-number`) → env var
(`LITMUS_SERIAL_NUMBER`) → the declared prompt. Unresolved after all three
fails the session before any test collects. Don't reach for this to gate a
single test — that's `litmus_prompts`.

`prompt_for_serial` is a separate, TTY-only helper used for `--uut-serial`
collection on non-development test phases — it raises immediately under CI
or a non-tty session rather than falling back to auto-confirm. It is not
part of the `litmus_prompts`/`required_inputs` path; don't reach for it in
new tests.

Depth: `litmus docs show how-to/execution/operator-prompts` — the three
prompt types, marker-scope rules, and message-wording guidance in full.
Hung/timed-out prompts in CI are a `litmus-debug` triage case
(`PromptUnavailableError`, `LITMUS_AUTO_CONFIRM` unset).

## 2. A custom NiceGUI operator page

Reuse the same primitives the built-in pages (`/results`, `/channels`, ...)
are built from — a custom page matches the site's look for free:

```python
from nicegui import ui
from litmus.ui import page_layout, page_header, data_table, format_datetime

@ui.page("/my-bringup")
def my_panel():
    with page_layout():
        page_header("Bringup", icon="build")
        data_table(columns=[...], rows=[...], row_key="name")
```

**The live-update rule: only the event loop touches a NiceGUI element.**
Litmus's live data (channel samples, session/instrument events) arrives on a
background gRPC Flight reader thread, not the UI loop. `litmus.ui.channel_data(id)`
already marshals each sample onto the loop for you — safe to mutate one
element directly in the callback:

```python
from litmus.ui import channel_data

reading = ui.label("No reading")
channel_data("dmm.voltage").subscribe(
    lambda sample: setattr(reading, "text", f"{sample.value:.4f}")
)
```

Once a page paints more than one element per sample (a table of channels, a
chart), stop mutating from the callback — write a plain holder instead and
let a single `ui.timer` render at a capped rate. Reference implementation:
`src/litmus/ui/components/channel_values.py`. Full recipe + the
subscribe-vs-raw-thread distinction: `references/live-ui-patterns.md`.

**Anti-pattern — do not teach or write this:**

```python
ui.label().bind_text_from(channel_data("oven.temp_c"), "latest", ...)  # broken
```

`channel_data(id)` returns a bare `nicegui.Event` — a pub/sub emitter with no
`.latest` attribute. This raises. Use `.subscribe(callback)`.

Bridge a station into the page once, at startup, then subscribe from pages:

```python
import litmus
from litmus.ui import bind_channel_store

station = litmus.connect("bench_01", mock=True)
station.start()
if station.channel_store:
    bind_channel_store(station.channel_store)   # once, at app startup
```

Depth: `litmus docs show how-to/execution/custom-operator-ui` — full
step-by-step including reading channel data outside a page context.

## 3. Standalone interactive bench control (no pytest)

`litmus.connect(station, mock=?)` returns a `StationConnection` for scripts,
notebooks, and the operator UI itself:

```python
import litmus

with litmus.connect("cell-7", mock=True) as station:
    dmm = station.instrument("dmm")                    # connects + reserves
    v = dmm.measure_voltage()
    station.configure("scope", "start_continuous", channel="CH1")  # log a UI-initiated action
    uri = station.observe("dmm.voltage", v, unit="V")   # -> channel:// URI
```

`station.instrument(role, reserve=True, timeout=0)` connects and locks by
default (`reserve=False` for connect-only); `station.reserve(role)` /
`.release_reservation(role)` manage the lock explicitly, and
`station.reservation(role)` is the RAII form. `station.configure(role, method,
**params)` logs a UI-driven action that isn't a driver call (e.g. changing
scope display mode) — it does not call the driver itself.

Live monitoring outside a page uses the module-level `litmus.channels`
verbs, not a `StationConnection` method — `channels.latest(name, cb)` (newest
sample, conflated), `channels.live(name, cb, max_hz=...)` (every sample,
batched), `channels.window(name, cb, dur=...)` (backfill N seconds then go
live). **These callbacks fire on a raw background thread — never touch a
NiceGUI element from one directly.** Outside a UI page (bare script,
notebook) that's fine as-is; inside a page, marshal first exactly as in §2.

Worked example, runnable end to end: `examples/interactive_station.py`
(station monitor + control: instrument cards, a scope waveform card, an
instrument-activity log, a session table). Run it, then run `pytest` in the
same directory in another terminal to watch its events and channel data
appear live.

## Best-practice defaults

- **Per-test pause → `litmus_prompts`/`prompt`; session-start ask →
  `required_inputs`.** Don't use one for the other's job.
- **`LITMUS_AUTO_CONFIRM=1` in every CI path** that touches a prompted test.
- **Only the event loop touches a NiceGUI element** — subscribe-and-mutate
  for one element, holder-and-`ui.timer` for many.
- **`channel_data(...).subscribe(cb)` is pre-marshaled; `litmus.channels.live/
  window/latest` are raw threads** — know which one you're in before you
  touch a widget.

## Deeper
`references/live-ui-patterns.md` — the chosen-pattern decision table and
anti-pattern catalog for building NiceGUI pages against live Litmus data.
Sibling skills: `litmus-tests` (verb choice, `context` fixture),
`litmus-capture` (channel/file store mechanics and readback),
`litmus-debug` (hung/timed-out prompt triage), `litmus-stations` (station
YAML and instrument roles `station.instrument()` resolves).
