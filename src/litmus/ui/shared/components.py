"""Reusable UI components.

This module contains shared UI components used across pages.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from nicegui import ui

if TYPE_CHECKING:
    from litmus.connect import StationConnection


def format_datetime(dt: datetime | str | None) -> str:
    """Render a UTC timestamp as a browser-local-time string.

    Returns an HTML ``<span>`` with the raw ISO-8601 UTC value as
    ``data-utc``; the global formatter script in
    :func:`local_time_init_script` converts to the browser's locale
    on page load (and on Quasar table virtual-scroll, via a
    MutationObserver). Stored value stays UTC throughout the
    backend; only display is local.

    Returning HTML rather than a plain string lets the caller drop
    it straight into a label, table cell slot, or info_field
    without each site reinventing the JS hook.
    """
    if not dt:
        return ""
    if isinstance(dt, datetime):
        # Treat naive datetimes as UTC (the backend's storage
        # convention). Aware datetimes get normalized to UTC ISO.
        if dt.tzinfo is None:
            iso = dt.isoformat() + "Z"
        else:
            iso = dt.astimezone().isoformat()
    else:
        iso = str(dt)
    # Server-side fallback so text appears immediately even before
    # the JS converter runs (e.g. in non-JS contexts like reports).
    fallback = iso[:19].replace("T", " ")
    return f'<span class="litmus-time" data-utc="{iso}">{fallback}</span>'


def local_time_init_script() -> str:
    """Return the JS that converts every ``.litmus-time`` span on the
    page to browser-local-time.

    Runs once on DOMContentLoaded and re-runs on DOM mutations (so
    rows added by Quasar's virtual-scroll get formatted as they
    appear). Idempotent: each span is converted at most once,
    flagged via ``data-formatted``.
    """
    return """
    (() => {
      if (window.__litmus_time_init) return;
      window.__litmus_time_init = true;
      const pad = (n) => String(n).padStart(2, '0');
      const fmt = (el) => {
        if (el.dataset.formatted) return;
        const utc = el.dataset.utc;
        if (!utc) return;
        const d = new Date(utc);
        if (isNaN(d.getTime())) return;
        // Same ``YYYY-MM-DD HH:MM:SS`` format we'd produce server-side,
        // but in the browser's local timezone. Locale-default
        // formatting (toLocaleString) drifts to ``1/3/26, 11:39 AM``
        // which breaks scanning and sortability — keep ISO-ish.
        // Seconds matter for ordering bursts of events that land in
        // the same minute (especially in the Gantt / step list).
        el.textContent =
          d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
          + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes())
          + ':' + pad(d.getSeconds());
        el.title = utc;
        el.dataset.formatted = '1';
      };
      const sweep = () => {
        document.querySelectorAll('.litmus-time:not([data-formatted])').forEach(fmt);
      };
      sweep();
      const obs = new MutationObserver(() => sweep());
      obs.observe(document.body, {childList: true, subtree: true});
    })();
    """


def info_field(label: str, value: str) -> None:
    """Render a read-only label/value pair (small label on top, bold value below).

    ``value`` may be plain text or HTML — :func:`format_datetime`
    emits a ``<span class="litmus-time">`` so the browser-local
    formatter can rewrite it on load. ``ui.html`` accepts both.
    """
    with ui.column().classes("gap-1"):
        ui.label(label).classes("text-xs text-slate-500 uppercase")
        ui.html(value or "", sanitize=False).classes("font-semibold")


def page_header(
    title: str,
    *,
    icon: str | None = None,
    badge: str | None = None,
    actions: list[tuple[str, str, Callable[[], Any]]] | None = None,
) -> None:
    """Litmus design-system page-header strip.

    Standard layout for the top of every list / detail page —
    icon + title on the left, optional badge, optional action
    buttons on the right. Same shape across the site so users
    learn the pattern once.
    """
    actions = actions or []
    with ui.row().classes("items-center justify-between w-full"):
        with ui.row().classes("items-center gap-2"):
            if icon:
                ui.icon(icon).classes("text-slate-600")
            ui.label(title).classes("text-lg font-semibold text-slate-700")
            if badge:
                ui.badge(badge).props("outline")
        if actions:
            with ui.row().classes("items-center gap-2"):
                for label, action_icon, on_click in actions:
                    ui.button(label, icon=action_icon, on_click=on_click).props(
                        "color=primary dense"
                    )


def stat_card(value: str, label: str, color_class: str = "text-slate-700") -> None:
    """Litmus stat card — big value on top, small label below.

    Uniform shape for the KPI cards used in the run-detail
    Overview tab, dashboard summaries, etc. ``value`` may be plain
    text or HTML (e.g. a :func:`format_datetime` ``<span>`` that
    the global browser-local-time sweep formats on load).
    """
    with ui.column().classes("items-center"):
        ui.html(value, sanitize=False).classes(f"text-3xl font-bold {color_class}")
        ui.label(label).classes("text-sm text-slate-500")


def page_layout(*, padding: str = "p-6", gap: str = "gap-4"):
    """Litmus design-system page shell — viewport-bound flex column.

    Every page that hosts tables / scrollable panels uses this. The
    shell constrains the page area to the visible viewport (header
    excluded) so any ``data_table`` inside scrolls its own rows
    instead of pushing the rest of the page off-screen.

    Pages that flow naturally (docs, long forms) shouldn't use this
    — they should behave like a regular scrolling page.

    Usage::

        @ui.page("/results")
        def results_page():
            create_layout("Results")
            with page_layout():
                # banner, tabs, data_table, etc.
                ...
    """
    return ui.column().classes(f"litmus-page {padding} {gap}")


def info_field_link(label: str, value: str | None, base_path: str) -> None:
    """Render a read-only label/value pair where value is a link."""
    with ui.column().classes("gap-1"):
        ui.label(label).classes("text-xs text-slate-500 uppercase")
        if value:
            ui.link(value, f"{base_path}/{value}").classes(
                "font-semibold text-blue-600 hover:underline"
            )
        else:
            ui.label("-").classes("font-semibold")


# ---------------------------------------------------------------------------
# Summary banner — every detail page's pinned header strip
# ---------------------------------------------------------------------------

# Status → Tailwind classes for the colored badge in the summary
# banner and any status cell. Keyed by the *display status*
# (:func:`display_status`) — we add the derived ``running`` /
# ``waiting`` here so they share the same vocabulary as the
# past-tense outcomes.
_OUTCOME_BADGE_CLASSES: dict[str, str] = {
    "passed": "bg-emerald-100 text-emerald-800",
    "done": "bg-emerald-100 text-emerald-800",
    "failed": "bg-red-100 text-red-800",
    "errored": "bg-amber-100 text-amber-800",
    "terminated": "bg-amber-100 text-amber-800",
    "aborted": "bg-red-100 text-red-800",
    "skipped": "bg-slate-100 text-slate-700",
    "waiting": "bg-slate-100 text-slate-700",
    "never ran": "bg-slate-100 text-slate-700",
    "running": "bg-blue-100 text-blue-800",
}


def status_chip_classes(status: str) -> str:
    """Return the Tailwind chip classes for a display status string.

    Use after :func:`display_status` to color the badge: the input
    is the same lowercased display label (``passed``, ``running``,
    ``waiting``, etc.) the function emits.
    """
    return _OUTCOME_BADGE_CLASSES.get(status.lower(), "bg-slate-100 text-slate-700")


def display_status(
    *,
    started_at,
    ended_at,
    outcome: str | None,
    parent_ended_at=None,
) -> str:
    """Compute the display status from raw row fields.

    Outcomes are past-tense by construction (the row reflects what
    happened). For a row that's still in flight or never reached, we
    derive a present-tense display label without polluting the
    ``Outcome`` enum:

    * ``Running`` — ``started_at`` set, ``ended_at`` not set.
      In flight right now.
    * ``Waiting`` — outcome ``planned`` and the parent run is still
      in flight (``parent_ended_at`` is None). Queued, hasn't had
      its turn yet.
    * ``Planned`` — outcome ``planned`` and the parent run already
      finished. The step never ran.
    * Otherwise — the past-tense outcome, title-cased.

    Pass ``parent_ended_at`` only for step rows; runs are top-level
    and don't have a parent.
    """
    if started_at and not ended_at:
        return "Running"
    if not outcome:
        # outcome=None at finalize means the row was collected but
        # never ran — display "Never Ran" once the parent run has
        # finalized; otherwise the row is still queued ("Waiting").
        return "Waiting" if parent_ended_at is None else "Never Ran"
    return outcome.title()


def render_summary_banner(
    title: str,
    *,
    badge: str | None = None,
    fields: list[tuple[str, str, str | None]] | None = None,
    actions: list[tuple[str, str, Callable[[], Any]]] | None = None,
    sticky: bool = True,
) -> None:
    """Render the pinned header strip every detail page uses.

    Args:
        title: The big-text identifier — "Test Run Summary",
            "Station bench-01", etc.
        badge: Optional outcome label. Mapped to a Tailwind chip
            via :data:`_OUTCOME_BADGE_CLASSES`; unknown values get
            the neutral chip.
        fields: ``(label, value, link_base or None)`` triples. A
            non-empty ``link_base`` renders as a link to
            ``{link_base}/{value}``; otherwise plain text. Rows
            wrap so wide screens collapse to one line.
        actions: ``(label, icon, on_click)`` triples — buttons
            anchored to the right of the title row (Back, Edit,
            etc.). All flat+dense.
        sticky: Pin the banner to the top of the scrollable
            content area (default). Set False for non-tabbed
            pages where pinning isn't needed.
    """
    fields = fields or []
    actions = actions or []
    sticky_classes = " sticky top-0 z-10 bg-white" if sticky else ""
    with ui.card().classes(f"w-full{sticky_classes}"):
        with ui.card_section().classes("py-2 px-3"):
            with ui.row().classes("items-center justify-between w-full"):
                with ui.row().classes("items-center gap-3"):
                    ui.label(title).classes("text-base font-semibold")
                    if badge is not None:
                        chip = _OUTCOME_BADGE_CLASSES.get(badge, "bg-slate-100 text-slate-700")
                        ui.label(badge.upper()).classes(
                            f"px-2 py-0.5 rounded text-xs font-medium {chip}"
                        )
                if actions:
                    with ui.row().classes("items-center gap-1"):
                        for label, icon, on_click in actions:
                            ui.button(label, icon=icon, on_click=on_click).props("flat dense")

        if fields:
            with ui.card_section().classes("py-2 px-3"):
                with ui.row().classes("flex-wrap gap-x-10 gap-y-2 w-full"):
                    for label, value, link_base in fields:
                        if link_base:
                            info_field_link(label, value or "", link_base)
                        else:
                            info_field(label, value or "")


def labeled_input(
    label: str,
    value: str = "",
    *,
    readonly: bool = False,
    on_change: Callable[..., Any] | None = None,
    placeholder: str = "",
) -> ui.input:
    """Render a labeled text input that grows to fill its parent column."""
    with ui.column().classes("gap-1 flex-1"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        props = "outlined dense"
        if readonly:
            props += " readonly"
        return (
            ui.input(value=value, placeholder=placeholder, on_change=on_change)
            .props(props)
            .classes("w-full")
        )


def labeled_textarea(
    label: str,
    value: str = "",
    *,
    on_change: Callable[..., Any] | None = None,
) -> ui.textarea:
    """Render a labeled textarea."""
    with ui.column().classes("gap-1 w-full"):
        ui.label(label).classes("text-sm font-medium text-slate-700")
        return (
            ui.textarea(value=value, on_change=on_change).props("outlined dense").classes("w-full")
        )


_RESOURCE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$"


def validate_resource_id(
    value: str,
    existing_ids: set[str],
    entity_label: str,
    *,
    pattern: str = _RESOURCE_ID_PATTERN,
    pattern_message: str = (
        "Must start/end with letter or number, only contain letters, numbers, hyphens"
    ),
) -> str:
    """Validate a resource ID against a regex pattern and uniqueness.

    Returns the error message, or an empty string if valid. Callers pass the
    message directly to a reactive error field.
    """
    if not value:
        return f"{entity_label} is required"
    if not re.match(pattern, value):
        return pattern_message
    if value in existing_ids:
        return f"{entity_label} already exists"
    return ""


def render_empty_card(container: Any, title: str, message: str) -> None:
    """Render an empty-state card with title and message."""
    with container:
        with ui.card().classes("w-full"):
            ui.label(title).classes("text-lg font-semibold mb-4")
            ui.label(message).classes("text-slate-500 italic")


def render_skeleton(container: Any, height: str = "h-32") -> None:
    """Drop a Tailwind-pulse skeleton into container.

    Call before issuing any data fetch so the user sees the layout
    shape immediately. Caller clears + replaces when real data lands.
    """
    container.clear()
    with container, ui.card().classes("w-full"), ui.card_section():
        ui.element("div").classes(f"animate-pulse bg-slate-200 rounded {height} w-full")


# Sentinel values that mean "no filter active" — these never get
# written to the URL even when the widget holds them.
_URL_STATE_OMIT_VALUES: tuple[Any, ...] = (None, "", "(any)", "All", 0, "0")


def subscribe_with_refresh(
    event_store: Any,
    event_types: list[str],
    refresh: Callable[[], Any],
    *,
    debounce_seconds: float = 0.25,
) -> Callable[[], None]:
    """Subscribe to ``event_types`` and debounce-call ``refresh`` on each.

    Replaces ``ui.timer(seconds, refresh)`` polling on pages where
    a fresh-state cue is event-driven. Coalesces bursts (a multi-slot
    session fires N RunStarted events back-to-back) so ``refresh``
    runs at most once per ``debounce_seconds`` instead of per event.

    Args:
        event_store: A :class:`litmus.data.event_store.EventStore`
            instance scoped to the same results dir the page reads.
        event_types: Event type strings the page cares about, e.g.
            ``["run.started", "run.ended"]``. One subscription per
            type — single-type filter on the EventStore side avoids
            piping every event through the UI thread just to drop it.
        refresh: Callable with no args; the page's existing refresh
            function. Runs on the NiceGUI event loop (safe to mutate
            UI state).
        debounce_seconds: Minimum gap between successive ``refresh``
            calls. Default 0.25s — fast enough to feel live, slow
            enough that a 50-event burst lands as one refresh.

    Returns:
        Unsubscribe callable. The page should call it on disconnect
        to release the subscriptions and the pending timer.
    """
    import asyncio as _asyncio  # used by loop.call_later in _on_event
    import time as _time

    from litmus.ui.shared.event_binding import ui_subscribe

    # Capture the NiceGUI client at subscription time (inside the page handler).
    # Used to restore slot context when scheduling async refresh from background
    # threads (EventStore callbacks). Without this, ui.run_javascript raises
    # "slot stack is empty" because ensure_future creates a context-free Task.
    _client: Any = None
    try:
        from nicegui import context as _ctx

        _client = _ctx.client
    except RuntimeError:
        pass

    state: dict[str, Any] = {"last_run": 0.0, "pending": False}
    loop = _asyncio.get_event_loop()

    def _trigger() -> None:
        state["pending"] = False
        state["last_run"] = _time.monotonic()
        try:
            if inspect.iscoroutinefunction(refresh):
                if _client is not None:
                    # Wrap in captured client context so ui.run_javascript works.
                    # call_soon_threadsafe is safe from both event-loop and
                    # background-thread callers.
                    async def _in_ctx() -> None:
                        with _client:
                            await refresh()

                    loop.call_soon_threadsafe(lambda: _asyncio.ensure_future(_in_ctx()))
                else:
                    # No NiceGUI context (tests, CLI). Best-effort schedule.
                    loop.call_soon_threadsafe(lambda: _asyncio.ensure_future(refresh()))
            else:
                # Sync refresh: call directly.
                refresh()
        except Exception:  # noqa: BLE001 — never let a broken refresh kill the subscription
            pass

    def _on_event(_evt: dict) -> None:
        now = _time.monotonic()
        gap = now - state["last_run"]
        if gap >= debounce_seconds:
            _trigger()
            return
        if state["pending"]:
            return
        # Schedule a deferred refresh that lands at the end of the
        # debounce window, so a burst collapses to one tick.
        state["pending"] = True
        delay = debounce_seconds - gap
        loop.call_later(delay, _trigger)

    unsubs: list[Callable[[], None]] = []
    for event_type in event_types:
        unsubs.append(ui_subscribe(event_store, _on_event, event_type=event_type))

    def _cleanup() -> None:
        for unsub in unsubs:
            try:
                unsub()
            except Exception:  # noqa: BLE001
                pass

    # Auto-unsubscribe when this client (page) disconnects — fires on
    # tab close AND on internal navigation away from the page (after
    # reconnect_timeout). Without this, every page navigation leaks
    # event subscribers; over a session, each event fires N stale
    # callbacks pointing at deleted UI elements.
    # Only registers if called from inside a NiceGUI page handler;
    # in tests / non-UI contexts the cleanup callable is the
    # caller's responsibility.
    try:
        from nicegui import context as _ctx

        _ctx.client.on_disconnect(_cleanup)
    except RuntimeError:
        # No client context (test, background thread, etc.). Caller
        # uses the returned _cleanup directly.
        pass

    return _cleanup


def multi_select_filter(
    label: str,
    options: list[str] | dict[str, str],
    value: list[str] | str | None,
    on_change: Callable[[list[str]], Any],
    *,
    placeholder: str = "All",
    classes: str = "w-56",
) -> ui.select:
    """Canonical filter widget — multi-select, autocomplete, chip display.

    Every filter on every page goes through this helper. Single-
    select dropdowns and free-text inputs for facets that have a
    known value set are an anti-pattern: an operator can't pick
    "Phase=production AND Phase=qual" with a single-select, and
    typing a free-text value invites typos.

    Quasar's ``q-select`` with ``multiple use-chips with-input``:

    * **Multi-select** — pick zero, one, or many values. The
      callback receives the *list* (empty list = "no filter,
      match all").
    * **Filter-while-typing** — operator types to narrow the
      dropdown ("dmm" filters to dmm-prefixed options).
    * **Chip display** — selected values render as removable
      chips inside the input so the operator sees their picks
      at a glance.

    Args:
        label: Field label shown above the input.
        options: Either a flat list of value strings, or a
            ``{value: display_label}`` map for human-readable
            labels with raw values still queryable.
        value: Initial selection — list of values, single string,
            or None.
        on_change: Callback receiving the selected list. Empty
            list means "no filter applied"; the page should pass
            ``None`` to its query method in that case.
        placeholder: Empty-state hint inside the input.
        classes: Tailwind class string for sizing. Default ``w-56``.

    Returns:
        The underlying ``ui.select`` so callers can attach extra
        bindings if needed (rarely).
    """
    if isinstance(value, str):
        initial: list[str] = [value] if value else []
    elif value is None:
        initial = []
    else:
        initial = list(value)

    sel = (
        ui.select(
            options,  # type: ignore[arg-type]
            multiple=True,
            value=initial,
            label=label,
            with_input=True,
            on_change=lambda e: on_change(list(e.value or [])),
        )
        .classes(classes)
        .props(f'use-chips dense outlined clearable hint="{placeholder}"')
    )
    return sel


def push_url_state(path: str, params: dict[str, Any]) -> None:
    """Mirror filter state into the URL via ``history.replaceState``.

    Single source of truth for "page filter changed → reflect in
    URL". Used by every page with filters so deep links are
    bookmarkable and shareable.

    ``params`` keys map to query-string keys; values are either a
    bare scalar or a list. Sentinel "no-filter" values
    (``None``, empty string, ``"(any)"``, ``"All"``, ``0``) are
    omitted from the URL so a default-state page stays at its bare
    URL instead of carrying noise.

    List values render as repeated keys (``?k=a&k=b``) — matching
    FastAPI's ``query_params.getlist`` decoding so the page can
    round-trip multi-value filters cleanly.
    """
    import json as _json
    from urllib.parse import quote

    parts: list[str] = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            for item in value:
                if item in _URL_STATE_OMIT_VALUES:
                    continue
                parts.append(f"{quote(key)}={quote(str(item))}")
        else:
            if value in _URL_STATE_OMIT_VALUES:
                continue
            parts.append(f"{quote(key)}={quote(str(value))}")

    qs = "&".join(parts)
    new_url = f"{path}{'?' + qs if qs else ''}"
    ui.run_javascript(f"history.replaceState(null, '', {_json.dumps(new_url)})")


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
        except (OSError, ValueError, RuntimeError) as e:
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
                        ui.label(f"  {name}: {fmt}").classes("text-sm font-mono ml-2")
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
    table.add_slot(
        f"body-cell-{col}",
        f"""
        <q-td :props="props">
            <span class="{css_class}">{{{{ props.value }}}}</span>
        </q-td>
    """,
    )


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
    return (
        ui.table(
            columns=columns,
            rows=rows or [],
            row_key=row_key,
            pagination=pagination,
        )
        .classes("w-full litmus-table")
        .props("dense flat hide-pagination")
    )


def attach_status_chip(table: ui.table, column: str = "status") -> None:
    """Render a colored chip for any table column whose value is a
    display status (``passed``, ``running``, ``waiting``, …).

    Each row in the table needs two fields:

    * ``<column>`` — the human-readable status label
      (e.g. ``"Passed"``, ``"Running"``)
    * ``<column>_class`` — the Tailwind classes from
      :func:`status_chip_classes`

    Use :func:`status_row_fields` to build both at once.
    """
    # Vue's mustache ``{{ ... }}`` collides with ``str.format``'s
    # placeholder syntax — ``.format()`` would collapse the doubles
    # to single braces and Vue would never interpolate. Build the
    # template via concat so the mustaches reach Vue intact.
    template = (
        '<q-td :props="props">'
        f'<span :class="props.row.{column}_class" '
        'class="px-2 py-0.5 rounded text-xs font-medium">'
        "{{ props.value }}"
        "</span>"
        "</q-td>"
    )
    table.add_slot(f"body-cell-{column}", template)


def status_row_fields(
    *,
    started_at,
    ended_at,
    outcome: str | None,
    parent_ended_at=None,
    column: str = "status",
) -> dict[str, str]:
    """Build the ``<column>`` + ``<column>_class`` fields for a row.

    One call covers what every row needs to render through
    :func:`attach_status_chip` — the human label and its color
    classes, computed via :func:`display_status` and
    :func:`status_chip_classes` so the rest of the codebase doesn't
    repeat the lookup.
    """
    label = display_status(
        started_at=started_at,
        ended_at=ended_at,
        outcome=outcome,
        parent_ended_at=parent_ended_at,
    )
    return {
        column: label,
        f"{column}_class": status_chip_classes(label),
    }


def data_table(
    columns: list[dict],
    rows: list[dict],
    *,
    row_key: str,
    on_row_click: Callable[[dict], None] | None = None,
    time_columns: list[str] | None = None,
    total_rows: int | None = None,
    fetch_page: Callable[[int, int], tuple[list[dict], int]] | None = None,
) -> ui.table:
    """Canonical Litmus table — every list view goes through this.

    Always fills the available content area, scrolls internally with
    a sticky header, and uses Quasar's virtual-scroll for large
    datasets. The page (or tab panel) just needs to be a flex
    column — global.css makes every ``.q-page`` exactly that, so
    callers don't need to set up any container plumbing.

    Single shared component: any improvement here — chip filters,
    row-detail panels, column visibility menu, export, etc. — lands
    everywhere at once. Page authors don't choose layout, they pick
    columns + rows.

    Args:
        columns: Quasar q-table column defs.
        rows: Row dicts.
        row_key: Unique-per-row column name.
        on_row_click: Optional ``(row_dict) -> None`` callback. The
            wrapper handles Quasar event-args unwrapping.
        time_columns: Names of columns whose cell values are HTML
            from :func:`format_datetime` (i.e. ``<span class="litmus-time">``
            wrappers). Quasar renders cells as text by default, so
            we install a ``v-html`` body-cell slot for these so the
            global browser-local-time sweep can pick them up.

    Returns the underlying ``ui.table`` so callers can attach extra
    slots (badge cells, etc.) when needed.
    """
    # Server-side pagination per the Quasar QTable contract:
    # https://quasar.dev/vue-components/table/#server-side-pagination
    # https://nicegui.io/documentation/table
    #
    # ``pagination`` carries ``page`` / ``rowsPerPage`` /
    # ``rowsNumber``. Setting ``rowsNumber`` flips QTable into
    # server-side mode — the @request event fires on every page /
    # rows-per-page / sort change, and we re-fetch from the daemon.
    #
    # Two modes:
    #   * ``fetch_page`` provided → true server-side pagination.
    #     ``rows`` is the first page; later pages come from the
    #     callback ``(page, rows_per_page) -> (rows, total)``.
    #   * ``fetch_page`` None → client-side pagination over ``rows``;
    #     ``total_rows`` (if passed) seeds the footer's "of N".
    pagination: dict[str, int] = {"page": 1, "rowsPerPage": 50}
    if total_rows is not None:
        pagination["rowsNumber"] = int(total_rows)
    elif fetch_page is not None:
        pagination["rowsNumber"] = len(rows)  # placeholder until first request
    table = (
        ui.table(
            columns=columns,
            rows=rows,
            row_key=row_key,
            pagination=pagination,
        )
        .classes("w-full flex-1 min-h-0 litmus-data-table")
        .props("flat bordered :rows-per-page-options='[10, 25, 50, 100, 0]'")
    )

    if fetch_page is not None:

        def _on_request(e: Any) -> None:
            """Fire on page / rows-per-page / sort change."""
            new_p = dict(e.args.get("pagination", {}))
            page = int(new_p.get("page", 1) or 1)
            rpp = int(new_p.get("rowsPerPage", 50) or 50)
            try:
                page_rows, total = fetch_page(page, rpp)
            except Exception:  # noqa: BLE001 — keep table responsive on transient daemon errors
                return
            new_p["rowsNumber"] = int(total)
            table.pagination.update(new_p)
            table.update_rows(page_rows)

        table.on("request", _on_request)

    for col in time_columns or ():
        table.add_slot(
            f"body-cell-{col}",
            '<q-td :props="props"><span v-html="props.value"></span></q-td>',
        )

    if on_row_click is not None:
        table.on(
            "row-click",
            lambda e, cb=on_row_click: cb(e.args[1]),
        )
    return table


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
                station.event_store,
                self._on_instrument_event,
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
        except (OSError, RuntimeError, ValueError, KeyError) as e:
            ui.notify(f"Connection failed ({type(e).__name__}): {e}", type="negative")
            return False

    def _toggle(self) -> None:
        if self.connected:
            self._station.release(self.role)
        else:
            try:
                self._station.instrument(self.role)
            except (OSError, RuntimeError, ValueError, KeyError) as e:
                ui.notify(f"Connection failed ({type(e).__name__}): {e}", type="negative")
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
    ui.run_javascript(f"""
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
    """)
