"""Reusable UI components.

This module contains shared UI components used across pages.
"""

from nicegui import ui


def create_hash_tabs(
    tab_definitions: list[dict],
    default_tab: str | None = None,
) -> tuple:
    """Create tabs that sync with URL hash.

    Args:
        tab_definitions: List of dicts with 'name' and optional 'icon' keys.
            Example: [{"name": "Overview", "icon": "info"}, {"name": "Settings"}]
        default_tab: Name of the default tab. If None, uses first tab.

    Returns:
        Tuple of (tabs container, dict of tab name -> tab element, tab_panels element)

    Usage:
        tabs, tab_map, panels = create_hash_tabs([
            {"name": "Pins", "icon": "memory"},
            {"name": "Characteristics", "icon": "tune"},
        ])

        with panels:
            with ui.tab_panel(tab_map["Pins"]):
                ui.label("Pins content")
            with ui.tab_panel(tab_map["Characteristics"]):
                ui.label("Characteristics content")
    """
    if not tab_definitions:
        raise ValueError("tab_definitions cannot be empty")

    default_name = default_tab or tab_definitions[0]["name"]
    tab_map = {}

    # Create tabs container
    tabs = ui.tabs().classes("w-full")

    with tabs:
        for tab_def in tab_definitions:
            name = tab_def["name"]
            icon = tab_def.get("icon")
            tab = ui.tab(name, icon=icon) if icon else ui.tab(name)
            tab_map[name] = tab

    # Get the default tab element
    default_tab_element = tab_map.get(default_name, tab_map[tab_definitions[0]["name"]])

    # Create tab panels
    panels = ui.tab_panels(tabs, value=default_tab_element).classes("w-full")

    # JavaScript to read hash on load and sync tab
    tab_names = [t["name"] for t in tab_definitions]
    tab_names_js = ", ".join(f'"{n}"' for n in tab_names)

    # On tab change, update URL hash
    def on_tab_change(e):
        if e.value:
            tab_name = e.value.replace(" ", "-").lower()
            ui.run_javascript(f'window.location.hash = "{tab_name}";')

    tabs.on_value_change(on_tab_change)

    # Read initial hash and set tab
    ui.run_javascript(f'''
        (function() {{
            const hash = window.location.hash.slice(1);
            if (hash) {{
                const tabNames = [{tab_names_js}];
                const normalizedHash = hash.toLowerCase().replace(/-/g, " ");
                const matchedTab = tabNames.find(t => t.toLowerCase() === normalizedHash);
                if (matchedTab) {{
                    // Find and click the matching tab
                    const tabElements = document.querySelectorAll('.q-tab');
                    tabElements.forEach(el => {{
                        if (el.textContent.trim().toLowerCase()
                            .includes(matchedTab.toLowerCase())) {{
                            el.click();
                        }}
                    }});
                }}
            }}
        }})();
    ''')

    return tabs, tab_map, panels


