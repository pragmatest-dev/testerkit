"""Profile detail page — view one profile's resolved YAML + extends chain."""

import yaml
from nicegui import ui

from litmus.ui.shared.components import page_layout
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import discover_profiles, load_profile_config


@ui.page("/profiles/{name}")
def profile_detail_page(name: str):
    """One profile — header card + resolved YAML + extends-chain map.

    The YAML is the merged in-memory ProfileConfig (after extends-chain
    resolution at session start). Read-only — edits land via the
    file system (no in-browser editor yet).
    """
    create_layout(f"Profile · {name}")

    profile = load_profile_config(name)

    with page_layout():
        if profile is None:
            with ui.card().classes("w-full p-6 text-center"):
                ui.label(f"Profile '{name}' not found.").classes("text-xl text-slate-600")
                ui.link("← Back to Profiles", "/profiles").classes("text-blue-600 hover:underline")
            return

        # Header row
        with ui.row().classes("items-center justify-between w-full"):
            with ui.row().classes("items-center gap-2"):
                ui.icon("layers").classes("text-slate-600")
                ui.label(name).classes("text-lg font-semibold text-slate-700")
            ui.button(
                "Launch Test",
                icon="play_arrow",
                on_click=lambda: ui.navigate.to(f"/launch?test_profile={name}"),
            ).props("color=primary")

        # Summary card — extends, station_type, fixture, facets
        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Summary").classes("font-semibold")
            with ui.card_section(), ui.row().classes("gap-6 flex-wrap"):
                _meta_field("Extends", profile.extends or "—")
                _meta_field("Station type", profile.station_type or "—")
                _meta_field("Fixture", profile.fixture or "—")
                facets_str = ", ".join(f"{k}={v}" for k, v in (profile.facets or {}).items()) or "—"
                _meta_field("Facets", facets_str)
                _meta_field("Tests", str(len(profile.tests or {})))

        # Extends chain — walk parents recursively for the visual map
        chain = _resolve_extends_chain(name)
        if len(chain) > 1:
            with ui.card().classes("w-full"):
                with ui.card_section():
                    ui.label("Inheritance").classes("font-semibold")
                    ui.label(
                        "Parent profiles are applied first; this profile's "
                        "fields override the merged result."
                    ).classes("text-xs text-slate-500")
                with ui.card_section(), ui.row().classes("items-center gap-2"):
                    for i, link in enumerate(chain):
                        if i > 0:
                            ui.icon("arrow_forward", size="sm").classes("text-slate-400")
                        if link == name:
                            ui.badge(link, color="primary")
                        else:
                            ui.link(link, target=f"/profiles/{link}").classes("text-blue-600")

        # Resolved YAML — single source of truth for what this profile
        # actually configures
        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Resolved YAML").classes("font-semibold")
                ui.label("After the extends chain resolves at session start.").classes(
                    "text-xs text-slate-500"
                )
            with ui.card_section():
                yaml_text = yaml.safe_dump(
                    profile.model_dump(exclude_none=True),
                    sort_keys=False,
                    default_flow_style=False,
                )
                ui.code(yaml_text, language="yaml").classes("w-full")

        with ui.row().classes("mt-2"):
            ui.link("← Back to Profiles", "/profiles").classes("text-blue-600 hover:underline")


def _meta_field(label: str, value: str) -> None:
    with ui.column().classes("gap-0"):
        ui.label(label).classes("text-xs text-slate-500 uppercase")
        ui.label(value).classes("text-sm font-medium text-slate-800")


def _resolve_extends_chain(name: str) -> list[str]:
    """Walk the ``extends:`` chain from root → leaf. Stops if a parent
    isn't a known profile (silently — the runtime resolver would raise).
    """
    profiles = {p.name: p for p in discover_profiles()}
    chain: list[str] = []
    current = name
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        row = profiles.get(current)
        if not row or not row.extends:
            break
        current = row.extends
    return list(reversed(chain))
