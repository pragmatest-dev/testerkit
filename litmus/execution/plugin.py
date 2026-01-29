"""pytest plugin for Litmus test framework."""

import time

import pytest
from _pytest.runner import runtestprotocol

from litmus.data.backends.parquet import ParquetBackend
from litmus.execution.decorators import set_current_logger
from litmus.execution.logger import TestRunLogger

# Track test outcomes for skip-on-failure logic
STEP_OUTCOMES: dict[str, bool] = {}


def pytest_configure(config):
    """Register Litmus markers."""
    config.addinivalue_line(
        "markers",
        "litmus_retry(max_attempts, delay): Retry test on failure",
    )
    config.addinivalue_line(
        "markers",
        "litmus_skip_on(dependencies): Skip if dependencies failed",
    )


def pytest_sessionstart(session):
    """Clear outcomes at session start."""
    STEP_OUTCOMES.clear()


def pytest_sessionfinish(session, exitstatus):
    """Clean up after session."""
    STEP_OUTCOMES.clear()


def pytest_addoption(parser):
    """Add Litmus command-line options."""
    group = parser.getgroup("litmus")
    group.addoption("--dut-serial", default="DUT001", help="DUT serial number")
    group.addoption("--station", default="station_001", help="Station ID")
    group.addoption("--operator", default=None, help="Operator name")
    group.addoption("--results-dir", default="results", help="Directory for Parquet results")
    group.addoption("--spec", default=None, help="Path to product spec YAML file")
    group.addoption("--guardband", default="0", help="Default guardband percentage")


@pytest.fixture(scope="session", autouse=True)
def litmus_logger(request) -> TestRunLogger:
    """Provide test run logger for the session.

    This fixture is autouse=True so it's always active, enabling
    @litmus_test decorated functions to log measurements.
    """
    logger = TestRunLogger(
        dut_serial=request.config.getoption("--dut-serial"),
        station_id=request.config.getoption("--station"),
        operator=request.config.getoption("--operator"),
        test_sequence_id=request.config.rootpath.name,
    )
    set_current_logger(logger)
    yield logger

    # Finalize and save
    test_run = logger.finalize()
    results_dir = request.config.getoption("--results-dir")
    backend = ParquetBackend(results_dir=results_dir)
    backend.save_test_run(test_run)
    set_current_logger(None)


@pytest.fixture
def litmus_step(litmus_logger, request):
    """Auto-create step for each test function."""
    litmus_logger.start_step(request.node.name)
    yield
    litmus_logger.end_step()


# Sentinel object to detect pytest-injected vector
_PYTEST_VECTOR_SENTINEL = object()


@pytest.fixture
def vector():
    """Placeholder fixture for @litmus_test decorated functions.

    The @litmus_test decorator injects the actual Vector object.
    This fixture just satisfies pytest's fixture resolution.
    """
    return _PYTEST_VECTOR_SENTINEL


@pytest.fixture(scope="session")
def spec_context(request):
    """Provide product spec context for spec-driven testing.

    Loads product spec from --spec option or auto-discovers from specs/ directory.
    Provides SpecContext for deriving limits and tracking channel traceability.

    Usage in tests:
        def test_voltage(spec_context, dmm):
            limit = spec_context.get_limit("output_voltage", temperature=25)
            value = dmm.measure_dc_voltage()
            # Use limit for validation...

    Returns:
        SpecContext or None if no spec configured.
    """
    from decimal import Decimal
    from pathlib import Path

    from litmus.products.context import SpecContext

    spec_path = request.config.getoption("--spec")
    guardband = Decimal(request.config.getoption("--guardband"))

    if spec_path:
        return SpecContext.from_file(spec_path, guardband_pct=guardband)

    # Try auto-discover from specs/ directory
    root = request.config.rootpath
    specs_dir = root / "specs"
    if specs_dir.exists():
        # Find first .yaml file
        yaml_files = list(specs_dir.glob("*.yaml"))
        if yaml_files:
            # Prefer non-underscore files
            for f in yaml_files:
                if not f.name.startswith("_"):
                    return SpecContext.from_file(f, guardband_pct=guardband)
            return SpecContext.from_file(yaml_files[0], guardband_pct=guardband)

    return None


def pytest_runtest_makereport(item, call):
    """Record test outcomes for skip-on-failure logic."""
    if call.when == "call":
        passed = call.excinfo is None
        STEP_OUTCOMES[item.name] = passed
        # Also track by nodeid for more specific matching
        STEP_OUTCOMES[item.nodeid] = passed


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Skip tests if their dependencies failed."""
    marker = item.get_closest_marker("litmus_skip_on")
    if marker is None:
        return

    dependencies = marker.args[0] if marker.args else []

    for dep in dependencies:
        # Check by exact test name or nodeid
        if dep in STEP_OUTCOMES and not STEP_OUTCOMES[dep]:
            pytest.skip(f"Dependency '{dep}' failed")
        # Also check partial matches (test name at end of nodeid)
        for key, passed in STEP_OUTCOMES.items():
            if key.endswith(dep) and not passed:
                pytest.skip(f"Dependency '{dep}' failed")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    """Implement retry logic for tests with litmus_retry marker."""
    marker = item.get_closest_marker("litmus_retry")
    if marker is None:
        return None  # Use default protocol

    max_attempts = marker.kwargs.get("max_attempts", 3)
    delay = marker.kwargs.get("delay", 0.0)

    for attempt in range(max_attempts):
        # Run the test
        reports = runtestprotocol(item, nextitem=nextitem, log=False)

        # Check if passed
        call_report = next((r for r in reports if r.when == "call"), None)
        if call_report and not call_report.failed:
            # Test passed, report and exit
            for report in reports:
                item.ihook.pytest_runtest_logreport(report=report)
            return True

        # Test failed
        if attempt < max_attempts - 1:
            # More attempts remaining, sleep and retry
            if delay > 0:
                time.sleep(delay)
        else:
            # Final attempt failed, report failure
            for report in reports:
                item.ihook.pytest_runtest_logreport(report=report)

    return True
