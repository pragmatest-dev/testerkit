"""Tests for report generation."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from litmus.data.backends.parquet import ParquetBackend
from litmus.data.models import DUT, Measurement, Outcome, TestRun, TestStep, TestVector
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
        dut=DUT(serial="SN-001", part_number="PN-100", revision="A"),
        station_id="bench_01",
        product_id="widget_v1",
        product_name="Widget",
        operator_id="test_op",
        test_phase="development",
        git_commit="abc123",
        outcome=Outcome.PASS,
        steps=[
            TestStep(
                name="test_voltage",
                outcome=Outcome.PASS,
                vectors=[
                    TestVector(
                        outcome=Outcome.PASS,
                        measurements=[
                            Measurement(
                                name="vout",
                                value=3.301,
                                units="V",
                                low_limit=3.0,
                                high_limit=3.6,
                                outcome=Outcome.PASS,
                            ),
                            Measurement(
                                name="vout_ripple",
                                value=0.015,
                                units="V",
                                high_limit=0.050,
                                outcome=Outcome.PASS,
                            ),
                        ],
                    )
                ],
            ),
            TestStep(
                name="test_current",
                outcome=Outcome.FAIL,
                vectors=[
                    TestVector(
                        outcome=Outcome.FAIL,
                        measurements=[
                            Measurement(
                                name="iout",
                                value=2.5,
                                units="A",
                                low_limit=0.0,
                                high_limit=2.0,
                                outcome=Outcome.FAIL,
                            ),
                        ],
                    )
                ],
            ),
        ],
    )
    return run


@pytest.fixture
def results_dir(tmp_path, sample_run):
    """Save sample run to a temp results dir and return the dir path."""
    rd = tmp_path / "results"
    backend = ParquetBackend(results_dir=rd / "runs")
    backend.save_test_run(sample_run)
    return rd


@pytest.fixture
def run_id(sample_run):
    return str(sample_run.id)


class TestLoadRunData:
    def test_basic_fields(self, results_dir, run_id):
        data = load_run_data(run_id, str(results_dir))
        assert data.run_id == run_id
        assert data.dut_serial == "SN-001"
        assert data.station_id == "bench_01"
        assert data.product_id == "widget_v1"
        assert data.operator_id == "test_op"
        assert data.git_commit == "abc123"

    def test_measurement_stats(self, results_dir, run_id):
        data = load_run_data(run_id, str(results_dir))
        assert data.total_measurements == 3
        assert data.passed_measurements == 2
        assert data.failed_measurements == 1
        assert data.pass_rate == 66.7

    def test_step_names(self, results_dir, run_id):
        data = load_run_data(run_id, str(results_dir))
        assert "test_voltage" in data.step_names
        assert "test_current" in data.step_names

    def test_not_found(self, results_dir):
        with pytest.raises(FileNotFoundError):
            load_run_data("nonexistent", str(results_dir))


class TestGenerateReport:
    def test_json(self, results_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(results_dir))
        out = generate_report(data, tmp_path / "report.json", fmt="json")
        assert out.exists()
        obj = json.loads(out.read_text())
        assert obj["run_id"] == run_id
        assert obj["summary"]["total"] == 3
        assert obj["summary"]["passed"] == 2
        assert obj["dut"]["serial"] == "SN-001"
        assert len(obj["measurements"]) == 3

    def test_csv(self, results_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(results_dir))
        out = generate_report(data, tmp_path / "report.csv", fmt="csv")
        assert out.exists()
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 4  # header + 3 measurements
        assert "measurement_name" in lines[0]

    def test_html(self, results_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(results_dir))
        out = generate_report(data, tmp_path / "report.html", fmt="html")
        assert out.exists()
        html = out.read_text()
        assert "Test Report" in html
        assert "SN-001" in html
        assert "vout" in html

    def test_directory_output(self, results_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(results_dir))
        out_dir = tmp_path / "reports"
        out = generate_report(data, out_dir, fmt="json")
        assert out.parent == out_dir
        assert out.suffix == ".json"

    def test_pdf_requires_weasyprint(self, results_dir, run_id, tmp_path):
        """PDF generation requires weasyprint — test import error handling."""
        data = load_run_data(run_id, str(results_dir))
        try:
            import weasyprint  # noqa: F401

            out = generate_report(data, tmp_path / "report.pdf", fmt="pdf")
            assert out.exists()
        except ImportError:
            with pytest.raises(ImportError, match="weasyprint"):
                generate_report(data, tmp_path / "report.pdf", fmt="pdf")


class TestTemplateResolution:
    def test_project_template_overrides(self, results_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(results_dir))

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

    def test_missing_template_raises(self, results_dir, run_id, tmp_path):
        data = load_run_data(run_id, str(results_dir))
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            generate_report(data, tmp_path / "report.html", fmt="html", template="nonexistent")


class TestProjectConfig:
    def test_load_missing(self, tmp_path):
        from litmus.store import load_project_config

        config = load_project_config(tmp_path / "litmus.yaml")
        assert config.results_dir is None
        assert config.outputs == []

    def test_load_valid(self, tmp_path):
        from litmus.store import load_project_config

        (tmp_path / "litmus.yaml").write_text(
            "name: test\nresults_dir: my_results\noutputs:\n  - format: html\n"
        )
        config = load_project_config(tmp_path / "litmus.yaml")
        assert config.results_dir == "my_results"
        assert len(config.outputs) == 1
        assert config.outputs[0].format == "html"


class TestCLI:
    def test_show_with_format(self, results_dir, run_id, tmp_path):
        from click.testing import CliRunner

        from litmus.cli import main

        runner = CliRunner()
        out_file = str(tmp_path / "report.json")
        result = runner.invoke(
            main,
            [
                "show",
                run_id,
                "--results-dir",
                str(results_dir),
                "-f",
                "json",
                "-o",
                out_file,
            ],
        )
        assert result.exit_code == 0
        assert "Report generated" in result.output
        assert Path(out_file).exists()

    def test_show_terminal(self, results_dir, run_id):
        from click.testing import CliRunner

        from litmus.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "show",
                run_id,
                "--results-dir",
                str(results_dir),
            ],
        )
        assert result.exit_code == 0
        assert "SN-001" in result.output
