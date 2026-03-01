"""Sequences list page."""

from nicegui import ui

from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_sequences


@ui.page("/sequences")
def sequences_page():
    """Sequences listing page."""
    create_layout("Test Sequences")

    sequences = discover_sequences()

    with ui.column().classes("w-full p-6 gap-6"):
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("list_alt").classes("text-slate-600")
                ui.label("Test Sequences").classes(
                    "text-lg font-semibold text-slate-700"
                )
            ui.button(
                "New Sequence",
                icon="add",
                on_click=lambda: ui.navigate.to("/sequences/new"),
            ).props("color=primary")

        if sequences:
            with ui.row().classes("gap-4 flex-wrap"):
                for seq in sequences:
                    _sequence_card(seq)
        else:
            with ui.card().classes("w-full p-6 text-center"):
                ui.icon("list_alt").classes("text-4xl text-slate-300")
                ui.label("No test sequences found.").classes("text-slate-500 mt-2")
                ui.label(
                    "Create a sequence to define the order and configuration of tests."
                ).classes("text-sm text-slate-400")
                ui.button(
                    "Create Sequence",
                    icon="add",
                    on_click=lambda: ui.navigate.to("/sequences/new"),
                ).classes("mt-4")


def _sequence_card(seq):
    """Render a sequence card for a TestSequenceConfig model."""
    step_count = len(seq.steps) if seq.steps else 0

    with ui.card().classes("w-96"):
        with ui.card_section():
            with ui.row().classes("items-start justify-between"):
                ui.label(seq.name or seq.id).classes("text-lg font-semibold")
                phase = seq.test_phase
                if phase:
                    phase_colors = {
                        "validation": "blue",
                        "characterization": "purple",
                        "production": "green",
                    }
                    ui.badge(phase, color=phase_colors.get(phase, "gray")).props("outline")

        with ui.card_section():
            ui.label(seq.description or "").classes("text-sm text-slate-600")
            with ui.row().classes("text-xs text-slate-500 gap-4 mt-3"):
                with ui.row().classes("items-center gap-1"):
                    ui.icon("tag", size="xs")
                    ui.label(seq.id)
                if seq.product_family:
                    with ui.row().classes("items-center gap-1"):
                        ui.icon("inventory_2", size="xs")
                        ui.link(
                            seq.product_family,
                            f"/products/{seq.product_family}",
                        ).classes("text-blue-600 hover:underline")

            with ui.row().classes("items-center gap-1 mt-2"):
                ui.icon("format_list_numbered", size="xs").classes("text-slate-400")
                ui.label(f"{step_count} steps").classes("text-sm text-slate-600")

        with ui.card_actions():
            ui.button(
                "View Details",
                icon="visibility",
                on_click=lambda s=seq: ui.navigate.to(f"/sequences/{s.id}"),
            ).props("flat")
            ui.button(
                "Run",
                icon="play_arrow",
                on_click=lambda s=seq: ui.navigate.to(f"/launch?sequence={s.id}"),
            ).props("flat color=primary")
