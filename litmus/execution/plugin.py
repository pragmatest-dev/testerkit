"""pytest plugin for Litmus test framework."""

from __future__ import annotations

import os
import time
import warnings
from collections.abc import Generator
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from _pytest.runner import runtestprotocol

from litmus.config.test_config import FixtureConfig
from litmus.data.models import TestRun
from litmus.execution.accessors import InstrumentAccessor
from litmus.execution.decorators import set_current_logger
from litmus.execution.harness import Context
from litmus.execution.logger import RunContext, TestRunLogger
from litmus.fixtures.manager import FixtureManager, PinAccessor
from litmus.instruments.lifecycle import (
    disconnect,
    load_and_connect,
    verify_and_wrap,
)
from litmus.instruments.models import InstrumentRecord
from litmus.products.context import SpecContext
from litmus.schemas import OutputConfig, ProjectConfig, StationConfig

# ---------------------------------------------------------------------------
# ContextVars — ALL mutable module state lives here.
#
# Session-scoped getters create and store an empty dict on first access,
# so callers can safely mutate the returned dict without an explicit init step.
# Per-test getters return a throwaway empty value (without storing it),
# so stale state never leaks across tests.
# ---------------------------------------------------------------------------
_step_outcomes_var: ContextVar[dict[str, bool]] = ContextVar("_step_outcomes")
_active_instruments_var: ContextVar[dict[str, Any]] = ContextVar("_active_instruments")
_instrument_records_var: ContextVar[dict[str, InstrumentRecord]] = ContextVar("_instrument_records")
_current_step_aliases_var: ContextVar[dict[str, str]] = ContextVar("_current_step_aliases")
_current_step_config_var: ContextVar[dict[str, Any]] = ContextVar("_current_step_config")
_active_spec_context_var: ContextVar[Any] = ContextVar("_active_spec_context")
_test_node_aliases_var: ContextVar[dict[str, dict[str, str]]] = ContextVar("_test_node_aliases")
_test_node_configs_var: ContextVar[dict[str, dict[str, Any]]] = ContextVar("_test_node_configs")
_sequence_test_phase_var: ContextVar[str | None] = ContextVar("_sequence_test_phase")
_channel_store_var: ContextVar[Any] = ContextVar("_channel_store")


# --- Session-scoped getters (create-and-store on first access) ---
#
# Two patterns are used here:
#
# 1. **Create-and-store** (session-scoped): First call creates a dict and
#    stores it in the ContextVar. Callers mutate the returned dict in place.
#    Cleanup sets the var to a fresh empty dict.
#
# 2. **Return throwaway** (per-test-scoped): First call returns a new empty
#    dict WITHOUT storing it. This prevents stale state from leaking across
#    tests — each test gets its own empty dict that is never persisted.


def get_step_outcomes() -> dict[str, bool]:
    """Create-and-store on first access; callers mutate in place."""
    try:
        return _step_outcomes_var.get()
    except LookupError:
        d: dict[str, bool] = {}
        _step_outcomes_var.set(d)
        return d


def get_active_instruments() -> dict[str, Any]:
    """Create-and-store on first access; callers mutate in place."""
    try:
        return _active_instruments_var.get()
    except LookupError:
        d: dict[str, Any] = {}
        _active_instruments_var.set(d)
        return d


def get_instrument_records() -> dict[str, InstrumentRecord]:
    """Create-and-store on first access; callers mutate in place."""
    try:
        return _instrument_records_var.get()
    except LookupError:
        d: dict[str, InstrumentRecord] = {}
        _instrument_records_var.set(d)
        return d


def get_test_node_aliases() -> dict[str, dict[str, str]]:
    """Create-and-store on first access; callers mutate in place."""
    try:
        return _test_node_aliases_var.get()
    except LookupError:
        d: dict[str, dict[str, str]] = {}
        _test_node_aliases_var.set(d)
        return d


def get_test_node_configs() -> dict[str, dict[str, Any]]:
    """Create-and-store on first access; callers mutate in place."""
    try:
        return _test_node_configs_var.get()
    except LookupError:
        d: dict[str, dict[str, Any]] = {}
        _test_node_configs_var.set(d)
        return d


