"""pytest plugin for Litmus test framework."""

import os
import time
from pathlib import Path
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
        "--mock-instruments",
        action="store_true",
        default=False,
        help="Use mock instruments instead of real hardware",
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


def _get_git_commit() -> str | None:
    """Get current git commit hash, or None if not in a git repo."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()[:12]  # Short hash
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _serialize_config(config: dict | None) -> str | None:
    """Serialize config dict to YAML string for storage."""
    if config is None:
        return None
    return yaml.dump(config, default_flow_style=False, sort_keys=False)


def _safe_get_session_fixture(request, name):
    """Safely get a session-scoped fixture value, returning None if not available.

    Only attempts to access fixtures that exist at session scope to avoid
    ScopeMismatch errors from test-defined fixtures with the same name.
    """
    # Check if the fixture exists and is session-scoped before accessing
    try:
        # Look up the fixture definition
        fixturedefs = request._fixturemanager.getfixturedefs(name, request._pyfuncitem.nodeid)
        if not fixturedefs:
            return None
        # Check if any fixture with this name is session-scoped
        for fixturedef in fixturedefs:
            if fixturedef.scope == "session":
                return request.getfixturevalue(name)
        return None
    except Exception:
        return None


@pytest.fixture(scope="session", autouse=True)
def litmus_logger(request) -> TestRunLogger:
    """Provide test run logger for the session.

    This fixture is autouse=True so it's always active, enabling
    @litmus_test decorated functions to log measurements.

    Captures config snapshots at run start for full traceability.
    Streams measurements to a JSONL journal for live observability.
    """
    # Safely access optional session-scoped fixtures
    # (avoids ScopeMismatch from test-defined fixtures with same name)
    station_config = _safe_get_session_fixture(request, "station_config")
    fixture_config = _safe_get_session_fixture(request, "fixture_config")
    spec_context = _safe_get_session_fixture(request, "spec_context")

    # Serialize configs for storage
    station_yaml = _serialize_config(station_config) if station_config else None

    # Get product info from spec_context
    product_id = None
    product_name = None
    product_revision = None
    product_yaml = None
    if spec_context:
        product_id = spec_context.product.id
        product_name = spec_context.product.name
        product_revision = spec_context.product.revision
        # Serialize product spec
        product_yaml = yaml.dump(
            spec_context.product.model_dump(mode="json", exclude_none=True),
            default_flow_style=False,
            sort_keys=False,
        )

    # Get fixture info
    fixture_id = None
    fixture_yaml = None
    if fixture_config:
        fixture_id = getattr(fixture_config, "id", None) or getattr(fixture_config, "name", None)
        fixture_yaml = yaml.dump(
            fixture_config.model_dump(mode="json", exclude_none=True)
            if hasattr(fixture_config, "model_dump")
            else fixture_config,
            default_flow_style=False,
            sort_keys=False,
        )

    # Get station info
    station_id = request.config.getoption("--station")
    station_type = None
    station_location = None
    if station_config:
        station_type = station_config.get("station_type") or station_config.get("type")
        station_location = station_config.get("location")

    # Get results directory for journal streaming
    results_dir = request.config.getoption("--results-dir")

    logger = TestRunLogger(
        dut_serial=request.config.getoption("--dut-serial"),
        station_id=station_id,
        station_type=station_type,
        station_location=station_location,
        operator_id=request.config.getoption("--operator"),
        test_sequence_id=request.config.rootpath.name,
        product_id=product_id,
        product_name=product_name,
        product_revision=product_revision,
        fixture_id=fixture_id,
        station_config_yaml=station_yaml,
        product_spec_yaml=product_yaml,
        fixture_config_yaml=fixture_yaml,
        git_commit=_get_git_commit(),
        results_dir=results_dir,  # Enable journal streaming
    )
    set_current_logger(logger)
    yield logger

    # Finalize and save
    test_run = logger.finalize()
    backend = ParquetBackend(results_dir=results_dir)

    # Convert journal to parquet if journaling was enabled
    journal_dir = logger.journal_dir
    backend.save_test_run(test_run, journal_dir=journal_dir)

    set_current_logger(None)


@pytest.fixture(scope="session")
def run_context(litmus_logger):
    """Provide run context for adding custom metadata.

    This is the run-level context that persists across all tests in the session.
    For step or vector-scoped context, use the `context` fixture instead.

    Usage:
        def test_example(run_context):
            run_context.set("operator_badge", "EMP-12345")
            run_context.set("fixture_serial", "FIX-001")
    """
    return litmus_logger.run_context


@pytest.fixture
def litmus_step(litmus_logger, request):
    """Auto-create step for each test function."""
    litmus_logger.start_step(request.node.name)
    yield
    litmus_logger.end_step()


# Sentinel object to detect pytest-injected context
_PYTEST_CONTEXT_SENTINEL = object()


@pytest.fixture
def context():
    """Context fixture for @litmus_test decorated functions.

    The @litmus_test decorator injects the actual Context object from
    the TestHarness. Context is THE primary API for test functions:

    Access vector parameters (inputs):
        temp = context.get_in("temperature")
        vin = context.inputs["vin"]

    Record observations:
        context.observe("dut_temp", 42.3)

    Record commanded values:
        context.configure("psu.voltage", 5.0)

    The context contains all vector parameters automatically.
    This fixture just satisfies pytest's fixture resolution.
    """
    return _PYTEST_CONTEXT_SENTINEL


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
    from litmus.products.context import SpecContext

    spec_path = request.config.getoption("--spec")
    guardband = float(request.config.getoption("--guardband"))

    if spec_path:
        return SpecContext.from_file(spec_path, guardband_pct=guardband)

    # Try auto-discover from products/ directory
    # Check both rootpath and invocation directory (cwd) for nested project support
    from pathlib import Path

    search_roots = [
        request.config.rootpath,
        Path(request.config.invocation_params.dir),  # Where pytest was invoked
    ]

    for root in search_roots:
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
def mock_instruments(request) -> bool:
    """Return whether to use mock instruments instead of real hardware.

    Checks both:
    - --mock-instruments pytest option
    - LITMUS_MOCK_INSTRUMENTS environment variable (set by UI)
    """
    return (
        request.config.getoption("--mock-instruments")
        or os.environ.get("LITMUS_MOCK_INSTRUMENTS") == "1"
    )


@pytest.fixture(scope="session")
def station_config(request) -> dict[str, Any] | None:
    """Load station configuration from --station-config option.

    Returns:
        Station configuration dict, or None if not specified.
    """
    config_path = request.config.getoption("--station-config")
    if not config_path:
        # Try auto-discover from stations/ directory
        # Check both rootpath and invocation directory (for nested projects like demo/)
        station_id = request.config.getoption("--station")
        search_roots = [
            request.config.rootpath,
            Path(request.config.invocation_params.dir),  # Where pytest was invoked
        ]
        for root in search_roots:
            stations_dir = root / "stations"
            if stations_dir.exists():
                station_file = stations_dir / f"{station_id}.yaml"
                if station_file.exists():
                    config_path = str(station_file)
                    break

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
        # Check both rootpath and invocation directory (for nested projects like demo/)
        search_roots = [
            request.config.rootpath,
            Path(request.config.invocation_params.dir),  # Where pytest was invoked
        ]
        for root in search_roots:
            fixtures_dir = root / "fixtures"
            if fixtures_dir.exists():
                yaml_files = list(fixtures_dir.glob("*.yaml"))
                if yaml_files:
                    config_path = str(yaml_files[0])
                    break

    if config_path:
        with open(config_path) as f:
            data = yaml.safe_load(f)
            return FixtureConfig.model_validate(data.get("fixture", data))
    return None


def _get_driver_class(instrument_type: str):
    """Get driver class for an instrument type."""
    from litmus.instruments import DMM, PSU, ELoad, Scope

    drivers = {
        "dmm": DMM,
        "psu": PSU,
        "eload": ELoad,
        "scope": Scope,
    }
    return drivers.get(instrument_type.lower())


@pytest.fixture(scope="session")
def instruments(request, station_config, mock_instruments) -> dict[str, Any]:
    """Create instrument instances from station configuration.

    Instruments are connected at session start and disconnected at end.

    When --mock-instruments is passed (or LITMUS_MOCK_INSTRUMENTS=1), uses mock
    instruments instead of real drivers. Mocks are config-driven and instant.

    Returns:
        Dictionary mapping instrument names to instances.
    """
    global _ACTIVE_INSTRUMENTS
    _ACTIVE_INSTRUMENTS.clear()

    if station_config:
        from litmus.instruments.mocks import Mock

        inst_configs = station_config.get("instruments", {})
        for name, config in inst_configs.items():
            inst_type = config.get("type", "")
            mock_config = config.get("mock_config", config.get("sim_config", {}))
            use_mock = mock_instruments or config.get("mock", config.get("simulate", False))

            driver_class = _get_driver_class(inst_type)
            if driver_class is None:
                continue

            if use_mock:
                # Use mock instruments - config-driven, instant
                inst = Mock(driver_class, **mock_config)
            else:
                # Real hardware path
                resource = config.get("resource", "")
                inst = driver_class(resource=resource)

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
    """Reset mock state and skip tests if their dependencies failed."""
    # Reset mock state for clean test isolation
    for inst in _ACTIVE_INSTRUMENTS.values():
        if hasattr(inst, "reset_mock_state"):
            inst.reset_mock_state()

    # Check skip-on-failure dependencies
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
