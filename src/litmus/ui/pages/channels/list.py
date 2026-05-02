"""Channels browser — list every registered channel with its descriptor."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import list_channels


@ui.page("/channels")
def channels_page() -> None:
    """List all registered channels with click-through to detail."""
    create_layout("Channels")

    payload = list_channels()
    channels = payload.get("channels") or {}

    with ui.column().classes("w-full p-6 gap-4"):
        ui.label("Channels").classes("text-2xl font-semibold")
        ui.label(
            "Channels are streaming numeric / array signals captured during "
            "test runs — scope traces, PSU readback, sensor logs. The "
            "registry below tracks every channel ever written."
        ).classes("text-sm text-slate-500")

        if not channels:
            with ui.card().classes("w-full"):
                with ui.card_section():
                    ui.label("No channels recorded yet.").classes("text-slate-500 italic")
                    ui.label(
                        "Channels appear once a test writes to ChannelStore "
                        "(e.g. ``context.observe('scope', ndarray)`` or "
                        "instrument observers)."
                    ).classes("text-xs text-slate-400")
            return

        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label(f"{len(channels)} channel(s)").classes("text-sm text-slate-600")

            with ui.card_section().classes("p-0"):
                columns = [
                    {
                        "name": "channel_id",
                        "label": "Channel ID",
                        "field": "channel_id",
                        "align": "left",
                    },
                    {
                        "name": "data_type",
                        "label": "Type",
                        "field": "data_type",
                        "align": "left",
                    },
                    {
                        "name": "instrument_role",
                        "label": "Instrument",
                        "field": "instrument_role",
                        "align": "left",
                    },
                    {
                        "name": "units",
                        "label": "Units",
                        "field": "units",
                        "align": "left",
                    },
                    {
                        "name": "first_seen",
                        "label": "First seen",
                        "field": "first_seen",
                        "align": "left",
                    },
                ]
                rows = [
                    _row_for_channel(cid, descriptor)
                    for cid, descriptor in sorted(channels.items())
                ]
                table = ui.table(columns=columns, rows=rows, row_key="channel_id").classes("w-full")
                table.on(
                    "row-click",
                    lambda e: ui.navigate.to(f"/channels/{e.args[1]['channel_id']}"),
                )


def _row_for_channel(channel_id: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "data_type": descriptor.get("data_type") or "",
        "instrument_role": descriptor.get("instrument_role") or "",
        "units": descriptor.get("units") or "",
        "first_seen": _format_timestamp(descriptor.get("first_seen")),
    }


def _format_timestamp(ts: Any) -> str:
    if not ts:
        return ""
    s = str(ts)
    if "T" in s:
        date, _, rest = s.partition("T")
        time = rest.split(".", 1)[0].split("+", 1)[0].split("-", 1)[0]
        return f"{date} {time}"
    return s