# --- Per-test getters (return throwaway empty, no storing) ---


def get_current_step_aliases() -> dict[str, str]:
    """Return throwaway empty; never stored. Stale state never leaks."""
    try:
        return _current_step_aliases_var.get()
    except LookupError:
        return {}


def get_current_step_config() -> dict[str, Any]:
    """Return throwaway empty; never stored. Stale state never leaks."""
    try:
        return _current_step_config_var.get()
    except LookupError:
        return {}


def get_active_spec_context() -> Any:
    """Return None if not set."""
    try:
        return _active_spec_context_var.get()
    except LookupError:
        return None


def get_sequence_test_phase() -> str | None:
    """Return None if not set."""
    try:
        return _sequence_test_phase_var.get()
    except LookupError:
        return None


# --- Setters ---


def set_step_outcomes(value: dict[str, bool]) -> None:
    """Set value. Returns None."""
    _step_outcomes_var.set(value)


def set_active_instruments(value: dict[str, Any]) -> None:
    """Set value. Returns None."""
    _active_instruments_var.set(value)


def set_instrument_records(value: dict[str, InstrumentRecord]) -> None:
    """Set value. Returns None."""
    _instrument_records_var.set(value)


def set_current_step_aliases(value: dict[str, str]) -> None:
    """Set value. Returns None."""
    _current_step_aliases_var.set(value)


def set_current_step_config(value: dict[str, Any]) -> None:
    """Set value. Returns None."""
    _current_step_config_var.set(value)


def set_active_spec_context(value: Any) -> None:
    """Set value. Returns None."""
    _active_spec_context_var.set(value)


def set_test_node_aliases(value: dict[str, dict[str, str]]) -> None:
    """Set value. Returns None."""
    _test_node_aliases_var.set(value)


def set_test_node_configs(value: dict[str, dict[str, Any]]) -> None:
    """Set value. Returns None."""
    _test_node_configs_var.set(value)


def set_sequence_test_phase(value: str | None) -> None:
    """Set value. Returns None."""
    _sequence_test_phase_var.set(value)


def get_channel_store() -> Any:
    """Return None if not set."""
    try:
        return _channel_store_var.get()
    except LookupError:
        return None


def set_channel_store(value: Any) -> None:
    """Set value. Returns None."""
    _channel_store_var.set(value)


