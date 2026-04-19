"""pytest plugin for Litmus test framework."""

from __future__ import annotations

import os
import sys
import time
import warnings
from collections.abc import Callable, Generator
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from _pytest.runner import runtestprotocol

from litmus.config.test_config import FixtureConfig
from litmus.data.models import CollectedItem, TestRun
from litmus.execution.accessors import InstrumentAccessor
from litmus.execution.decorators import set_current_logger
from litmus.execution.harness import Context
from litmus.execution.logger import RunContext, TestRunLogger
from litmus.fixtures.manager import FixtureManager, PinAccessor
from litmus.instruments.pool import InstrumentPool
from litmus.instruments.route_manager import RouteManager
from litmus.models.instrument import InstrumentRecord
from litmus.models.project import OutputConfig, ProjectConfig
from litmus.models.station import StationConfig
from litmus.products.context import SpecContext

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
_sequence_required_fixture_var: ContextVar[str | None] = ContextVar("_sequence_required_fixture")
_channel_store_var: ContextVar[Any] = ContextVar("_channel_store")
_collected_items_var: ContextVar[list[CollectedItem]] = ContextVar("_collected_items")
_current_code_identity_var: ContextVar[dict[str, str | None]] = ContextVar("_current_code_identity")
_event_store_var: ContextVar[Any] = ContextVar("_event_store")


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


def get_sequence_required_fixture() -> str | None:
    """Return None if not set."""
    try:
        return _sequence_required_fixture_var.get()
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


def set_sequence_required_fixture(value: str | None) -> None:
    """Set value. Returns None."""
    _sequence_required_fixture_var.set(value)


def get_channel_store() -> Any:
    """Return None if not set."""
    try:
        return _channel_store_var.get()
    except LookupError:
        return None


def set_channel_store(value: Any) -> None:
    """Set value. Returns None."""
    _channel_store_var.set(value)


def get_event_store() -> Any:
    """Return the session EventStore, or None if not set."""
    try:
        return _event_store_var.get()
    except LookupError:
        return None


def set_event_store(value: Any) -> None:
    """Set the session EventStore. Returns None."""
    _event_store_var.set(value)


def get_collected_items() -> list[CollectedItem]:
    """Return collected pytest items, or empty list if not set."""
    try:
        return _collected_items_var.get()
    except LookupError:
        return []


def set_collected_items(value: list[CollectedItem]) -> None:
    """Set value. Returns None."""
    _collected_items_var.set(value)


def get_current_code_identity() -> dict[str, str | None]:
    """Return code identity for the currently running test item."""
    try:
        return _current_code_identity_var.get()
    except LookupError:
        return {}


def set_current_code_identity(value: dict[str, str | None]) -> None:
    """Set code identity for the currently running test item."""
    _current_code_identity_var.set(value)


def _load_sequence_steps(config):
    """Load sequence config from --sequence option.

    Returns the full TestSequenceConfig, or None if no sequence.
    Also sets the sequence test phase contextvar.
    """

    seq_option = config.getoption("--sequence", default=None)
    if not seq_option:
        return None

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
            return None

    try:
        from litmus.store import load_sequence

        seq_file = load_sequence(seq_path)
    except Exception as exc:
        warnings.warn(
            f"Failed to load sequence '{seq_option}': {exc}",
            stacklevel=1,
        )
        return None

    # Store test phase for mock validation
    set_sequence_test_phase(seq_file.test_phase)
    set_sequence_required_fixture(seq_file.required_fixture)

    return seq_file


def _load_step_aliases_and_configs(config):
    """Load per-step aliases and configs from sequence in a single pass.

    Applies sequence-level defaults (raise_on_fail, retry) to steps
    that don't set their own. Resolution: step > sequence > decorator.

    Returns:
        (aliases, configs) where:
        - aliases: dict of test node ID → {alias_name: station_role}
        - configs: dict of test node ID → step config dict
    """
    seq = _load_sequence_steps(config)
    if not seq:
        return {}, {}

    # Sequence-level defaults for params that make sense globally
    seq_defaults: dict[str, Any] = {}
    if seq.raise_on_fail is not None:
        seq_defaults["raise_on_fail"] = seq.raise_on_fail
    if seq.retry is not None:
        seq_defaults["retry"] = seq.retry

    aliases: dict[str, dict[str, str]] = {}
    configs: dict[str, dict[str, Any]] = {}
    for step in seq.steps:
        test_node = step.test
        if not test_node:
            continue
        if step.aliases:
            aliases[test_node] = step.aliases
        step_config: dict[str, Any] = {}
        for key in ("vectors", "limits", "mocks", "retry", "raise_on_fail"):
            val = getattr(step, key, None)
            if val is not None:
                step_config[key] = val
            elif key in seq_defaults:
                step_config[key] = seq_defaults[key]
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


