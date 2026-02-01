"""Sequence edit page."""

from nicegui import ui

from litmus.ui.shared.components import setup_hash_sync_for_tabs
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_products,
    discover_sequences,
    discover_tests,
    load_sequence_config,
    save_sequence,
)


@ui.page("/sequences/{sequence_id}/edit")
def sequence_edit_page(sequence_id: str):
    """Sequence edit page with form interface."""
    config = load_sequence_config(sequence_id)

    if config:
        seq = config.get("sequence", {})
        create_layout(f"Edit {seq.get('name', sequence_id)}")
    else:
        create_layout("Sequence Not Found")

    if not config:
        with ui.column().classes("w-full p-6"):
            ui.label("Sequence not found.").classes("text-xl text-slate-600")
            ui.link("← Back to Sequences", "/sequences").classes(
                "text-blue-600 hover:underline"
            )
        return

    seq = config.get("sequence", {})

    # Get available options for dropdowns
    products = discover_products()
    product_options = {"": "-- None --"}
    product_options.update({p["id"]: p.get("name", p["id"]) for p in products})

    sequences = discover_sequences()
    sequence_options = {s["id"]: s["name"] for s in sequences if s["id"] != sequence_id}

    tests = discover_tests()
    test_options = [t["path"] for t in tests]

    phase_options = {
        "validation": "Validation",
        "characterization": "Characterization",
        "production": "Production",
    }

    # Form state
    form_data = {
        "sequence": {
            "id": seq.get("id", sequence_id),
            "name": seq.get("name", ""),
            "description": seq.get("description", ""),
            "product_family": seq.get("product_family", ""),
            "test_phase": seq.get("test_phase", "validation"),
            "required_fixture": seq.get("required_fixture", ""),
            "required_station_type": seq.get("required_station_type", ""),
            "timeout_seconds": seq.get("timeout_seconds"),
        },
        "steps": list(config.get("steps", [])),
        "dialogs": dict(config.get("dialogs", {})),
    }

    with ui.column().classes("w-full p-6 gap-6"):
        # Header
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("edit").classes("text-slate-600")
                ui.label(f"Edit Sequence: {seq.get('name', sequence_id)}").classes(
                    "text-lg font-semibold text-slate-700"
                )

            with ui.row().classes("gap-2"):
                ui.button(
                    "Cancel",
                    icon="close",
                    on_click=lambda: ui.navigate.to(f"/sequences/{sequence_id}"),
                ).props("flat")

                def save_changes():
                    # Clean up sequence data (remove empty optional fields)
                    seq_data = {k: v for k, v in form_data["sequence"].items() if v}
                    if save_sequence(
                        sequence_id, seq_data, form_data["steps"], form_data["dialogs"]
                    ):
                        ui.notify("Sequence saved successfully", type="positive")
                        ui.navigate.to(f"/sequences/{sequence_id}")
                    else:
                        ui.notify("Failed to save sequence", type="negative")

                ui.button("Save", icon="save", on_click=save_changes).props(
                    "color=primary"
                )

        # Tabs
        with ui.tabs().classes("w-full") as tabs:
            info_tab = ui.tab("Info", icon="info")
            steps_tab = ui.tab("Steps", icon="format_list_numbered")
            dialogs_tab = ui.tab("Dialogs", icon="chat")

        setup_hash_sync_for_tabs(tabs, ["Info", "Steps", "Dialogs"])

        with ui.tab_panels(tabs, value=info_tab).classes("w-full"):
            with ui.tab_panel(info_tab):
                _render_info_tab(form_data, product_options, phase_options)

            with ui.tab_panel(steps_tab):
                _render_steps_tab(form_data, sequence_options, test_options)

            with ui.tab_panel(dialogs_tab):
                _render_dialogs_tab(form_data)

        ui.link("← Back to Sequence", f"/sequences/{sequence_id}").classes(
            "text-blue-600 hover:underline mt-4"
        )