def _load_sequence_steps(config):
    """Load sequence file and return the step models.

    Returns the list of SequenceStep models (not dicts).
    Also sets the sequence test phase contextvar.
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
            fix_hint = (
                f"Fix: check path '{seq_option}'"
                if Path(seq_option).is_absolute()
                else f"Fix: create sequences/{seq_option}.yaml"
            )
            warnings.warn(
                f"Sequence '{seq_option}' not found. No test ordering will be applied. {fix_hint}",
                stacklevel=1,
            )
            return []

    try:
        from litmus.store import load_sequence

        seq_file = load_sequence(seq_path)
    except Exception as exc:
        warnings.warn(
            f"Failed to load sequence '{seq_option}': {exc}",
            stacklevel=1,
        )
        return []

    # Store test phase for mock validation
    set_sequence_test_phase(seq_file.test_phase)

    return seq_file.steps


def _load_step_aliases_and_configs(config):
    """Load per-step aliases and configs from sequence in a single pass.

    Returns:
        (aliases, configs) where:
        - aliases: dict of test node ID → {alias_name: station_role}
        - configs: dict of test node ID → config dict (vectors, limits, mocks, retry)
    """
    steps = _load_sequence_steps(config)
    aliases: dict[str, dict[str, str]] = {}
    configs: dict[str, dict[str, Any]] = {}
    for step in steps:
        test_node = step.test
        if not test_node:
            continue
        if step.aliases:
            aliases[test_node] = step.aliases
        step_config: dict[str, Any] = {}
        for key in ("vectors", "limits", "mocks", "retry"):
            val = getattr(step, key, None)
            if val is not None:
                step_config[key] = val
        if step_config:
            configs[test_node] = step_config
    return aliases, configs


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
        from litmus.store import load_station

        station_model = load_station(station_path)
    except Exception:
        return

    if not station_model:
        return

    instruments_map = station_model.instruments or {}

    # Load per-step aliases and configs from sequence (if --sequence provided)
    node_aliases, node_configs = _load_step_aliases_and_configs(config)
    set_test_node_aliases(node_aliases)
    set_test_node_configs(node_configs)

    # Collect all alias names used across all steps
    all_alias_names: set[str] = set()
    for step_aliases in get_test_node_aliases().values():
        all_alias_names.update(step_aliases.keys())

    # Build a plugin class with fixture functions per role.
    # Wrap each fixture in staticmethod to prevent Python's descriptor
    # protocol from injecting self as the first argument.
    class _InstrumentFixtures:
        pass

    # Fixture scoping strategy:
    # - Non-aliased roles → session-scoped (one instance for entire run)
    # - Aliased roles → function-scoped (re-resolved per test, since a
    #   sequence step may remap "dmm" to a different station instrument)
    # - Pure alias names (not station roles) → function-scoped
    aliased_role_names = all_alias_names & set(instruments_map.keys())

    def _make_resolved(name: str):
        """Create a function-scoped fixture that resolves aliases."""

        @pytest.fixture
        def _fix(instruments):
            target = get_current_step_aliases().get(name, name)
            if target not in instruments:
                from litmus.execution.accessors import _instrument_not_found

                raise _instrument_not_found(name, target, instruments)
            return instruments[target]

        _fix.__name__ = name
        _fix.__qualname__ = name
        return _fix

    for role in instruments_map:
        if role in aliased_role_names:
            setattr(_InstrumentFixtures, role, staticmethod(_make_resolved(role)))
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
        setattr(_InstrumentFixtures, alias, staticmethod(_make_resolved(alias)))

    config.pluginmanager.register(_InstrumentFixtures(), "litmus_instrument_fixtures")


def pytest_sessionstart(session):
    """Clear outcomes at session start."""
    set_step_outcomes({})


def pytest_sessionfinish(session, exitstatus):
    """Clean up after session."""
    set_step_outcomes({})


def _load_project_defaults() -> ProjectConfig:
    """Load ProjectConfig from litmus.yaml, falling back to defaults."""
    try:
        from litmus.config.project import load_project_config

        return load_project_config()
    except Exception:
        # Bad or missing litmus.yaml — don't crash pytest over config
        return ProjectConfig(name="litmus")


def pytest_addoption(parser):
    """Add Litmus command-line options."""
    project = _load_project_defaults()
    group = parser.getgroup("litmus")
    group.addoption("--dut-serial", default="DUT001", help="DUT serial number")
    group.addoption("--dut-part-number", default=None, help="DUT part number")
    group.addoption("--dut-revision", default=None, help="DUT revision")
    group.addoption("--dut-lot", default=None, help="DUT lot/batch number")
    group.addoption("--station", default=project.default_station, help="Station ID")
    group.addoption("--operator", default=None, help="Operator name")
    group.addoption(
        "--results-dir",
        default=None,
        help="Directory for Parquet results (default: platform data dir)",
    )
    group.addoption("--spec", default=None, help="Path to product spec YAML file")
    group.addoption("--guardband", default="0", help="Default guardband percentage")
    group.addoption(
        "--mock-instruments",
        action="store_true",
        default=project.mock_instruments,
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

    # Clean repo - use requested phase or default to development
    return requested_phase or "development"


def _serialize_config(config: dict | None) -> str | None:
    """Serialize config dict to YAML string for storage."""
    if config is None:
        return None
    return yaml.dump(config, default_flow_style=False, sort_keys=False)


def _require_fixture_and_instruments(
    fixture_config: Any, instruments: dict[str, Any], feature: str
) -> None:
    """Validate that fixture config and instruments are available."""
    if not fixture_config:
        raise pytest.UsageError(
            f"The '{feature}' fixture requires a fixture config. "
            "Provide --fixture-config <path> or create a fixtures/*.yaml file."
        )
    if not instruments:
        raise pytest.UsageError(
            f"The '{feature}' fixture requires instruments. "
            "Provide --station-config <path> or create a stations/*.yaml file."
        )


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


def _create_subscriber(
    cls: type,
    fmt: str,
    output_cfg: OutputConfig,
    results_path: Path,
    session_id: UUID,
) -> Any:
    """Instantiate a subscriber with format-specific constructor args."""
    from litmus.data.backends.parquet import ParquetBackend, ParquetSubscriber
    from litmus.data.sessions import SessionSubscriber

    # Resolve output directory from config (strips "results/" prefix since
    # results_path already points at the results root).
    output_dir = output_cfg.default_output_dir()
    subdir = output_dir.removeprefix("results/")  # results_path is already the root

    if cls is ParquetSubscriber:
        backend = ParquetBackend(results_dir=str(results_path))
        return ParquetSubscriber(backend)
    if cls is SessionSubscriber:
        return SessionSubscriber(results_path / subdir)
    # Unknown subscriber — try no-arg constructor
    return cls()


def _build_run_metadata(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Build kwargs dict for TestRunLogger from session fixtures and CLI options."""
    station_config = _safe_get_session_fixture(request, "station_config")
    fixture_config = _safe_get_session_fixture(request, "fixture_config")
    spec_context = _safe_get_session_fixture(request, "spec_context")

    station_yaml = _serialize_config(station_config) if station_config else None

    # Product info from spec_context
    product_id = None
    product_name = None
    product_revision = None
    product_yaml = None
    if spec_context:
        product_id = spec_context.product.id
        product_name = spec_context.product.name
        product_revision = spec_context.product.revision
        product_yaml = yaml.dump(
            spec_context.product.model_dump(mode="json", exclude_none=True),
            default_flow_style=False,
            sort_keys=False,
        )

    # Fixture info
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

    # Station info
    station_id = request.config.getoption("--station")
    station_name = None
    station_type = None
    station_location = None
    if station_config:
        station_name = station_config.name
        station_type = getattr(station_config, "station_type", None) or getattr(
            station_config, "type", None
        )
        station_location = station_config.location

    results_dir = request.config.getoption("--results-dir")

    requested_phase = request.config.getoption("--test-phase") or os.environ.get(
        "LITMUS_TEST_PHASE"
    )
    test_phase = _resolve_test_phase(requested_phase)

    instrument_records = _safe_get_session_fixture(request, "instrument_records")

    cli_part_number = request.config.getoption("--dut-part-number")
    dut_part_number = cli_part_number or (
        spec_context.product.part_number if spec_context else None
    )
    cli_revision = request.config.getoption("--dut-revision")
    dut_revision = cli_revision or (spec_context.product.revision if spec_context else None)

    from litmus.environment import capture_environment

    env = capture_environment()

    return {
        "dut_serial": request.config.getoption("--dut-serial"),
        "dut_part_number": dut_part_number,
        "dut_revision": dut_revision,
        "dut_lot_number": request.config.getoption("--dut-lot"),
        "station_id": station_id,
        "station_name": station_name,
        "station_type": station_type,
        "station_location": station_location,
        "operator_id": request.config.getoption("--operator"),
        "test_sequence_id": request.config.rootpath.name,
        "product_id": product_id,
        "product_name": product_name,
        "product_revision": product_revision,
        "fixture_id": fixture_id,
        "station_config_yaml": station_yaml,
        "product_spec_yaml": product_yaml,
        "fixture_config_yaml": fixture_yaml,
        "git_commit": _get_git_commit(),
        "results_dir": results_dir,
        "test_phase": test_phase,
        "instruments": instrument_records,
        "environment": env,
    }


