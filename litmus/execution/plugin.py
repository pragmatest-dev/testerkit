"""pytest plugin for Litmus test framework."""

import os
import time
import warnings
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import yaml
from _pytest.runner import runtestprotocol

from litmus.data.backends.parquet import ParquetBackend
from litmus.execution.decorators import set_current_logger
from litmus.execution.logger import TestRunLogger
from litmus.instruments.models import CalibrationInfo, InstrumentInfo, InstrumentRecord

# Track test outcomes for skip-on-failure logic
STEP_OUTCOMES: dict[str, bool] = {}

# Track instrument instances for cleanup
_ACTIVE_INSTRUMENTS: dict[str, Any] = {}

# Track instrument records for traceability
_INSTRUMENT_RECORDS: dict[str, InstrumentRecord] = {}

# Per-step alias state (set in pytest_runtest_setup, read by alias fixtures)
_CURRENT_STEP_ALIASES: dict[str, str] = {}

# Per-step config (vectors, limits, mocks, retry) from sequence
_CURRENT_STEP_CONFIG: dict[str, Any] = {}

# Track active spec context for use by @litmus_test decorator
_ACTIVE_SPEC_CONTEXT: Any = None

# Mapping: test node ID → step aliases (built from --sequence)
_TEST_NODE_ALIASES: dict[str, dict[str, str]] = {}

# Mapping: test node ID → full step config (built from --sequence)
_TEST_NODE_CONFIGS: dict[str, dict[str, Any]] = {}


def _load_sequence_data(config) -> list[dict[str, Any]]:
    """Load steps from a sequence file.

    Returns the raw list of step dicts from the sequence YAML.
    """
    seq_option = config.getoption("--sequence", default=None)
    if not seq_option:
        return []

    # Find the sequence file
    seq_path = Path(seq_option)
    if not seq_path.exists():
        # Try sequences/ directories
        search_roots = [
            config.rootpath,
            Path(config.invocation_params.dir),
        ]
        for root in search_roots:
            candidate = root / "sequences" / f"{seq_option}.yaml"
            if candidate.exists():
                seq_path = candidate
                break
        else:
            return []

    try:
        with open(seq_path) as f:
            data = yaml.safe_load(f)
    except Exception:
        return []

    if not data:
        return []

    # Handle both top-level 'steps' and nested 'sequence.steps'
    seq = data.get("sequence", data)
    return data.get("steps", seq.get("steps", []))


def _load_step_aliases(config) -> dict[str, dict[str, str]]:
    """Load per-step aliases from a sequence file.

    Returns mapping of test node ID → {alias_name: station_role}.
    """
    steps = _load_sequence_data(config)
    result: dict[str, dict[str, str]] = {}
    for step in steps:
        test_node = step.get("test")
        aliases = step.get("aliases", {})
        if test_node and aliases:
            result[test_node] = aliases
    return result


def _load_step_configs(config) -> dict[str, dict[str, Any]]:
    """Load per-step configs (vectors, limits, mocks, retry) from sequence.

    Returns mapping of test node ID → config dict with keys:
    vectors, limits, mocks, retry (only present if specified in sequence).
    """
    steps = _load_sequence_data(config)
    result: dict[str, dict[str, Any]] = {}
    for step in steps:
        test_node = step.get("test")
        if not test_node:
            continue
        step_config: dict[str, Any] = {}
        for key in ("vectors", "limits", "mocks", "retry"):
            if key in step:
                step_config[key] = step[key]
        if step_config:
            result[test_node] = step_config
    return result


def _find_station_file(config) -> Path | None:
    """Find station config file from pytest config options.

    Extracts station file resolution logic so both the station_config fixture
    and the auto-registration hook can reuse it.

    Args:
        config: pytest Config object

    Returns:
        Path to station config file, or None if not found.
    """
    config_path = config.getoption("--station-config")
    if config_path:
        return Path(config_path)

    # Try auto-discover from stations/ directory
    station_id = config.getoption("--station")
    search_roots = [
        config.rootpath,
        Path(config.invocation_params.dir),
    ]
    for root in search_roots:
        stations_dir = root / "stations"
        if stations_dir.exists():
            station_file = stations_dir / f"{station_id}.yaml"
            if station_file.exists():
                return station_file

    return None