def _find_fixture_file(config) -> Path | None:
    """Find fixture config file from pytest config options.

    Resolution: --fixture-config path → --fixture ID → sequence
    required_fixture → single-file fallback.
    """
    # Explicit path (highest priority)
    config_path = config.getoption("--fixture-config")
    if config_path:
        return Path(config_path)

    # --fixture ID → fixtures/{id}.yaml
    fixture_id = config.getoption("--fixture")
    if fixture_id:
        search_roots = [
            config.rootpath,
            Path(config.invocation_params.dir),
        ]
        for root in search_roots:
            fixture_file = root / "fixtures" / f"{fixture_id}.yaml"
            if fixture_file.exists():
                return fixture_file
        warnings.warn(
            f"Fixture '{fixture_id}' not found in fixtures/ directory.",
            stacklevel=2,
        )
        return None

    # Sequence required_fixture (hard error if declared but missing)
    seq_fixture = get_sequence_required_fixture()
    if seq_fixture:
        search_roots = [
            config.rootpath,
            Path(config.invocation_params.dir),
        ]
        for root in search_roots:
            fixture_file = root / "fixtures" / f"{seq_fixture}.yaml"
            if fixture_file.exists():
                return fixture_file
        raise pytest.UsageError(
            f"Sequence requires fixture '{seq_fixture}' but "
            f"fixtures/{seq_fixture}.yaml was not found."
        )

    # Single-file fallback
    search_roots = [
        config.rootpath,
        Path(config.invocation_params.dir),
    ]
    for root in search_roots:
        fixtures_dir = root / "fixtures"
        if fixtures_dir.exists():
            yaml_files = list(fixtures_dir.glob("*.yaml"))
            if len(yaml_files) == 1:
                return yaml_files[0]

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
    # @litmus_test returns TestStep for programmatic callers; suppress pytest warning
    config.addinivalue_line(
        "filterwarnings",
        "ignore::pytest.PytestReturnNotNoneWarning",
    )

    # Auto-register instrument role fixtures from station config
    station_path = _find_station_file(config)
    if station_path is None:
        return

    try:
        from litmus.store import load_station

        station_model = load_station(station_path)
    except Exception:
        # ValidationError, bad YAML, missing deps — don't crash pytest
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


def pytest_report_header(config):
    """Show litmus results location in the pytest header."""
    from litmus.data.results_dir import resolve_results_dir

    results_dir = config.getoption("--results-dir", default=None)
    resolved = resolve_results_dir(results_dir)
    if results_dir:
        return (
            f"litmus: results → {resolved}"
            " (local — remove results_dir from litmus.yaml for global storage)"
        )
    return f"litmus: results → {resolved}"


