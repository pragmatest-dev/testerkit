"""Live test progress page with streaming measurements."""

from pathlib import Path

from nicegui import ui

from litmus.ui.shared.layout import create_layout


def _outcome_badge(outcome: str | None) -> str:
    """Return Tailwind classes for outcome badge."""
    if outcome == "pass":
        return "bg-emerald-100 text-emerald-800"
    elif outcome == "fail":
        return "bg-red-100 text-red-800"
    elif outcome == "error":
        return "bg-amber-100 text-amber-800"
    return "bg-slate-100 text-slate-600"


@ui.page("/live/{run_id}")
async def live_page(run_id: str):
    """Live test progress page with streaming measurements."""
    create_layout(f"Test Run: {run_id}")

    from litmus.execution.runner import get_runner
    from litmus.ui.shared.dialogs import create_dialog_container

    runner = get_runner()

    # Dialog container for operator prompts during test
    create_dialog_container(run_id)

    # Track measurements we've already displayed
    displayed_measurements = []
    run_complete = False

    with ui.column().classes("w-full p-6 gap-6"):
        # Status card
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

        # Live measurements card
        with ui.card().classes("w-full"):
            with ui.card_section():
                with ui.row().classes("items-center justify-between"):
                    ui.label("Live Measurements").classes("font-semibold")
                    measurement_count = ui.label("0 measurements").classes("text-sm text-slate-500")

            measurements_container = ui.column().classes("w-full max-h-64 overflow-y-auto")

        # Output log card
        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Output").classes("font-semibold")
            log = ui.log(max_lines=100).classes(
                "w-full h-48 bg-slate-900 text-slate-100 font-mono text-sm"
            )

        results_link = ui.link("View Full Results →", f"/results/{run_id}").classes("hidden")

        def poll_journal():
            """Poll journal file for new measurements."""
            nonlocal displayed_measurements, run_complete

            if run_complete:
                return

            # Find journal directory for this run
            from litmus.data.backends.parquet import ParquetBackend

            # Use runner's results directory
            backend = ParquetBackend(results_dir=runner.results_dir)
            journals = backend.list_journals()

            # Find journal matching run_id (exact match or prefix match for partial IDs)
            journal_dir = None
            for j in journals:
                j_run_id = j.get("run_id", "")
                if j_run_id == run_id or j_run_id.startswith(run_id):
                    journal_dir = Path(j["journal_dir"])
                    break

            if journal_dir is None:
                return

            # Read journal
            measurements = backend.get_journal_measurements(journal_dir)

            # Display new measurements
            new_count = len(measurements) - len(displayed_measurements)
            if new_count > 0:
                for m in measurements[len(displayed_measurements) :]:
                    with measurements_container:
                        row_cls = "w-full items-center justify-between px-3 py-2"
                        row_cls += " border-b border-slate-100"
                        with ui.row().classes(row_cls):
                            with ui.column().classes("gap-0"):
                                ui.label(m.get("measurement_name", "")).classes(
                                    "font-medium text-sm"
                                )
                                step = m.get("step_name", "")
                                ts = m.get("measurement_timestamp", "")[:19]
                                ui.label(f"{step} • {ts}").classes("text-xs text-slate-400")
                            with ui.row().classes("items-center gap-2"):
                                value = m.get("value")
                                units = m.get("units") or ""
                                ui.label(
                                    f"{value:.4g} {units}" if value is not None else "—"
                                ).classes("font-mono text-sm")
                                outcome = m.get("outcome")
                                badge_cls = _outcome_badge(outcome)
                                ui.label(outcome or "—").classes(
                                    f"px-2 py-0.5 rounded text-xs font-medium {badge_cls}"
                                )

                displayed_measurements = measurements
                measurement_count.set_text(f"{len(measurements)} measurements")

        # Poll journal every 500ms while run is in progress
        journal_timer = ui.timer(0.5, poll_journal)

        async def update_progress():
            nonlocal run_complete

            async for event in runner.stream(run_id):
                if event["type"] == "output":
                    log.push(event["data"])
                elif event["type"] == "progress":
                    progress.set_value(event["progress_pct"] / 100)
                    step_label.set_text(event.get("current_step") or "")
                    status_label.set_text(event["status"].upper())
                elif event["type"] == "complete":
                    run_complete = True
                    journal_timer.deactivate()

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

                    # Final poll to get any remaining measurements
                    poll_journal()
                    break

        ui.timer(0.1, update_progress, once=True)