def render_capability_detail(cap: dict):
    """Render read-only detail view for a capability's signals, conditions, controls, attributes.

    Args:
        cap: Capability dict (from model_dump or raw YAML) with optional keys:
             signals, conditions, controls, attributes, specs, units.
    """
    units = cap.get("units", "")

    # Signals
    signals = cap.get("signals", {})
    if signals:
        ui.label("Signals").classes("text-xs text-slate-500 uppercase font-semibold mt-2")
        for name, sig in signals.items():
            parts = [name]
            if isinstance(sig, dict):
                r = sig.get("range")
                if r and isinstance(r, dict):
                    u = r.get("units") or units
                    rmin, rmax = r.get("min"), r.get("max")
                    if rmin is not None and rmax is not None:
                        parts.append(f"{rmin}–{rmax} {u}".strip())
                    elif rmin is not None:
                        parts.append(f"≥ {rmin} {u}".strip())
                    elif rmax is not None:
                        parts.append(f"≤ {rmax} {u}".strip())
                v = sig.get("value")
                if v is not None:
                    parts.append(f"= {v}")
                acc = sig.get("accuracy")
                if acc and isinstance(acc, dict):
                    acc_parts = []
                    if acc.get("pct_reading") is not None:
                        acc_parts.append(f"±{acc['pct_reading']}% rdg")
                    if acc.get("pct_range") is not None:
                        acc_parts.append(f"±{acc['pct_range']}% rng")
                    if acc.get("absolute") is not None:
                        acc_parts.append(f"±{acc['absolute']}")
                    if acc_parts:
                        parts.append(f"({', '.join(acc_parts)})")
                res = sig.get("resolution")
                if res and isinstance(res, dict):
                    if res.get("digits") is not None:
                        parts.append(f"{res['digits']}½ digits")
                    elif res.get("bits") is not None:
                        parts.append(f"{res['bits']}-bit")
            ui.label(" · ".join(parts)).classes("text-sm font-mono ml-2")

    # Conditions
    conditions = cap.get("conditions", {})
    if conditions:
        ui.label("Conditions").classes("text-xs text-slate-500 uppercase font-semibold mt-2")
        for name, cond in conditions.items():
            if isinstance(cond, dict):
                r = cond.get("range")
                if r and isinstance(r, dict):
                    u = r.get("units", "")
                    rmin, rmax = r.get("min"), r.get("max")
                    if rmin is not None and rmax is not None:
                        ui.label(f"  {name}: {rmin}–{rmax} {u}".strip()).classes(
                            "text-sm font-mono ml-2"
                        )
                    elif rmin is not None:
                        ui.label(f"  {name}: ≥ {rmin} {u}".strip()).classes(
                            "text-sm font-mono ml-2"
                        )
                    elif rmax is not None:
                        ui.label(f"  {name}: ≤ {rmax} {u}".strip()).classes(
                            "text-sm font-mono ml-2"
                        )
                else:
                    ui.label(f"  {name}: {cond}").classes("text-sm font-mono ml-2")

    # Controls
    controls = cap.get("controls", {})
    if controls:
        ui.label("Controls").classes("text-xs text-slate-500 uppercase font-semibold mt-2")
        for name, ctrl in controls.items():
            if isinstance(ctrl, dict):
                parts = [name]
                if ctrl.get("default") is not None:
                    parts.append(f"default={ctrl['default']}")
                r = ctrl.get("range")
                if r and isinstance(r, dict):
                    u = r.get("units", "")
                    parts.append(f"{r.get('min', '')}–{r.get('max', '')} {u}".strip())
                opts = ctrl.get("options")
                if opts:
                    parts.append(f"options={opts}")
                ui.label(" · ".join(parts)).classes("text-sm font-mono ml-2")

    # Attributes
    attributes = cap.get("attributes", {})
    if attributes:
        ui.label("Attributes").classes("text-xs text-slate-500 uppercase font-semibold mt-2")
        for name, attr in attributes.items():
            if isinstance(attr, dict):
                val = attr.get("value", "")
                u = attr.get("units", "")
                ui.label(f"  {name}: {val} {u}".strip()).classes("text-sm font-mono ml-2")
            else:
                ui.label(f"  {name}: {attr}").classes("text-sm font-mono ml-2")


def setup_hash_sync_for_tabs(tabs, tab_names: list[str]):
    """Add hash sync behavior to existing tabs.

    Args:
        tabs: The ui.tabs() element
        tab_names: List of tab names in order

    Usage:
        with ui.tabs() as tabs:
            pins_tab = ui.tab("Pins", icon="memory")
            char_tab = ui.tab("Characteristics", icon="tune")

        setup_hash_sync_for_tabs(tabs, ["Pins", "Characteristics"])
    """
    tab_names_js = ", ".join(f'"{n}"' for n in tab_names)

    # On tab change, update URL hash
    def on_tab_change(e):
        if e.value:
            tab_name = e.value.replace(" ", "-").lower()
            ui.run_javascript(f'window.location.hash = "{tab_name}";')

    tabs.on_value_change(on_tab_change)

    # Read initial hash and set tab on page load
    ui.run_javascript(f'''
        (function() {{
            const hash = window.location.hash.slice(1);
            if (hash) {{
                const tabNames = [{tab_names_js}];
                const normalizedHash = hash.toLowerCase().replace(/-/g, " ");
                const matchedTab = tabNames.find(t => t.toLowerCase() === normalizedHash);
                if (matchedTab) {{
                    setTimeout(() => {{
                        const tabElements = document.querySelectorAll('.q-tab');
                        tabElements.forEach(el => {{
                            const label = el.querySelector('.q-tab__label');
                            if (label && label.textContent.trim()
                                .toLowerCase() === matchedTab.toLowerCase()) {{
                                el.click();
                            }}
                        }});
                    }}, 100);
                }}
            }}
        }})();
    ''')
