"""Report generation core: data loading, formatting, and output.

Generate test reports in HTML, PDF, CSV, JSON formats from parquet test run
data. Provides both CLI and library interfaces.

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

from litmus.api.schemas import load_run_view


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

    # Part
    part_id: str = ""
    part_name: str = ""
    part_revision: str = ""

    # Station
    station_id: str = ""
    station_type: str = ""
    station_location: str = ""

    # Fixture
    fixture_id: str = ""

    # Execution context
    operator_id: str = ""
    project_name: str = ""
    test_phase: str = ""
    git_commit: str = ""
    git_branch: str = ""
    git_remote: str = ""

    # Flat dicts with step context added (step_name, step_index, step_path).
    # Stored as dicts (not MeasurementView/InstrumentView) because Jinja2 templates
    # are a write boundary — model_dump(mode="json") is the correct conversion point.
    measurements: list[dict[str, Any]] = field(default_factory=list)
    instruments: list[dict[str, Any]] = field(default_factory=list)

    # Summary stats
    total_measurements: int = 0
    passed_measurements: int = 0
    failed_measurements: int = 0
    skipped_measurements: int = 0
    error_measurements: int = 0
    step_names: list[str] = field(default_factory=list)
    pass_rate: float = 0.0


def load_run_data(run_id: str, data_dir: str = "results") -> ReportData:
    """Load a test run into ReportData via the typed run-detail composition.

    Structure (run / steps / measurements) comes from
    :func:`load_run_view`. Extra report-only fields not in
    ``RunView`` (``dut_revision``, ``part_name``,
    ``git_commit``, etc.) are sniffed from the first measurement
    row when available and default to "" otherwise — typical for
    measurement-less runs.

    Args:
        run_id: Full or partial run ID.
        data_dir: Path to results directory.

    Raises:
        FileNotFoundError: If no run with ``run_id`` exists.
    """
    run_view = load_run_view(run_id, data_dir=data_dir)
    if run_view is None:
        raise FileNotFoundError(f"No run found for '{run_id}' in {data_dir}/")

    # Flatten steps → per-measurement dicts, adding step context for template compat.
    measurements: list[dict[str, Any]] = []
    for step in run_view.steps:
        for meas in step.measurements:
            flat = meas.model_dump(mode="json")
            flat["step_name"] = step.step_name
            flat["step_path"] = step.step_path
            flat["step_index"] = step.step_index
            measurements.append(flat)

    # Deduplicate instruments across all steps.
    seen_instr: set[str] = set()
    instruments: list[dict[str, Any]] = []
    for step in run_view.steps:
        for instr in step.instruments:
            key = f"{instr.role}:{instr.instrument_id}"
            if key not in seen_instr:
                seen_instr.add(key)
                instruments.append(instr.model_dump(mode="json"))

    # Extras not on RunView — sniff from the first measurement parquet
    # row when available. Empty defaults are fine for measurement-less runs.
    extras = _load_extras_from_parquet(run_id, data_dir)

    outcomes = [m.get("outcome") or "" for m in measurements]
    passed = sum(1 for o in outcomes if o == "passed")
    failed = sum(1 for o in outcomes if o == "failed")
    skipped = sum(1 for o in outcomes if o == "skipped")
    errored = sum(1 for o in outcomes if o == "errored")
    total = len(measurements)
    step_names = sorted(s.step_name for s in run_view.steps if s.step_name)

    return ReportData(
        run_id=run_view.run_id,
        started_at=_fmt_dt_raw(run_view.started_at),
        ended_at=_fmt_dt_raw(run_view.ended_at),
        outcome=run_view.outcome or "",
        dut_serial=run_view.dut_serial or "",
        dut_part_number=run_view.dut_part_number or "",
        dut_revision=extras.get("dut_revision", ""),
        dut_lot_number=extras.get("dut_lot_number", ""),
        part_id=run_view.part_id or "",
        part_name=extras.get("part_name", ""),
        part_revision=extras.get("part_revision", ""),
        station_id=run_view.station_id or "",
        station_type=extras.get("station_type", ""),
        station_location=extras.get("station_location", ""),
        fixture_id=extras.get("fixture_id", ""),
        operator_id=extras.get("operator_id", ""),
        project_name=extras.get("project_name", ""),
        test_phase=run_view.test_phase or "",
        git_commit=extras.get("git_commit", ""),
        git_branch=extras.get("git_branch", ""),
        git_remote=extras.get("git_remote", ""),
        measurements=measurements,
        instruments=instruments,
        total_measurements=total,
        passed_measurements=passed,
        failed_measurements=failed,
        skipped_measurements=skipped,
        error_measurements=errored,
        step_names=step_names,
        pass_rate=round(passed / total * 100, 1) if total > 0 else 0.0,
    )


def _load_extras_from_parquet(run_id: str, data_dir: str) -> dict[str, str]:
    """Sniff report-only fields from the first measurement row.

    The runs table doesn't denormalize every column the unified row schema carries
    (``dut_revision``, ``part_name``, ``git_commit``, …). Query the
    daemon's ``measurements`` view (parquet glob with ``union_by_name``)
    for one row matching this run; predicate pushdown on ``run_id``
    finds it without reading other files. Returns empty dict for
    measurement-less runs.
    """
    from pathlib import Path

    from litmus.data.run_store import RunStore

    store = RunStore(_data_dir=Path(data_dir))
    try:
        rows = store.get_measurements(run_id)
    except Exception:  # noqa: BLE001 — extras are optional; daemon unavailable is fine
        return {}
    finally:
        store.close()
    if not rows:
        return {}
    first = rows[0]
    return {
        k: str(first.get(k) or "")
        for k in (
            "dut_revision",
            "dut_lot_number",
            "part_name",
            "part_revision",
            "station_type",
            "station_location",
            "fixture_id",
            "operator_id",
            "project_name",
            "git_commit",
            "git_branch",
            "git_remote",
        )
    }


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
        "part": {
            "id": data.part_id,
            "name": data.part_name,
            "revision": data.part_revision,
        },
        "station": {
            "id": data.station_id,
            "type": data.station_type,
            "location": data.station_location,
        },
        "fixture_id": data.fixture_id,
        "operator_id": data.operator_id,
        "project_name": data.project_name,
        "test_phase": data.test_phase,
        "git_commit": data.git_commit,
        "git_branch": data.git_branch,
        "git_remote": data.git_remote,
        "summary": {
            "total": data.total_measurements,
            "passed": data.passed_measurements,
            "failed": data.failed_measurements,
            "skipped": data.skipped_measurements,
            "error": data.error_measurements,
            "pass_rate": data.pass_rate,
        },
        # Measurements are already JSON-safe: model_dump(mode="json") serializes
        # datetimes to ISO strings and nested inputs/outputs/custom dicts.
        "measurements": data.measurements,
        "instruments": data.instruments,
    }
    output.write_text(json.dumps(obj, indent=2, default=str) + "\n")


def _write_csv(data: ReportData, output: Path) -> None:
    """Write CSV report — one row per measurement."""
    if not data.measurements:
        output.write_text("")
        return

    columns = [
        "step_name",
        "measurement_name",
        "value",
        "units",
        "limit_low",
        "limit_high",
        "nominal",
        "outcome",
        "characteristic_id",
        "dut_pin",
        "instrument_name",
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
        raise ImportError("PDF reports require weasyprint. Install with: pip install 'litmus[pdf]'")

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
    """Format a measurement value for display in HTML reports.

    Distinct from datasheet.py's _fmt_value: operates on scalar floats only,
    no SI prefix — just decimal precision scaling by magnitude.
    """
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
