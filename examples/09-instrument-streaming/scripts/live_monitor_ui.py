"""Interactive live monitor — the consumer verbs, with controls you can poke.

Self-contained: a background producer writes two channels of different cadence,
and a NiceGUI page consumes them via the verbs so the policy difference is
visible side by side:

- ``chamber.temp`` (~0.5 Hz, slow)  -> ``channels.latest`` -> a GAUGE (newest
  value, conflated — you never see a backlog).
- ``scope.ch1`` (50 Hz, fast)        -> ``channels.window(dur=, max_hz=)`` -> a
  rolling CHART of the last ``dur`` seconds that continues live, with ``dur`` and
  ``max_hz`` sliders — drag either and the chart re-fills from history instantly,
  never blank.

Run it and open the printed URL::

    cd examples/09-instrument-streaming
    uv run python scripts/live_monitor_ui.py

This is a demo of the SDK, not the operator UI — the operator UI consumes the
same verbs internally.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from uuid import uuid4

from nicegui import ui

import testerkit.channels as channels
from testerkit.data.channels.store import ChannelStore
from testerkit.data.data_dir import resolve_data_dir

_PRODUCE_HZ = 50
_STOP = threading.Event()
_STATE: dict = {"temp": None, "trace": deque(maxlen=200), "dur": 5, "max_hz": 10}
_SUBS: dict = {}


def _producer() -> None:
    """Write a slow temp channel and a fast scope channel into the store."""
    store = ChannelStore(resolve_data_dir(), uuid4(), serve=True)
    store.open()
    i = 0
    try:
        while not _STOP.is_set():
            t = i / 50.0
            store.write("scope.ch1", 3.3 + 0.2 * math.sin(2 * math.pi * 2 * t))
            if i % 100 == 0:  # ~0.5 Hz
                store.write("chamber.temp", 25.0 + 2.0 * math.sin(t / 30.0))
            i += 1
            time.sleep(1 / 50.0)
    finally:
        store.close()


def _on_temp(sample) -> None:  # noqa: ANN001 — ChannelSample
    _STATE["temp"] = sample.value


def _on_trace(batch) -> None:  # noqa: ANN001 — pyarrow.RecordBatch
    for v in batch.column("value").to_pylist():
        _STATE["trace"].append(json.loads(v))


def _start_window(dur: int, max_hz: int) -> None:
    """(Re)subscribe the scope chart as a rolling window of the last ``dur`` s.

    window() backfills the last ``dur`` seconds immediately, then continues
    live — so the chart is never blank, even right after you drag a slider.
    The buffer is re-sized to hold the chosen depth (50 Hz producer + headroom).
    """
    _STATE["dur"], _STATE["max_hz"] = dur, max_hz
    if "trace" in _SUBS:
        _SUBS["trace"]()
    _STATE["trace"] = deque(maxlen=max(200, int(dur * _PRODUCE_HZ * 1.5)))
    _SUBS["trace"] = channels.window("scope.ch1", _on_trace, dur=dur, max_hz=max_hz)


# Start producing + subscribing once, at import.
threading.Thread(target=_producer, daemon=True).start()
time.sleep(0.6)  # let the daemon spawn
_SUBS["temp"] = channels.latest("chamber.temp", _on_temp)
_start_window(5, 10)


@ui.page("/")
def page() -> None:
    ui.label("Live Monitor — channel consumer verbs").classes("text-2xl font-bold")
    with ui.row().classes("w-full items-stretch gap-4"):
        with ui.card().classes("w-64"):
            ui.label("chamber.temp").classes("text-sm text-gray-500")
            ui.label("channels.latest — gauge").classes("text-xs text-gray-400")
            temp = ui.label("—").classes("text-5xl font-mono")
            ui.label("°C").classes("text-gray-400")
        with ui.card().classes("flex-1"):
            ui.label("scope.ch1").classes("text-sm text-gray-500")
            ui.label("channels.window(dur=…, max_hz=…) — last N s, then live").classes(
                "text-xs text-gray-400"
            )
            chart = ui.echart(
                {
                    "animation": False,
                    "xAxis": {"type": "category", "show": False},
                    "yAxis": {"type": "value", "scale": True},
                    "series": [{"type": "line", "data": [], "showSymbol": False}],
                    "grid": {"left": 50, "right": 20, "top": 20, "bottom": 20},
                }
            ).classes("w-full h-64")

    with ui.row().classes("items-center gap-4"):
        ui.label("window dur (s):")
        dur_val = ui.label("5").classes("font-mono w-8")

        def on_dur(e) -> None:  # noqa: ANN001 — nicegui ValueChangeEventArguments
            _start_window(int(e.value), _STATE["max_hz"])
            dur_val.set_text(str(int(e.value)))

        ui.slider(min=1, max=15, value=5, on_change=on_dur)
        ui.label("← deeper history; chart re-fills from the log instantly, never blank").classes(
            "text-xs text-gray-400"
        )

    with ui.row().classes("items-center gap-4"):
        ui.label("live max_hz:")
        hz_val = ui.label("10").classes("font-mono w-8")

        def on_hz(e) -> None:  # noqa: ANN001 — nicegui ValueChangeEventArguments
            _start_window(_STATE["dur"], int(e.value))
            hz_val.set_text(str(int(e.value)))

        ui.slider(min=1, max=50, value=10, on_change=on_hz)
        ui.label("← drag down: chart updates less often (coalesced), never loses points").classes(
            "text-xs text-gray-400"
        )

    def redraw() -> None:
        temp.text = f"{_STATE['temp']:.3f}" if _STATE["temp"] is not None else "—"
        chart.options["series"][0]["data"] = list(_STATE["trace"])
        chart.update()

    ui.timer(0.1, redraw)


if __name__ in {"__main__", "__mp_main__"}:
    print("Live monitor on http://localhost:8080  (Ctrl-C to stop)")
    ui.run(port=8080, reload=False, show=False, title="TesterKit — Live Monitor")