def _run_configured_outputs(test_run: TestRun, run_id: str, results_dir: str) -> None:
    """Run configured outputs (exports, reports, transports) from litmus.yaml."""
    try:
        from litmus.data.output_runner import run_outputs

        run_outputs(test_run, run_id, results_dir)
    except Exception as exc:
        warnings.warn(
            f"Output processing failed: {exc}",
            stacklevel=2,
        )


@pytest.fixture(scope="session", autouse=True)
def litmus_logger(request) -> Generator[TestRunLogger, None, None]:
    """Provide test run logger for the session.

    This fixture is autouse=True so it's always active, enabling
    @litmus_test decorated functions to log measurements.

    Captures config snapshots at run start for full traceability.
    Streams events to an event log for live observability.
    """
    from litmus.data.event_store import EventStore
    from litmus.data.events import SessionStarted
    from litmus.data.subscribers import get_subscriber_class

    meta = _build_run_metadata(request)
    from litmus.data.results_dir import resolve_results_dir

    results_dir = meta["results_dir"]
    if not results_dir:
        results_dir = str(resolve_results_dir())
        meta["results_dir"] = results_dir
    session_id = uuid4()
    meta["session_id"] = session_id

    logger = TestRunLogger(**meta)

    # Create event store + log and wire subscribers from config
    _event_store: EventStore | None = None
    if results_dir:
        results_path = Path(results_dir)

        _event_store = EventStore(_results_dir=results_path)
        event_log = _event_store.get_event_log(session_id)
        logger.event_log = event_log

        # Collect configured subscriber formats from outputs config
        configured: set[str] = set()
        try:
            from litmus.config.project import load_project_config

            config = load_project_config()
            for output_cfg in config.outputs:
                fmt = output_cfg.format
                if fmt:
                    cls = get_subscriber_class(fmt)
                    if cls is not None:
                        sub = _create_subscriber(cls, fmt, output_cfg, results_path, session_id)
                        event_log.add_subscriber(sub)
                        configured.add(fmt)
        except Exception:
            pass

        # Create ChannelStore directly (not via subscriber registry)
        from litmus.data.channels.store import ChannelStore as _ChannelStore

        _cs = _ChannelStore(results_path / "channels", session_id, serve=True)
        _cs.open()
        set_channel_store(_cs)

        # Register defaults not already configured
        for fmt in ("parquet", "sessions"):
            if fmt not in configured:
                cls = get_subscriber_class(fmt)
                if cls is not None:
                    sub = _create_subscriber(
                        cls,
                        fmt,
                        OutputConfig(format=fmt),
                        results_path,
                        session_id,
                    )
                    event_log.add_subscriber(sub)

        # Emit SessionStarted with full run context
        session_event = SessionStarted(
            session_id=logger._session_id,
            run_id=logger.test_run.id,
            station_id=logger.test_run.station_id,
            station_name=logger.test_run.station_name,
            station_type=logger.test_run.station_type,
            station_location=logger.test_run.station_location,
            dut_serial=logger.test_run.dut.serial,
            dut_part_number=logger.test_run.dut.part_number,
            dut_revision=logger.test_run.dut.revision,
            dut_lot_number=logger.test_run.dut.lot_number,
            product_id=logger.test_run.product_id,
            product_name=logger.test_run.product_name,
            product_revision=logger.test_run.product_revision,
            operator_id=logger.test_run.operator_id,
            operator_name=logger.test_run.operator_name,
            fixture_id=logger.test_run.fixture_id,
            sequence_id=logger.test_run.test_sequence_id,
            test_phase=logger.test_run.test_phase,
            git_commit=logger.test_run.git_commit,
            environment_json=logger.test_run.environment_json,
            station_config_yaml=logger.test_run.station_config_yaml,
            product_spec_yaml=logger.test_run.product_spec_yaml,
            fixture_config_yaml=logger.test_run.fixture_config_yaml,
            test_config_yaml=logger.test_run.test_config_yaml,
            custom_metadata=dict(logger.test_run.custom_metadata),
            pid=os.getpid(),
        )
        event_log.emit(session_event)

        # Emit InstrumentConnected for each instrument
        _emit_instrument_events(logger, event_log)

    set_current_logger(logger)
    yield logger

    # Close ChannelStore before finalizing (before EventLog closes subscribers)
    _cs_final = get_channel_store()
    if _cs_final is not None:
        _cs_final.close()
        set_channel_store(None)

    # Finalize emits RunEnded/SessionEnded and closes event log + subscribers.
    # EventStore.close() will flush remaining events to Flight/DuckDB after.
    test_run = logger.finalize()

    # Close EventStore — releases daemon ref. Event logs were already closed
    # by finalize(), and on_flush callback pushed final batches to Flight.
    if _event_store is not None:
        _event_store.close()

    # Run configured outputs (exports, reports, transports)
    _run_configured_outputs(test_run, str(test_run.id), results_dir)
    set_current_logger(None)


