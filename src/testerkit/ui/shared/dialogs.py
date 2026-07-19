"""Dialog components for operator interactions."""

from nicegui import ui

from testerkit.api.dialogs import DialogResponse, DialogType, get_dialog_manager


def create_dialog_container(run_id: str | None = None):
    """Create a container for operator dialogs.

    This sets up a timer that polls for pending dialogs and displays them.
    Uses in-process DialogManager directly (same process as server).
    """

    dialog_container = ui.column().classes("hidden")
    state = {"current_dialog_id": None, "choice": 0, "input": ""}

    def check_dialogs():
        manager = get_dialog_manager()
        dialog = manager.get_pending_dialog(run_id)

        if dialog and str(dialog.id) != state["current_dialog_id"]:
            state["current_dialog_id"] = str(dialog.id)
            state["choice"] = getattr(dialog, "default_choice", 0)
            state["input"] = getattr(dialog, "default_value", "")
            dialog_container.classes(remove="hidden")
            dialog_container.clear()

            with dialog_container:
                with ui.dialog(value=True) as modal:
                    with ui.card().classes("w-96"):
                        with ui.card_section():
                            ui.label(dialog.title).classes("text-lg font-semibold")

                        with ui.card_section():
                            ui.label(dialog.message).classes("text-slate-600 mb-4")

                            # Dialog-specific content
                            if dialog.type == DialogType.CONFIRM:
                                pass  # Just buttons

                            elif dialog.type == DialogType.CHOICE:
                                choices = getattr(dialog, "choices", [])
                                options = {i: choice for i, choice in enumerate(choices)}
                                ui.radio(
                                    options=options,
                                    value=state["choice"],
                                    on_change=lambda e: state.update({"choice": e.value}),
                                ).classes("w-full")

                            elif dialog.type == DialogType.INPUT:
                                ui.input(
                                    placeholder=getattr(dialog, "placeholder", ""),
                                    value=state["input"],
                                    on_change=lambda e: state.update({"input": e.value}),
                                ).classes("w-full")

                            elif dialog.type == DialogType.IMAGE:
                                image_url = getattr(dialog, "image_url", None)
                                image_path = getattr(dialog, "image_path", None)
                                if image_url:
                                    ui.image(image_url).classes("w-full rounded")
                                elif image_path:
                                    ui.image(image_path).classes("w-full rounded")

                        with ui.card_actions().classes("justify-end gap-2"):
                            # Capture dialog in closure
                            captured_dialog = dialog

                            def respond_cancel():
                                manager.respond(
                                    captured_dialog.id,
                                    DialogResponse(dialog_id=captured_dialog.id, cancelled=True),
                                )
                                modal.close()
                                state["current_dialog_id"] = None
                                dialog_container.classes(add="hidden")

                            def respond_confirm():
                                response = DialogResponse(
                                    dialog_id=captured_dialog.id,
                                    confirmed=True,
                                )
                                if captured_dialog.type == DialogType.CHOICE:
                                    response.choice = state["choice"]
                                elif captured_dialog.type == DialogType.INPUT:
                                    response.value = state["input"]

                                manager.respond(captured_dialog.id, response)
                                modal.close()
                                state["current_dialog_id"] = None
                                dialog_container.classes(add="hidden")

                            ui.button("Cancel", on_click=respond_cancel).props("flat")
                            ui.button(
                                getattr(dialog, "confirm_label", "OK"),
                                on_click=respond_confirm,
                            ).props("color=primary")

        elif not dialog and state["current_dialog_id"]:
            state["current_dialog_id"] = None
            dialog_container.classes(add="hidden")

    ui.timer(0.5, check_dialogs)
    return dialog_container
