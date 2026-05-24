"""Live test progress page with streaming event log."""

import logging

from nicegui import ui

from litmus.api.runner import get_runner
from litmus.data.event_store import EventStore
from litmus.ui.components.channel_values import create_channel_values_panel
from litmus.ui.components.event_timeline import create_event_timeline
from litmus.ui.shared.dialogs import create_dialog_container
from litmus.ui.shared.layout import create_layout

logger = logging.getLogger(__name__)


@ui.page("/live/{run_id}")
async def live_page(run_id: str):
    """Live test progress page with streaming event log."""
    create_layout(f"Test Run: {run_id}")

    runner = get_runner()

    # Dialog container for operator prompts during test
    create_dialog_container(run_id)

    with ui.column().classes("w-full p-6 gap-6"):
        # Status card
        with ui.card().classes("w-full"):
            with ui.card_section():
                with ui.row().classes("items-center gap-4"):
                    ui.label("Status:").classes("font-semibold")
                    status_label = ui.label("Starting...").classes(
                        "px-3 py-1 rounded bg-blue-100 text-blue-800 text-sm font-medium"
                    )
                # Run ID is kept on the page so the URL is copyable /
                # bookmarkable, but rendered small and muted — operators
                # identify runs by DUT serial + start time, not UUID.
                with ui.row().classes("items-center gap-2 mt-2"):
                    ui.label("Run ID:").classes("text-xs text-slate-400")
                    ui.label(run_id).classes("text-xs font-mono text-slate-400 select-all")

            with ui.card_section():
                progress = ui.linear_progress(value=0).classes("w-full")
                step_label = ui.label("").classes("text-sm text-slate-600 mt-2")

        # Tabbed content — EventStore provides push-based subscriptions
        event_store = EventStore(_data_dir=runner.data_dir)

        with ui.tabs().classes("w-full") as tabs:
            events_tab = ui.tab("Events")
            channels_tab = ui.tab("Channels")
            output_tab = ui.tab("Output")

        with ui.tab_panels(tabs, value=events_tab).classes("w-full"):
            with ui.tab_panel(events_tab):
                _timeline_container, unsub_timeline = create_event_timeline(event_store)

            with ui.tab_panel(channels_tab):
                _channels_container, unsub_channels = create_channel_values_panel(event_store)

            with ui.tab_panel(output_tab):
                log = ui.log(max_lines=100).classes(
                    "w-full h-48 bg-slate-900 text-slate-100 font-mono text-sm"
                )

        results_link = ui.link("View Full Results →", f"/results/{run_id}").classes("hidden")

        async def update_progress():
            try:
                async for event in runner.stream(run_id):
                    if event["type"] == "output":
                        log.push(event["data"])
                    elif event["type"] == "progress":
                        progress.set_value(event["progress_pct"] / 100)
                        step_label.set_text(event.get("current_step") or "")
                        status_label.set_text(event["status"].upper())
                    elif event["type"] == "complete":
                        progress.set_value(1.0)
                        if event["returncode"] == 0:
                            status_label.set_text("PASSED")
                            status_label.classes(remove="bg-blue-100 text-blue-800")
                            status_label.classes(add="bg-emerald-100 text-emerald-800")
                        else:
                            status_label.set_text("FAILED")
                            status_label.classes(remove="bg-blue-100 text-blue-800")
                            status_label.classes(add="bg-red-100 text-red-800")
                        results_link.classes(remove="hidden")
                        break
            except (OSError, RuntimeError, ValueError) as exc:
                logger.exception("Live page stream error for run %s", run_id)
                status_label.set_text("ERROR")
                status_label.classes(remove="bg-blue-100 text-blue-800")
                status_label.classes(add="bg-red-100 text-red-800")
                ui.notify(
                    f"Stream error ({type(exc).__name__}): {exc}",
                    type="negative",
                    multi_line=True,
                )
            finally:
                unsub_timeline()
                unsub_channels()
                event_store.close()

        ui.timer(0.1, update_progress, once=True)