def _emit_instrument_events(logger: TestRunLogger, event_log: Any) -> None:
    """Emit InstrumentConnected events from instrument records."""
    from litmus.data.events import InstrumentConnected

    records = get_instrument_records()
    for role, rec in records.items():
        event = InstrumentConnected(
            session_id=logger._session_id,
            run_id=logger.test_run.id,
            role=role,
            instrument_id=rec.instrument_id,
            driver=rec.driver,
            resource=rec.resource,
            protocol=rec.protocol,
            manufacturer=rec.info.manufacturer if rec.info else None,
            model=rec.info.model if rec.info else None,
            serial=rec.info.serial if rec.info else None,
            firmware=rec.info.firmware if rec.info else None,
            cal_due=(
                rec.calibration.due_date.isoformat()
                if rec.calibration and rec.calibration.due_date
                else None
            ),
            cal_last=(
                rec.calibration.last_cal.isoformat()
                if rec.calibration and rec.calibration.last_cal
                else None
            ),
            cal_certificate=rec.calibration.certificate if rec.calibration else None,
            cal_lab=rec.calibration.lab if rec.calibration else None,
            mocked=rec.mocked,
        )
        event_log.emit(event)


@pytest.fixture(scope="session")
def run_context(litmus_logger) -> RunContext:
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
def litmus_step(litmus_logger, request) -> Generator[None, None, None]:
    """Create step for test function (use when NOT using @litmus_test).

    Note: @litmus_test decorated tests already create their own steps.
    Only use this fixture for tests that need step tracking without @litmus_test.
    """
    litmus_logger.start_step(request.node.name)
    yield
    litmus_logger.end_step()


