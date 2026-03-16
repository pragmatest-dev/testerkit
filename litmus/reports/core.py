"""Report generation core: data loading, formatting, and output.

Note: pyarrow.parquet and weasyprint are optional dependencies,
imported inline where needed to avoid hard requirements.
"""

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ReportData:
    """Format-independent report data extracted from a Parquet run."""

    # Run identity
    run_id: str = ""
    started_at: str = ""
    ended_at: str = ""
    outcome: str = ""

    # DUT
    dut_serial: str = ""
    dut_part_number: str = ""
    dut_revision: str = ""
    dut_lot_number: str = ""

    # Product
    product_id: str = ""
    product_name: str = ""
    product_revision: str = ""

    # Station
    station_id: str = ""
    station_type: str = ""
    station_location: str = ""

    # Fixture
    fixture_id: str = ""

    # Execution context
    operator_id: str = ""
    sequence_id: str = ""
    test_phase: str = ""
    git_commit: str = ""
    git_branch: str = ""
    git_remote: str = ""

    # Raw measurement rows
    measurements: list[dict[str, Any]] = field(default_factory=list)

    # Deduplicated instruments
    instruments: list[dict[str, Any]] = field(default_factory=list)

    # Summary stats
    total_measurements: int = 0
    passed_measurements: int = 0
    failed_measurements: int = 0
    skipped_measurements: int = 0
    step_names: list[str] = field(default_factory=list)
    pass_rate: float = 0.0


