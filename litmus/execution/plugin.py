"""pytest plugin for Litmus test framework."""

import time
from typing import Any

import pytest
import yaml
from _pytest.runner import runtestprotocol

from litmus.data.backends.parquet import ParquetBackend
from litmus.execution.decorators import set_current_logger
from litmus.execution.logger import TestRunLogger

# Track test outcomes for skip-on-failure logic
STEP_OUTCOMES: dict[str, bool] = {}

# Track instrument instances for cleanup
_ACTIVE_INSTRUMENTS: dict[str, Any] = {}


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
    group.addoption(
        "--simulate",
        action="store_true",
        default=False,
        help="Run instruments in simulation mode",
    )
    group.addoption(
        "--fixture-config",
        default=None,
        help="Path to fixture configuration YAML file",
    )
    group.addoption(
        "--station-config",
        default=None,
        help="Path to station configuration YAML file",
    )


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

    Loads product spec from --spec option or auto-discovers from products/ directory.
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

    from litmus.products.context import SpecContext

    spec_path = request.config.getoption("--spec")
    guardband = Decimal(request.config.getoption("--guardband"))

    if spec_path:
        return SpecContext.from_file(spec_path, guardband_pct=guardband)

    # Try auto-discover from products/ directory
    root = request.config.rootpath
    products_dir = root / "products"
    if products_dir.exists():
        # Find first product folder with spec.yaml
        for product_folder in products_dir.iterdir():
            if product_folder.is_dir() and not product_folder.name.startswith("_"):
                spec_file = product_folder / "spec.yaml"
                if spec_file.exists():
                    return SpecContext.from_file(spec_file, guardband_pct=guardband)

    return None


@pytest.fixture(scope="session")
def simulate(request) -> bool:
    """Return whether instruments should run in simulation mode.

    Checks both:
    - --simulate pytest option
    - LITMUS_SIMULATE environment variable (set by UI when launching with simulation)
    """
    import os

    return request.config.getoption("--simulate") or os.environ.get("LITMUS_SIMULATE") == "1"


@pytest.fixture(scope="session")
def station_config(request) -> dict[str, Any] | None:
    """Load station configuration from --station-config option.

    Returns:
        Station configuration dict, or None if not specified.
    """
    config_path = request.config.getoption("--station-config")
    if not config_path:
        # Try auto-discover from stations/ directory
        root = request.config.rootpath
        station_id = request.config.getoption("--station")
        stations_dir = root / "stations"
        if stations_dir.exists():
            station_file = stations_dir / f"{station_id}.yaml"
            if station_file.exists():
                config_path = str(station_file)

    if config_path:
        with open(config_path) as f:
            return yaml.safe_load(f)
    return None


@pytest.fixture(scope="session")
def fixture_config(request):
    """Load fixture configuration from --fixture-config option.

    Returns:
        FixtureConfig instance, or None if not specified.
    """
    from litmus.config.models import FixtureConfig

    config_path = request.config.getoption("--fixture-config")
    if not config_path:
        # Try auto-discover from fixtures/ directory
        root = request.config.rootpath
        fixtures_dir = root / "fixtures"
        if fixtures_dir.exists():
            yaml_files = list(fixtures_dir.glob("*.yaml"))
            if yaml_files:
                config_path = str(yaml_files[0])

    if config_path:
        with open(config_path) as f:
            data = yaml.safe_load(f)
            return FixtureConfig.model_validate(data.get("fixture", data))
    return None


def _get_driver_class(instrument_type: str):
    """Get driver class for an instrument type."""
    from litmus.instruments import DMM, ELoad, PSU, Scope

    drivers = {
        "dmm": DMM,
        "psu": PSU,
        "eload": ELoad,
        "scope": Scope,
    }
    return drivers.get(instrument_type.lower())


@pytest.fixture(scope="session")
def instruments(request, station_config, simulate) -> dict[str, Any]:
    """Create instrument instances from station configuration.

    Instruments are connected at session start and disconnected at end.
    Uses simulation mode if --simulate is passed.

    Returns:
        Dictionary mapping instrument names to instances.
    """
    global _ACTIVE_INSTRUMENTS
    _ACTIVE_INSTRUMENTS.clear()

    if station_config:
        # Load instruments from station config
        inst_configs = station_config.get("instruments", {})
        for name, config in inst_configs.items():
            driver_class = _get_driver_class(config.get("type", ""))
            if driver_class is None:
                continue

            resource = config.get("resource", "")
            sim_config = config.get("sim_config", {})

            # Create and connect instrument
            inst = driver_class(
                resource=resource,
                simulate=simulate or config.get("simulate", False),
                sim_config=sim_config,
            )
            inst.connect()
            _ACTIVE_INSTRUMENTS[name] = inst

    yield _ACTIVE_INSTRUMENTS

    # Cleanup: disconnect all instruments
    for inst in _ACTIVE_INSTRUMENTS.values():
        try:
            inst.disconnect()
        except Exception:
            pass
    _ACTIVE_INSTRUMENTS.clear()


@pytest.fixture(scope="session")
def pins(instruments, fixture_config):
    """UUT-centric pin accessor for tests.

    Resolves DUT pin names to instrument instances:

        def test_output(pins):
            pins["VIN"].set_voltage(5.0)
            pins["VIN"].enable_output()
            assert pins["VOUT"].measure_voltage() > 3.0

    Returns:
        PinAccessor instance, or None if no fixture configured.
    """
    from litmus.fixtures.manager import FixtureManager, PinAccessor

    if not fixture_config or not instruments:
        return None

    manager = FixtureManager(fixture_config, instruments)
    return PinAccessor(manager)


@pytest.fixture(scope="session")
def fixture_manager(instruments, fixture_config):
    """Fixture manager for advanced pin/net routing.

    Provides direct access to the FixtureManager for tests that need
    advanced routing methods beyond the simple pins[] accessor:

        def test_with_net_lookup(fixture_manager):
            point = fixture_manager.get_point_for_net("VOUT_3V3")
            instrument = fixture_manager.get_instrument_for_point(point.name)

    Returns:
        FixtureManager instance, or None if no fixture configured.
    """
    from litmus.fixtures.manager import FixtureManager

    if not fixture_config or not instruments:
        return None

    return FixtureManager(fixture_config, instruments)


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