# Sentinel object to detect pytest-injected context
_PYTEST_CONTEXT_SENTINEL = object()


@pytest.fixture
def context() -> Context:
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
    return _PYTEST_CONTEXT_SENTINEL  # type: ignore[return-value]  # decorator injects real Context


@pytest.fixture(scope="session")
def spec_context(request) -> SpecContext | None:
    """Provide product spec context for spec-driven testing.

    Loads product spec from --spec option or auto-discovers from products/ directory.
    Provides SpecContext for deriving limits and tracking channel traceability.

    Usage in tests:
        def test_voltage(spec_context, dmm):
            limit = spec_context.get_limit("output_voltage", temperature=25)
            value = dmm.measure_dc_voltage()
            # Use limit for validation...

    Returns:
        SpecContext, or None if no product spec configured.
    """
    spec_path = request.config.getoption("--spec")
    guardband = float(request.config.getoption("--guardband"))

    ctx = None

    if spec_path:
        ctx = SpecContext.from_file(spec_path, guardband_pct=guardband)
    else:
        # Try auto-discover from products/ directory
        # Check both rootpath and invocation directory (cwd) for nested project support
        search_roots = [
            request.config.rootpath,
            Path(request.config.invocation_params.dir),  # Where pytest was invoked
        ]

        for root in search_roots:
            products_dir = root / "products"
            if products_dir.exists():
                for yaml_file in sorted(products_dir.rglob("*.yaml")):
                    if yaml_file.name.startswith("_"):
                        continue
                    ctx = SpecContext.from_file(yaml_file, guardband_pct=guardband)
                    break
            if ctx:
                break

    set_active_spec_context(ctx)
    return ctx


@pytest.fixture(scope="session")
def mock_instruments(request) -> bool:
    """Return whether to use mock instruments instead of real hardware.

    Checks both:
    - --mock-instruments pytest option
    - LITMUS_MOCK_INSTRUMENTS environment variable (set by UI)

    Raises:
        pytest.UsageError: If mocks requested for non-dev test phase.
    """
    use_mocks = (
        request.config.getoption("--mock-instruments")
        or os.environ.get("LITMUS_MOCK_INSTRUMENTS") == "1"
    )

    # Prevent mocks in production/validation/characterization phases
    test_phase = get_sequence_test_phase()
    if use_mocks and test_phase is not None and test_phase != "development":
        raise pytest.UsageError(
            f"Mock instruments not allowed for test_phase='{test_phase}'. "
            f"Mocks are only permitted for test_phase='development'. "
            f"Remove --mock-instruments or change sequence test_phase to 'development'."
        )

    return use_mocks


