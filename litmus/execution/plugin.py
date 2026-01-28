"""pytest plugin for Litmus test framework."""

import pytest

from litmus.data.backends.parquet import ParquetBackend
from litmus.execution.decorators import set_current_logger
from litmus.execution.logger import TestRunLogger


def pytest_addoption(parser):
    """Add Litmus command-line options."""
    group = parser.getgroup("litmus")
    group.addoption("--dut-serial", default="DUT001", help="DUT serial number")
    group.addoption("--station", default="station_001", help="Station ID")
    group.addoption("--operator", default=None, help="Operator name")
    group.addoption("--results-dir", default="results", help="Directory for Parquet results")


@pytest.fixture(scope="session")
def litmus_logger(request) -> TestRunLogger:
    """Provide test run logger for the session."""
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