def _render_info_tab(form_data: dict, product_options: dict, phase_options: dict):
    """Render the info edit tab."""
    seq = form_data["sequence"]

    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Basic Information").classes("font-semibold mb-4")

            with ui.column().classes("gap-4 w-full max-w-xl"):
                _labeled_input(
                    "Sequence ID",
                    seq["id"],
                    readonly=True,
                )
                _labeled_input(
                    "Name",
                    seq["name"],
                    on_change=lambda e: seq.update({"name": e.value}),
                )
                _labeled_textarea(
                    "Description",
                    seq["description"],
                    on_change=lambda e: seq.update({"description": e.value}),
                )

                with ui.row().classes("gap-4 w-full"):
                    with ui.column().classes("gap-1 flex-1"):
                        ui.label("Product Family").classes(
                            "text-sm font-medium text-slate-700"
                        )
                        ui.select(
                            options=product_options,
                            value=seq["product_family"],
                            on_change=lambda e: seq.update({"product_family": e.value}),
                        ).props("outlined dense").classes("w-full")

                    with ui.column().classes("gap-1 flex-1"):
                        ui.label("Test Phase").classes(
                            "text-sm font-medium text-slate-700"
                        )
                        ui.select(
                            options=phase_options,
                            value=seq["test_phase"],
                            on_change=lambda e: seq.update({"test_phase": e.value}),
                        ).props("outlined dense").classes("w-full")

    with ui.card().classes("w-full mt-4"):
        with ui.card_section():
            ui.label("Requirements").classes("font-semibold mb-4")

            with ui.column().classes("gap-4 w-full max-w-xl"):
                _labeled_input(
                    "Required Fixture",
                    seq.get("required_fixture", ""),
                    placeholder="e.g., power_board_fixture_v1",
                    on_change=lambda e: seq.update({"required_fixture": e.value}),
                )
                _labeled_input(
                    "Required Station Type",
                    seq.get("required_station_type", ""),
                    placeholder="e.g., power_test_station",
                    on_change=lambda e: seq.update({"required_station_type": e.value}),
                )
                with ui.column().classes("gap-1 w-full"):
                    ui.label("Timeout (seconds)").classes(
                        "text-sm font-medium text-slate-700"
                    )
                    ui.number(
                        value=seq.get("timeout_seconds"),
                        min=0,
                        on_change=lambda e: seq.update({"timeout_seconds": e.value}),
                    ).props("outlined dense").classes("w-full")


def _render_steps_tab(form_data: dict, sequence_options: dict, test_options: list):
    """Render the steps edit tab."""
    steps = form_data["steps"]

    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("Steps").classes("font-semibold")
                    ui.badge(f"{len(steps)} steps").props("outline")

                ui.button(
                    "Add Step",
                    icon="add",
                    on_click=lambda: _show_add_step_dialog(
                        form_data, sequence_options, test_options, steps_container
                    ),
                ).props("flat color=primary dense")

        steps_container = ui.column().classes("w-full gap-2")

        def refresh_steps():
            steps_container.clear()
            with steps_container:
                if steps:
                    for i, step in enumerate(steps):
                        _render_step_card(
                            i,
                            step,
                            form_data,
                            sequence_options,
                            test_options,
                            refresh_steps,
                        )
                else:
                    ui.label(
                        "No steps defined. Click 'Add Step' to create one."
                    ).classes("text-slate-500 italic")

        refresh_steps()


