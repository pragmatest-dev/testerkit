"""Execution timeline (Gantt chart) for multi-UUT parallel runs.

Renders an ECharts Gantt chart showing per-site test step execution
over time. Each site gets a Y-axis row; each step is a colored bar
showing its duration.

Sources from typed :class:`StepRow` objects so the chart never sees
raw measurement dicts — site_index, started_at, ended_at, outcome, and
uut_serial_number are all first-class fields.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from litmus.analysis.steps_query import StepRow

_OUTCOME_COLORS = {
    "passed": "#10b981",  # emerald-500
    "done": "#10b981",  # emerald-500
    "failed": "#ef4444",  # red-500
    "errored": "#f59e0b",  # amber-500
    "terminated": "#f59e0b",  # amber-500
    "aborted": "#ef4444",  # red-500
    "skipped": "#94a3b8",  # slate-400
}
_DEFAULT_COLOR = "#94a3b8"  # slate-400


def render_execution_gantt(
    steps: list[StepRow],
    *,
    current_site_index: int | None = None,
) -> ui.echart | None:
    """Render a Gantt chart of step execution grouped by ``site_index``.

    Uses separate stacks per site so offset+duration bars don't
    interfere across sites. If ``current_site_index`` is provided, that
    lane's label is highlighted with an arrow marker.
    """
    # Group steps by site_index (one lane per site, deduplicated by step_name)
    sites: dict[int, dict[str, dict[str, Any]]] = {}
    site_names: dict[int, str | None] = {}
    for s in steps:
        # site_index is always present at rest (0-based, default 0 — see
        # the site-model contract), but StepRow types it Optional (a
        # generic query-row shape); the None check below is a type-narrow
        # for the dict-key use, not a "should we render" signal — the
        # caller decides whether to render this component at all (gated
        # on session→runs fan-out, not on any per-step field).
        if s.site_index is None or s.started_at is None or s.ended_at is None:
            continue
        step_name = s.step_name or ""
        site = sites.setdefault(s.site_index, {})
        site_names.setdefault(s.site_index, s.site_name)
        existing = site.get(step_name)
        if existing is None:
            site[step_name] = {
                "step_name": step_name,
                "started": s.started_at,
                "ended": s.ended_at,
                "outcome": s.outcome,
                "uut_serial_number": s.uut_serial_number or "",
            }
        else:
            # Worst-outcome wins when multiple step rows share a name in a site.
            if s.outcome == "failed":
                existing["outcome"] = "failed"
            elif s.outcome == "errored" and existing["outcome"] != "failed":
                existing["outcome"] = "errored"

    if not sites:
        ui.label("No multi-site execution data available.").classes("text-slate-500 italic")
        return None

    # Reference time and duration
    all_starts = [i["started"] for s in sites.values() for i in s.values()]
    all_ends = [i["ended"] for s in sites.values() for i in s.values()]
    t0 = min(all_starts)
    t_end = max(all_ends)
    parallel_duration = (t_end - t0).total_seconds()

    # Build site labels — highlight current site with arrow
    site_indices = sorted(sites.keys())
    site_labels = []
    for idx in site_indices:
        uut = next(iter(sites[idx].values()))["uut_serial_number"]
        name = site_names.get(idx)
        display = name if name else f"Site {idx}"
        label = f"{display} ({uut})" if uut else display
        if idx == current_site_index:
            label = f"► {label}"
        site_labels.append(label)

    # Build series: for each site, sort steps by start time and create
    # offset+duration bar pairs in that site's own stack.
    series: list[dict[str, Any]] = []
    sequential_total = 0.0
    n_sites = len(site_indices)

    for site_idx, idx in enumerate(site_indices):
        stack_name = f"site_{site_idx}"
        step_list = sorted(sites[idx].values(), key=lambda s: s["started"])
        cursor = 0.0  # current stack position in seconds

        for info in step_list:
            start_sec = (info["started"] - t0).total_seconds()
            dur = (info["ended"] - info["started"]).total_seconds()
            sequential_total += dur
            color = _OUTCOME_COLORS.get(info["outcome"] or "", _DEFAULT_COLOR)
            # Dim non-current sites when a current site is specified
            is_current = current_site_index is None or idx == current_site_index
            opacity = 1.0 if is_current else 0.35

            # Gap from cursor to start of this bar
            gap = max(0, start_sec - cursor)

            # Build data array: one value per site, 0 for all except this site
            gap_data = [0] * n_sites
            gap_data[site_idx] = round(gap, 4)
            dur_data: list[Any] = [0] * n_sites
            dur_data[site_idx] = {
                "value": round(dur, 4),
                "itemStyle": {"color": color, "opacity": opacity},
            }

            # Transparent offset bar
            series.append(
                {
                    "type": "bar",
                    "stack": stack_name,
                    "silent": True,
                    "itemStyle": {"color": "transparent", "borderWidth": 0},
                    "emphasis": {"disabled": True},
                    "tooltip": {"show": False},
                    "data": gap_data,
                }
            )

            # Visible duration bar
            series.append(
                {
                    "name": info["step_name"],
                    "type": "bar",
                    "stack": stack_name,
                    "barMaxWidth": 30,
                    "data": dur_data,
                    "label": {
                        "show": True,
                        "position": "inside",
                        "fontSize": 10,
                        "color": "#fff",
                        "formatter": info["step_name"],
                        "overflow": "truncate",
                    },
                }
            )

            cursor = start_sec + dur

    speedup = sequential_total / parallel_duration if parallel_duration > 0 else 1.0

    # Summary stats
    with ui.row().classes("gap-6 mb-4"):
        _stat("Parallel Time", f"{parallel_duration:.1f}s")
        _stat("Sequential Est.", f"{sequential_total:.1f}s")
        _stat("Speedup", f"{speedup:.1f}x")

    option: dict[str, Any] = {
        "tooltip": {
            "trigger": "item",
        },
        "grid": {
            "left": "15%",
            "right": "3%",
            "top": 10,
            "bottom": 40,
        },
        "xAxis": {
            "type": "value",
            "name": "Time (s)",
            "nameLocation": "middle",
            "nameGap": 25,
            "min": 0,
            "max": round(parallel_duration * 1.05, 3),
        },
        "yAxis": {
            "type": "category",
            "data": site_labels,
            "inverse": True,
            "axisLabel": {"fontSize": 12},
        },
        "series": series,
    }

    chart_height = max(200, len(site_indices) * 80 + 80)
    chart = ui.echart(option).classes("w-full").style(f"height: {chart_height}px")

    # Return chart so caller can trigger resize when tab becomes visible
    return chart


def _stat(label: str, value: str) -> None:
    """Render a small stat badge."""
    with ui.column().classes("items-center"):
        ui.label(value).classes("text-xl font-bold text-slate-700")
        ui.label(label).classes("text-xs text-slate-500")
