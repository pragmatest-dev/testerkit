"""Product edit page."""

from nicegui import ui

from litmus.ui.shared.components import setup_hash_sync_for_tabs
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_products, save_product


@ui.page("/products/{product_id}/edit")
def product_edit_page(product_id: str):
    """Product edit page with form interface."""
    products = discover_products()
    product = next((p for p in products if p["id"] == product_id), None)

    create_layout(f"Edit {product['name']}" if product else "Edit Product")

    if not product:
        with ui.column().classes("w-full p-6"):
            ui.label("Product not found.").classes("text-xl text-slate-600")
            ui.link("← Back to Products", "/products").classes("text-blue-600 hover:underline")
        return

    # Mutable form data
    form_data = {
        "name": product.get("name", ""),
        "description": product.get("description", ""),
        "revision": product.get("revision", ""),
        "pins": list(product.get("pins") or []),
        "characteristics": dict(product.get("characteristics") or {}),
        "test_requirements": dict(product.get("test_requirements") or {}),
    }

    with ui.column().classes("w-full p-6 gap-6"):
        # Header
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("edit").classes("text-slate-600")
                ui.label(f"Edit Product: {product['name']}").classes(
                    "text-lg font-semibold text-slate-700"
                )

            with ui.row().classes("gap-2"):
                ui.button(
                    "Cancel",
                    icon="close",
                    on_click=lambda: ui.navigate.to(f"/products/{product_id}"),
                ).props("flat")

                def save_changes():
                    updated = {
                        "id": product_id,
                        "name": form_data["name"],
                        "description": form_data["description"],
                        "revision": form_data["revision"],
                        "characteristics": form_data["characteristics"],
                        "test_requirements": form_data["test_requirements"],
                        "pins": form_data["pins"],
                    }
                    if save_product(product_id, updated):
                        ui.notify("Product saved successfully", type="positive")
                        ui.navigate.to(f"/products/{product_id}")
                    else:
                        ui.notify("Failed to save product", type="negative")

                ui.button("Save", icon="save", on_click=save_changes).props("color=primary")

        # Tabs
        with ui.tabs().classes("w-full") as tabs:
            info_tab = ui.tab("Info", icon="info")
            pins_tab = ui.tab("Pins", icon="memory")
            chars_tab = ui.tab("Characteristics", icon="tune")
            reqs_tab = ui.tab("Requirements", icon="checklist")

        setup_hash_sync_for_tabs(tabs, ["Info", "Pins", "Characteristics", "Requirements"])

        with ui.tab_panels(tabs, value=info_tab).classes("w-full"):
            with ui.tab_panel(info_tab):
                _render_info_tab(product_id, form_data)

            with ui.tab_panel(pins_tab):
                _render_pins_tab(form_data)

            with ui.tab_panel(chars_tab):
                _render_characteristics_tab(form_data)

            with ui.tab_panel(reqs_tab):
                _render_requirements_tab(form_data)

        ui.link("← Back to Product", f"/products/{product_id}").classes(
            "text-blue-600 hover:underline mt-4"
        )


def _render_info_tab(product_id: str, form_data: dict):
    """Render the info edit tab."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Basic Information").classes("font-semibold mb-4")
            with ui.column().classes("gap-4 w-full max-w-xl"):
                _labeled_input("Product ID", product_id, readonly=True)
                _labeled_input(
                    "Name",
                    form_data["name"],
                    on_change=lambda e: form_data.update({"name": e.value}),
                )
                _labeled_input(
                    "Revision",
                    form_data["revision"],
                    on_change=lambda e: form_data.update({"revision": e.value}),
                )
                _labeled_textarea(
                    "Description",
                    form_data["description"],
                    on_change=lambda e: form_data.update({"description": e.value}),
                )


def _render_pins_tab(form_data: dict):
    """Render the pins edit tab."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                ui.label("Pin Definitions").classes("font-semibold")

                def on_add_pin(pin):
                    form_data["pins"].append(pin)
                    ui.notify(f"Added pin: {pin['name']}. Click Save to persist.", type="positive")

                ui.button(
                    "Add Pin",
                    icon="add",
                    on_click=lambda: _show_add_pin_dialog(on_add_pin),
                ).props("flat color=primary dense")

            if form_data["pins"]:
                columns = [
                    {"name": "name", "label": "Name", "field": "name", "align": "left"},
                    {"name": "type", "label": "Type", "field": "type"},
                    {"name": "net", "label": "Net", "field": "net"},
                ]
                rows = [
                    {"name": p.get("name", ""), "type": p.get("type", ""), "net": p.get("net", "")}
                    for p in form_data["pins"]
                ]
                ui.table(columns=columns, rows=rows, row_key="name").classes("w-full")
            else:
                ui.label("No pins defined. Click 'Add Pin' to add one.").classes(
                    "text-slate-500 italic"
                )


