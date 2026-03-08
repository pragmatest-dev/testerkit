"""Interactive station monitor + control with NiceGUI.

Canonical example of a Litmus station UI. Demonstrates:

- **Channel data** via ``ui_channel_event`` — PSU readback, DMM readings,
  scope waveforms all update live from any process (this UI, pytest, scripts).
- **Session events** via ``ui_subscribe`` — instrument activity log and
  session table stream cross-process events from the shared EventStore.
- **Instrument control** — connect/disconnect, apply settings, take readings.

Architecture:

    bind_channel_store(store)           # once at startup
    ui_channel_event("ch").subscribe()  # per component, auto-unsubscribe
    ui_subscribe(store, callback)       # for session/instrument events

Run from the demo/ directory::

    cd demo && uv run python interactive_station.py

Then in another terminal, run pytest to see events + channel data appear live::

    cd demo && uv run pytest tests/ -q
"""

from __future__ import annotations

import asyncio
import math
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

from nicegui import app, ui

# Ensure the parent repo is importable when running from demo/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import litmus
from litmus.connect import StationConnection
from litmus.data.channels.models import ChannelSample
from litmus.data.event_store import EventStore
from litmus.ui.shared.components import (
    InstrumentToggle,
    litmus_table,
    table_cell_slot,
    table_col,
)
from litmus.ui.shared.event_binding import (
    bind_channel_store,
    ui_channel_event,
    ui_subscribe,
)
from litmus.utils import local_time

# ---------------------------------------------------------------------------
# Station — one physical station, initialized once at app startup
# ---------------------------------------------------------------------------

_station: StationConnection | None = None


def _init_station() -> None:
    global _station
    _station = litmus.connect(
        "demo_station_001", results_dir=Path("results"), mock=True,
    )
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


def _event_store() -> EventStore:
    assert _station is not None and _station.event_store is not None
    return _station.event_store


# ---------------------------------------------------------------------------
# Mock helpers (demo only — not part of the canonical pattern)
# ---------------------------------------------------------------------------


def _noisy(nominal: float, noise: float = 0.02) -> float:
    return round(nominal + random.gauss(0, noise), 4)


