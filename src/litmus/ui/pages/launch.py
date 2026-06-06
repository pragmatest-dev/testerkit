"""Launch test page."""

import logging

from nicegui import ui

from litmus.api.models import LaunchRequest
from litmus.api.runner import get_runner
from litmus.ui.shared.components import push_url_state
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    discover_products,
    discover_profiles,
    discover_stations,
    discover_tests,
    get_compatible_stations_for_product,
)

logger = logging.getLogger(__name__)


@ui.page("/launch")
def launch_page(product: str = "", station: str = "", test_profile: str = "", mock: str = ""):
    """Test launch page.

    Args:
        product: Pre-fill product ID from query param
        station: Pre-fill station ID from query param
        test_profile: Pre-fill profile name from query param. Set by the
            ``/profiles/{name}`` Launch button so a profile-driven run
            opens with the right profile already chosen.
        mock: Pre-fill mock checkbox ("1" = checked)
    """
    create_layout("Launch Test")

    products = discover_products()
    all_stations = discover_stations()
    tests = discover_tests()
    profiles = discover_profiles()

    # Form state - use dict for NiceGUI binding
    # Pre-fill from query params if provided
    form = {
        "product_id": product,
        "dut_serial": "",
        "test_path": "",
        "station_id": station,
        "test_profile": test_profile,
        "operator": "",
        "mock": mock == "1",
    }

    # Reactive state for filtered stations
    station_options = {}
    station_select = None
    station_hint = None

    def update_station_options():
        """Update station dropdown based on selected product."""
        nonlocal station_options
        compatible: list | None = None
        # Display label rule: friendly name only (operator-facing). The
        # internal station_id stays as the form value so the backend
        # can identify the target station; it is NOT shown in the
        # dropdown text per the "no station_id in operator UI" rule.
        if form["product_id"]:
            compatible = get_compatible_stations_for_product(form["product_id"])
            if compatible:
                station_options = {s["id"]: s["name"] or s["id"] for s in compatible}
            else:
                # No compatible stations - show all with warning
                station_options = {s.id: s.name or s.id for s in all_stations}
        else:
            # No product selected - show all stations
            station_options = {s.id: s.name or s.id for s in all_stations}

        if station_select:
            station_select.options = station_options
            station_select.update()

        # Update hint
        if station_hint:
            if not form["product_id"]:
                station_hint.text = "Showing all stations. Select a product to filter."
                station_hint.classes(replace="text-xs text-slate-500")
            elif compatible:
                station_hint.text = f"{len(compatible)} compatible station(s)"
                station_hint.classes(replace="text-xs text-emerald-600")
            else:
                station_hint.text = "No compatible stations - consider mock mode"
                station_hint.classes(replace="text-xs text-amber-600")
            station_hint.update()

    # Initialize station options
    update_station_options()

    def _mirror_to_url() -> None:
        """Mirror the current form's URL-shareable fields back to the URL.

        Lets an operator open `/launch?test_profile=smoke`, change the
        dropdown, and share the resulting URL — same bookmarkability
        rule the filter pages follow. Form-only fields (DUT serial,
        operator name) are intentionally NOT mirrored: they belong to
        the run instance, not the launch configuration.
        """
        push_url_state(
            "/launch",
            {
                "product": form["product_id"],
                "station": form["station_id"],
                "test_profile": form["test_profile"],
                "mock": "1" if form["mock"] else "",
            },
        )

    async def submit_launch():
        if not form["dut_serial"] or not form["station_id"]:
            ui.notify("Please fill in required fields", type="warning")
            return
        if not form["test_path"]:
            ui.notify("Select a test directory", type="warning")
            return

        request = LaunchRequest(
            product_id=form["product_id"] or None,
            dut_serial=form["dut_serial"],
            station_id=form["station_id"],
            test_path=form["test_path"] or "tests",
            test_profile=form["test_profile"] or None,
            operator=form["operator"] or None,
            mock_instruments=form["mock"],
        )
        runner = get_runner()
        try:
            run_id = await runner.start(request)
        except (OSError, ValueError, RuntimeError) as exc:
            logger.exception("Failed to start test run")
            ui.notify(f"Failed to start test run: {exc}", type="negative")
            return
        ui.navigate.to(f"/live/{run_id}")

    # data-testid attribute is a stable selector for the
    # screenshot-regeneration script (scripts/regenerate-ui-
    # screenshots.py). Don't drop it without updating that script's
    # MANIFEST.
    with ui.column().classes("w-full max-w-xl p-6 gap-6"):
        with ui.card().classes("w-full").props('data-testid="launch-form"'):
            with ui.card_section():
                ui.label("Test Configuration").classes("text-lg font-semibold")

            with ui.card_section().classes("flex flex-col gap-4"):
                # 1. Product selection (first)
                with ui.column().classes("gap-1"):
                    ui.label("Product").classes("text-sm font-medium text-slate-700")
                    ui.select(
                        options={p["id"]: p["name"] for p in products},
                    ).bind_value(form, "product_id").on_value_change(
                        lambda _: (update_station_options(), _mirror_to_url())
                    ).classes("w-full").props("outlined dense clearable")

                # 2. DUT Serial
                _labeled_input(form, "dut_serial", "DUT Serial Number", "e.g., DPB001-0001")

                # 3. Test path
                ui.separator().classes("my-2")
                with ui.column().classes("gap-1"):
                    ui.label("Test Path").classes("text-sm font-medium text-slate-700")
                    ui.select(
                        options={t["path"]: f"{t['name']} ({t['path']})" for t in tests},
                    ).bind_value(form, "test_path").classes("w-full").props(
                        "outlined dense clearable"
                    )

                ui.separator().classes("my-2")

                # 4. Station (filtered by product)
                with ui.column().classes("gap-1"):
                    ui.label("Station").classes("text-sm font-medium text-slate-700")
                    station_select = (
                        ui.select(
                            options=station_options,
                        )
                        .bind_value(form, "station_id")
                        .on_value_change(lambda _: _mirror_to_url())
                        .classes("w-full")
                        .props("outlined dense")
                    )
                    station_hint = ui.label(
                        "Showing all stations. Select a product to filter."
                    ).classes("text-xs text-slate-500")

                # 5. Profile (optional) — selects a litmus.yaml profile
                # for limit / fixture / sweep overrides. Empty = no
                # profile (use the test's own defaults). Pre-filled from
                # ?test_profile=<name> when navigated from /profiles/{name}.
                with ui.column().classes("gap-1"):
                    ui.label("Profile (optional)").classes("text-sm font-medium text-slate-700")
                    profile_options = {"": "(none)"}
                    for p in profiles:
                        profile_options[p.name] = p.name
                    # If the URL specifies a profile not in the discovered
                    # set, still allow it to round-trip as the form value
                    # — the run will fail visibly at pytest time instead
                    # of silently dropping the param.
                    if test_profile and test_profile not in profile_options:
                        profile_options[test_profile] = f"{test_profile} (unknown)"
                    ui.select(options=profile_options).bind_value(
                        form, "test_profile"
                    ).on_value_change(lambda _: _mirror_to_url()).classes("w-full").props(
                        "outlined dense clearable"
                    )

                # 6. Simulate checkbox
                with ui.row().classes("items-center gap-2 mt-2"):
                    ui.checkbox("Mock Hardware").bind_value(form, "mock").on_value_change(
                        lambda _: _mirror_to_url()
                    )
                    ui.label("Run without real instruments").classes("text-xs text-slate-500")

                ui.separator().classes("my-2")

                # 7. Operator (optional)
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
