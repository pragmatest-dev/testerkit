"""Interactive station monitor + control with NiceGUI.

Canonical example of a Litmus station UI. Demonstrates:

- **Channel data** via ``ui_channel_data`` — PSU readback, DMM readings,
  scope waveforms all update live from any process (this UI, pytest, scripts).
- **Session events** via ``ui_subscribe`` — instrument activity log and
  session table stream cross-process events from the shared EventStore.
- **Instrument control** — connect/disconnect, apply settings, take readings.

Run from the demo/ directory::

    cd demo && uv run python interactive_station.py

Then in another terminal, run pytest to see events + channel data appear live::

    cd demo && uv run pytest -q
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from nicegui import app, ui

# Ensure the parent repo is importable when running from demo/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import litmus
from litmus.connect import StationConnection
from litmus.data.channels.models import ChannelSample
from litmus.data.events import InstrumentConfigure
from litmus.instruments.proxy import _READ_PREFIXES, _SET_PREFIXES
from litmus.ui.components import create_instrument_activity, create_session_table
from litmus.ui.shared.components import InstrumentToggle
from litmus.ui.shared.event_binding import (
    bind_channel_store,
    ui_channel_data,
)

# ---------------------------------------------------------------------------
# Station — one physical station, initialized once at app startup
# ---------------------------------------------------------------------------

_station: StationConnection | None = None


def _init_station() -> None:
    global _station
    _station = litmus.connect("demo_station_001", mock=True)
    _station.start()
    if _station.channel_store:
        bind_channel_store(_station.channel_store)


def _shutdown_station() -> None:
    global _station
    if _station is not None:
        _station.stop()
        _station = None


app.on_startup(_init_station)
app.on_shutdown(_shutdown_station)


def _emit_configure(role: str, method: str, **params: object) -> None:
    """Emit an instrument.configure event for a UI-level operation."""
    assert _station is not None and _station.event_log is not None
    _station.event_log.emit(InstrumentConfigure(
        session_id=_station.session_id,
        instrument_role=role,
        method=method,
        parameters={k: v for k, v in params.items() if v is not None},
    ))


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def _strip(name: str, prefixes: tuple[str, ...]) -> str:
    for p in prefixes:
        if name.startswith(p):
            return name[len(p):]
    return name


@ui.page("/")
def main_page() -> None:
    assert _station is not None
    ui.add_css(Path(__file__).parent / "static" / "station.css")
    config = _station.config

    with ui.header().classes("bg-slate-800 text-white items-center px-6"):
        ui.label(config.name).classes("text-lg font-semibold")

    with ui.column().classes("w-full max-w-7xl mx-auto p-6 gap-6"):
        # Build instrument cards from station config
        inst_configs = config.instruments or {}
        readback = []
        scopes = []
        for role, inst_config in inst_configs.items():
            if inst_config.type == "scope":
                scopes.append((role, inst_config))
            elif inst_config.type in ("psu", "dmm", "eload"):
                readback.append((role, inst_config))

        for i in range(0, len(readback), 2):
            with ui.row().classes("w-full gap-6"):
                for role, ic in readback[i:i + 2]:
                    _build_readback_card(_station, role, ic)

        for role, ic in scopes:
            _build_scope_card(_station, role, ic)

        store = _station.event_store
        assert store is not None
        with ui.card().classes("w-full"):
            ui.label("Instrument Activity").classes(
                "text-lg font-semibold mb-2",
            )
            create_instrument_activity(store)

        with ui.card().classes("w-full"):
            ui.label("Station Sessions").classes(
                "text-lg font-semibold mb-1",
            )
            create_session_table(store, height="130px")


# ---------------------------------------------------------------------------
# Readback instrument card (PSU, DMM, ELoad)
# ---------------------------------------------------------------------------


def _build_readback_card(
    station: StationConnection, role: str, inst_config: object,
) -> None:
    desc = getattr(inst_config, "description", "") or role.upper()
    mock_cfg = getattr(inst_config, "mock_config", {}) or {}

    read_methods = [k for k in mock_cfg if k.startswith(_READ_PREFIXES)]
    set_methods = [k for k in mock_cfg if k.startswith(_SET_PREFIXES)]

    with ui.card().classes("flex-1"):
        with ui.row().classes("items-center justify-between w-full mb-2"):
            ui.label(desc).classes("text-lg font-semibold")
            toggle = InstrumentToggle(station, role)

        reading = ui.label("No reading").classes(
            "text-xl font-mono text-slate-400 mb-2",
        )

        def _on_sample(sample: ChannelSample) -> None:
            try:
                val = float(sample.value)
            except (TypeError, ValueError):
                return
            stem = _strip(sample.channel_id, (f"{role}.",))
            reading.text = f"{stem}: {val:.4f}"
            reading.classes(
                remove="text-slate-400", add="text-emerald-700",
            )

        for method in read_methods:
            channel_id = f"{role}.{_strip(method, _READ_PREFIXES)}"
            ui_channel_data(channel_id).subscribe(_on_sample)

        ui.separator()

        if set_methods:
            with ui.row().classes("items-end gap-4 mt-2"):
                set_inputs: dict[str, ui.number] = {}
                for method in set_methods:
                    stem = _strip(method, _SET_PREFIXES)
                    default = mock_cfg.get(method, 0)
                    if isinstance(default, (int, float)):
                        label = stem.replace("_", " ").title()
                        inp = ui.number(label, value=default)
                        inp.classes("w-28")
                        set_inputs[method] = inp

            if set_inputs:
                def _apply() -> None:
                    if not toggle.ensure():
                        return
                    for m, inp in set_inputs.items():
                        getattr(toggle.driver, m)(inp.value)
                    ui.notify("Applied", type="positive")

                ui.button("Apply", on_click=_apply).props(
                    "color=primary dense outline",
                ).classes("mt-2")

        if read_methods:
            with ui.row().classes("gap-2 mt-3"):
                for method in read_methods:
                    label = _strip(method, _READ_PREFIXES).replace(
                        "_", " ",
                    ).title()

                    def _read(fn: str = method) -> None:
                        if not toggle.ensure():
                            return
                        getattr(toggle.driver, fn)()

                    ui.button(label, on_click=_read).props(
                        "color=teal dense outline",
                    )


# ---------------------------------------------------------------------------
# Scope card
# ---------------------------------------------------------------------------


def _build_scope_card(
    station: StationConnection, role: str, inst_config: object,
) -> None:
    desc = getattr(inst_config, "description", "") or role.upper()

    with ui.card().classes("w-full"):
        with ui.row().classes("items-center justify-between w-full mb-2"):
            ui.label(desc).classes("text-lg font-semibold")
            toggle = InstrumentToggle(station, role)

        chart = ui.echart({
            "xAxis": {
                "type": "value", "name": "Sample", "boundaryGap": False,
            },
            "yAxis": {
                "type": "value", "name": "V",
                "min": "dataMin", "max": "dataMax",
            },
            "series": [{
                "type": "line", "data": [], "smooth": True,
                "lineStyle": {"width": 2, "color": "#6366f1"},
                "symbol": "none",
                "areaStyle": {"opacity": 0.08, "color": "#6366f1"},
            }],
            "grid": {"top": 30, "bottom": 30, "left": 50, "right": 20},
            "animation": False,
        }).classes("w-full h-48")

        status = ui.label("No waveform").classes(
            "text-sm font-mono text-slate-400",
        )
        acq_count = [0]

        def _on_waveform(sample: ChannelSample) -> None:
            val = sample.value
            if isinstance(val, dict):
                samples = val.get("samples", [])
                dt = val.get("sample_interval", 0)
            elif isinstance(val, list):
                samples = val
                dt = sample.sample_interval or 0
            else:
                return
            if not samples:
                return
            acq_count[0] += 1
            chart.options["series"][0]["data"] = [
                [i, v] for i, v in enumerate(samples)
            ]
            chart.update()
            vpp = (max(samples) - min(samples)) * 1000
            status.text = (
                f"Acq #{acq_count[0]}: {len(samples)} pts, "
                f"{vpp:.1f} mVpp, dt={dt:.1e}s"
            )
            status.classes(
                remove="text-slate-400", add="text-indigo-700",
            )

        ui_channel_data(f"{role}.waveform").subscribe(_on_waveform)

        running = {"active": False, "task": None}

        async def _continuous() -> None:
            loop = asyncio.get_event_loop()
            while running["active"]:
                if not toggle.connected:
                    await asyncio.sleep(0.1)
                    continue
                await loop.run_in_executor(
                    None, toggle.driver.fetch_waveform, "CH1",
                )
                await asyncio.sleep(0.05)

        def _start() -> None:
            if running["active"] or not toggle.ensure():
                return
            _emit_configure(role, "start_continuous", channel="CH1")
            running["active"] = True
            running["task"] = asyncio.ensure_future(_continuous())
            run_btn.props(remove="color=green", add="color=red")
            run_btn.text = "Stop"

        def _stop() -> None:
            running["active"] = False
            if running["task"]:
                running["task"].cancel()
                running["task"] = None
            _emit_configure(role, "stop_continuous")
            run_btn.props(remove="color=red", add="color=green")
            run_btn.text = "Run"
            acq_count[0] = 0

        with ui.row().classes("gap-2 mt-2"):
            def _single() -> None:
                if not toggle.ensure():
                    return
                _emit_configure(role, "single_acquisition", channel="CH1")
                toggle.driver.fetch_waveform("CH1")

            ui.button("Single", on_click=_single).props(
                "color=indigo dense outline",
            )
            run_btn = ui.button(
                "Run",
                on_click=lambda: (
                    _stop() if running["active"] else _start()
                ),
            ).props("color=green dense")


ui.run(title="Litmus Station Monitor", port=8080, reload=True)