def _render_step_card(
    index: int,
    step: dict,
    form_data: dict,
    sequence_options: dict,
    test_options: list,
    refresh_callback,
):
    """Render a step card with edit/delete options."""
    step_id = step.get("id", f"step_{index}")
    is_sequence = bool(step.get("sequence"))

    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                with ui.row().classes("items-center gap-2"):
                    ui.badge(f"#{index + 1}").props("outline")
                    ui.icon("folder" if is_sequence else "science").classes(
                        "text-slate-500"
                    )
                    ui.label(step_id).classes("font-semibold font-mono")
                with ui.row().classes("gap-1"):
                    # Move up
                    if index > 0:
                        ui.button(
                            icon="arrow_upward",
                            on_click=lambda i=index: _move_step(
                                form_data["steps"], i, -1, refresh_callback
                            ),
                        ).props("flat dense round")
                    # Move down
                    if index < len(form_data["steps"]) - 1:
                        ui.button(
                            icon="arrow_downward",
                            on_click=lambda i=index: _move_step(
                                form_data["steps"], i, 1, refresh_callback
                            ),
                        ).props("flat dense round")
                    # Delete
                    ui.button(
                        icon="delete",
                        on_click=lambda i=index: _delete_step(
                            form_data["steps"], i, refresh_callback
                        ),
                    ).props("flat dense round color=red")

        with ui.card_section():
            if step.get("description"):
                ui.label(step["description"]).classes("text-sm text-slate-600 mb-2")

            with ui.row().classes("gap-4"):
                if step.get("test"):
                    ui.chip(f"Test: {step['test']}", icon="science").props("outline")
                if step.get("sequence"):
                    ui.chip(f"Sequence: {step['sequence']}", icon="folder").props(
                        "outline"
                    )
                if step.get("limit_ref"):
                    ui.chip(f"Limit: {step['limit_ref']}", icon="rule").props("outline")
                if step.get("retry"):
                    retry = step["retry"]
                    ui.chip(
                        f"Retry: {retry.get('max_attempts', 1)}x", icon="refresh"
                    ).props("outline")

        # Edit in expansion
        with ui.expansion("Edit Step", icon="edit").classes("w-full"):
            with ui.column().classes("gap-4 p-2"):
                _labeled_input(
                    "Step ID",
                    step.get("id", ""),
                    on_change=lambda e, s=step: s.update({"id": e.value}),
                )
                _labeled_input(
                    "Description",
                    step.get("description", ""),
                    on_change=lambda e, s=step: s.update({"description": e.value}),
                )

                with ui.row().classes("gap-4 w-full"):
                    with ui.column().classes("gap-1 flex-1"):
                        ui.label("Test Path").classes(
                            "text-sm font-medium text-slate-700"
                        )
                        ui.select(
                            options=[""] + test_options,
                            value=step.get("test", ""),
                            with_input=True,
                            on_change=lambda e, s=step: s.update({"test": e.value}),
                        ).props("outlined dense").classes("w-full")

                    with ui.column().classes("gap-1 flex-1"):
                        ui.label("Nested Sequence").classes(
                            "text-sm font-medium text-slate-700"
                        )
                        ui.select(
                            options={"": "-- None --", **sequence_options},
                            value=step.get("sequence", ""),
                            on_change=lambda e, s=step: s.update({"sequence": e.value}),
                        ).props("outlined dense").classes("w-full")

                _labeled_input(
                    "Limit Reference",
                    step.get("limit_ref", ""),
                    placeholder="e.g., specs.power_board.rail_5v",
                    on_change=lambda e, s=step: s.update({"limit_ref": e.value}),
                )

                with ui.row().classes("gap-4 w-full"):
                    _labeled_input(
                        "Pre-Dialog",
                        step.get("pre_dialog", ""),
                        placeholder="Dialog ID",
                        on_change=lambda e, s=step: s.update({"pre_dialog": e.value}),
                    )
                    _labeled_input(
                        "Post-Dialog",
                        step.get("post_dialog", ""),
                        placeholder="Dialog ID",
                        on_change=lambda e, s=step: s.update({"post_dialog": e.value}),
                    )

                # Retry settings
                with ui.expansion("Retry Settings", icon="refresh").classes("w-full"):
                    retry = step.setdefault("retry", {})
                    with ui.column().classes("gap-4 p-2"):
                        with ui.row().classes("gap-4"):
                            with ui.column().classes("gap-1"):
                                ui.label("Max Attempts").classes(
                                    "text-sm font-medium text-slate-700"
                                )
                                ui.number(
                                    value=retry.get("max_attempts", 1),
                                    min=1,
                                    on_change=lambda e, r=retry: r.update(
                                        {"max_attempts": int(e.value) if e.value else 1}
                                    ),
                                ).props("outlined dense")

                            with ui.column().classes("gap-1"):
                                ui.label("Delay (seconds)").classes(
                                    "text-sm font-medium text-slate-700"
                                )
                                ui.number(
                                    value=retry.get("delay_seconds", 0),
                                    min=0,
                                    step=0.1,
                                    on_change=lambda e, r=retry: r.update(
                                        {"delay_seconds": float(e.value) if e.value else 0}
                                    ),
                                ).props("outlined dense")

                        with ui.row().classes("gap-4"):
                            with ui.column().classes("gap-1"):
                                ui.label("Strategy").classes(
                                    "text-sm font-medium text-slate-700"
                                )
                                ui.select(
                                    options={
                                        "": "-- None --",
                                        "immediate": "Immediate",
                                        "dialog": "Dialog",
                                    },
                                    value=retry.get("strategy", ""),
                                    on_change=lambda e, r=retry: r.update(
                                        {"strategy": e.value}
                                    ),
                                ).props("outlined dense")

                            _labeled_input(
                                "Dialog Ref",
                                retry.get("dialog_ref", ""),
                                placeholder="Dialog ID for retry",
                                on_change=lambda e, r=retry: r.update(
                                    {"dialog_ref": e.value}
                                ),
                            )


