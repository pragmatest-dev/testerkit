"""Live test progress page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout


@ui.page("/live/{run_id}")
async def live_page(run_id: str):
    """Live test progress page."""
    create_layout(f"Test Run: {run_id}")

    from litmus.execution.runner import get_runner
    from litmus.ui.shared.dialogs import create_dialog_container

    runner = get_runner()

    # Dialog container for operator prompts during test
    create_dialog_container(run_id)

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.card().classes("w-full"):
            with ui.card_section():
                with ui.row().classes("items-center gap-4"):
                    ui.label("Status:").classes("font-semibold")
                    status_label = ui.label("Starting...").classes(
                        "px-3 py-1 rounded bg-blue-100 text-blue-800 text-sm font-medium"
                    )
                with ui.row().classes("items-center gap-4 mt-2"):
                    ui.label("Run ID:").classes("text-sm text-slate-500")
                    ui.label(run_id).classes("text-sm font-mono text-slate-600")

            with ui.card_section():
                progress = ui.linear_progress(value=0).classes("w-full")
                step_label = ui.label("").classes("text-sm text-slate-600 mt-2")

        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Output").classes("font-semibold")
            log = ui.log(max_lines=100).classes(
                "w-full h-80 bg-slate-900 text-slate-100 font-mono text-sm"
            )

        results_link = ui.link("View Full Results →", f"/results/{run_id}").classes("hidden")

        async def update_progress():
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

        ui.timer(0.1, update_progress, once=True)
