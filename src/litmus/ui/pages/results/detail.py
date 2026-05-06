"""Results detail page — skeleton-first, reactive bindings for in-flight runs."""

from __future__ import annotations

from typing import Any

from nicegui import run, ui

from litmus.data.event_store import EventStore
from litmus.data.models import RunSummary
from litmus.data.results_dir import resolve_results_dir
from litmus.ui.components.artifact_viewer import list_artifacts, render_artifact_buttons
from litmus.ui.shared.components import (
    attach_status_chip,
    data_table,
    display_status,
    format_datetime,
    info_field,
    page_layout,
    push_url_state,
    render_skeleton,
    status_chip_classes,
    status_row_fields,
    subscribe_with_refresh,
)
from litmus.ui.shared.layout import create_layout
from litmus.ui.shared.services import (
    aggregate_run_stats,
    get_run_detail,
    get_session_steps,
    list_all_runs,
)

_LIVE_EVENT_TYPES = [
    "run.ended",
    "test.step_started",
    "test.step_ended",
    "test.measurement",
]


@ui.page("/results/{run_id}")
async def result_detail_page(run_id: str, tab: str = ""):
    """Single result detail page.

    Skeleton-first: chrome appears immediately while data loads off the
    event loop. For in-flight runs three reactive strategies are used:

    * ``@ui.refreshable`` — status chip (CSS class + conditional "Live"
      indicator) and overview stats (conditional counts). Rebuilds the
      widget subtree when called; correct for sections with structural
      changes.
    * ``bind_text_from`` — "Ended" timestamp label.  NiceGUI polls the
      state dict and patches the DOM text node — no widget rebuild.
    * ``table.rows = …; table.update()`` — steps and measurements.
      Vue diffs the new row list against the rendered rows; only changed
      cells are patched.  The ``no-data`` Quasar slot handles the
      empty-state message without a separate visibility toggle.
    """
    create_layout(f"Run {run_id[:8]}")

    with page_layout(gap="gap-3"):
        loading = ui.column().classes("w-full gap-3")
        with loading:
            ui.card().classes("w-full h-20 animate-pulse bg-slate-200 rounded")
            ui.card().classes("w-full h-48 animate-pulse bg-slate-200 rounded")

        run_obj, steps, measurements = await run.io_bound(get_run_detail, run_id)

        loading.delete()

        if not run_obj:
            _render_not_found()
            return

        is_live = run_obj.ended_at is None

        # Per-connection state.  All reactive bindings and closures below
        # read from this dict; there is one instance per page load.
        state: dict[str, Any] = {
            "run": run_obj,
            "steps": steps,
            "measurements": measurements,
            # Plain string for bind_text_from on the "Ended" label.
            "ended_text": format_datetime(run_obj.ended_at) if run_obj.ended_at else "—",
        }

        # ── @ui.refreshable (structural changes or CSS class swaps) ──────

        @ui.refreshable
        def render_status_chip() -> None:
            """Outcome chip + animated "Live" dot.

            @ui.refreshable because the chip's Tailwind colour class changes
            on finalisation, and the "Live" row is conditionally present.
            """
            r: RunSummary = state["run"]
            status = display_status(
                started_at=r.started_at,
                ended_at=r.ended_at,
                outcome=r.outcome or None,
            )
            chip_cls = status_chip_classes(status)
            ui.label(status.upper()).classes(f"px-2 py-0.5 rounded text-xs font-medium {chip_cls}")
            if r.ended_at is None:
                with ui.row().classes("items-center gap-1"):
                    ui.element("span").classes("w-2 h-2 rounded-full bg-blue-500 animate-pulse")
                    ui.label("Live").classes("text-xs text-blue-600 font-medium")

        @ui.refreshable
        def render_overview() -> None:
            """Stats cards.

            @ui.refreshable because counts and pass-rate percentages change
            with every step/measurement event.  tab_panels / steps_tab /
            measurements_tab are captured by closure (late-binding; they
            exist by the time this is first called).
            """
            stats = aggregate_run_stats(state["steps"], state["measurements"])
            _render_overview_tab(
                stats["total_steps"],
                stats["failed_steps"],
                stats["total_measurements"],
                stats["passed_measurements"],
                stats["failed_measurements"],
                on_show_steps=lambda: tab_panels.set_value(steps_tab),
                on_show_measurements=lambda: tab_panels.set_value(measurements_tab),
            )

        # ── Static header card ────────────────────────────────────────────
        with ui.card().classes("w-full sticky top-0 z-10"):
            with ui.card_section().classes("py-2 px-3"):
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-3"):
                        ui.label("Test Run Summary").classes("text-base font-semibold")
                        render_status_chip()
                    ui.button(
                        "Back",
                        icon="arrow_back",
                        on_click=lambda: ui.navigate.to("/results"),
                    ).props("flat dense")

            with ui.card_section().classes("py-2 px-3"):
                with ui.row().classes("flex-wrap gap-x-10 gap-y-2 w-full"):
                    info_field("Part Number", run_obj.dut_part_number or "")
                    info_field("Serial", run_obj.dut_serial or "")
                    info_field("Hostname", run_obj.station_hostname or "")
                    info_field("Project", run_obj.project_name or "")
                    info_field("Started", format_datetime(run_obj.started_at))
                    # bind_content_from: NiceGUI patches only the HTML content
                    # when state["ended_text"] changes — no widget rebuild.
                    # ui.html (not ui.label) because format_datetime returns
                    # a <span class="litmus-time"> wrapper for browser-local
                    # time formatting; bind_text_from would escape it as text.
                    with ui.column().classes("gap-1"):
                        ui.label("Ended").classes("text-xs text-slate-500 uppercase")
                        ui.html(content=state["ended_text"], sanitize=False).bind_content_from(
                            state, "ended_text"
                        ).classes("font-semibold")

        has_slots = any(m.get("slot_id") for m in measurements)
        session_id = run_obj.session_id

        timeline_tab: Any = None
        with ui.tabs().props("inline-label no-caps dense").classes("w-full") as tabs:
            overview_tab = ui.tab("Overview", icon="dashboard")
            steps_tab = ui.tab("Steps", icon="list_alt")
            measurements_tab = ui.tab("Measurements", icon="science")
            if has_slots and session_id:
                timeline_tab = ui.tab("Execution Timeline", icon="timeline")
            history_tab = ui.tab("DUT History", icon="history")
        ui.add_css(
            ".q-tab__icon { font-size: 1rem !important; }"
            ".q-tab { min-height: 32px !important; padding: 0 12px !important; }"
        )

        timeline_container: Any = None
        history_container: Any = None

        # Initial tab from URL ?tab= param.
        _tab_lookup: dict[str, Any] = {
            "Steps": steps_tab,
            "Measurements": measurements_tab,
            "DUT History": history_tab,
        }
        if timeline_tab is not None:
            _tab_lookup["Execution Timeline"] = timeline_tab
        initial_tab = _tab_lookup.get(tab, overview_tab)

        # tab_panels / steps_tab / measurements_tab are now in scope for the
        # render_overview closure (late-binding).
        with ui.tab_panels(tabs, value=initial_tab).classes("w-full flex-1 min-h-0") as tab_panels:
            with ui.tab_panel(overview_tab):
                render_overview()

            with ui.tab_panel(steps_tab):
                # table.rows + table.update() path: create the table once,
                # store the ref, update rows on live refresh.  The Quasar
                # no-data slot shows the empty-state message without a
                # separate visibility binding.
                steps_table = _create_steps_table(state["steps"], parent_ended_at=run_obj.ended_at)

            with ui.tab_panel(measurements_tab):
                meas_table = _create_meas_table(run_id, state["measurements"])

            if has_slots and timeline_tab is not None and session_id:
                with ui.tab_panel(timeline_tab):
                    timeline_container = ui.column().classes("w-full")
                    render_skeleton(timeline_container, "h-64")

            with ui.tab_panel(history_tab):
                history_container = ui.column().classes("w-full")
                render_skeleton(history_container, "h-32")

        # ── Lazy secondary tabs (static; no live refresh needed) ──────────
        timeline_loaded = {"done": False}
        history_loaded = {"done": False}

        async def _load_timeline() -> None:
            if timeline_loaded["done"] or timeline_container is None or not session_id:
                return
            timeline_loaded["done"] = True
            session_steps = await run.io_bound(get_session_steps, session_id)
            current_slot = next((m.get("slot_id") for m in measurements if m.get("slot_id")), None)
            _render_timeline_tab(timeline_container, session_steps, current_slot_id=current_slot)

        async def _load_history() -> None:
            if history_loaded["done"] or history_container is None:
                return
            history_loaded["done"] = True
            all_runs = await run.io_bound(list_all_runs, 100)
            _render_history_tab(history_container, run_id, run_obj, all_runs)

        async def _on_tab_change(_: Any) -> None:
            active = str(tabs.value or "")
            # Mirror tab into URL so bookmarks / back-nav land on the same view.
            push_url_state(
                f"/results/{run_id}",
                {"tab": active if active != "Overview" else ""},
            )
            if active == "Execution Timeline":
                await _load_timeline()
            elif active == "DUT History":
                await _load_history()

        tabs.on_value_change(_on_tab_change)

        if not is_live:
            ui.timer(0.1, _load_history, once=True)

        # ── Live refresh (in-flight runs only) ────────────────────────────
        if is_live:
            unsubscribe_ref: list[Any] = []

            async def _live_refresh() -> None:
                new_run, new_steps, new_meas = await run.io_bound(get_run_detail, run_id)
                if new_run is None:
                    return

                state.update({"run": new_run, "steps": new_steps, "measurements": new_meas})

                # @ui.refreshable: structural/CSS sections.
                render_status_chip.refresh()
                render_overview.refresh()

                # table.rows + update(): Vue diffs rows — no widget teardown.
                steps_table.rows = _build_step_rows(new_steps, parent_ended_at=new_run.ended_at)
                steps_table.update()

                meas_table.rows = _build_meas_rows(new_meas)
                meas_table.update()

                # bind_text_from polling picks this up automatically.
                if new_run.ended_at is not None:
                    state["ended_text"] = format_datetime(new_run.ended_at)
                    if unsubscribe_ref:
                        unsubscribe_ref[0]()  # stop receiving events

            try:
                es = EventStore.get_shared(resolve_results_dir())
                unsub = subscribe_with_refresh(
                    es,
                    _LIVE_EVENT_TYPES,
                    _live_refresh,
                    debounce_seconds=0.5,
                )
                unsubscribe_ref.append(unsub)
            except Exception:  # noqa: BLE001 — no events daemon; page stays static
                pass

            # One immediate refresh catches anything emitted between page
            # load and subscription attachment.
            ui.timer(0, _live_refresh, once=True)

        ui.link("← Back to Results", "/results").classes("text-blue-600 hover:underline")