def _mock_waveform(phase: float, n: int = 200, dt: float = 1e-5) -> list[float]:
    return [
        round(3.3 + 0.5 * math.sin(2 * math.pi * 1000 * (i * dt) + phase)
              + random.gauss(0, 0.01), 4)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


@ui.page("/")
def main_page() -> None:
    ui.add_css(Path(__file__).parent / "static" / "station.css")

    with ui.header().classes("bg-slate-800 text-white items-center px-6"):
        ui.label("Litmus Station Monitor").classes("text-lg font-semibold")

    with ui.column().classes("w-full max-w-7xl mx-auto p-6 gap-6"):
        with ui.row().classes("w-full gap-6"):
            _build_psu_card()
            _build_dmm_card()
        _build_scope_card()
        _build_instrument_activity()
        _build_session_log()


# ---------------------------------------------------------------------------
# Power Supply card
# ---------------------------------------------------------------------------


def _build_psu_card() -> None:
    latest: dict[str, float] = {}
    setpoints = {"voltage": 5.0, "current": 0.5, "output_on": False}

    with ui.card().classes("flex-1"):
        with ui.row().classes("items-center justify-between w-full mb-2"):
            ui.label("Power Supply").classes("text-lg font-semibold")
            toggle = InstrumentToggle(_station, "psu")

        readback = ui.label("No readback").classes(
            "text-sm font-mono text-slate-400 mb-2",
        )

        def _on_sample(sample: ChannelSample) -> None:
            try:
                latest[sample.channel_id] = float(sample.value)
            except (TypeError, ValueError):
                return
            v = latest.get("psu.voltage", 0)
            i = latest.get("psu.current", 0)
            readback.text = f"{v:.4f} V / {i:.4f} A"
            readback.classes(remove="text-slate-400", add="text-emerald-700")

        ui_channel_event("psu.voltage").subscribe(_on_sample)
        ui_channel_event("psu.current").subscribe(_on_sample)
        ui.separator()

        with ui.row().classes("items-end gap-4 mt-2"):
            v_in = ui.number("Voltage (V)", value=5.0, min=0, max=30, step=0.1).classes("w-28")
            i_in = ui.number("Current (A)", value=0.5, min=0, max=5, step=0.01).classes("w-28")

        with ui.row().classes("gap-2 mt-3"):
            def apply() -> None:
                if not toggle.ensure():
                    return
                psu = toggle.driver
                setpoints["voltage"] = v_in.value
                setpoints["current"] = i_in.value
                psu.set_voltage(v_in.value)
                psu.set_current(i_in.value)
                psu.set_mock_value("measure_voltage", v_in.value)
                psu.set_mock_value("measure_current", i_in.value * 0.5)
                ui.notify("Applied", type="positive")

            def toggle_output() -> None:
                if not toggle.ensure():
                    return
                psu = toggle.driver
                on = setpoints["output_on"]
                psu.disable_output() if on else psu.enable_output()
                setpoints["output_on"] = not on
                ui.notify("Output OFF" if on else "Output ON", type="positive")

            def read() -> None:
                if not toggle.ensure():
                    return
                psu = toggle.driver
                psu.set_mock_value("measure_voltage", _noisy(setpoints["voltage"]))
                psu.set_mock_value("measure_current", _noisy(setpoints["current"] * 0.5, 0.005))
                psu.measure_voltage()
                psu.measure_current()

            ui.button("Apply", on_click=apply).props("color=primary dense outline")
            ui.button("Output", on_click=toggle_output).props("color=amber dense")
            ui.button("Read", on_click=read).props("color=teal dense outline")


# ---------------------------------------------------------------------------
# DMM card
# ---------------------------------------------------------------------------

_DMM_CHANNELS = {"dmm.dc_voltage": "V", "dmm.dc_current": "mA", "dmm.resistance": "\u03a9"}


def _build_dmm_card() -> None:
    with ui.card().classes("flex-1"):
        with ui.row().classes("items-center justify-between w-full mb-2"):
            ui.label("Digital Multimeter").classes("text-lg font-semibold")
            toggle = InstrumentToggle(_station, "dmm")

        reading = ui.label("No reading").classes("text-xl font-mono text-slate-400 mb-2")

        for channel_id, unit in _DMM_CHANNELS.items():
            def _handler(sample: ChannelSample, u: str = unit) -> None:
                try:
                    val = float(sample.value)
                except (TypeError, ValueError):
                    return
                reading.text = f"{val:.4f} {u}"
                reading.classes(remove="text-slate-400", add="text-blue-700")
            ui_channel_event(channel_id).subscribe(_handler)

        ui.separator()

        with ui.row().classes("gap-2 mt-3"):
            def _measure(fn: str, nominal: float, noise: float):  # type: ignore[no-untyped-def]
                def _do() -> None:
                    if not toggle.ensure():
                        return
                    dmm = toggle.driver
                    dmm.set_mock_value(fn, _noisy(nominal, noise))
                    getattr(dmm, fn)()
                return _do

            for label, fn, nom, ns in [
                ("DC Voltage", "measure_dc_voltage", 3.3, 0.01),
                ("DC Current", "measure_dc_current", 1.2, 0.1),
                ("Resistance", "measure_resistance", 1000.0, 2.0),
            ]:
                ui.button(label, on_click=_measure(fn, nom, ns)).props(
                    "color=blue dense outline",
                )


# ---------------------------------------------------------------------------
# Scope card
# ---------------------------------------------------------------------------


def _build_scope_card() -> None:
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center justify-between w-full mb-2"):
            ui.label("Oscilloscope").classes("text-lg font-semibold")
            toggle = InstrumentToggle(_station, "scope")

        chart = ui.echart({
            "xAxis": {"type": "category", "name": "Sample"},
            "yAxis": {"type": "value", "name": "V", "min": "dataMin", "max": "dataMax"},
            "series": [{
                "type": "line", "data": [], "smooth": True,
                "lineStyle": {"width": 2, "color": "#6366f1"},
                "symbol": "none",
                "areaStyle": {"opacity": 0.08, "color": "#6366f1"},
            }],
            "grid": {"top": 30, "bottom": 30, "left": 50, "right": 20},
            "animation": False,
        }).classes("w-full h-48")

        status = ui.label("No waveform").classes("text-sm font-mono text-slate-400")
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
            chart.options["series"][0]["data"] = samples
            chart.options["xAxis"]["data"] = list(range(len(samples)))
            chart.update()
            vpp = (max(samples) - min(samples)) * 1000
            status.text = f"Acq #{acq_count[0]}: {len(samples)} pts, {vpp:.1f} mVpp, dt={dt:.1e}s"
            status.classes(remove="text-slate-400", add="text-indigo-700")

        ui_channel_event("scope.waveform").subscribe(_on_waveform)

        running = {"active": False, "task": None}

        async def _continuous() -> None:
            phase = 0.0
            while running["active"]:
                if not toggle.connected:
                    await asyncio.sleep(0.1)
                    continue
                scope = toggle.driver
                waveform = _mock_waveform(phase)
                phase += 0.3
                scope.set_mock_value("fetch_waveform", (waveform, 1e-5))
                scope.fetch_waveform("CH1")
                await asyncio.sleep(0.1)

        def _start() -> None:
            if running["active"] or not toggle.ensure():
                return
            running["active"] = True
            running["task"] = asyncio.ensure_future(_continuous())
            run_btn.props(remove="color=green", add="color=red")
            run_btn.text = "Stop"

        def _stop() -> None:
            running["active"] = False
            if running["task"]:
                running["task"].cancel()
                running["task"] = None
            run_btn.props(remove="color=red", add="color=green")
            run_btn.text = "Run"
            acq_count[0] = 0

        with ui.row().classes("gap-2 mt-2"):
            def _single() -> None:
                if not toggle.ensure():
                    return
                toggle.driver.fetch_waveform("CH1")

            ui.button("Single", on_click=_single).props("color=indigo dense outline")
            run_btn = ui.button(
                "Run", on_click=lambda: _stop() if running["active"] else _start(),
            ).props("color=green dense")


# ---------------------------------------------------------------------------
# Instrument Activity — EventStore subscription
# ---------------------------------------------------------------------------

_INSTRUMENT_TYPES = {"instrument.read", "instrument.set", "instrument.configure"}


def _build_instrument_activity() -> None:
    store = _event_store()
    label_cache: dict[str, str] = {}

    def _session_label(sid: str) -> str:
        if sid not in label_cache:
            short = sid[:4]
            for s in store.sessions():
                if str(s.get("session_id", "")) == sid:
                    client = s.get("client") or s.get("session_type") or "session"
                    label_cache[sid] = f"{client} #{short}"
                    break
            else:
                label_cache[sid] = f"#{short}"
        return label_cache[sid]

    def _format_row(evt: dict) -> dict | None:
        et = evt.get("event_type", "")
        if et not in _INSTRUMENT_TYPES:
            return None
        ts = evt.get("received_at") or evt.get("occurred_at") or ""
        sid = str(evt.get("session_id", ""))
        if et == "instrument.read":
            detail = f"{evt.get('channel_id', '')} = {evt.get('value', '')}"
        elif et == "instrument.set":
            ch = evt.get("channel_id", "")
            detail = f"{ch}.{evt.get('attribute', '')} = {evt.get('value', '')}"
        elif et == "instrument.configure":
            detail = f"{evt.get('instrument_role', '')}.{evt.get('method', '')}()"
        else:
            detail = ""
        return {
            "time": local_time(str(ts)) if ts else "",
            "event": et,
            "source": _session_label(sid),
            "detail": detail,
        }

    with ui.card().classes("w-full"):
        ui.label("Instrument Activity").classes("text-lg font-semibold mb-2")

        table = litmus_table([
            table_col("time", "Time", width="80px"),
            table_col("event", "Event", width="100px"),
            table_col("source", "Source", width="180px"),
            table_col("detail", "Detail"),
        ])
        table_cell_slot(table, "time", "cell-muted")
        table.add_slot("body-cell-event", """
            <q-td :props="props">
                <span :class="'event-badge ' + props.value.split('.')[1]">
                    {{ props.value.split('.')[1] }}
                </span>
            </q-td>
        """)
        table_cell_slot(table, "source", "cell-dim")

        row_idx = [0]

        def _on_activity(evt: dict) -> None:
            row = _format_row(evt)
            if row is None:
                return
            row["idx"] = row_idx[0]
            row_idx[0] += 1
            table.add_rows([row])

        ui_subscribe(store, _on_activity, since=datetime.now(UTC))


# ---------------------------------------------------------------------------
# Session Log — EventStore subscription
# ---------------------------------------------------------------------------


def _build_session_log() -> None:
    store = _event_store()

    def _rows() -> list[dict]:
        ended = {str(e.get("session_id", "")) for e in store.events(event_type="session.ended")}
        rows = []
        for sess in store.sessions()[-10:]:
            sid = str(sess.get("session_id", ""))
            if not sid:
                continue
            ts = sess.get("occurred_at") or sess.get("received_at") or ""
            rows.append({
                "started": local_time(str(ts)) if ts else "",
                "status": "ended" if sid in ended else "active",
                "client": str(sess.get("client") or sess.get("session_type") or ""),
                "pid": str(sess.get("pid") or ""),
                "session": sid[:4],
            })
        return rows

    with ui.card().classes("w-full"):
        ui.label("Station Sessions").classes("text-lg font-semibold mb-1")

        table = litmus_table(
            [
                table_col("started", "Started", width="80px"),
                table_col("status", "", width="24px", align="center"),
                table_col("client", "Client", width="140px"),
                table_col("session", "ID", width="60px"),
                table_col("pid", "PID", width="60px"),
            ],
            rows=_rows(),
            row_key="session",
            per_page=3,
        )
        table_cell_slot(table, "started", "cell-muted")
        table.add_slot("body-cell-status", """
            <q-td :props="props"><span :class="'status-dot ' + props.value"></span></q-td>
        """)
        table.add_slot("body-cell-client", """
            <q-td :props="props">
                <span :class="'session-badge ' + props.value">{{ props.value }}</span>
            </q-td>
        """)
        table_cell_slot(table, "session", "cell-dim font-mono")
        table_cell_slot(table, "pid", "cell-muted")

        def _on_session(evt: dict) -> None:
            if evt.get("event_type", "") in ("session.started", "session.ended"):
                table.update_rows(_rows())

        ui_subscribe(store, _on_session, since=datetime.now(UTC))


ui.run(title="Litmus Station Monitor", port=8080, reload=True)