def pytest_configure(config):
    """Register Litmus markers and auto-register instrument role fixtures."""
    config.addinivalue_line(
        "markers",
        "litmus_retry(max_attempts, delay): Retry test on failure",
    )
    config.addinivalue_line(
        "markers",
        "litmus_skip_on(dependencies): Skip if dependencies failed",
    )

    # Auto-register instrument role fixtures from station config
    station_path = _find_station_file(config)
    if station_path is None:
        return

    try:
        with open(station_path) as f:
            station_data = yaml.safe_load(f)
    except Exception:
        return

    if not station_data:
        return

    instruments_map = station_data.get("instruments", {})
    if not instruments_map:
        return

    # Load per-step aliases and configs from sequence (if --sequence provided)
    global _TEST_NODE_ALIASES, _TEST_NODE_CONFIGS
    _TEST_NODE_ALIASES = _load_step_aliases(config)
    _TEST_NODE_CONFIGS = _load_step_configs(config)

    # Collect all alias names used across all steps
    all_alias_names: set[str] = set()
    for step_aliases in _TEST_NODE_ALIASES.values():
        all_alias_names.update(step_aliases.keys())

    # Build a plugin class with fixture functions per role.
    # Wrap each fixture in staticmethod to prevent Python's descriptor
    # protocol from injecting self as the first argument.
    class _InstrumentFixtures:
        pass

    # Determine which station roles need to become function-scoped
    # (because an alias in some step overrides that name)
    aliased_role_names = all_alias_names & set(instruments_map.keys())

    for role in instruments_map:
        if role in aliased_role_names:
            # This role name is also used as an alias target — make it
            # function-scoped so it can resolve differently per step
            def _make_aliased(r=role):
                @pytest.fixture
                def _fix(instruments):
                    target = _CURRENT_STEP_ALIASES.get(r, r)
                    if target not in instruments:
                        available = ", ".join(sorted(instruments)) or "(none)"
                        raise KeyError(
                            f"Alias '{r}' targets '{target}' which is not in "
                            f"station instruments. Available: {available}"
                        )
                    return instruments[target]
                _fix.__name__ = r
                _fix.__qualname__ = r
                return _fix
            setattr(_InstrumentFixtures, role, staticmethod(_make_aliased()))
        else:
            def _make(r=role):
                @pytest.fixture(scope="session")
                def _fix(instruments):
                    return instruments.get(r)
                _fix.__name__ = r
                _fix.__qualname__ = r
                return _fix
            setattr(_InstrumentFixtures, role, staticmethod(_make()))

    # Register function-scoped fixtures for alias names that aren't station roles
    for alias in all_alias_names - set(instruments_map.keys()):
        def _make_alias(a=alias):
            @pytest.fixture
            def _fix(instruments):
                target = _CURRENT_STEP_ALIASES.get(a, a)
                if target not in instruments:
                    available = ", ".join(sorted(instruments)) or "(none)"
                    raise KeyError(
                        f"Alias '{a}' targets '{target}' which is not in "
                        f"station instruments. Available: {available}"
                    )
                return instruments[target]
            _fix.__name__ = a
            _fix.__qualname__ = a
            return _fix
        setattr(_InstrumentFixtures, alias, staticmethod(_make_alias()))

    config.pluginmanager.register(
        _InstrumentFixtures(), "litmus_instrument_fixtures"
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
    group.addoption("--dut-part-number", default=None, help="DUT part number")
    group.addoption("--dut-revision", default=None, help="DUT revision")
    group.addoption("--dut-lot", default=None, help="DUT lot/batch number")
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
    group.addoption(
        "--sequence",
        default=None,
        help="Sequence ID or path to sequence YAML (enables per-step aliases)",
    )
    group.addoption(
        "--test-phase",
        default=None,
        help="Test phase (development, validation, characterization, production). "
        "If not specified, auto-detects from git status.",
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


def _is_git_clean() -> bool:
    """Check if we're in a clean git repository.

    Returns True only if:
    - Git is installed
    - We're in a git repository
    - There are no uncommitted changes

    Returns False otherwise.
    """
    import subprocess

    try:
        # Check if we're in a git repo
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False

        # Check for uncommitted changes (staged or unstaged)
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False

        # If there's any output, the repo is dirty
        if result.stdout.strip():
            return False

        return True

    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _resolve_test_phase(requested_phase: str | None) -> str:
    """Resolve test phase, enforcing development for dirty/non-git repos.

    If git is unavailable or repo has uncommitted changes, always returns
    "development" regardless of requested phase. This prevents non-development
    runs from being created in untracked environments.

    Args:
        requested_phase: Explicitly requested phase, or None for auto-detect

    Returns:
        Resolved test phase string
    """
    if not _is_git_clean():
        # Can't run anything other than development without clean git
        return "development"

    # Clean repo - use requested phase or default to production
    return requested_phase or "production"


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
    try:
        return request.getfixturevalue(name)
    except pytest.FixtureLookupError:
        return None
    except Exception:
        return None


def _maybe_auto_report(run_id: str, results_dir: str) -> None:
    """Generate a report after test run if configured in litmus.yaml."""
    try:
        from litmus.config.project import load_project_config

        config = load_project_config()
        reports_config = config.get("reports", {})
        if not reports_config.get("auto", False):
            return

        from litmus.reports import generate_report, load_run_data

        fmt = reports_config.get("format", "html")
        template = reports_config.get("template", "default")
        output_dir = reports_config.get("output_dir", "reports")

        data = load_run_data(run_id, results_dir)
        generate_report(data, output_dir, fmt=fmt, template=template)
    except Exception:
        pass  # Auto-report is best-effort; don't fail the test run


@pytest.fixture(scope="session", autouse=True)
def litmus_logger(request) -> Generator[TestRunLogger]:
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
    station_name = None
    station_type = None
    station_location = None
    if station_config:
        station_data = station_config.get("station", {})
        station_name = station_data.get("name") if isinstance(station_data, dict) else None
        station_type = station_config.get("station_type") or station_config.get("type")
        station_location = station_config.get("location")

    # Get results directory for journal streaming
    results_dir = request.config.getoption("--results-dir")

    # Determine test phase: requested phase is validated against git status
    # If repo is dirty or git unavailable, always "development"
    requested_phase = (
        request.config.getoption("--test-phase")
        or os.environ.get("LITMUS_TEST_PHASE")
    )
    test_phase = _resolve_test_phase(requested_phase)

    # Get instrument records for traceability
    instrument_records = _safe_get_session_fixture(request, "instrument_records")

    # Auto-populate DUT part_number/revision from product spec when CLI flags absent
    cli_part_number = request.config.getoption("--dut-part-number")
    dut_part_number = cli_part_number or (
        spec_context.product.part_number if spec_context else None
    )
    cli_revision = request.config.getoption("--dut-revision")
    dut_revision = cli_revision or (
        spec_context.product.revision if spec_context else None
    )

    logger = TestRunLogger(
        dut_serial=request.config.getoption("--dut-serial"),
        dut_part_number=dut_part_number,
        dut_revision=dut_revision,
        dut_lot_number=request.config.getoption("--dut-lot"),
        station_id=station_id,
        station_name=station_name,
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
        test_phase=test_phase,  # Auto-detected from git status
        instruments=instrument_records,  # Full instrument records with calibration
    )
    set_current_logger(logger)
    yield logger

    # Finalize and save
    test_run = logger.finalize()
    backend = ParquetBackend(results_dir=results_dir)

    # Convert journal to parquet if journaling was enabled
    # Instrument arrays are per-step (embedded in each step and journal row)
    journal_dir = logger.journal_dir
    backend.save_test_run(
        test_run,
        journal_dir=journal_dir,
    )

    _maybe_auto_report(str(test_run.id), results_dir)
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

    global _ACTIVE_SPEC_CONTEXT

    ctx = None

    if spec_path:
        ctx = SpecContext.from_file(spec_path, guardband_pct=guardband)
    else:
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
                            ctx = SpecContext.from_file(spec_file, guardband_pct=guardband)
                            break
            if ctx:
                break

    _ACTIVE_SPEC_CONTEXT = ctx
    return ctx


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
    station_path = _find_station_file(request.config)
    if station_path:
        with open(station_path) as f:
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


def _load_driver_class(driver_path: str | None):
    """Load a driver class from import path.

    Args:
        driver_path: Dotted import path like "pymeasure.instruments.keithley.Keithley2400"

    Returns the class or None if not found.
    """
    if not driver_path:
        return None

    try:
        import importlib

        module_path, class_name = driver_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except (ImportError, AttributeError, ValueError):
        return None


def _check_calibration(role: str, calibration: CalibrationInfo) -> None:
    """Check calibration status and emit warnings if needed."""
    if not calibration or not calibration.due_date:
        return

    days_until = calibration.days_until_due()
    if days_until is None:
        return

    if days_until < 0:
        warnings.warn(
            f"{role}: CALIBRATION EXPIRED (due {calibration.due_date}, "
            f"{-days_until} days overdue)",
            UserWarning,
            stacklevel=3,
        )
    elif days_until < 30:
        warnings.warn(
            f"{role}: calibration due soon ({calibration.due_date}, "
            f"{days_until} days remaining)",
            UserWarning,
            stacklevel=3,
        )


def _verify_instrument_identity(
    role: str,
    actual: InstrumentInfo,
    expected: InstrumentInfo,
    strict: bool = False,
) -> None:
    """Verify instrument identity matches expected configuration.

    Args:
        role: Instrument role name for error messages
        actual: InstrumentInfo queried from device
        expected: InstrumentInfo from configuration
        strict: If True, raise error on mismatch. If False, warn only.

    Raises:
        RuntimeError: If strict and identity doesn't match
    """
    if not expected:
        return  # No expected identity configured

    matches, mismatches = actual.matches(expected)
    if not matches:
        msg = f"{role}: instrument identity mismatch - {'; '.join(mismatches)}"
        if strict:
            raise RuntimeError(msg)
        warnings.warn(msg, UserWarning, stacklevel=3)


class StationError(Exception):
    """Error during station instrument setup."""

    pass


@pytest.fixture(scope="session")
def instrument_records(request, station_config, mock_instruments) -> dict[str, InstrumentRecord]:
    """Load and resolve instrument records from configuration.

    This fixture loads instrument files and station config, resolving
    all references to produce InstrumentRecord objects with full
    identity and calibration info.

    Returns:
        Dict mapping role name to InstrumentRecord
    """
    global _INSTRUMENT_RECORDS
    _INSTRUMENT_RECORDS.clear()

    if not station_config:
        return _INSTRUMENT_RECORDS

    # Try to find and load instrument files
    from litmus.instruments.loader import (
        find_instruments_dir,
        load_instrument_files,
        resolve_station_instruments,
    )

    # Search from pytest invocation directory
    invocation_dir = Path(request.config.invocation_params.dir)
    instruments_dir = find_instruments_dir(invocation_dir)

    instrument_files: dict[str, dict[str, Any]] = {}
    if instruments_dir:
        instrument_files = load_instrument_files(instruments_dir)

    # Resolve station instruments to records
    _INSTRUMENT_RECORDS = resolve_station_instruments(station_config, instrument_files)

    return _INSTRUMENT_RECORDS


@pytest.fixture(scope="session")
def instruments(
    request, station_config, mock_instruments, instrument_records
) -> Generator[dict[str, Any]]:
    """Create instrument instances from station configuration.

    Instruments are connected at session start and disconnected at end.
    For real hardware, identity is verified against configuration.
    Calibration status is checked and warnings issued if due/expired.

    When --mock-instruments is passed (or LITMUS_MOCK_INSTRUMENTS=1), uses mock
    instruments instead of real drivers. Mocks are config-driven and instant.

    Station config formats supported:

    Legacy format (inline config):
        instruments:
          dmm:
            driver: pymeasure.instruments.keithley.Keithley2000
            resource: GPIB::16::INSTR
            mock_config:
              measure_voltage: 3.3

    New format (reference to instrument files):
        instruments:
          dmm: keithley_dmm_001
        resources:
          keithley_dmm_001: GPIB::16::INSTR

    Returns:
        Dictionary mapping instrument role names to driver instances.
    """
    global _ACTIVE_INSTRUMENTS
    _ACTIVE_INSTRUMENTS.clear()

    if not station_config:
        yield _ACTIVE_INSTRUMENTS
        return

    from litmus.instruments.mocks import Mock

    # Get inline instrument configs (legacy format)
    inst_configs = station_config.get("instruments", {})

    for role, record in instrument_records.items():
        # Get config - either from record (new format) or inline (legacy)
        inline_config = inst_configs.get(role, {})
        if isinstance(inline_config, str):
            # New format - role points to instrument ID, config in record
            inline_config = {}

        mock_config = inline_config.get("mock_config", {})
        use_mock = mock_instruments or inline_config.get("mock", False)
        record.mocked = use_mock

        driver_class = _load_driver_class(record.driver)
        if driver_class is None and not use_mock:
            # No driver and not in mock mode - can't connect to real hardware
            continue

        if use_mock:
            # Mock mode - use Mock factory
            # If driver specified, mock that class; otherwise create generic mock
            inst = Mock(driver_class, **mock_config) if driver_class else Mock(**mock_config)
            # For mocks, identity comes from config (no real device to query)
            if record.info:
                inst.manufacturer = record.info.manufacturer
                inst.model = record.info.model
                inst.serial = record.info.serial
                inst.firmware = record.info.firmware
        else:
            # Real hardware path
            inst = driver_class(record.resource)

            if hasattr(inst, "connect"):
                inst.connect()

            # Query and verify identity
            actual_info = _get_instrument_info(inst, record.protocol)
            if actual_info:
                # Verify against expected (warn on mismatch, don't fail)
                _verify_instrument_identity(role, actual_info, record.info, strict=False)
                # Update record with actual info for logging
                record.info = actual_info
            elif record.info:
                # Couldn't query device but have expected info - warn
                warnings.warn(
                    f"{role}: could not query instrument identity",
                    UserWarning,
                    stacklevel=2,
                )

        if hasattr(inst, "connect") and use_mock:
            inst.connect()

        # Check calibration status
        _check_calibration(role, record.calibration)

        _ACTIVE_INSTRUMENTS[role] = inst

    yield _ACTIVE_INSTRUMENTS

    # Cleanup: disconnect all instruments
    for inst in _ACTIVE_INSTRUMENTS.values():
        try:
            if hasattr(inst, "disconnect"):
                inst.disconnect()
        except Exception:
            pass
    _ACTIVE_INSTRUMENTS.clear()


class InstrumentAccessor:
    """Callable accessor for instruments by role, with type-based grouping."""

    def __init__(self, instruments: dict[str, Any], records: dict[str, InstrumentRecord]):
        self._instruments = instruments
        self._records = records

    def __call__(self, role: str) -> Any:
        """Get instrument by role name, resolving aliases. Raises KeyError with available roles."""
        # Resolve through per-step aliases first
        resolved = _CURRENT_STEP_ALIASES.get(role, role)
        if resolved not in self._instruments:
            available = ", ".join(sorted(self._instruments)) or "(none)"
            if resolved != role:
                raise KeyError(
                    f"Alias '{role}' targets '{resolved}' which is not in "
                    f"station instruments. Available: {available}"
                )
            raise KeyError(
                f"No instrument with role '{role}'. Available: {available}"
            )
        return self._instruments[resolved]

    def by_type(self, driver_path: str) -> dict[str, Any]:
        """Get all instruments matching a driver class import path."""
        return {
            role: self._instruments[role]
            for role, record in self._records.items()
            if record.driver == driver_path and role in self._instruments
        }

    def roles(self) -> list[str]:
        """List available instrument role names, including active aliases."""
        names = set(self._instruments.keys())
        names.update(_CURRENT_STEP_ALIASES.keys())
        return sorted(names)


@pytest.fixture
def instrument(instruments, instrument_records):
    """Accessor for instruments by role with grouping support.

    Usage:
        def test_voltage(instrument):
            dmm = instrument("dmm")

        def test_all_dmms(instrument):
            dmms = instrument.by_type("pymeasure.instruments.keithley.Keithley2000")
    """
    return InstrumentAccessor(instruments, instrument_records)


def _get_instrument_info(inst: Any, protocol: str = "visa") -> InstrumentInfo | None:
    """Get instrument identity from connected instance.

    For VISA instruments, queries *IDN?. For other protocols,
    uses protocol-specific methods.

    Args:
        inst: Connected instrument instance
        protocol: Protocol type ("visa", "ni", etc.)

    Returns:
        InstrumentInfo or None if query fails
    """
    # First check if instrument already has identity attributes
    if hasattr(inst, "manufacturer") and inst.manufacturer:
        return InstrumentInfo(
            manufacturer=getattr(inst, "manufacturer", None),
            model=getattr(inst, "model", None),
            serial=getattr(inst, "serial", None),
            firmware=getattr(inst, "firmware", None),
        )

    # Try to query via SCPI *IDN?
    if hasattr(inst, "query"):
        try:
            from litmus.instruments.discovery import parse_idn

            idn = inst.query("*IDN?")
            return parse_idn(idn)
        except Exception:
            pass

    return None


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
    """Reset mock state, set per-step aliases/config, and skip tests if dependencies failed."""
    # Set per-step aliases and config from sequence
    global _CURRENT_STEP_ALIASES, _CURRENT_STEP_CONFIG
    _CURRENT_STEP_ALIASES = {}
    _CURRENT_STEP_CONFIG = {}
    for node_id, aliases in _TEST_NODE_ALIASES.items():
        if item.nodeid.endswith(node_id) or node_id in item.nodeid:
            _CURRENT_STEP_ALIASES = aliases
            break
    for node_id, step_config in _TEST_NODE_CONFIGS.items():
        if item.nodeid.endswith(node_id) or node_id in item.nodeid:
            _CURRENT_STEP_CONFIG = step_config
            break

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