# ── Table row builders (pure — no NiceGUI calls) ──────────────────────────────


def _build_step_rows(steps: list, *, parent_ended_at: Any = None) -> list[dict]:
    return [
        {
            "step_index": s.step_index,
            "step_name": s.step_name or "",
            "step_path": s.step_path or "",
            **status_row_fields(
                started_at=s.started_at,
                ended_at=s.ended_at,
                outcome=s.outcome,
                parent_ended_at=parent_ended_at,
                column="outcome",
            ),
            "duration_s": f"{s.duration_s:.3f}" if s.duration_s is not None else "—",
            "measurement_count": s.measurement_count if s.measurement_count is not None else 0,
        }
        for s in steps
    ]


def _build_meas_rows(measurements: list) -> list[dict]:
    return [
        {
            "step_name": m.get("step_name", ""),
            "name": m.get("measurement_name", ""),
            "value": _format_measurement_value(m),
            "limits": (
                f"{m.get('limit_low', '—')} – {m.get('limit_high', '—')}"
                if m.get("limit_low") is not None or m.get("limit_high") is not None
                else "—"
            ),
            "outcome": m.get("measurement_outcome", ""),
        }
        for m in measurements
    ]


def _format_measurement_value(m: dict) -> str:
    """Prefer fixed-schema fields; fall back to legacy dynamic-attr expansion."""
    val = m.get("measurement_value") if m.get("measurement_value") is not None else m.get("value")
    units = m.get("measurement_units") or m.get("units") or ""
    if val is None:
        return "—"
    formatted = f"{val:g}" if isinstance(val, float) else str(val)
    return f"{formatted} {units}".strip() if units else formatted