def _render_characteristics_tab(form_data: dict):
    """Render the characteristics edit tab."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                ui.label("Characteristics").classes("font-semibold")

                def on_add_char(name, data):
                    form_data["characteristics"][name] = data
                    ui.notify(
                        f"Added characteristic: {name}. Click Save to persist.", type="positive"
                    )

                ui.button(
                    "Add Characteristic",
                    icon="add",
                    on_click=lambda: _show_add_char_dialog(on_add_char),
                ).props("flat color=primary dense")

            characteristics = form_data["characteristics"]
            if characteristics:
                for char_name, char_data in characteristics.items():
                    with ui.expansion(char_name, icon="tune").classes("w-full"):
                        with ui.grid(columns=3).classes("gap-4 p-2"):
                            direction = char_data.get("direction", "")
                            _labeled_input("Function", char_data.get("function", ""), readonly=True)
                            _labeled_input("Direction", direction, readonly=True)
                            _labeled_input("Units", char_data.get("units", ""), readonly=True)

                        conditions = char_data.get("conditions", [])
                        if conditions:
                            ui.label("Conditions").classes("font-semibold text-sm mt-4 px-2")
                            for i, cond in enumerate(conditions):
                                _render_condition(i, cond)
            else:
                ui.label(
                    "No characteristics defined. Click 'Add Characteristic' to add one."
                ).classes("text-slate-500 italic")


def _render_condition(index: int, cond: dict):
    """Render a condition card."""
    with ui.card().classes("w-full mt-2"):
        with ui.card_section():
            cond_params = {
                k: v
                for k, v in cond.items()
                if k not in ["nominal", "limit_low", "limit_high", "tolerance_pct", "description"]
            }
            ui.label(f"Condition {index + 1}: {cond_params}").classes("text-xs text-slate-500")
            with ui.row().classes("gap-4 mt-2"):
                if cond.get("nominal") is not None:
                    ui.chip(f"Nominal: {cond['nominal']}").props("outline")
                if cond.get("limit_low") is not None:
                    ui.chip(f"Min: {cond['limit_low']}").props("outline color=red")
                if cond.get("limit_high") is not None:
                    ui.chip(f"Max: {cond['limit_high']}").props("outline color=red")
                if cond.get("tolerance_pct") is not None:
                    ui.chip(f"±{cond['tolerance_pct']}%").props("outline color=blue")


def _render_requirements_tab(form_data: dict):
    """Render the requirements edit tab."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                ui.label("Test Requirements").classes("font-semibold")

                def on_add_req(name, data):
                    form_data["test_requirements"][name] = data
                    ui.notify(f"Added requirement: {name}. Click Save to persist.", type="positive")

                char_names = list(form_data["characteristics"].keys())
                ui.button(
                    "Add Requirement",
                    icon="add",
                    on_click=lambda: _show_add_req_dialog(char_names, on_add_req),
                ).props("flat color=primary dense")

            requirements = form_data["test_requirements"]
            if requirements:
                columns = [
                    {"name": "name", "label": "Name", "field": "name", "align": "left"},
                    {"name": "char_ref", "label": "Characteristic", "field": "char_ref"},
                    {"name": "priority", "label": "Priority", "field": "priority"},
                    {"name": "guardband", "label": "Guardband", "field": "guardband"},
                ]
                rows = [
                    {
                        "name": name,
                        "char_ref": req.get("characteristic_ref", "-"),
                        "priority": req.get("priority", "standard"),
                        "guardband": f"{req.get('guardband_pct', 0)}%",
                    }
                    for name, req in requirements.items()
                ]
                ui.table(columns=columns, rows=rows, row_key="name").classes("w-full")
            else:
                ui.label(
                    "No test requirements defined. Click 'Add Requirement' to add one."
                ).classes("text-slate-500 italic")


# -----------------------------------------------------------------------------
# Form Components (local to this page)
# -----------------------------------------------------------------------------