def _render_dialogs_tab(form_data: dict):
    """Render the dialogs edit tab."""
    dialogs = form_data["dialogs"]

    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("Dialogs").classes("font-semibold")
                    ui.badge(f"{len(dialogs)} dialogs").props("outline")

                ui.button(
                    "Add Dialog",
                    icon="add",
                    on_click=lambda: _show_add_dialog_dialog(form_data, dialogs_container),
                ).props("flat color=primary dense")

        dialogs_container = ui.column().classes("w-full gap-2")

        def refresh_dialogs():
            dialogs_container.clear()
            with dialogs_container:
                if dialogs:
                    for dialog_id, dialog in dialogs.items():
                        _render_dialog_card(dialog_id, dialog, form_data, refresh_dialogs)
                else:
                    ui.label(
                        "No dialogs defined. Click 'Add Dialog' to create one."
                    ).classes("text-slate-500 italic")

        refresh_dialogs()


def _render_dialog_card(dialog_id: str, dialog: dict, form_data: dict, refresh_callback):
    """Render a dialog card with edit/delete options."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("chat").classes("text-slate-500")
                    ui.label(dialog_id).classes("font-semibold font-mono")
                    dialog_type = dialog.get("dialog_type", "")
                    if dialog_type:
                        ui.badge(dialog_type).props("outline")
                ui.button(
                    icon="delete",
                    on_click=lambda did=dialog_id: _delete_dialog(
                        form_data["dialogs"], did, refresh_callback
                    ),
                ).props("flat dense round color=red")

        with ui.card_section():
            ui.label(dialog.get("message", "")).classes("text-sm text-slate-600")

        with ui.expansion("Edit Dialog", icon="edit").classes("w-full"):
            with ui.column().classes("gap-4 p-2"):
                _labeled_input(
                    "Dialog ID",
                    dialog.get("id", dialog_id),
                    readonly=True,
                )
                _labeled_textarea(
                    "Message",
                    dialog.get("message", ""),
                    on_change=lambda e, d=dialog: d.update({"message": e.value}),
                )
                with ui.row().classes("gap-4"):
                    with ui.column().classes("gap-1 flex-1"):
                        ui.label("Dialog Type").classes(
                            "text-sm font-medium text-slate-700"
                        )
                        ui.select(
                            options={
                                "confirm": "Confirm",
                                "input": "Input",
                                "choice": "Choice",
                            },
                            value=dialog.get("dialog_type", "confirm"),
                            on_change=lambda e, d=dialog: d.update(
                                {"dialog_type": e.value}
                            ),
                        ).props("outlined dense").classes("w-full")

                    with ui.column().classes("gap-1 flex-1"):
                        ui.label("Timeout (seconds)").classes(
                            "text-sm font-medium text-slate-700"
                        )
                        ui.number(
                            value=dialog.get("timeout_seconds"),
                            min=0,
                            on_change=lambda e, d=dialog: d.update(
                                {"timeout_seconds": int(e.value) if e.value else None}
                            ),
                        ).props("outlined dense").classes("w-full")


# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------


def _move_step(steps: list, index: int, direction: int, refresh_callback):
    """Move a step up or down."""
    new_index = index + direction
    if 0 <= new_index < len(steps):
        steps[index], steps[new_index] = steps[new_index], steps[index]
        refresh_callback()


def _delete_step(steps: list, index: int, refresh_callback):
    """Delete a step."""
    if 0 <= index < len(steps):
        del steps[index]
        refresh_callback()


def _delete_dialog(dialogs: dict, dialog_id: str, refresh_callback):
    """Delete a dialog."""
    if dialog_id in dialogs:
        del dialogs[dialog_id]
        refresh_callback()


def _show_add_step_dialog(
    form_data: dict, sequence_options: dict, test_options: list, container
):
    """Show dialog to add a new step."""
    step_form = {
        "id": "",
        "description": "",
        "test": "",
        "sequence": "",
    }

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        with ui.card_section():
            ui.label("Add Step").classes("text-lg font-semibold")
        with ui.card_section().classes("flex flex-col gap-4"):
            _labeled_input(
                "Step ID",
                on_change=lambda e: step_form.update({"id": e.value}),
            )
            _labeled_input(
                "Description",
                on_change=lambda e: step_form.update({"description": e.value}),
            )
            with ui.column().classes("gap-1"):
                ui.label("Test Path").classes("text-sm font-medium text-slate-700")
                ui.select(
                    options=[""] + test_options,
                    value="",
                    with_input=True,
                    on_change=lambda e: step_form.update({"test": e.value}),
                ).props("outlined dense").classes("w-full")
            with ui.column().classes("gap-1"):
                ui.label("Or Nested Sequence").classes(
                    "text-sm font-medium text-slate-700"
                )
                ui.select(
                    options={"": "-- None --", **sequence_options},
                    value="",
                    on_change=lambda e: step_form.update({"sequence": e.value}),
                ).props("outlined dense").classes("w-full")
        with ui.card_actions().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def add():
                if not step_form["id"]:
                    ui.notify("Step ID is required", type="warning")
                    return
                if not step_form["test"] and not step_form["sequence"]:
                    ui.notify("Either test or sequence is required", type="warning")
                    return

                new_step = {"id": step_form["id"]}
                if step_form["description"]:
                    new_step["description"] = step_form["description"]
                if step_form["test"]:
                    new_step["test"] = step_form["test"]
                if step_form["sequence"]:
                    new_step["sequence"] = step_form["sequence"]

                form_data["steps"].append(new_step)
                dialog.close()
                container.clear()
                with container:
                    for i, step in enumerate(form_data["steps"]):
                        _render_step_card(
                            i,
                            step,
                            form_data,
                            sequence_options,
                            test_options,
                            lambda: None,  # Will be replaced on refresh
                        )

            ui.button("Add", on_click=add).props("color=primary")
    dialog.open()


def _show_add_dialog_dialog(form_data: dict, container):
    """Show dialog to add a new dialog."""
    dialog_form = {
        "id": "",
        "message": "",
        "dialog_type": "confirm",
        "timeout_seconds": None,
    }

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        with ui.card_section():
            ui.label("Add Dialog").classes("text-lg font-semibold")
        with ui.card_section().classes("flex flex-col gap-4"):
            _labeled_input(
                "Dialog ID",
                on_change=lambda e: dialog_form.update({"id": e.value}),
            )
            _labeled_textarea(
                "Message",
                on_change=lambda e: dialog_form.update({"message": e.value}),
            )
            with ui.column().classes("gap-1"):
                ui.label("Dialog Type").classes("text-sm font-medium text-slate-700")
                ui.select(
                    options={
                        "confirm": "Confirm",
                        "input": "Input",
                        "choice": "Choice",
                    },
                    value="confirm",
                    on_change=lambda e: dialog_form.update({"dialog_type": e.value}),
                ).props("outlined dense").classes("w-full")
            with ui.column().classes("gap-1"):
                ui.label("Timeout (seconds)").classes(
                    "text-sm font-medium text-slate-700"
                )
                ui.number(
                    min=0,
                    on_change=lambda e: dialog_form.update(
                        {"timeout_seconds": int(e.value) if e.value else None}
                    ),
                ).props("outlined dense").classes("w-full")
        with ui.card_actions().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def add():
                if not dialog_form["id"]:
                    ui.notify("Dialog ID is required", type="warning")
                    return
                if not dialog_form["message"]:
                    ui.notify("Message is required", type="warning")
                    return

                new_dialog = {
                    "id": dialog_form["id"],
                    "message": dialog_form["message"],
                    "dialog_type": dialog_form["dialog_type"],
                }
                if dialog_form["timeout_seconds"]:
                    new_dialog["timeout_seconds"] = dialog_form["timeout_seconds"]

                form_data["dialogs"][dialog_form["id"]] = new_dialog
                dialog.close()
                container.clear()
                with container:
                    for did, d in form_data["dialogs"].items():
                        _render_dialog_card(did, d, form_data, lambda: None)

            ui.button("Add", on_click=add).props("color=primary")
    dialog.open()


# -----------------------------------------------------------------------------
# Form Components
# -----------------------------------------------------------------------------


def _labeled_input(
    label: str,
    value: str = "",
    placeholder: str = "",
    readonly: bool = False,
    on_change=None,
):
    """Create a labeled input field."""
    with ui.column().classes("gap-1 flex-1"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        props = "outlined dense"
        if readonly:
            props += " readonly"
        ui.input(value=value, placeholder=placeholder, on_change=on_change).props(
            props
        ).classes("w-full")


def _labeled_textarea(label: str, value: str = "", on_change=None):
    """Create a labeled textarea."""
    with ui.column().classes("gap-1 w-full"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        ui.textarea(value=value, on_change=on_change).props("outlined dense").classes(
            "w-full"
        )
