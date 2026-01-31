"""Test configuration editor page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    get_test_functions,
    load_test_config,
    save_test_config,
)


@ui.page("/tests/{test_path_encoded}/config")
def test_config_page(test_path_encoded: str):
    """Test configuration editor page."""
    # Decode path (-- becomes /)
    test_path = test_path_encoded.replace("--", "/")

    create_layout(f"Test Config - {test_path}")

    # Load existing config or create empty
    config = load_test_config(test_path) or {}
    test_functions = get_test_functions(test_path)

    # Form state - deep copy
    form_data = {name: dict(data) for name, data in config.items()}

    with ui.column().classes("w-full p-6 gap-6"):
        # Header
        with ui.row().classes("w-full items-center justify-between"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("settings").classes("text-slate-600")
                ui.label(f"Test Configuration: {test_path}").classes(
                    "text-lg font-semibold text-slate-700"
                )

            with ui.row().classes("gap-2"):
                ui.button(
                    "Cancel",
                    icon="close",
                    on_click=lambda: ui.navigate.to("/tests"),
                ).props("flat")

                def save_changes():
                    if save_test_config(test_path, form_data):
                        ui.notify("Configuration saved successfully", type="positive")
                    else:
                        ui.notify("Failed to save configuration", type="negative")

                ui.button("Save", icon="save", on_click=save_changes).props(
                    "color=primary"
                )

        # Info about available test functions
        if test_functions:
            with ui.card().classes("w-full bg-slate-50"):
                with ui.card_section():
                    ui.label("Available Test Functions").classes(
                        "text-xs text-slate-500 uppercase"
                    )
                    with ui.row().classes("gap-2 flex-wrap mt-2"):
                        for func in test_functions:
                            if func in form_data:
                                ui.badge(func, color="green").props("outline")
                            else:
                                # Allow adding config for this function
                                ui.button(
                                    func,
                                    icon="add",
                                    on_click=lambda f=func: _add_test_config(
                                        form_data, f, config_container
                                    ),
                                ).props("flat dense outline")

        # Test configurations
        config_container = ui.column().classes("w-full gap-4")

        def refresh_configs():
            config_container.clear()
            with config_container:
                if form_data:
                    for test_name, test_config in form_data.items():
                        _render_test_config_card(
                            test_name, test_config, form_data, refresh_configs
                        )
                else:
                    ui.label(
                        "No test configurations. Click a test function above to add one."
                    ).classes("text-slate-500 italic")

        refresh_configs()

        ui.link("← Back to Tests", "/tests").classes(
            "text-blue-600 hover:underline mt-4"
        )


def _render_test_config_card(
    test_name: str, test_config: dict, form_data: dict, refresh_callback
):
    """Render a test configuration card."""
    with ui.card().classes("w-full"):
        with ui.card_section():
            with ui.row().classes("items-center justify-between"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("science").classes("text-slate-500")
                    ui.label(test_name).classes("font-semibold font-mono")
                ui.button(
                    icon="delete",
                    on_click=lambda n=test_name: _delete_test_config(
                        form_data, n, refresh_callback
                    ),
                ).props("flat dense round color=red")

        # Limits section
        with ui.expansion("Limits", icon="rule", value=True).classes("w-full"):
            limits = test_config.setdefault("limits", {})
            _render_limits_editor(test_name, limits)

        # Vectors section
        with ui.expansion("Vectors", icon="loop").classes("w-full"):
            vectors = test_config.get("vectors")
            _render_vectors_editor(test_name, test_config, vectors)


def _render_limits_editor(test_name: str, limits: dict):
    """Render the limits editor for a test."""
    with ui.column().classes("gap-4 p-2"):
        # Default limit for this test
        test_limit = limits.setdefault(test_name, {})

        with ui.grid(columns=4).classes("gap-4 w-full"):
            with ui.column().classes("gap-1"):
                ui.label("Low").classes("text-xs text-slate-500")
                ui.number(
                    value=test_limit.get("low"),
                    on_change=lambda e, lim=test_limit: lim.update({"low": e.value}),
                ).props("outlined dense").classes("w-full")

            with ui.column().classes("gap-1"):
                ui.label("Nominal").classes("text-xs text-slate-500")
                ui.number(
                    value=test_limit.get("nominal"),
                    on_change=lambda e, lim=test_limit: lim.update({"nominal": e.value}),
                ).props("outlined dense").classes("w-full")

            with ui.column().classes("gap-1"):
                ui.label("High").classes("text-xs text-slate-500")
                ui.number(
                    value=test_limit.get("high"),
                    on_change=lambda e, lim=test_limit: lim.update({"high": e.value}),
                ).props("outlined dense").classes("w-full")

            with ui.column().classes("gap-1"):
                ui.label("Units").classes("text-xs text-slate-500")
                ui.input(
                    value=test_limit.get("units", ""),
                    placeholder="e.g., V, A, %",
                    on_change=lambda e, lim=test_limit: lim.update({"units": e.value}),
                ).props("outlined dense").classes("w-full")

        with ui.column().classes("gap-1 w-full max-w-md"):
            ui.label("Spec Reference").classes("text-xs text-slate-500")
            ui.input(
                value=test_limit.get("spec_ref", ""),
                placeholder="e.g., PWR-OUT-001",
                on_change=lambda e, lim=test_limit: lim.update({"spec_ref": e.value}),
            ).props("outlined dense").classes("w-full")


def _render_vectors_editor(test_name: str, test_config: dict, vectors):
    """Render the vectors editor for a test."""
    with ui.column().classes("gap-4 p-2"):
        if vectors is None:
            ui.label("No vectors defined.").classes("text-slate-500 italic")

            def add_simple_vectors():
                test_config["vectors"] = [{"sample": 1}]

            def add_product_vectors():
                test_config["vectors"] = {"expand": "product"}

            with ui.row().classes("gap-2"):
                ui.button(
                    "Add Simple List",
                    icon="add",
                    on_click=add_simple_vectors,
                ).props("flat dense")
                ui.button(
                    "Add Product Expansion",
                    icon="add",
                    on_click=add_product_vectors,
                ).props("flat dense")
            return

        if isinstance(vectors, list):
            # Simple list of vector dicts
            ui.label("Vector List (one iteration per item)").classes(
                "text-xs text-slate-500 uppercase"
            )
            for i, vec in enumerate(vectors):
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"{i + 1}.").classes("text-slate-400 w-6")
                    ui.input(
                        value=str(vec),
                        on_change=lambda e, idx=i: _update_vector_item(
                            test_config, idx, e.value
                        ),
                    ).props("outlined dense").classes("flex-1")

            def add_vector_item():
                test_config["vectors"].append({"sample": len(vectors) + 1})

            ui.button("Add Item", icon="add", on_click=add_vector_item).props(
                "flat dense"
            )

        elif isinstance(vectors, dict):
            # Expansion config
            expand_type = vectors.get("expand", "product")
            ui.label(f"Expansion: {expand_type}").classes(
                "text-xs text-slate-500 uppercase"
            )

            with ui.column().classes("gap-1 w-full"):
                ui.label("Expand Mode").classes("text-sm font-medium text-slate-700")
                ui.select(
                    options={"product": "Product (Cartesian)", "nested": "Nested Loops"},
                    value=expand_type,
                    on_change=lambda e: vectors.update({"expand": e.value}),
                ).props("outlined dense").classes("w-full max-w-xs")

            # Show variables
            for key, value in vectors.items():
                if key not in ["expand", "loops"]:
                    with ui.row().classes("items-center gap-4"):
                        ui.label(key).classes("font-mono w-32")
                        ui.input(
                            value=str(value),
                            on_change=lambda e, k=key: vectors.update({k: eval(e.value)}),
                        ).props("outlined dense").classes("flex-1")


def _update_vector_item(test_config: dict, index: int, value: str):
    """Update a vector item from string representation."""
    try:
        test_config["vectors"][index] = eval(value)
    except (SyntaxError, ValueError):
        pass


def _add_test_config(form_data: dict, test_name: str, container):
    """Add a new test configuration."""
    form_data[test_name] = {
        "limits": {
            test_name: {
                "low": None,
                "high": None,
                "nominal": None,
                "units": "",
            }
        }
    }
    ui.notify(f"Added configuration for {test_name}", type="positive")
    # Refresh the container
    container.update()


def _delete_test_config(form_data: dict, test_name: str, refresh_callback):
    """Delete a test configuration."""
    if test_name in form_data:
        del form_data[test_name]
        refresh_callback()