def _find_parquet(run_id: str, results_dir: str = "results") -> Path | None:
    """Find a Parquet file matching a run ID.

    Supports two layouts:
    - Current: results/runs/{date}/{timestamp}_{serial}.parquet
    - Legacy: results/runs/{date}/{run_id}/measurements.parquet
    """
    runs_dir = Path(results_dir) / "runs"
    if not runs_dir.exists():
        return None

    for date_dir in sorted(runs_dir.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        # New layout: flat parquet files in date dir
        for f in date_dir.iterdir():
            if f.suffix == ".parquet" and not f.is_dir():
                # Check if run_id is in the file content
                try:
                    import pyarrow.parquet as pq

                    table = pq.read_table(f, columns=["run_id"])
                    if table.num_rows > 0:
                        file_run_id = str(table.column("run_id")[0])
                        if run_id in file_run_id or file_run_id.startswith(run_id):
                            return f
                except (OSError, IndexError):
                    continue
        # Old layout: run_id subdirectory
        for run_dir in date_dir.iterdir():
            if run_dir.is_dir() and run_id in run_dir.name:
                mf = run_dir / "measurements.parquet"
                if mf.exists():
                    return mf

    return None


def load_run_data(run_id: str, results_dir: str = "results") -> ReportData:
    """Load a test run from Parquet into ReportData.

    Args:
        run_id: Full or partial run ID.
        results_dir: Path to results directory.

    Returns:
        Populated ReportData.

    Raises:
        FileNotFoundError: If no matching Parquet file found.
    """
    import pyarrow.parquet as pq

    parquet_path = _find_parquet(run_id, results_dir)
    if parquet_path is None:
        raise FileNotFoundError(f"No Parquet file found for run '{run_id}' in {results_dir}/")

    table = pq.read_table(parquet_path)
    rows = table.to_pylist()

    if not rows:
        raise FileNotFoundError(f"Parquet file is empty for run '{run_id}'")

    first = rows[0]

    # Extract instruments from instr_* parallel arrays
    instruments = _extract_instruments(rows)

    # Compute stats
    outcomes = [r.get("outcome", "") for r in rows]
    passed = sum(1 for o in outcomes if o == "pass")
    failed = sum(1 for o in outcomes if o == "fail")
    skipped = sum(1 for o in outcomes if o == "skip")
    total = len(rows)
    step_names = sorted({r.get("step_name", "") for r in rows if r.get("step_name")})

    return ReportData(
        run_id=str(first.get("run_id") or ""),
        started_at=_fmt_dt_raw(first.get("run_started_at")),
        ended_at=_fmt_dt_raw(first.get("run_ended_at")),
        outcome=str(first.get("run_outcome") or first.get("outcome") or ""),
        dut_serial=str(first.get("dut_serial") or ""),
        dut_part_number=str(first.get("dut_part_number") or ""),
        dut_revision=str(first.get("dut_revision") or ""),
        dut_lot_number=str(first.get("dut_lot_number") or ""),
        product_id=str(first.get("product_id") or ""),
        product_name=str(first.get("product_name") or ""),
        product_revision=str(first.get("product_revision") or ""),
        station_id=str(first.get("station_id") or ""),
        station_type=str(first.get("station_type") or ""),
        station_location=str(first.get("station_location") or ""),
        fixture_id=str(first.get("fixture_id") or ""),
        operator_id=str(first.get("operator_id") or ""),
        sequence_id=str(first.get("sequence_id") or ""),
        test_phase=str(first.get("test_phase") or ""),
        git_commit=str(first.get("git_commit") or ""),
        git_branch=str(first.get("git_branch") or ""),
        git_remote=str(first.get("git_remote") or ""),
        measurements=rows,
        instruments=instruments,
        total_measurements=total,
        passed_measurements=passed,
        failed_measurements=failed,
        skipped_measurements=skipped,
        step_names=step_names,
        pass_rate=round(passed / total * 100, 1) if total > 0 else 0.0,
    )


def _extract_instruments(rows: list[dict]) -> list[dict]:
    """Deduplicate instruments from instr_* parallel arrays across all rows."""
    seen: set[str] = set()
    instruments: list[dict] = []

    for row in rows:
        names = row.get("instr_name") or []
        if not isinstance(names, list):
            continue
        ids = row.get("instr_id") or []
        manufacturers = row.get("instr_manufacturer") or []
        models = row.get("instr_model") or []
        serials = row.get("instr_serial") or []
        resources = row.get("instr_resource") or []
        cal_dues = row.get("instr_cal_due") or []

        for i, name in enumerate(names):
            key = f"{name}:{_safe_idx(ids, i)}"
            if key in seen:
                continue
            seen.add(key)
            instruments.append({
                "name": name,
                "id": _safe_idx(ids, i),
                "manufacturer": _safe_idx(manufacturers, i),
                "model": _safe_idx(models, i),
                "serial": _safe_idx(serials, i),
                "resource": _safe_idx(resources, i),
                "cal_due": _safe_idx(cal_dues, i),
            })

    return instruments


def _safe_idx(lst: list, i: int, default: str = "") -> str:
    try:
        v = lst[i]
        return str(v) if v is not None else default
    except (IndexError, TypeError):
        return default


def _fmt_dt_raw(val: Any) -> str:
    """Format a datetime value from Parquet to ISO string."""
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def generate_report(
    data: ReportData,
    output: Path | str,
    fmt: str = "html",
    template: str = "default",
    template_dir: str | None = None,
) -> Path:
    """Generate a report file from ReportData.

    Args:
        data: Report data to render.
        output: Output file path or directory.
        fmt: Format — html, pdf, json, csv.
        template: Template name (without .html extension).
        template_dir: Optional project template directory override.

    Returns:
        Path to the generated file.
    """
    output = Path(output)

    # If output is a directory, ends with /, or has no file extension
    # (e.g. "reports" without .html), auto-generate a filename inside it.
    if output.is_dir() or str(output).endswith("/") or not output.suffix:
        output.mkdir(parents=True, exist_ok=True)
        safe_id = data.run_id[:8] if data.run_id else "report"
        ext = "pdf" if fmt == "pdf" else fmt
        output = output / f"report_{safe_id}.{ext}"
    else:
        output.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        _write_json(data, output)
    elif fmt == "csv":
        _write_csv(data, output)
    elif fmt == "html":
        _write_html(data, output, template, template_dir)
    elif fmt == "pdf":
        _write_pdf(data, output, template, template_dir)
    else:
        raise ValueError(f"Unknown format: {fmt}")

    return output


def _write_json(data: ReportData, output: Path) -> None:
    """Write JSON report."""
    obj = {
        "run_id": data.run_id,
        "started_at": data.started_at,
        "ended_at": data.ended_at,
        "outcome": data.outcome,
        "dut": {
            "serial": data.dut_serial,
            "part_number": data.dut_part_number,
            "revision": data.dut_revision,
            "lot_number": data.dut_lot_number,
        },
        "product": {
            "id": data.product_id,
            "name": data.product_name,
            "revision": data.product_revision,
        },
        "station": {
            "id": data.station_id,
            "type": data.station_type,
            "location": data.station_location,
        },
        "fixture_id": data.fixture_id,
        "operator_id": data.operator_id,
        "sequence_id": data.sequence_id,
        "test_phase": data.test_phase,
        "git_commit": data.git_commit,
        "git_branch": data.git_branch,
        "git_remote": data.git_remote,
        "summary": {
            "total": data.total_measurements,
            "passed": data.passed_measurements,
            "failed": data.failed_measurements,
            "skipped": data.skipped_measurements,
            "pass_rate": data.pass_rate,
        },
        "measurements": _serialize_measurements(data.measurements),
        "instruments": data.instruments,
    }
    output.write_text(json.dumps(obj, indent=2, default=str) + "\n")


def _serialize_measurements(rows: list[dict]) -> list[dict]:
    """Clean measurement rows for JSON serialization."""
    result = []
    for row in rows:
        clean = {}
        for k, v in row.items():
            if v is None:
                continue
            if isinstance(v, datetime):
                clean[k] = v.isoformat()
            elif isinstance(v, list):
                clean[k] = [str(x) if isinstance(x, datetime) else x for x in v]
            else:
                clean[k] = v
        result.append(clean)
    return result


def _write_csv(data: ReportData, output: Path) -> None:
    """Write CSV report — one row per measurement."""
    if not data.measurements:
        output.write_text("")
        return

    # Use measurement columns
    columns = [
        "step_name", "measurement_name", "value", "units",
        "low_limit", "high_limit", "nominal", "outcome",
        "spec_id", "dut_pin", "instrument_name",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in data.measurements:
        writer.writerow(row)

    output.write_text(buf.getvalue())


def _resolve_template(template: str, template_dir: str | None) -> str:
    """Resolve and read a Jinja2 template.

    Resolution order:
    1. Project templates: {template_dir}/{template}.html or reports/templates/{template}.html
    2. Built-in templates: litmus/reports/templates/{template}.html
    """
    # Project templates
    project_paths = []
    if template_dir:
        project_paths.append(Path(template_dir) / f"{template}.html")
    project_paths.append(Path("reports") / "templates" / f"{template}.html")

    for p in project_paths:
        if p.exists():
            return p.read_text()

    # Built-in templates
    builtin = Path(__file__).parent / "templates" / f"{template}.html"
    if builtin.exists():
        return builtin.read_text()

    raise FileNotFoundError(
        f"Template '{template}' not found. Searched: "
        f"{', '.join(str(p) for p in project_paths)}, {builtin}"
    )


def _render_html(data: ReportData, template: str, template_dir: str | None) -> str:
    """Render HTML from template and data."""
    from jinja2 import Environment

    template_str = _resolve_template(template, template_dir)
    env = Environment(autoescape=True)
    env.filters["fmt_dt"] = _filter_fmt_dt
    env.filters["fmt_value"] = _filter_fmt_value

    tmpl = env.from_string(template_str)
    return tmpl.render(data=data)


def _write_html(data: ReportData, output: Path, template: str, template_dir: str | None) -> None:
    """Write HTML report."""
    html = _render_html(data, template, template_dir)
    output.write_text(html)


def _write_pdf(data: ReportData, output: Path, template: str, template_dir: str | None) -> None:
    """Write PDF report via WeasyPrint."""
    try:
        from weasyprint import HTML
    except ImportError:
        raise ImportError(
            "PDF reports require weasyprint. Install with: pip install 'litmus[pdf]'"
        )

    html = _render_html(data, template, template_dir)
    HTML(string=html).write_pdf(str(output))


# -- Jinja2 filters --


def _filter_fmt_dt(val: str) -> str:
    """Format ISO datetime for display."""
    if not val:
        return ""
    try:
        dt = datetime.fromisoformat(val)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return str(val)


def _filter_fmt_value(val: Any) -> str:
    """Format a measurement value."""
    if val is None:
        return "—"
    if isinstance(val, float):
        if abs(val) >= 1000:
            return f"{val:.1f}"
        if abs(val) >= 1:
            return f"{val:.3f}"
        if abs(val) >= 0.001:
            return f"{val:.6f}"
        return f"{val:.9f}"
    return str(val)
