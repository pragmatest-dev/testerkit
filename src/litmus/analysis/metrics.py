"""Pure computation functions for manufacturing metrics.

All functions take plain Python data (dicts, lists) and return results.
No I/O, no PyArrow — just math.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from litmus.analysis._common import parse_datetime


def _yield_by_position(runs: list[dict], position: int) -> float:
    """Calculate yield at a run position per serial (0=first, -1=last).

    Groups runs by uut_serial, sorts each group by run_started_at,
    then checks the run at the given position.

    Returns:
        Yield as a fraction (0.0–1.0). Returns 0.0 if no serials.
    """
    if not runs:
        return 0.0

    by_serial: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        serial = r.get("uut_serial")
        if serial:
            by_serial[serial].append(r)

    if not by_serial:
        return 0.0

    passed = 0
    for serial_runs in by_serial.values():
        serial_runs.sort(key=lambda r: r.get("run_started_at") or "")
        if serial_runs[position].get("run_outcome") == "passed":
            passed += 1

    return passed / len(by_serial)


def calculate_fpy(runs: list[dict]) -> float:
    """First-pass yield: % of serials whose *first* run passed.

    Args:
        runs: List of run dicts with keys: uut_serial, run_outcome, run_started_at.
              Must be deduplicated to one row per run_id.

    Returns:
        FPY as a fraction (0.0–1.0). Returns 0.0 if no serials.
    """
    return _yield_by_position(runs, 0)


def calculate_final_yield(runs: list[dict]) -> float:
    """Final yield: % of serials whose *last* run passed.

    Args:
        runs: List of run dicts (deduplicated to one row per run_id).

    Returns:
        Final yield as a fraction (0.0–1.0).
    """
    return _yield_by_position(runs, -1)


def calculate_rty(fpy_by_phase: dict[str, float]) -> float:
    """Rolled throughput yield: product of FPY across test phases.

    Args:
        fpy_by_phase: Mapping of phase name to FPY fraction.

    Returns:
        RTY as a fraction (0.0–1.0). Returns 0.0 if any phase is empty.
    """
    if not fpy_by_phase:
        return 0.0

    rty = 1.0
    for fpy in fpy_by_phase.values():
        rty *= fpy
    return rty


def calculate_ppk(
    values: list[float],
    lsl: float | None,
    usl: float | None,
    min_samples: int = 30,
) -> dict:
    """Process performance index (overall σ).

    Args:
        values: Measured values (numeric).
        lsl: Lower specification limit (None if one-sided).
        usl: Upper specification limit (None if one-sided).
        min_samples: Minimum sample size. Returns warning if below.

    Returns:
        Dict with keys: ppk, pp, mean, sigma, lsl, usl, n, warning.
    """
    n = len(values)
    result: dict = {"n": n, "lsl": lsl, "usl": usl}

    if n < 2:
        result.update(
            {
                "ppk": None,
                "pp": None,
                "mean": None,
                "sigma": None,
                "warning": "insufficient data",
            }
        )
        return result

    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    sigma = math.sqrt(variance)

    result["mean"] = mean
    result["sigma"] = sigma

    warning = None
    if n < min_samples:
        warning = f"only {n} samples (recommend {min_samples}+)"

    if sigma == 0:
        result.update({"ppk": None, "pp": None, "warning": "zero variance"})
        return result

    # Pp = (USL - LSL) / (6 * sigma)
    if usl is not None and lsl is not None:
        result["pp"] = (usl - lsl) / (6 * sigma)
        ppu = (usl - mean) / (3 * sigma)
        ppl = (mean - lsl) / (3 * sigma)
        result["ppk"] = min(ppu, ppl)
    elif usl is not None:
        result["pp"] = None
        result["ppk"] = (usl - mean) / (3 * sigma)
    elif lsl is not None:
        result["pp"] = None
        result["ppk"] = (mean - lsl) / (3 * sigma)
    else:
        result.update({"ppk": None, "pp": None, "warning": "no limits defined"})
        return result

    result["warning"] = warning
    return result


def calculate_ppk_for_measurements(
    measurements: list[dict],
    min_samples: int = 10,
) -> list[dict]:
    """Calculate Ppk for all measurement types in a dataset.

    Groups measurements by name, extracts values and limits, then computes
    Ppk for each group. Results are sorted by Ppk descending.

    Args:
        measurements: List of measurement dicts with measurement_name,
            value, limit_low, limit_high.
        min_samples: Minimum sample size for Ppk warning.

    Returns:
        List of Ppk result dicts, each with an added measurement_name key.
    """
    by_name: dict[str, list[dict]] = defaultdict(list)
    for m in measurements:
        name = m.get("measurement_name")
        if name:
            by_name[name].append(m)

    ppk_results = []
    for name, meas_list in by_name.items():
        values = [
            float(m["measurement_value"])
            for m in meas_list
            if m.get("measurement_value") is not None
        ]
        lsl = next(
            (float(m["limit_low"]) for m in meas_list if m.get("limit_low") is not None),
            None,
        )
        usl = next(
            (float(m["limit_high"]) for m in meas_list if m.get("limit_high") is not None),
            None,
        )

        if values and (lsl is not None or usl is not None):
            result = calculate_ppk(values, lsl, usl, min_samples=min_samples)
            result["measurement_name"] = name
            ppk_results.append(result)

    ppk_results.sort(key=lambda x: x.get("ppk") or 0, reverse=True)
    return ppk_results


def pareto_analysis(
    measurements: list[dict],
    top_n: int = 10,
) -> list[dict]:
    """Pareto analysis of failure modes.

    Args:
        measurements: List of measurement dicts with step_name, measurement_name, outcome.
        top_n: Number of top failures to return.

    Returns:
        List of dicts sorted by count desc: step_name, measurement_name, count, pct, cumulative_pct.
    """
    # Count failures by (step_name, measurement_name)
    fail_counts: dict[tuple[str, str], int] = defaultdict(int)
    for m in measurements:
        if m.get("measurement_outcome") == "failed":
            key = (m.get("step_name", ""), m.get("measurement_name", ""))
            fail_counts[key] += 1

    if not fail_counts:
        return []

    total_fails = sum(fail_counts.values())
    sorted_items = sorted(fail_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]

    result = []
    cumulative = 0.0
    for (step, meas), count in sorted_items:
        pct = count / total_fails * 100
        cumulative += pct
        result.append(
            {
                "step_name": step,
                "measurement_name": meas,
                "count": count,
                "pct": round(pct, 1),
                "cumulative_pct": round(cumulative, 1),
            }
        )

    return result


def trend_by_period(
    runs: list[dict],
    period: str = "day",
) -> list[dict]:
    """Yield trend grouped by time period.

    Args:
        runs: Deduplicated run dicts with run_outcome, run_started_at.
        period: Grouping period — "day", "week", or "month".

    Returns:
        List of dicts sorted by period: period, total, passed, yield_pct.
    """
    if not runs:
        return []

    def _period_key(dt: datetime) -> str:
        if period == "week":
            # ISO week: YYYY-Www
            return f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        elif period == "month":
            return dt.strftime("%Y-%m")
        else:
            return dt.strftime("%Y-%m-%d")

    buckets: dict[str, dict] = defaultdict(lambda: {"total": 0, "passed": 0})
    for r in runs:
        started = parse_datetime(r.get("run_started_at"))
        if started is None:
            continue

        key = _period_key(started)
        buckets[key]["total"] += 1
        if r.get("run_outcome") == "passed":
            buckets[key]["passed"] += 1

    result = []
    for p in sorted(buckets):
        b = buckets[p]
        yield_pct = (b["passed"] / b["total"] * 100) if b["total"] > 0 else 0.0
        result.append(
            {
                "period": p,
                "total": b["total"],
                "passed": b["passed"],
                "yield_pct": round(yield_pct, 1),
            }
        )

    return result


def timing_stats(
    rows: list[dict],
    by: str = "run",
) -> dict:
    """Test duration statistics.

    Args:
        rows: Measurement rows or deduplicated run rows.
        by: "run" for run-level stats, "step" for per-step breakdown.

    Returns:
        Dict with avg_s, min_s, max_s, p95_s, count, and optionally per_step.
    """
    if by == "step":
        return _step_time_stats(rows)
    return _run_time_stats(rows)


def _run_time_stats(runs: list[dict]) -> dict:
    """Compute run duration stats."""
    durations: list[float] = []
    for r in runs:
        started = parse_datetime(r.get("run_started_at"))
        ended = parse_datetime(r.get("run_ended_at"))
        if started is None or ended is None:
            continue
        dt = (ended - started).total_seconds()
        if dt >= 0:
            durations.append(dt)

    return _compute_duration_stats(durations)


def _step_time_stats(rows: list[dict]) -> dict:
    """Compute per-step duration stats."""
    # Deduplicate to one entry per (run_id, step_name)
    seen: set[tuple] = set()
    by_step: dict[str, list[float]] = defaultdict(list)

    for r in rows:
        step = r.get("step_name")
        started = r.get("step_started_at")
        ended = r.get("step_ended_at")
        if not step or started is None or ended is None:
            continue

        run_id = r.get("run_id", "")
        key = (run_id, step)
        if key in seen:
            continue
        seen.add(key)

        started = parse_datetime(started)
        ended = parse_datetime(ended)
        if started is None or ended is None:
            continue

        dt = (ended - started).total_seconds()
        if dt >= 0:
            by_step[step].append(dt)

    # Aggregate all steps for overall stats
    all_durations = [d for durs in by_step.values() for d in durs]
    result = _compute_duration_stats(all_durations)

    per_step = {}
    for step_name in sorted(by_step):
        per_step[step_name] = _compute_duration_stats(by_step[step_name])
    result["per_step"] = per_step

    return result


def _compute_duration_stats(durations: list[float]) -> dict:
    """Compute avg/min/max/p95 from a list of durations in seconds."""
    if not durations:
        return {"avg_s": None, "min_s": None, "max_s": None, "p95_s": None, "count": 0}

    durations_sorted = sorted(durations)
    n = len(durations_sorted)
    avg = sum(durations_sorted) / n
    p95_idx = min(int(n * 0.95), n - 1)

    return {
        "avg_s": round(avg, 2),
        "min_s": round(durations_sorted[0], 2),
        "max_s": round(durations_sorted[-1], 2),
        "p95_s": round(durations_sorted[p95_idx], 2),
        "count": n,
    }