def pytest_sessionstart(session):
    """Clear outcomes at session start and validate DUT serial."""
    set_step_outcomes({})

    config = session.config
    dut_serial = config.getoption("--dut-serial")
    dut_serials = config.getoption("--dut-serials")

    # Skip validation if per-slot serials were explicitly provided
    if dut_serials:
        return

    requested_phase = config.getoption("--test-phase") or os.environ.get("LITMUS_TEST_PHASE")
    test_phase = _resolve_test_phase(requested_phase)

    if test_phase == "development":
        return

    # Non-development phase: require explicit DUT serial
    if dut_serial == "DUT001":
        serial = _prompt_for_serial(test_phase)
        config.option.dut_serial = serial


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Capture collected items so the step manifest can include not-started steps."""
    collected = []
    for item in items:
        markers = ",".join(sorted(m.name for m in item.iter_markers()))
        parts = item.nodeid.rsplit("::", 1)
        func_name = parts[-1] if len(parts) > 1 else item.name
        mod = getattr(item, "module", None)
        cls = getattr(item, "cls", None)
        collected.append(
            CollectedItem(
                node_id=item.nodeid,
                file=str(item.path) if hasattr(item, "path") else None,
                module=mod.__name__ if mod else None,
                class_name=cls.__name__ if cls else None,
                function=func_name,
                markers=markers or None,
            )
        )
    set_collected_items(collected)


def pytest_sessionfinish(session, exitstatus):
    """Clean up all session-scoped ContextVars."""
    set_step_outcomes({})
    set_active_instruments({})
    set_instrument_records({})
    set_test_node_aliases({})
    set_test_node_configs({})
    set_sequence_test_phase(None)
    set_sequence_required_fixture(None)
    set_collected_items([])
    set_channel_store(None)
    set_event_store(None)


def _load_project_defaults() -> ProjectConfig:
    """Load ProjectConfig from litmus.yaml, falling back to defaults."""
    try:
        from litmus.store import load_project_config

        return load_project_config()
    except Exception:
        # Bad or missing litmus.yaml — don't crash pytest over config
        return ProjectConfig(name="litmus")


def pytest_addoption(parser):
    """Add Litmus command-line options."""
    project = _load_project_defaults()
    group = parser.getgroup("litmus")
    group.addoption("--dut-serial", default="DUT001", help="DUT serial number")
    group.addoption(
        "--dut-serials",
        default=None,
        help="Per-slot DUT serials: slot_1=SN1,slot_2=SN2",
    )
    group.addoption("--dut-part-number", default=None, help="DUT part number")
    group.addoption("--dut-revision", default=None, help="DUT revision")
    group.addoption("--dut-lot", default=None, help="DUT lot/batch number")
    group.addoption("--station", default=project.default_station, help="Station ID")
    group.addoption("--operator", default=None, help="Operator name")
    group.addoption(
        "--results-dir",
        default=project.results_dir,
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
    group.addoption("--fixture", default=project.default_fixture, help="Fixture ID")
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
    from litmus.execution._git import is_git_clean

    if not is_git_clean():
        # Can't run anything other than development without clean git
        return "development"

    # Clean repo - use requested phase or default to development
    return requested_phase or "development"


def _prompt_for_serial(test_phase: str, slot_id: str | None = None) -> str:
    """Prompt for DUT serial or raise if non-interactive.

    Args:
        test_phase: Current test phase (for error message).
        slot_id: If provided, prompt for a specific slot.

    Returns:
        Non-empty serial string.
    """
    label = f" for slot '{slot_id}'" if slot_id else ""

    if sys.stdin.isatty():
        serial = input(
            f"[litmus] test_phase='{test_phase}' requires a DUT serial{label}.\n"
            f"  Enter DUT serial (or Ctrl+C to abort): "
        )
        serial = serial.strip()
        if not serial:
            raise pytest.UsageError(
                f"DUT serial number is required{label} for "
                f"non-development test phases. "
                "Use --dut-serial <serial> or enter a serial when prompted."
            )
        return serial

    raise pytest.UsageError(
        f"DUT serial number is required for test_phase='{test_phase}'{label}. "
        "Use --dut-serial <serial> or --dut-serials slot=serial."
    )


def _prompt_for_slot_serials(
    slot_ids: list[str],
    test_phase: str,
) -> dict[str, str]:
    """Prompt for DUT serial for each slot.

    Args:
        slot_ids: Ordered list of slot IDs from fixture config.
        test_phase: Current test phase (for error message).

    Returns:
        Dict mapping slot_id → serial.
    """
    serials: dict[str, str] = {}
    for slot_id in slot_ids:
        serials[slot_id] = _prompt_for_serial(test_phase, slot_id)
    return serials


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
        # Fixture exists but raised during setup (e.g. ValidationError)
        return None


def _create_subscriber(
    cls: type,
    fmt: str,
    output_cfg: OutputConfig,
    results_path: Path,
    session_id: UUID,
) -> Any:
    """Instantiate a subscriber with the uniform contract.

    All subscribers take ``(output_dir, *, on_output=...)`` where
    ``output_dir`` is the results root and each subscriber creates
    its own subfolder.
    """
    on_output = _make_transport_callback(output_cfg, results_path) if output_cfg.transport else None
    output_dir = Path(output_cfg.default_output_dir())
    return cls(output_dir, on_output=on_output)


def _make_transport_callback(
    output_cfg: OutputConfig,
    results_path: Path,
) -> Callable[[Any], None]:
    """Return a callback that enqueues OutputFiles for transport."""
    from litmus.data.subscribers._output_file import OutputFile

    transport_name = output_cfg.transport
    assert transport_name is not None  # caller checks before calling

    def _on_output(output: OutputFile) -> None:
        try:
            from litmus.data.transports.upload_queue import drain, enqueue

            enqueue(output.path, transport_name, output_cfg, str(results_path))
            drain(str(results_path))
        except Exception as exc:
            warnings.warn(
                f"Transport callback failed for {output.path}: {exc}",
                stacklevel=2,
            )

    return _on_output


def _find_format_transport_callback(
    format_name: str,
    results_path: Path,
) -> Callable[[Any], None] | None:
    """If litmus.yaml has an output entry for this format with transport, wire it."""
    try:
        from litmus.store import load_project_config

        config = load_project_config()
    except Exception:
        return None
    for output_cfg in config.outputs:
        if output_cfg.format == format_name and output_cfg.transport:
            return _make_transport_callback(output_cfg, results_path)
    return None


def _build_run_metadata(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Build kwargs dict for TestRunLogger from session fixtures and CLI options."""
    station_config = _safe_get_session_fixture(request, "station_config")
    fixture_config = _safe_get_session_fixture(request, "fixture_config")
    spec_context = _safe_get_session_fixture(request, "spec_context")

    # Product info from spec_context
    product_id = None
    product_name = None
    product_revision = None
    if spec_context:
        product_id = spec_context.product.id
        product_name = spec_context.product.name
        product_revision = spec_context.product.revision

    # Fixture info
    fixture_id = None
    if fixture_config:
        fixture_id = getattr(fixture_config, "id", None) or getattr(fixture_config, "name", None)

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
    from litmus.execution._git import get_project_name

    env = capture_environment()
    project_name = get_project_name(request.config.rootpath)

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
        "project_name": project_name,
        "project_dir": request.config.rootpath,
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
    from litmus.data.events import RunStarted, SessionEnded, SessionStarted
    from litmus.data.subscribers import get_subscriber_class

    meta = _build_run_metadata(request)
    from litmus.data.results_dir import resolve_results_dir

    results_dir = meta["results_dir"]
    if not results_dir:
        results_dir = str(resolve_results_dir())
        meta["results_dir"] = results_dir

    # Worker mode: inherit session_id and DUT serial from env
    env_session_id = os.environ.get("LITMUS_SESSION_ID")
    session_id = UUID(env_session_id) if env_session_id else uuid4()
    meta["session_id"] = session_id

    env_dut_serial = os.environ.get("LITMUS_DUT_SERIAL")
    if env_dut_serial:
        meta["dut_serial"] = env_dut_serial

    env_slot_id = os.environ.get("LITMUS_SLOT_ID")

    logger = TestRunLogger(**meta)

    # Create event store + log and wire subscribers from config
    _event_store: EventStore | None = None
    if results_dir:
        results_path = Path(results_dir)

        # Reuse EventStore if already created (e.g. by orchestrator)
        _event_store = get_event_store()
        if _event_store is None:
            _event_store = EventStore(_results_dir=results_path)
            set_event_store(_event_store)
        event_log = _event_store.get_event_log(session_id)
        logger.event_log = event_log

        # Create ParquetSubscriber directly (default, always on)
        from litmus.data.backends.parquet import ParquetSubscriber

        parquet_on_output = _find_format_transport_callback("parquet", results_path)
        _pq_sub = ParquetSubscriber(results_path, on_output=parquet_on_output)
        event_log.add_subscriber(_pq_sub)

        # Create ChannelStore directly (default, always on)
        from litmus.data.channels.store import ChannelStore as _ChannelStore

        channels_on_output = _find_format_transport_callback("channels", results_path)
        _cs = _ChannelStore(
            results_path / "channels",
            session_id,
            serve=True,
            on_output=channels_on_output,
        )
        _cs.open()
        set_channel_store(_cs)

        # Wire additional configured subscriber formats
        try:
            from litmus.store import load_project_config

            config = load_project_config()
            for output_cfg in config.outputs:
                fmt = output_cfg.format
                if fmt and fmt not in {"parquet", "channels"}:
                    cls = get_subscriber_class(fmt)
                    if cls is not None:
                        sub = _create_subscriber(cls, fmt, output_cfg, results_path, session_id)
                        event_log.add_subscriber(sub)
        except Exception as exc:
            warnings.warn(
                f"Failed to register configured output subscribers: {exc}",
                stacklevel=2,
            )

        # In multi-slot worker mode, the orchestrator emits SessionStarted/
        # SessionEnded. Workers only emit RunStarted/RunEnded.
        is_worker = env_slot_id is not None and int(os.environ.get("LITMUS_SLOT_COUNT", "1")) > 1
        if not is_worker:
            event_log.emit(
                SessionStarted.from_station(
                    session_id=logger._session_id,
                    station_id=logger.test_run.station_id,
                    station_name=logger.test_run.station_name,
                    station_type=logger.test_run.station_type,
                    station_location=logger.test_run.station_location,
                    station_hostname=logger.test_run.station_hostname,
                    operator_id=logger.test_run.operator_id,
                    operator_name=logger.test_run.operator_name,
                    fixture_id=logger.test_run.fixture_id,
                )
            )

        # Compute slot_index from LITMUS_SLOT_INDEX env (set by orchestrator)
        env_slot_index_str = os.environ.get("LITMUS_SLOT_INDEX")
        env_slot_index = int(env_slot_index_str) if env_slot_index_str else None

        event_log.emit(
            RunStarted(
                session_id=logger._session_id,
                run_id=logger.test_run.id,
                slot_id=env_slot_id,
                slot_index=env_slot_index,
                station_id=logger.test_run.station_id,
                station_name=logger.test_run.station_name,
                station_type=logger.test_run.station_type,
                station_location=logger.test_run.station_location,
                station_hostname=logger.test_run.station_hostname,
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
                git_branch=logger.test_run.git_branch,
                git_remote=logger.test_run.git_remote,
                environment_json=logger.test_run.environment_json,
                custom_metadata=dict(logger.test_run.custom_metadata),
                pid=os.getpid(),
            )
        )

        # Emit InstrumentConnected for each instrument
        _emit_instrument_events(logger, event_log)

        # Emit StepsDiscovered so subscribers know the full manifest
        from litmus.data.events import StepsDiscovered

        collected = get_collected_items()
        if collected:
            event_log.emit(
                StepsDiscovered(
                    session_id=logger._session_id,
                    run_id=logger.test_run.id,
                    items=[ci.model_dump() for ci in collected],
                )
            )

    set_current_logger(logger)
    yield logger

    # Copy collected items onto test_run so the manifest captures not-started steps
    logger.test_run.collected_items = get_collected_items()

    # Close ChannelStore before finalizing (before EventLog closes subscribers)
    _cs_final = get_channel_store()
    if _cs_final is not None:
        _cs_final.close()
        set_channel_store(None)

    # Finalize emits RunEnded (does not close event log).
    test_run = logger.finalize()

    # In multi-slot worker mode, orchestrator handles SessionEnded.
    # Workers only close their event log.
    _is_worker = (
        os.environ.get("LITMUS_SLOT_ID") is not None
        and int(os.environ.get("LITMUS_SLOT_COUNT", "1")) > 1
    )
    if logger.event_log is not None:
        if not _is_worker:
            logger.event_log.emit(
                SessionEnded(
                    session_id=logger._session_id,
                    outcome=test_run.outcome.value,
                )
            )
        logger.event_log.close()

    # Close EventStore — releases daemon ref. Event logs were already closed
    # by finalize(), and on_flush callback pushed final batches to Flight.
    if _event_store is not None:
        _event_store.close()
        set_event_store(None)

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


