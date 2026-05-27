"""Profiles list page — one row per configured profile."""

from nicegui import ui

from litmus.ui.shared.components import data_table, page_layout
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_profiles


@ui.page("/profiles")
def profiles_page():
    """Profiles list — every profile declared in ``litmus.yaml`` or under
    ``profiles/*.yaml``. Click a row for the detail view.

    Profiles are config-only today — the runs parquet doesn't carry the
    profile that was active for a given run, so the merged-with-badge
    pattern doesn't apply here yet.
    """
    create_layout("Profiles")

    rows_data = discover_profiles()

    with page_layout():
        with (
            ui.row()
            .classes("items-center justify-between w-full")
            .props('data-testid="profiles-header"')
        ):
            with ui.row().classes("items-center gap-2"):
                ui.icon("layers").classes("text-slate-600")
                ui.label("Profiles").classes("text-lg font-semibold text-slate-700")
                ui.badge(f"{len(rows_data)} profiles").props("outline")

        if not rows_data:
            with ui.card().classes("w-full p-6 text-center"):
                ui.icon("layers").classes("text-4xl text-slate-300")
                ui.label("No profiles configured.").classes("text-slate-500 mt-2")
                ui.label(
                    "Add profile YAML files under profiles/ or declare them inline "
                    "in litmus.yaml. See the profiles reference for the schema."
                ).classes("text-sm text-slate-400")
            return

        columns = [
            {"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True},
            {
                "name": "extends",
                "label": "Extends",
                "field": "extends",
                "align": "left",
                "sortable": True,
            },
            {
                "name": "station_type",
                "label": "Station Type",
                "field": "station_type",
                "align": "left",
                "sortable": True,
            },
            {"name": "fixture", "label": "Fixture", "field": "fixture", "align": "left"},
            {"name": "facets", "label": "Facets", "field": "facets", "align": "left"},
            {
                "name": "tests_count",
                "label": "Tests",
                "field": "tests_count",
                "align": "right",
                "sortable": True,
            },
        ]
        rows = [
            {
                "name": r.name,
                "extends": r.extends or "—",
                "station_type": r.station_type or "—",
                "fixture": r.fixture or "—",
                "facets": r.facets or "—",
                "tests_count": r.tests_count,
            }
            for r in rows_data
        ]
        data_table(
            columns=columns,
            rows=rows,
            row_key="name",
            on_row_click=lambda r: ui.navigate.to(f"/profiles/{r['name']}"),
        ).props('data-testid="profiles-table"')