# ── Table factory functions (create once; updated via table.rows + update()) ──


_STEP_COLUMNS = [
    {"name": "step_index", "label": "#", "field": "step_index", "align": "right"},
    {"name": "step_name", "label": "Step", "field": "step_name", "align": "left"},
    {"name": "step_path", "label": "Path", "field": "step_path", "align": "left"},
    {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
    {"name": "duration_s", "label": "Duration (s)", "field": "duration_s", "align": "right"},
    {
        "name": "measurement_count",
        "label": "Measurements",
        "field": "measurement_count",
        "align": "right",
    },
]

_MEAS_COLUMNS = [
    {"name": "step", "label": "Step", "field": "step_name", "align": "left"},
    {"name": "name", "label": "Measurement", "field": "name", "align": "left"},
    {"name": "value", "label": "Value", "field": "value", "align": "right"},
    {"name": "limits", "label": "Limits", "field": "limits", "align": "center"},
    {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
]


def _create_steps_table(steps: list, *, parent_ended_at: Any = None) -> ui.table:
    """Create the steps table widget.  Returns the table so callers can
    update ``table.rows`` directly without rebuilding the widget tree."""
    rows = _build_step_rows(steps, parent_ended_at=parent_ended_at)
    with ui.card().classes("w-full h-full flex flex-col"):
        with ui.card_section().classes("py-2"):
            ui.label(
                "Steps in execution order — including skipped, planned, and setup-only steps."
            ).classes("text-sm text-slate-500")
        table = data_table(columns=_STEP_COLUMNS, rows=rows, row_key="step_index")
    table.add_slot(
        "no-data",
        '<div class="text-slate-500 italic p-4 text-sm">No steps recorded yet.</div>',
    )
    attach_status_chip(table, column="outcome")
    return table


def _create_meas_table(run_id: str, measurements: list) -> ui.table:
    """Create the measurements table widget.  Returns the table so callers
    can update ``table.rows`` directly without rebuilding the widget tree."""
    rows = _build_meas_rows(measurements)
    with ui.card().classes("w-full h-full flex flex-col"):
        table = data_table(columns=_MEAS_COLUMNS, rows=rows, row_key="name")
    table.add_slot(
        "no-data",
        '<div class="text-slate-500 italic p-4 text-sm">No measurements recorded yet.</div>',
    )

    artifact_rows = [m for m in measurements if list_artifacts(m)]
    if artifact_rows:
        with ui.card().classes("w-full"):
            with ui.card_section():
                ui.label("Artifacts").classes("font-semibold")
                ui.label(
                    "Waveforms, screenshots, logs, and other large observations "
                    "captured during this run."
                ).classes("text-sm text-slate-500")
            with ui.card_section().classes("flex flex-col gap-3"):
                for m in artifact_rows:
                    render_artifact_buttons(run_id, m)
    return table


# ── Overview tab ──────────────────────────────────────────────────────────────


def _render_overview_tab(
    total_steps: int,
    failed_steps: int,
    total_meas: int,
    passed_meas: int,
    failed_meas: int,
    *,
    on_show_steps: Any,
    on_show_measurements: Any,
) -> None:
    clickable = "cursor-pointer hover:shadow-md transition-shadow"
    with ui.row().classes("w-full gap-4 items-stretch"):
        with ui.card().classes(f"flex-1 {clickable}").on("click", lambda _: on_show_steps()):
            with ui.card_section():
                with ui.row().classes("items-center justify-between"):
                    ui.label("Test Statistics").classes("font-semibold")
                    ui.icon("arrow_forward").classes("text-slate-400 text-sm")
            with ui.card_section():
                with ui.row().classes("gap-8"):
                    _stat_card(str(total_steps), "Steps", "text-slate-700")
                    _stat_card(str(total_steps - failed_steps), "Passed", "text-emerald-600")
                    _stat_card(str(failed_steps), "Failed", "text-red-600")
                    if total_steps > 0:
                        pct = int(((total_steps - failed_steps) / total_steps) * 100)
                        _stat_card(f"{pct}%", "Pass Rate", "text-blue-600")

        with ui.card().classes(f"flex-1 {clickable}").on("click", lambda _: on_show_measurements()):
            with ui.card_section():
                with ui.row().classes("items-center justify-between"):
                    ui.label("Measurement Statistics").classes("font-semibold")
                    ui.icon("arrow_forward").classes("text-slate-400 text-sm")
            with ui.card_section():
                with ui.row().classes("gap-8"):
                    _stat_card(str(total_meas), "Measurements", "text-slate-700")
                    _stat_card(str(passed_meas), "Passed", "text-emerald-600")
                    _stat_card(str(failed_meas), "Failed", "text-red-600")
                    if total_meas > 0:
                        pct = int((passed_meas / total_meas) * 100)
                        _stat_card(f"{pct}%", "Pass Rate", "text-blue-600")


def _stat_card(value: str, label: str, color_class: str) -> None:
    from litmus.ui.shared.components import stat_card

    stat_card(value, label, color_class)


# ── Secondary tabs ────────────────────────────────────────────────────────────


def _render_history_tab(
    container: Any,
    run_id: str,
    run_obj: RunSummary,
    all_runs: list,
) -> None:
    container.clear()
    dut_serial = run_obj.dut_serial or ""
    dut_runs = [r for r in all_runs if r.dut_serial == dut_serial and r.test_run_id != run_id]

    with container:
        if dut_runs:
            ui.label(f"Other runs for DUT: {dut_serial}").classes("text-sm text-slate-500 mb-2")
            columns = [
                {"name": "run_id", "label": "Run ID", "field": "run_id", "align": "left"},
                {"name": "project", "label": "Project", "field": "project", "align": "left"},
                {"name": "started", "label": "Started", "field": "started", "align": "left"},
                {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "center"},
            ]
            rows = [
                {
                    "run_id": (r.test_run_id or "")[:8],
                    "full_run_id": r.test_run_id or "",
                    "project": r.project_name or "",
                    "started": format_datetime(r.started_at),
                    "outcome": r.outcome or "",
                }
                for r in dut_runs[:10]
            ]
            data_table(
                columns=columns,
                rows=rows,
                row_key="run_id",
                on_row_click=lambda r: ui.navigate.to(f"/results/{r['full_run_id']}"),
                time_columns=["started"],
            )
        else:
            ui.label(f"No other runs found for DUT: {dut_serial}").classes("text-slate-500 italic")


def _render_timeline_tab(
    container: Any,
    steps: list,
    *,
    current_slot_id: str | None = None,
) -> None:
    from litmus.ui.components.execution_gantt import render_execution_gantt

    container.clear()
    with container, ui.card().classes("w-full"):
        with ui.card_section():
            ui.label("Execution Timeline").classes("font-semibold")
            ui.label(
                "Combined view of all slots in this parallel session. "
                "This run's slot is highlighted."
            ).classes("text-sm text-slate-500")
        with ui.card_section().classes("w-full"):
            render_execution_gantt(steps, current_slot_id=current_slot_id)


def _render_not_found() -> None:
    with ui.card().classes("w-full p-6 text-center"):
        ui.label("Run not found.").classes("text-xl text-slate-600")
        ui.link("← Back to Results", "/results").classes("text-blue-600 hover:underline")