def _extract_code_identity(item: Any) -> dict[str, str | None]:
    """Extract code identity fields from a pytest.Item node."""
    identity: dict[str, str | None] = {}
    identity["node_id"] = getattr(item, "nodeid", None)
    identity["function"] = getattr(item, "originalname", None) or getattr(item, "name", None)
    identity["class_name"] = item.cls.__name__ if getattr(item, "cls", None) else None
    mod = getattr(item, "module", None)
    identity["module"] = mod.__name__ if mod else None

    item_path = getattr(item, "path", None)
    if item_path is not None:
        rootdir = getattr(item.config, "rootpath", None)
        if rootdir:
            try:
                identity["file"] = str(item_path.relative_to(rootdir))
            except ValueError:
                identity["file"] = str(item_path)
        else:
            identity["file"] = str(item_path)
    else:
        identity["file"] = None

    own_markers = getattr(item, "own_markers", [])
    if own_markers:
        identity["markers"] = ",".join(m.name for m in own_markers)
    else:
        identity["markers"] = None

    return identity


@pytest.fixture
def litmus_step(litmus_logger, request) -> Generator[None, None, None]:
    """Create step for test function (use when NOT using @litmus_test).

    Note: @litmus_test decorated tests already create their own steps.
    Only use this fixture for tests that need step tracking without @litmus_test.
    """
    identity = _extract_code_identity(request.node)
    litmus_logger.start_step(request.node.name, **identity)
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

    In worker mode (``LITMUS_SLOT_ID`` set), extracts this slot's points
    from a multi-slot fixture config so downstream fixtures (pins,
    FixtureManager) see a flat ``points`` dict.

    Returns:
        FixtureConfig instance, or None if not specified.
    """
    fixture_path = _find_fixture_file(request.config)
    if not fixture_path:
        return None
    config_path = str(fixture_path)

    from litmus.store import load_fixture

    fc = load_fixture(Path(config_path))

    # Worker mode: extract this slot's points from multi-slot fixture
    slot_id = os.environ.get("LITMUS_SLOT_ID")
    if slot_id and fc.is_multi_slot and fc.slots:
        slot = fc.slots.get(slot_id)
        if slot is not None:
            # Return a flat fixture config with just this slot's points
            fc = FixtureConfig(
                id=fc.id,
                name=fc.name,
                description=fc.description,
                product_id=fc.product_id,
                product_family=fc.product_family,
                product_revision=fc.product_revision,
                points=slot.points,
                dut_resource=slot.dut_resource,
            )

    return fc


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

    # Set mocked flag early so InstrumentConnected events capture it
    inst_configs = station_config.instruments or {}
    for role, rec in records.items():
        inline = inst_configs.get(role)
        rec.mocked = mock_instruments or (inline.mock if inline else False)

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
    if not station_config:
        active: dict[str, Any] = {}
        set_active_instruments(active)
        yield active
        return

    inst_configs = station_config.instruments or {}
    session_id = litmus_logger._session_id if litmus_logger else None
    run_id = litmus_logger.test_run.id if litmus_logger else None
    event_log = litmus_logger.event_log if litmus_logger else None

    pool = InstrumentPool(
        session_id=session_id,
        event_log=event_log,
        channel_store=get_channel_store(),
        mock_all=mock_instruments,
        station_id=station_config.id or "",
        run_id=run_id,
    )

    for role, record in instrument_records.items():
        inline_config = inst_configs.get(role)
        use_mock = mock_instruments or (inline_config.mock if inline_config else False)
        record.mocked = use_mock
        try:
            pool.acquire(role, record, inline_config)
        except ValueError:
            continue

    set_active_instruments(pool.active)
    yield pool.active

    pool.release_all()


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
def dut(
    spec_context,
    fixture_config,
    mock_instruments,
) -> Generator[Any, None, None]:
    """Instantiate and yield the DUT communication driver.

    Resolves the driver class from ``Product.driver`` (loaded via spec_context)
    and connects using ``FixtureConfig.dut_resource``. Follows the same pattern
    as instrument fixtures — session-scoped, auto-disconnected at teardown.

    Usage in tests:
        def test_firmware_version(dut):
            assert dut.get_version().startswith("2.")

    Returns:
        Connected DUT driver instance, or None if product has no driver.
    """
    if not spec_context or not spec_context.product.driver:
        yield None
        return

    from litmus.products.loader import load_product_driver

    driver_class = load_product_driver(spec_context.product)
    if driver_class is None:
        warnings.warn(
            f"DUT driver {spec_context.product.driver!r} could not be imported",
            UserWarning,
            stacklevel=2,
        )
        yield None
        return

    # Resolve connection resource from fixture config
    dut_resource = fixture_config.dut_resource if fixture_config else None

    if mock_instruments:
        from litmus.instruments.mocks import Mock

        inst: Any = Mock(driver_class)
        yield inst
        return

    if dut_resource:
        inst = driver_class(dut_resource)
    else:
        inst = driver_class()

    connect_fn = getattr(inst, "connect", None)
    if callable(connect_fn):
        connect_fn()

    yield inst

    # Teardown: disconnect
    try:
        if hasattr(inst, "disconnect"):
            inst.disconnect()
        elif hasattr(inst, "close"):
            inst.close()
    except (OSError, RuntimeError) as exc:
        warnings.warn(f"Failed to cleanup DUT driver: {exc}", stacklevel=2)


@pytest.fixture(scope="session")
def _route_manager(
    instruments,
    fixture_config,
    litmus_logger,
) -> Generator[RouteManager | None, None, None]:
    """Session-scoped route manager for switched signal routing.

    Built from fixture points that have routes. Holds locks for the
    session duration. Yields None if no routes are configured.
    """
    if not fixture_config or not instruments:
        yield None
        return

    session_id = litmus_logger._session_id if litmus_logger else None
    event_log = litmus_logger.event_log if litmus_logger else None
    station_id = ""
    if litmus_logger and hasattr(litmus_logger, "_station_id"):
        station_id = getattr(litmus_logger, "_station_id", "")

    rm = RouteManager(
        points=fixture_config.points,
        instruments=instruments,
        session_id=session_id,
        station_id=station_id,
        event_log=event_log,
    )

    if not rm.has_routes:
        yield None
        return

    yield rm
    rm.deactivate_all()


@pytest.fixture
def routes(request) -> Generator[RouteManager | None, None, None]:
    """Per-test route manager for explicit switch routing.

    Use with the context-manager pattern for direct instrument access::

        def test_vout(dmm, routes):
            with routes.for_pin("VOUT"):
                v = dmm.measure_voltage()
            assert 3.2 < v < 3.4

    Yields None if no routes are configured (tests without switching
    can still request this fixture without error).
    """
    rm = _safe_get_session_fixture(request, "_route_manager")
    yield rm
    if rm is not None:
        rm.deactivate_all()


@pytest.fixture(autouse=True)
def _route_cleanup(request) -> Generator[None, None, None]:
    """Per-test cleanup for lazy-activated routes (pins[] pattern).

    Ensures all routes activated via RoutedProxy during a test are
    deactivated before the next test runs.
    """
    yield
    rm = _safe_get_session_fixture(request, "_route_manager")
    if rm is not None:
        rm.deactivate_all()


@pytest.fixture(scope="session")
def pins(instruments, fixture_config, _route_manager) -> PinAccessor:
    """UUT-centric pin accessor for tests.

    Resolves DUT pin names to instrument instances. When fixture points
    have switch routes, instruments are wrapped in RoutedProxy for
    transparent route activation on first use.

        def test_output(pins):
            pins["VIN"].set_voltage(5.0)
            pins["VIN"].enable_output()
            assert pins["VOUT"].measure_voltage() > 3.0

    Raises:
        pytest.UsageError: If no fixture config or instruments available.
    """
    _require_fixture_and_instruments(fixture_config, instruments, "pins")

    manager = FixtureManager(fixture_config, instruments, route_manager=_route_manager)
    return PinAccessor(manager)


@pytest.fixture(scope="session")
def fixture_manager(instruments, fixture_config, _route_manager) -> FixtureManager:
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
    return FixtureManager(fixture_config, instruments, route_manager=_route_manager)


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

    set_current_code_identity(_extract_code_identity(item))

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


# ---------------------------------------------------------------------------
# Multi-slot orchestrator/worker mode
# ---------------------------------------------------------------------------


def _is_orchestrator_mode(config) -> bool:
    """Detect if this process should orchestrate multi-slot execution.

    Orchestrator mode activates when:
    1. LITMUS_SLOT_ID is NOT set (we're not a worker child)
    2. A multi-slot fixture config is detected
    """
    if os.environ.get("LITMUS_SLOT_ID"):
        return False  # Already a worker

    fixture_path = config.getoption("--fixture-config", default=None)
    if not fixture_path:
        return False

    try:
        from litmus.store import load_fixture

        fc = load_fixture(Path(fixture_path))
        return fc.is_multi_slot
    except Exception:
        return False


def _is_worker_mode() -> bool:
    """Detect if this process is a multi-slot worker child."""
    return bool(os.environ.get("LITMUS_SLOT_ID"))


def _build_child_cmd(config) -> list[str]:
    """Build the pytest command for child processes.

    Reconstructs the original pytest invocation, stripping --dut-serials
    (each child gets --dut-serial via env var).
    """
    args = list(config.invocation_params.args)

    # Remove --dut-serials from args (children get individual --dut-serial via env)
    filtered: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("--dut-serials="):
            continue
        if arg == "--dut-serials":
            skip_next = True
            continue
        # Remove --dut-serial too (children get it from env)
        if arg.startswith("--dut-serial="):
            continue
        if arg == "--dut-serial":
            skip_next = True
            continue
        filtered.append(arg)

    return [sys.executable, "-m", "pytest"] + filtered


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session):
    """Override test loop for multi-slot orchestration.

    When a multi-slot fixture is detected and LITMUS_SLOT_ID is not set,
    this hook takes over: spawns N child pytest processes (one per slot),
    coordinates sync points, and reports aggregate results.

    In worker mode or single-slot mode, returns None to use the default loop.
    """
    if not _is_orchestrator_mode(session.config):
        return None  # Use default test loop

    from litmus.execution.dut_provider import CLIDUTProvider
    from litmus.execution.slots import (
        detect_shared_instruments,
        resolve_fixture_slots,
    )
    from litmus.store import load_fixture, load_station

    fixture_path = session.config.getoption("--fixture-config")
    fixture_config = load_fixture(Path(fixture_path))

    # Load station config for instrument types/resources
    station_path = _find_station_file(session.config)
    station_config_obj: StationConfig | None = None
    if station_path:
        station_config_obj = load_station(station_path)

    # Resolve slots
    slots = resolve_fixture_slots(fixture_config)
    slot_ids = list(slots.keys())  # preserve YAML definition order

    # Detect shared instruments — ALL shared roles are served via InstrumentServer
    shared_roles = detect_shared_instruments(slots)
    station_instruments = station_config_obj.instruments if station_config_obj else {}

    # Resolve DUT identities
    dut_serial = session.config.getoption("--dut-serial")
    dut_serials_raw = session.config.getoption("--dut-serials")
    provider = CLIDUTProvider.from_cli_args(
        dut_serial=dut_serial,
        dut_serials=dut_serials_raw,
        slot_ids=slot_ids,
    )
    duts = {sid: provider.get_dut(sid) for sid in slot_ids}

    if dut_serial and not dut_serials_raw and len(slot_ids) > 1:
        warnings.warn(
            f"Single --dut-serial '{dut_serial}' applied to all {len(slot_ids)} slots. "
            f"Use --dut-serials for per-slot assignment.",
            stacklevel=1,
        )

    # Create EventStore for sync coordination. pytest_runtestloop runs after
    # litmus_logger session setup (session-scoped fixture), so get_event_store()
    # will return the store litmus_logger already created. litmus_logger owns
    # the close() call in its teardown, so SlotRunner must not close it.
    from litmus.data.event_store import EventStore

    event_store = get_event_store()
    if event_store is None:
        event_store = EventStore()
        set_event_store(event_store)

    # Use the logger's session_id so sync events from children correlate
    # with the parent's EventStore subscriptions.
    from litmus.execution.decorators import get_current_logger

    current_logger = get_current_logger()
    session_id = current_logger.test_run.session_id if current_logger else uuid4()

    # Always subprocess mode — instrument server handles all shared instruments
    _run_subprocess_mode(
        session,
        slots,
        duts,
        session_id,
        event_store,
        shared_roles=shared_roles,
        station_instruments=station_instruments,
        mock_all=session.config.getoption("--mock-instruments"),
    )

    return True  # Suppress default test execution


def _run_subprocess_mode(
    session: Any,
    slots: dict[str, Any],
    duts: dict[str, Any],
    session_id: UUID,
    event_store: Any,
    shared_roles: set[str] | None = None,
    station_instruments: dict[str, Any] | None = None,
    mock_all: bool = False,
) -> None:
    """Run multi-slot tests using subprocess-per-slot.

    If shared instruments are detected, starts an InstrumentServer in the
    orchestrator process and passes the address to workers via env vars.
    Workers get RemoteInstrumentProxy objects for those roles.
    """
    from litmus.execution.slot_runner import SlotRunner

    server = None
    shared_drivers: dict[str, Any] = {}
    shared_roles = shared_roles or set()
    station_instruments = station_instruments or {}

    # Only serve non-mocked shared instruments through the server.
    # Mocked instruments get independent instances per worker so each
    # worker has its own mock state (per-test mock values don't leak).
    served_roles: set[str] = set()
    if shared_roles:
        from litmus.instruments.lifecycle import load_and_connect
        from litmus.instruments.server import InstrumentServer
        from litmus.models.instrument import InstrumentRecord

        concurrent_roles: set[str] = set()
        resources: dict[str, str] = {}
        current_role = ""

        try:
            for role in shared_roles:
                current_role = role
                inst_cfg = station_instruments.get(role)
                if inst_cfg is None:
                    continue

                is_mocked = mock_all or inst_cfg.mock
                if is_mocked:
                    continue  # Workers get independent mocks

                record = InstrumentRecord(
                    role=role,
                    instrument_id=role,
                    driver=inst_cfg.driver,
                    resource=inst_cfg.resource or "",
                    protocol="visa",
                    mocked=False,
                )

                mock_config = inst_cfg.mock_config if inst_cfg else {}
                driver = load_and_connect(
                    record,
                    mock=False,
                    mock_config=mock_config,
                )
                shared_drivers[role] = driver
                served_roles.add(role)

                if inst_cfg.resource:
                    resources[role] = inst_cfg.resource
                if inst_cfg.type == "switch":
                    concurrent_roles.add(role)
        except Exception as exc:
            # Clean up any already-connected drivers
            from litmus.instruments.lifecycle import disconnect

            for cleanup_role, driver in shared_drivers.items():
                disconnect(driver, cleanup_role)
            raise RuntimeError(
                f"Failed to connect shared instrument '{current_role}': {exc}"
            ) from exc

        if shared_drivers:
            server = InstrumentServer(
                shared_drivers,
                resources=resources,
                concurrent_roles=concurrent_roles,
            )
            server.start()

    # Emit orchestrator-level SessionStarted before spawning workers
    from litmus.data.events import SessionEnded, SessionStarted
    from litmus.execution.decorators import get_current_logger

    current_logger = get_current_logger()
    event_log = None
    if event_store is not None:
        event_log = event_store.get_event_log(session_id)

    station_id = ""
    station_name = None
    station_type = None
    station_location = None
    operator_id = None
    operator_name = None
    fixture_id = None
    if current_logger:
        tr = current_logger.test_run
        station_id = tr.station_id
        station_name = tr.station_name
        station_type = tr.station_type
        station_location = tr.station_location
        operator_id = tr.operator_id
        operator_name = tr.operator_name
        fixture_id = tr.fixture_id

    if event_log is not None:
        event_log.emit(
            SessionStarted.from_station(
                session_id=session_id,
                station_id=station_id,
                station_name=station_name,
                station_type=station_type,
                station_location=station_location,
                operator_id=operator_id,
                operator_name=operator_name,
                fixture_id=fixture_id,
                slot_count=len(slots),
            )
        )

    try:

        def _stream_output(slot_id: str, line: str) -> None:
            sys.stdout.write(f"[{slot_id}] {line}")
            sys.stdout.flush()

        runner = SlotRunner(
            slots,
            duts,
            session_id=session_id,
            instrument_server_address=server.address_str if server else None,
            shared_roles=served_roles if server else None,
        )
        child_cmd = _build_child_cmd(session.config)
        results = runner.run(
            child_cmd,
            on_output=_stream_output,
            event_store=event_store,
        )

        _report_slot_results(session, results)

        # Emit orchestrator-level SessionEnded with worst outcome
        if event_log is not None:
            worst = "pass"
            for r in results.values():
                if r.outcome == "error":
                    worst = "error"
                    break
                if r.outcome == "fail":
                    worst = "fail"
            event_log.emit(
                SessionEnded(
                    session_id=session_id,
                    outcome=worst,
                )
            )
    finally:
        if event_log is not None:
            event_log.close()

        if server is not None:
            server.stop(force=True)

        from litmus.instruments.lifecycle import disconnect

        for role, driver in shared_drivers.items():
            disconnect(driver, role)


def _extract_pytest_summary(output_lines: list[str]) -> str:
    """Extract the pytest summary line from worker output.

    Scans from the end looking for lines matching pytest's summary format
    (e.g., "1 passed", "2 failed, 1 passed").
    """
    import re

    pattern = re.compile(r"\d+ (passed|failed|error|warning|skipped|deselected)")
    for line in reversed(output_lines):
        if pattern.search(line):
            # Strip ANSI escape codes and leading/trailing whitespace
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            # Remove leading "=" decorations
            clean = clean.strip("= ").strip()
            return clean
    return "(no summary)"


def _report_slot_results(session: Any, results: dict[str, Any]) -> None:
    """Report per-slot results from subprocess mode."""
    import sys

    sys.stdout.write("\n" + "=" * 60 + "\n")
    sys.stdout.write("Multi-DUT Results\n")
    sys.stdout.write("=" * 60 + "\n")
    for slot_id in results:
        r = results[slot_id]
        status = "PASS" if r.outcome == "pass" else "FAIL"
        summary = _extract_pytest_summary(r.output_lines)
        sys.stdout.write(f"  {slot_id}: {status}  {summary}\n")
    sys.stdout.write("=" * 60 + "\n\n")
    sys.stdout.flush()

    failed_slots = [sid for sid, r in results.items() if r.outcome != "pass"]
    session.testsfailed = len(failed_slots)


# ---------------------------------------------------------------------------
# Worker-mode fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sync(litmus_logger):
    """Provide sync point for multi-DUT test coordination.

    In worker mode (LITMUS_SLOT_ID set), returns a SyncPoint that
    blocks until all slots arrive. In single-slot mode, returns None.

    Usage:
        def test_measure_hot(dmm, sync):
            if sync:
                sync.wait("thermal_soak", timeout=300)
            v = dmm.measure_voltage()
            assert v > 3.0
    """
    if not _is_worker_mode():
        yield None
        return

    from litmus.execution.sync import get_sync

    # Reuse the session EventStore from litmus_logger — never create a second one
    event_store = get_event_store()
    sync_point = get_sync(event_store)
    yield sync_point
