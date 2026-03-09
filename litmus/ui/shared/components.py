"""Reusable UI components.

This module contains shared UI components used across pages.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from nicegui import ui

if TYPE_CHECKING:
    from litmus.connect import StationConnection


def format_datetime(dt: datetime | str | None) -> str:
    """Format datetime for display."""
    if not dt:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(dt)[:16]


def render_empty_card(container: Any, title: str, message: str) -> None:
    """Render an empty-state card with title and message."""
    with container:
        with ui.card().classes("w-full"):
            ui.label(title).classes("text-lg font-semibold mb-4")
            ui.label(message).classes("text-slate-500 italic")


class AutoSaver:
    """Debounced auto-save for forms.

    Usage:
        saver = AutoSaver(save_fn, delay=1.0)

        # Bind to form fields
        ui.input(...).on('update:model-value', saver.trigger)

        # Or call directly
        saver.trigger()
    """

    def __init__(
        self,
        save_fn: Callable[[], Any],
        delay: float = 1.0,
        on_error: Callable[[Exception], None] | None = None,
    ):
        self.save_fn = save_fn
        self.delay = delay
        self.on_error = on_error or (lambda e: ui.notify(f"Save failed: {e}", type="negative"))
        self._timer: ui.timer | None = None
        self._dirty = False

    def trigger(self, *_args: Any) -> None:
        """Mark as dirty and schedule save."""
        self._dirty = True
        if self._timer:
            self._timer.cancel()
        self._timer = ui.timer(self.delay, self._do_save, once=True)

    def _do_save(self) -> None:
        """Execute save if dirty."""
        if not self._dirty:
            return
        try:
            self.save_fn()
            self._dirty = False
        except Exception as e:
            self.on_error(e)

    def save_now(self) -> None:
        """Save immediately without delay."""
        if self._timer:
            self._timer.cancel()
        self._dirty = True
        self._do_save()


def _format_range(r: dict[str, Any], units: str = "") -> str:
    """Format a min/max range dict as a human-readable string."""
    u = r.get("units") or units
    rmin, rmax = r.get("min"), r.get("max")
    if rmin is not None and rmax is not None:
        return f"{rmin}–{rmax} {u}".strip()
    if rmin is not None:
        return f"≥ {rmin} {u}".strip()
    if rmax is not None:
        return f"≤ {rmax} {u}".strip()
    return ""


def render_capability_detail(cap: dict[str, Any]) -> None:
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
                    fmt = _format_range(r, units)
                    if fmt:
                        parts.append(fmt)
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
                    fmt = _format_range(r)
                    if fmt:
                        ui.label(f"  {name}: {fmt}").classes(
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
                    fmt = _format_range(r)
                    if fmt:
                        parts.append(fmt)
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


# ---------------------------------------------------------------------------
# Table helpers — reduce boilerplate for styled Quasar tables
# ---------------------------------------------------------------------------

STICKY_TABLE_CSS = """
.litmus-sticky-table {
    overflow: auto;
}
.litmus-sticky-table tr th {
    position: sticky;
    top: 0;
    z-index: 2;
    background: white;
}
"""


def table_col(
    name: str,
    label: str = "",
    *,
    width: str | None = None,
    align: str = "left",
) -> dict:
    """Build a column definition dict for ``ui.table``."""
    col: dict[str, Any] = {
        "name": name,
        "label": label or name.title(),
        "field": name,
        "align": align,
    }
    if width:
        col["style"] = f"width: {width}"
    return col


def table_cell_slot(table: ui.table, col: str, css_class: str) -> None:
    """Add a simple styled cell slot to a table.

    For cells that just wrap the value in a ``<span class="...">``::

        table_cell_slot(table, "time", "cell-muted")
    """
    table.add_slot(f"body-cell-{col}", f"""
        <q-td :props="props">
            <span class="{css_class}">{{{{ props.value }}}}</span>
        </q-td>
    """)


def litmus_table(
    columns: list[dict],
    rows: list[dict] | None = None,
    *,
    row_key: str = "idx",
    per_page: int = 5,
) -> ui.table:
    """Create a dense flat table with standard litmus styling.

    Pass ``per_page=0`` to show all rows (no pagination limit).
    """
    pagination = {"rowsPerPage": 0} if per_page == 0 else {"rowsPerPage": per_page}
    return ui.table(
        columns=columns,
        rows=rows or [],
        row_key=row_key,
        pagination=pagination,
    ).classes("w-full litmus-table").props("dense flat hide-pagination")


# ---------------------------------------------------------------------------
# InstrumentToggle — connect/disconnect button for an instrument role
# ---------------------------------------------------------------------------


class InstrumentToggle:
    """Connect/disconnect button for an instrument role.

    Subscribes to cross-process EventStore events to show when another
    session has the instrument in use (disables the connect button).

    Usage::

        toggle = InstrumentToggle(station, "psu")
        # In a click handler:
        if not toggle.ensure():
            return
        psu = toggle.driver
    """

    def __init__(self, station: StationConnection, role: str) -> None:
        from litmus.ui.shared.event_binding import ui_subscribe

        self.role = role
        self._station = station
        self._my_session = str(station.session_id)
        # Sessions (other than ours) that have this role connected
        self._other_sessions: set[str] = set()
        self._btn = ui.button("Connect", on_click=self._toggle)
        self._btn.props("color=primary dense")
        self._sync()

        # Subscribe to cross-process instrument events
        if station.event_store is not None:
            self._unsub = ui_subscribe(
                station.event_store, self._on_instrument_event,
            )
        else:
            self._unsub = None

    def _on_instrument_event(self, evt: dict) -> None:
        et = evt.get("event_type", "")
        sid = str(evt.get("session_id", ""))
        if sid == self._my_session:
            return  # Our own events — _sync handles local state

        if et == "session.ended":
            # Other session ended — clear any in-use state for it
            if sid in self._other_sessions:
                self._other_sessions.discard(sid)
                self._sync()
            return

        if evt.get("role") != self.role:
            return

        if et == "fixture.instrument_connected":
            self._other_sessions.add(sid)
            self._sync()
        elif et == "fixture.instrument_disconnected":
            self._other_sessions.discard(sid)
            self._sync()

    @property
    def in_use(self) -> bool:
        """True if another session has this instrument connected."""
        return len(self._other_sessions) > 0

    @property
    def connected(self) -> bool:
        return self.role in self._station.instruments

    @property
    def driver(self) -> Any:
        return self._station.instruments[self.role]

    def ensure(self) -> bool:
        """Connect if needed. Returns True if connected."""
        if self.connected:
            return True
        try:
            self._station.instrument(self.role)
            self._sync()
            return True
        except Exception as e:
            ui.notify(f"Connection failed: {e}", type="negative")
            return False

    def _toggle(self) -> None:
        if self.connected:
            self._station.release(self.role)
        else:
            try:
                self._station.instrument(self.role)
            except Exception as e:
                ui.notify(f"Connection failed: {e}", type="negative")
        self._sync()

    def _sync(self) -> None:
        on = self.connected
        in_use = self.in_use
        if in_use and not on:
            self._btn.text = "In Use"
            self._btn.props(
                remove="color=primary color=red",
                add="color=amber",
            )
            self._btn.disable()
        else:
            self._btn.enable()
            self._btn.text = "Disconnect" if on else "Connect"
            self._btn.props(
                remove="color=primary color=red color=amber",
                add="color=red" if on else "color=primary",
            )


def setup_hash_sync_for_tabs(tabs: ui.tabs, tab_names: list[str]) -> None:
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
    def on_tab_change(e: Any) -> None:
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