@pytest.fixture(scope="session")
def station_config(request) -> StationConfig | None:
    """Load station configuration from --station-config option.

    Returns:
        StationConfig model, or None if not specified.
    """
    station_path = _find_station_file(request.config)
    if station_path:
        from litmus.store import load_station

        return load_station(station_path)

    # Check if --station was explicitly passed (not the default)
    station_id = request.config.getoption("--station")
    explicit = any(arg.startswith("--station") for arg in request.config.invocation_params.args)
    if explicit:
        warnings.warn(
            f"Station '{station_id}' not found in stations/ directory. "
            f"Instrument fixtures (psu, dmm, etc.) will not be available. "
            f"Fix: create stations/{station_id}.yaml",
            stacklevel=2,
        )
    return None


@pytest.fixture(scope="session")
def fixture_config(request) -> FixtureConfig | None:
    """Load fixture configuration from --fixture-config option.

    Returns:
        FixtureConfig instance, or None if not specified.
    """
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
        from litmus.store import load_fixture

        return load_fixture(Path(config_path))
    return None


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
    records: dict[str, InstrumentRecord] = {}
    set_instrument_records(records)

    if not station_config:
        return records

    # Try to find and load instrument files
    from litmus.instruments.loader import find_instruments_dir, resolve_station_instruments
    from litmus.store import load_instrument_files

    # Search from pytest invocation directory
    invocation_dir = Path(request.config.invocation_params.dir)
    instruments_dir = find_instruments_dir(invocation_dir)

    instrument_files = {}
    if instruments_dir:
        instrument_files = load_instrument_files(instruments_dir)

    # Resolve station instruments to records
    records = resolve_station_instruments(station_config, instrument_files)
    set_instrument_records(records)

    return records


@pytest.fixture(scope="session")
def instruments(
    request, station_config, mock_instruments, instrument_records, litmus_logger
) -> Generator[dict[str, Any], None, None]:
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
    from litmus.instruments.locks import (
        ResourceMeta,
        acquire_resource,
        release_resource,
    )

    active: dict[str, Any] = {}
    from filelock._api import BaseFileLock

    held_locks: dict[str, BaseFileLock] = {}  # role → FileLock
    set_active_instruments(active)

    if not station_config:
        yield active
        return

    # Get instrument configs from station
    inst_configs = station_config.instruments or {}
    session_id = litmus_logger._session_id if litmus_logger else None

    for role, record in instrument_records.items():
        # Get config - either from record (new format) or inline (legacy)
        inline_config = inst_configs.get(role)

        mock_config = (
            inline_config.mock_config if inline_config and inline_config.mock_config else {}
        )
        use_mock = mock_instruments or (inline_config.mock if inline_config else False)
        record.mocked = use_mock

        # Acquire resource lock (skip for mocks with no real resource)
        if record.resource and not record.mocked:
            from datetime import UTC, datetime
            from uuid import UUID

            meta = ResourceMeta(
                pid=os.getpid(),
                session_id=UUID(str(session_id)) if session_id else UUID(int=0),
                station_id=station_config.id or "",
                role=role,
                acquired_at=datetime.now(UTC),
            )
            lock = acquire_resource(record.resource, meta, timeout=0)
            held_locks[role] = lock

        try:
            inst: Any = load_and_connect(record, mock=use_mock, mock_config=mock_config)
        except ValueError:
            # No driver and no resource — skip this instrument
            if role in held_locks:
                release_resource(record.resource, held_locks.pop(role))
            continue

        run_id = litmus_logger.test_run.id if litmus_logger else None
        event_log = litmus_logger.event_log if litmus_logger else None
        inst = verify_and_wrap(
            inst, role, record, event_log, session_id, run_id,
            channel_store=get_channel_store(),
        )

        active[role] = inst

    yield active

    # Cleanup: disconnect/close all instruments, release locks
    for role, inst in active.items():
        # Emit disconnect event
        if litmus_logger is not None and litmus_logger.event_log is not None:
            from litmus.data.events import InstrumentDisconnected

            record = instrument_records.get(role)
            litmus_logger.event_log.emit(
                InstrumentDisconnected(
                    session_id=litmus_logger._session_id,
                    run_id=litmus_logger.test_run.id,
                    role=role,
                    instrument_id=record.instrument_id if record else role,
                )
            )

        disconnect(inst, role)

        # Release resource lock
        if role in held_locks:
            record = instrument_records.get(role)
            if record and record.resource:
                release_resource(record.resource, held_locks[role])

    held_locks.clear()
    active.clear()


