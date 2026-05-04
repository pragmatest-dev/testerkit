"""Execution timeline (Gantt chart) for multi-DUT parallel runs.

Renders an ECharts Gantt chart showing per-slot test step execution
over time. Each slot gets a Y-axis row; each step is a colored bar
showing its duration.

Sources from typed :class:`StepRow` objects so the chart never sees
raw measurement dicts — slot_id, started_at, ended_at, outcome, and
dut_serial are all first-class fields.
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
    current_slot_id: str | None = None,
) -> ui.echart | None:
    """Render a Gantt chart of step execution grouped by ``slot_id``.

    Uses separate stacks per slot so offset+duration bars don't
    interfere across slots. If ``current_slot_id`` is provided, that
    lane's label is highlighted with an arrow marker.
    """
    # Group steps by slot_id (one lane per slot, deduplicated by step_name)
    slots: dict[str, dict[str, dict[str, Any]]] = {}
    for s in steps:
        if not s.slot_id or s.started_at is None or s.ended_at is None:
            continue
        step_name = s.step_name or ""
        slot = slots.setdefault(s.slot_id, {})
        existing = slot.get(step_name)
        if existing is None:
            slot[step_name] = {
                "step_name": step_name,
                "started": s.started_at,
                "ended": s.ended_at,
                "outcome": s.outcome,
                "dut_serial": s.dut_serial or "",
            }
        else:
            # Worst-outcome wins when multiple step rows share a name in a slot.
            if s.outcome == "failed":
                existing["outcome"] = "failed"
            elif s.outcome == "errored" and existing["outcome"] != "failed":
                existing["outcome"] = "errored"

    if not slots:
        ui.label("No multi-slot execution data available.").classes("text-slate-500 italic")
        return None

    # Reference time and duration
    all_starts = [i["started"] for s in slots.values() for i in s.values()]
    all_ends = [i["ended"] for s in slots.values() for i in s.values()]
    t0 = min(all_starts)
    t_end = max(all_ends)
    parallel_duration = (t_end - t0).total_seconds()

    # Build slot labels — highlight current slot with arrow
    slot_ids = list(slots.keys())
    slot_labels = []
    for sid in slot_ids:
        dut = next(iter(slots[sid].values()))["dut_serial"]
        label = f"{sid} ({dut})" if dut else sid
        if sid == current_slot_id:
            label = f"► {label}"
        slot_labels.append(label)

    # Build series: for each slot, sort steps by start time and create
    # offset+duration bar pairs in that slot's own stack.
    series: list[dict[str, Any]] = []
    sequential_total = 0.0
    n_slots = len(slot_ids)

    for slot_idx, sid in enumerate(slot_ids):
        stack_name = f"slot_{slot_idx}"
        step_list = sorted(slots[sid].values(), key=lambda s: s["started"])
        cursor = 0.0  # current stack position in seconds

        for info in step_list:
            start_sec = (info["started"] - t0).total_seconds()
            dur = (info["ended"] - info["started"]).total_seconds()
            sequential_total += dur
            color = _OUTCOME_COLORS.get(info["outcome"] or "", _DEFAULT_COLOR)
            # Dim non-current slots when a current slot is specified
            is_current = current_slot_id is None or sid == current_slot_id
            opacity = 1.0 if is_current else 0.35

            # Gap from cursor to start of this bar
            gap = max(0, start_sec - cursor)

            # Build data array: one value per slot, 0 for all except this slot
            gap_data = [0] * n_slots
            gap_data[slot_idx] = round(gap, 4)
            dur_data: list[Any] = [0] * n_slots
            dur_data[slot_idx] = {
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
            "data": slot_labels,
            "inverse": True,
            "axisLabel": {"fontSize": 12},
        },
        "series": series,
    }

    chart_height = max(200, len(slot_ids) * 80 + 80)
    chart = ui.echart(option).classes("w-full").style(f"height: {chart_height}px")

    # Return chart so caller can trigger resize when tab becomes visible
    return chart


def _stat(label: str, value: str) -> None:
    """Render a small stat badge."""
    with ui.column().classes("items-center"):
        ui.label(value).classes("text-xl font-bold text-slate-700")
        ui.label(label).classes("text-xs text-slate-500")
