"""Tests for report generation.

Storage: canonical singleton (project-local via repo's
``litmus.yaml`` → ``<repo>/results/``). Per-test isolation is by
unique ``run_id`` (each ``sample_run`` mints a uuid4). Tests read
back through ``load_run_data(run_id)`` with no explicit
``data_dir`` so resolution falls through to the canonical
store the daemon already serves.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from litmus.data.backends.parquet import ParquetBackend
from litmus.data.data_dir import resolve_data_dir
from litmus.data.models import UUT, Measurement, Outcome, TestRun, TestStep, TestVector
from litmus.data.run_store import RunStore
from litmus.reports.core import (
    generate_report,
    load_run_data,
)


@pytest.fixture
def sample_run():
    """Create a test run with known data."""
    run = TestRun(
        id=uuid4(),
        started_at=datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 2, 7, 12, 5, 0, tzinfo=UTC),
        uut=UUT(serial="SN-001", part_number="PN-100", revision="A"),
        station_id="bench_01",
        part_id="widget_v1",
        part_name="Widget",
        operator_id="test_op",
        test_phase="development",
        git_commit="abc123",
        outcome=Outcome.PASSED,
        steps=[
            TestStep(
                name="test_voltage",
                outcome=Outcome.PASSED,
                vectors=[
                    TestVector(
                        outcome=Outcome.PASSED,
                        measurements=[
                            Measurement(
                                name="vout",
                                value=3.301,
                                units="V",
                                limit_low=3.0,
                                limit_high=3.6,
                                outcome=Outcome.PASSED,
                            ),
                            Measurement(
                                name="vout_ripple",
                                value=0.015,
                                units="V",
                                limit_high=0.050,
                                outcome=Outcome.PASSED,
                            ),
                        ],
                    )
                ],
            ),
            TestStep(
                name="test_current",
                outcome=Outcome.FAILED,
                vectors=[
                    TestVector(
                        outcome=Outcome.FAILED,
                        measurements=[
                            Measurement(
                                name="iout",
                                value=2.5,
                                units="A",
                                limit_low=0.0,
                                limit_high=2.0,
                                outcome=Outcome.FAILED,
                            ),
                        ],
                    )
                ],
            ),
        ],
    )
    return run


@pytest.fixture
def data_dir(sample_run):
    """Save sample run to the canonical data_dir.

    Per-test isolation is via the ``sample_run.id`` (uuid4) which
    is the parquet filename's run_id segment. Notifying the
    canonical daemon directly (bypassing
    ``LITMUS_SKIP_DAEMON_NOTIFY``) so ``load_run_data`` can find
    the run via the daemon's index.
    """
    rd = resolve_data_dir()
    backend = ParquetBackend(data_dir=rd)
    parquet_path = backend.save_test_run(sample_run)
    notifier = RunStore()
    try:
        notifier.notify_new_run(parquet_path)
    finally:
        notifier.close()
    return rd


@pytest.fixture
def run_id(sample_run):
    return str(sample_run.id)


class TestLoadRunData:
    def test_basic_fields(self, data_dir, run_id):
        data = load_run_data(run_id, str(data_dir))
        assert data.run_id == run_id
        assert data.uut_serial == "SN-001"
        assert data.station_id == "bench_01"
        assert data.part_id == "widget_v1"
        assert data.operator_id == "test_op"
        assert data.git_commit == "abc123"

    def test_measurement_stats(self, data_dir, run_id):
        data = load_run_data(run_id, str(data_dir))
        assert data.total_measurements == 3
        assert data.passed_measurements == 2
        assert data.failed_measurements == 1
        assert data.pass_rate == 66.7

    def test_step_names(self, data_dir, run_id):
        data = load_run_data(run_id, str(data_dir))
        assert "test_voltage" in data.step_names
        assert "test_current" in data.step_names

    def test_not_found(self, data_dir):
        with pytest.raises(FileNotFoundError):
            load_run_data("nonexistent", str(data_dir))


class TestGenerateReport:
    def test_json(self, data_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(data_dir))
        out = generate_report(data, tmp_path / "report.json", fmt="json")
        assert out.exists()
        obj = json.loads(out.read_text())
        assert obj["run_id"] == run_id
        assert obj["summary"]["total"] == 3
        assert obj["summary"]["passed"] == 2
        assert obj["uut"]["serial"] == "SN-001"
        assert len(obj["measurements"]) == 3

    def test_csv(self, data_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(data_dir))
        out = generate_report(data, tmp_path / "report.csv", fmt="csv")
        assert out.exists()
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 4  # header + 3 measurements
        assert "measurement_name" in lines[0]

    def test_html(self, data_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(data_dir))
        out = generate_report(data, tmp_path / "report.html", fmt="html")
        assert out.exists()
        html = out.read_text()
        assert "Test Report" in html
        assert "SN-001" in html
        assert "vout" in html

    def test_directory_output(self, data_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(data_dir))
        out_dir = tmp_path / "reports"
        out = generate_report(data, out_dir, fmt="json")
        assert out.parent == out_dir
        assert out.suffix == ".json"

    def test_pdf_requires_weasyprint(self, data_dir, run_id, tmp_path):
        """PDF generation requires weasyprint — test import error handling."""
        data = load_run_data(run_id, str(data_dir))
        try:
            import weasyprint  # noqa: F401

            out = generate_report(data, tmp_path / "report.pdf", fmt="pdf")
            assert out.exists()
        except ImportError:
            with pytest.raises(ImportError, match="weasyprint"):
                generate_report(data, tmp_path / "report.pdf", fmt="pdf")


class TestTemplateResolution:
    def test_project_template_overrides(self, data_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(data_dir))

        # Create a project template
        tmpl_dir = tmp_path / "templates"
        tmpl_dir.mkdir()
        (tmpl_dir / "custom.html").write_text("<h1>Custom: {{ data.run_id }}</h1>")

        out = generate_report(
            data,
            tmp_path / "report.html",
            fmt="html",
            template="custom",
            template_dir=str(tmpl_dir),
        )
        html = out.read_text()
        assert "Custom:" in html
        assert run_id in html

    def test_missing_template_raises(self, data_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(data_dir))
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            generate_report(data, tmp_path / "report.html", fmt="html", template="nonexistent")


class TestProjectConfig:
    def test_load_missing(self, tmp_path):
        from litmus.store import load_project_config

        config = load_project_config(tmp_path)
        assert config.data_dir is None

    def test_load_valid(self, tmp_path):
        from litmus.store import load_project_config

        (tmp_path / "litmus.yaml").write_text("name: test\ndata_dir: my_results\n")
        config = load_project_config(tmp_path)
        assert config.data_dir == "my_results"


class TestCLI:
    def test_show_with_format(self, data_dir, run_id, tmp_path):
        from click.testing import CliRunner

        from litmus.cli import main

        runner = CliRunner()
        out_file = str(tmp_path / "report.json")
        result = runner.invoke(
            main,
            [
                "show",
                run_id,
                "--data-dir",
                str(data_dir),
                "-f",
                "json",
                "-o",
                out_file,
            ],
        )
        assert result.exit_code == 0
        assert "Report generated" in result.output
        assert Path(out_file).exists()

    def test_show_terminal(self, data_dir, run_id):
        from click.testing import CliRunner

        from litmus.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "show",
                run_id,
                "--data-dir",
                str(data_dir),
            ],
        )
        assert result.exit_code == 0
        assert "SN-001" in result.output