def _labeled_input(label: str, value: str = "", readonly: bool = False, on_change=None):
    """Create a labeled input field."""
    with ui.column().classes("gap-1 w-full"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        props = "outlined dense"
        if readonly:
            props += " readonly"
        ui.input(value=value, on_change=on_change).props(props).classes("w-full")


def _labeled_textarea(label: str, value: str = "", on_change=None):
    """Create a labeled textarea."""
    with ui.column().classes("gap-1 w-full"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        ui.textarea(value=value, on_change=on_change).props("outlined dense").classes("w-full")


def _labeled_select(label: str, options, value=None, on_change=None):
    """Create a labeled select."""
    with ui.column().classes("gap-1 w-full"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        ui.select(options=options, value=value, on_change=on_change).props(
            "outlined dense"
        ).classes("w-full")


def _labeled_number(label: str, value: float = 0, min_val=None, max_val=None, on_change=None):
    """Create a labeled number input."""
    with ui.column().classes("gap-1 w-full"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        ui.number(value=value, min=min_val, max=max_val, on_change=on_change).props(
            "outlined dense"
        ).classes("w-full")


# -----------------------------------------------------------------------------
# Dialogs (local to this page)
# -----------------------------------------------------------------------------


def _show_add_pin_dialog(on_add: callable):
    """Show dialog to add a new pin."""
    pin_form = {"name": "", "type": "signal", "net": ""}

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        with ui.card_section():
            ui.label("Add Pin").classes("text-lg font-semibold")
        with ui.card_section().classes("flex flex-col gap-4"):
            _labeled_input("Name", on_change=lambda e: pin_form.update({"name": e.value}))
            _labeled_select(
                "Type",
                options=["signal", "power", "ground", "nc"],
                value="signal",
                on_change=lambda e: pin_form.update({"type": e.value}),
            )
            _labeled_input("Net", on_change=lambda e: pin_form.update({"net": e.value}))
        with ui.card_actions().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def add():
                if not pin_form["name"]:
                    ui.notify("Pin name is required", type="warning")
                    return
                on_add(dict(pin_form))
                dialog.close()

            ui.button("Add", on_click=add).props("color=primary")
    dialog.open()


def _show_add_char_dialog(on_add: callable):
    """Show dialog to add a new characteristic."""
    function_options = [
        "dc_voltage", "ac_voltage", "dc_current", "ac_current",
        "resistance", "resistance_4w", "capacitance", "inductance",
        "frequency", "temperature", "dc_power", "ac_power", "waveform",
    ]
    char_form = {"name": "", "function": "dc_voltage", "direction": "output", "units": "V"}

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        with ui.card_section():
            ui.label("Add Characteristic").classes("text-lg font-semibold")
        with ui.card_section().classes("flex flex-col gap-4"):
            _labeled_input("Name", on_change=lambda e: char_form.update({"name": e.value}))
            _labeled_select(
                "Function",
                options=function_options,
                value="dc_voltage",
                on_change=lambda e: char_form.update({"function": e.value}),
            )
            _labeled_select(
                "Direction",
                options=["input", "output", "bidir"],
                value="output",
                on_change=lambda e: char_form.update({"direction": e.value}),
            )
            _labeled_input(
                "Units", value="V", on_change=lambda e: char_form.update({"units": e.value})
            )
        with ui.card_actions().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def add():
                if not char_form["name"]:
                    ui.notify("Characteristic name is required", type="warning")
                    return
                name = char_form.pop("name")
                on_add(name, dict(char_form))
                dialog.close()

            ui.button("Add", on_click=add).props("color=primary")
    dialog.open()


def _show_add_req_dialog(char_names: list, on_add: callable):
    """Show dialog to add a new requirement."""
    req_form = {
        "name": "",
        "characteristic_ref": char_names[0] if char_names else "",
        "priority": "standard",
        "guardband_pct": 0,
    }

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        with ui.card_section():
            ui.label("Add Test Requirement").classes("text-lg font-semibold")
        with ui.card_section().classes("flex flex-col gap-4"):
            _labeled_input("Name", on_change=lambda e: req_form.update({"name": e.value}))
            if char_names:
                _labeled_select(
                    "Characteristic",
                    options=char_names,
                    value=char_names[0],
                    on_change=lambda e: req_form.update({"characteristic_ref": e.value}),
                )
            else:
                ui.label("No characteristics defined yet").classes("text-slate-500 italic")
            _labeled_select(
                "Priority",
                options=["critical", "standard", "informational"],
                value="standard",
                on_change=lambda e: req_form.update({"priority": e.value}),
            )
            _labeled_number(
                "Guardband %",
                value=0,
                min_val=0,
                max_val=50,
                on_change=lambda e: req_form.update({"guardband_pct": e.value or 0}),
            )
        with ui.card_actions().classes("justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def add():
                if not req_form["name"]:
                    ui.notify("Requirement name is required", type="warning")
                    return
                name = req_form.pop("name")
                on_add(name, dict(req_form))
                dialog.close()

            ui.button("Add", on_click=add).props("color=primary")
    dialog.open()
