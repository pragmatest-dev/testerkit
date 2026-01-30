"""Launch test page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_sequences, discover_stations, discover_tests


@ui.page("/launch")
def launch_page(station: str = "", sequence: str = ""):
    """Test launch page.

    Args:
        station: Pre-fill station ID from query param
        sequence: Pre-fill sequence ID from query param
    """
    create_layout("Launch Test")

    stations = discover_stations()
    tests = discover_tests()
    sequences = discover_sequences()

    # Form state - use dict for NiceGUI binding
    # Pre-fill from query params if provided
    form = {
        "dut_serial": "",
        "station_id": station,
        "sequence_id": sequence,
        "test_path": "",
        "operator": "",
    }

    async def submit_launch():
        if not form["dut_serial"] or not form["station_id"]:
            ui.notify("Please fill in required fields", type="warning")
            return
        if not form["sequence_id"] and not form["test_path"]:
            ui.notify("Select a test sequence or test suite", type="warning")
            return

        from litmus.api.models import LaunchRequest
        from litmus.execution.runner import get_runner

        request = LaunchRequest(
            dut_serial=form["dut_serial"],
            station_id=form["station_id"],
            sequence_id=form["sequence_id"] or None,
            test_path=form["test_path"] or "tests",
            operator=form["operator"] or None,
        )
        runner = get_runner()
        run_id = await runner.start(request)
        ui.navigate.to(f"/live/{run_id}")

    with ui.column().classes("w-full max-w-xl p-6 gap-6"):
        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Test Configuration").classes("text-lg font-semibold")

            with ui.card_section().classes("flex flex-col gap-4"):
                _labeled_input(form, "dut_serial", "DUT Serial Number", "e.g., DPB001-0001")

                with ui.column().classes("gap-1"):
                    ui.label("Station").classes("text-sm font-medium text-slate-700")
                    ui.select(
                        options={s["id"]: f"{s['name']} ({s['id']})" for s in stations},
                    ).bind_value(form, "station_id").classes("w-full").props("outlined dense")

                # Test sequence selection (primary method)
                if sequences:
                    ui.separator().classes("my-2")
                    with ui.column().classes("gap-1"):
                        ui.label("Test Sequence").classes("text-sm font-medium text-slate-700")
                        ui.select(
                            options={
                                s["id"]: f"{s['name']} ({s['test_phase']})" for s in sequences
                            },
                        ).bind_value(form, "sequence_id").classes("w-full").props(
                            "outlined dense clearable"
                        )

                    with ui.expansion("Advanced: Run by test path", icon="code").classes(
                        "w-full text-slate-500"
                    ):
                        ui.label(
                            "Run tests by pytest discovery instead of a defined sequence."
                        ).classes("text-xs text-slate-400 mb-2")
                        with ui.column().classes("gap-1"):
                            ui.label("Test Path").classes("text-sm font-medium text-slate-700")
                            ui.select(
                                options={t["path"]: f"{t['name']} ({t['path']})" for t in tests},
                            ).bind_value(form, "test_path").classes("w-full").props(
                                "outlined dense clearable"
                            )
                else:
                    # No sequences defined, show test path as primary
                    with ui.column().classes("gap-1"):
                        ui.label("Test Path").classes("text-sm font-medium text-slate-700")
                        ui.select(
                            options={t["path"]: f"{t['name']} ({t['path']})" for t in tests},
                        ).bind_value(form, "test_path").classes("w-full").props(
                            "outlined dense clearable"
                        )

                _labeled_input(form, "operator", "Operator (optional)", "Your name")

            with ui.card_actions().classes("justify-end"):
                ui.button("Start Test", icon="play_arrow", on_click=submit_launch).props(
                    "color=primary"
                )


def _labeled_input(form: dict, key: str, label: str, placeholder: str):
    """Create a labeled input field with binding."""
    with ui.column().classes("gap-1"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        ui.input(placeholder=placeholder).bind_value(form, key).classes("w-full").props(
            "outlined dense"
        )