@pytest.fixture
def instrument(instruments, instrument_records) -> InstrumentAccessor:
    """Accessor for instruments by role with grouping support.

    Usage:
        def test_voltage(instrument):
            dmm = instrument("dmm")

        def test_all_dmms(instrument):
            dmms = instrument.by_type("pymeasure.instruments.keithley.Keithley2000")
    """
    return InstrumentAccessor(instruments, instrument_records)


@pytest.fixture(scope="session")
def pins(instruments, fixture_config) -> PinAccessor:
    """UUT-centric pin accessor for tests.

    Resolves DUT pin names to instrument instances:

        def test_output(pins):
            pins["VIN"].set_voltage(5.0)
            pins["VIN"].enable_output()
            assert pins["VOUT"].measure_voltage() > 3.0

    Raises:
        pytest.UsageError: If no fixture config or instruments available.
    """
    _require_fixture_and_instruments(fixture_config, instruments, "pins")

    manager = FixtureManager(fixture_config, instruments)
    return PinAccessor(manager)


@pytest.fixture(scope="session")
def fixture_manager(instruments, fixture_config) -> FixtureManager:
    """Fixture manager for advanced pin/net routing.

    Provides direct access to the FixtureManager for tests that need
    advanced routing methods beyond the simple pins[] accessor:

        def test_with_net_lookup(fixture_manager):
            point = fixture_manager.get_point_for_net("VOUT_3V3")
            instrument = fixture_manager.get_instrument_for_point(point.name)

    Raises:
        pytest.UsageError: If no fixture config or instruments available.
    """
    _require_fixture_and_instruments(fixture_config, instruments, "fixture_manager")
    return FixtureManager(fixture_config, instruments)


def pytest_runtest_makereport(item, call):
    """Record test outcomes for skip-on-failure logic."""
    if call.when == "call":
        passed = call.excinfo is None
        outcomes = get_step_outcomes()
        outcomes[item.name] = passed
        # Also track by nodeid for more specific matching
        outcomes[item.nodeid] = passed


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Reset mock state, set per-step aliases/config, and skip tests if dependencies failed."""
    # Set per-step aliases and config from sequence
    step_aliases: dict[str, str] = {}
    step_config: dict[str, Any] = {}
    # Match sequence step node_id to pytest item. Sequence steps may use:
    # - bare function name ("test_voltage")
    # - partial path ("tests/test_power.py::test_voltage")
    # We try exact substring match first, then fall back to function name.
    node_aliases = get_test_node_aliases()
    node_configs = get_test_node_configs()
    item_func = item.nodeid.rsplit("::", 1)[-1]
    for node_id in set(node_aliases) | set(node_configs):
        if node_id in item.nodeid or node_id == item_func:
            step_aliases = node_aliases.get(node_id, {})
            step_config = node_configs.get(node_id, {})
            break
    set_current_step_aliases(step_aliases)
    set_current_step_config(step_config)

    # Reset mock state for clean test isolation
    for inst in get_active_instruments().values():
        if hasattr(inst, "reset_mock_state"):
            inst.reset_mock_state()

    # Check skip-on-failure dependencies
    marker = item.get_closest_marker("litmus_skip_on")
    if marker is None:
        return

    dependencies = marker.args[0] if marker.args else []

    outcomes = get_step_outcomes()
    for dep in dependencies:
        # Check by exact test name or nodeid
        if dep in outcomes and not outcomes[dep]:
            pytest.skip(f"Dependency '{dep}' failed")
        # Also check partial matches (test name at end of nodeid)
        for key, passed in outcomes.items():
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
