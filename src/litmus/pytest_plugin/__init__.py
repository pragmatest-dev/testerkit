"""pytest plugin for Litmus test framework."""

from __future__ import annotations

import os
import sys
import warnings
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from pydantic import ValidationError

from litmus.data.models import CollectedItem, TestVector
from litmus.execution._state import (
    _active_vector_index_var,
    get_active_connection,
    get_active_facets,
    get_active_instruments,
    get_active_limits,
    get_active_profile,
    get_active_spec_context,
    get_active_vector_index,
    get_active_vector_params,
    get_channel_store,
    get_collected_items,
    get_current_code_identity,
    get_current_step,
    get_current_step_aliases,
    get_current_step_config,
    get_event_store,
    get_instrument_records,
    get_session_inputs,
    push_current_vector,
    set_active_facets,
    set_active_instruments,
    set_active_limits,
    set_active_profile,
    set_active_spec_context,
    set_active_vector_index,
    set_active_vector_params,
    set_channel_store,
    set_collected_items,
    set_current_code_identity,
    set_current_step_aliases,
    set_current_step_config,
    set_event_store,
    set_instrument_records,
    set_test_node_aliases,
    set_test_node_configs,
)
from litmus.execution.accessors import InstrumentAccessor
from litmus.execution.audit import audit_traceability
from litmus.execution.cascade import cascade_for, find_unmatched_profile_keys
from litmus.execution.connections import ConnectionIterator
from litmus.execution.decorators import get_current_logger, set_current_logger
from litmus.execution.harness import Context
from litmus.execution.instrument_events import emit_instrument_events
from litmus.execution.logger import RunContext, TestRunLogger
from litmus.execution.metadata import build_run_metadata
from litmus.execution.outputs import (
    create_subscriber,
    find_format_transport_callback,
    run_configured_outputs,
)
from litmus.execution.profiles import (
    ProfileError,
    apply_profile_addopts_env,
    collect_profile_facet_keys,
    facet_key_to_cli_flag,
    install_active_profile,
    install_session_inputs,
    load_project_defaults,
    required_input_key_to_cli_flag,
    resolve_test_phase,
)
from litmus.execution.sidecar import load_sidecar as _load_sidecar
from litmus.execution.verify import (
    LimitsFn,
    VerifyFn,
    build_verify_callable,
)
from litmus.fixtures.manager import FixtureManager, PinAccessor
from litmus.instruments.pool import InstrumentPool
from litmus.instruments.route_manager import RouteManager
from litmus.models.instrument import InstrumentRecord
from litmus.models.station import StationConfig
from litmus.models.test_config import (
    FixtureConfig,
    PromptConfig,
    RetryPolicy,
    SweepEntry,
    TestEntry,
)
from litmus.products.context import SpecContext
from litmus.prompts import ask as ask_prompt
from litmus.pytest_plugin.autouse import (
    _litmus_apply_mocks,
    _litmus_push_limits,
    _litmus_push_params,
    _litmus_resolve_connections,
    _reseat_current_logger,
    _route_cleanup,
)
from litmus.pytest_plugin.helpers import (
    find_fixture_file as _find_fixture_file,
)
from litmus.pytest_plugin.helpers import (
    find_station_file as _find_station_file,
)
from litmus.pytest_plugin.helpers import (
    join_marker_names as _join_marker_names,
)
from litmus.pytest_plugin.helpers import (
    node_cls_func as _node_cls_func,
)
from litmus.pytest_plugin.helpers import (
    safe_get_session_fixture as _safe_get_session_fixture,
)
from litmus.pytest_plugin.markers import (
    StackedMarkersError,
    apply_entry_markers,
    enforce_no_inline_stacking,
    normalize_inline_list_payload,
)
from litmus.pytest_plugin.retry import retry_policy_to_flaky_kwargs
from litmus.pytest_plugin.sweeps import (
    parametrize_call_rows,
    parametrize_calls_for_entry,
    sweep_to_parametrize_args,
)

# State helpers re-exported for back-compat with consumers that import
# from litmus.pytest_plugin (logger, harness, accessors, manager, tests).
# Plus the autouse fixture names re-exported from autouse.py — pytest
# discovers them by inspecting this package's namespace.
__all__ = [
    # Autouse fixtures (pytest discovery — must live in this namespace)
    "_litmus_apply_mocks",
    "_litmus_push_limits",
    "_litmus_push_params",
    "_litmus_resolve_connections",
    "_reseat_current_logger",
    "_route_cleanup",
    # State helpers
    "get_active_facets",
    "get_active_instruments",
    "get_active_connection",
    "get_active_limits",
    "get_active_profile",
    "get_active_spec_context",
    "get_active_vector_index",
    "get_active_vector_params",
    "get_channel_store",
    "get_collected_items",
    "get_current_code_identity",
    "get_current_step_aliases",
    "get_current_step_config",
    "get_event_store",
    "get_instrument_records",
    "get_session_inputs",
    "set_active_facets",
    "set_active_instruments",
    "set_active_limits",
    "set_active_profile",
    "set_active_spec_context",
    "set_active_vector_index",
    "set_active_vector_params",
    "set_channel_store",
    "set_collected_items",
    "set_current_code_identity",
    "set_current_step_aliases",
    "set_current_step_config",
    "set_event_store",
    "set_instrument_records",
    "set_test_node_aliases",
    "set_test_node_configs",
]


def pytest_configure(config):
    """Register Litmus markers and auto-register instrument role fixtures."""
    for marker in (
        "litmus_sweeps([{argname: argvalues}, ...]): Declare nested "
        "parametric sweeps — runner-neutral alias for parametrize. The "
        "payload is a list of sweep dicts; each dict is one nesting "
        "level (top = outer, slowest loop). Single-key dict = one axis; "
        "multi-key dict = zipped axes (paired argvalues). Stacking "
        "multiple markers concatenates their lists.",
        "litmus_retry(max_attempts=N, delay=S, on=[...]): Declare retry "
        "policy — runner-neutral alias for retry markers. Translates to "
        "pytest-rerunfailures' @pytest.mark.flaky in pytest; OpenHTF / "
        "unittest wrappers map to their own retry primitives. "
        "max_attempts is total attempts (1 = no retry); delay is seconds "
        "between attempts; on is an optional list of exception class "
        "names to retry on (default: any exception).",
        "litmus_limits(**kwargs): Inject limits by measurement name (merges with sidecar limits:)",
        "litmus_specs([<characteristic_id>, ...]): Bind the test to one "
        "or more product characteristics; provides spec-relative limit "
        "context and auto-derives fixture connections from the "
        "characteristic's pins. v1 supports one binding per test (single "
        "iteration scope); multi-binding semantics may relax in future.",
        "litmus_connections(connections=[...] | instrument_channels={...}): "
        "Bind the test to explicit named connections or instrument-channel ranges.",
        "litmus_prompts(**kwargs): Declare named operator prompts; "
        "each kwarg is `name=PromptConfig-shaped dict`. The `prompt` "
        "fixture resolves them by name (or implicitly when only one is "
        "in scope).",
        "litmus_mocks([{target: <fixture.attr>, **patch_kwargs}, ...]): "
        "Install mocks for the duration of a test. The payload is a list "
        "of mock dicts; each dict's kwargs (excluding `target`) follow "
        "unittest.mock.patch.object(target, ...). Stacking multiple "
        "markers concatenates their lists.",
    ):
        config.addinivalue_line("markers", marker)
    try:
        install_active_profile(config)
        install_session_inputs(load_project_defaults(), config)
    except ProfileError as exc:
        raise pytest.UsageError(str(exc)) from exc

    # Auto-register instrument role fixtures from station config
    station_path = _find_station_file(config)
    if station_path is None:
        return

    try:
        from litmus.store import load_station

        station_model = load_station(station_path)
    except (ValidationError, yaml.YAMLError, OSError, ValueError) as exc:
        # Fail fast on station config errors — same posture as profile
        # load failures (see pytest_load_initial_conftests). A typo'd
        # station path silently warning would surface as a confusing
        # "instrument not found" later.
        raise pytest.UsageError(f"Failed to load station config {station_path!s}: {exc}") from exc

    if not station_model:
        return

    instruments_map = station_model.instruments or {}

    # Sequences (deleted) used to inject per-test fixture aliases and configs.
    # With sequences gone, both maps are empty for the lifetime of the session.
    set_test_node_aliases({})
    set_test_node_configs({})
    all_alias_names: set[str] = set()

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
    """Show litmus results location (and active profile's composed addopts) in the header."""
    from litmus.data.results_dir import resolve_results_dir

    results_dir = config.getoption("--results-dir", default=None)
    resolved = resolve_results_dir(results_dir)
    if results_dir:
        lines = [
            f"litmus: results → {resolved}"
            " (local — remove results_dir from litmus.yaml for global storage)"
        ]
    else:
        lines = [f"litmus: results → {resolved}"]

    profile_name = get_active_profile()
    if profile_name:
        composed = os.environ.get("PYTEST_ADDOPTS", "").strip()
        if composed:
            lines.append(f"litmus: profile={profile_name} addopts={composed!r}")
        else:
            lines.append(f"litmus: profile={profile_name}")

    return lines


def pytest_sessionstart(session):
    """Validate DUT serial at session start."""
    config = session.config
    dut_serial = config.getoption("--dut-serial")
    dut_serials = config.getoption("--dut-serials")

    # Skip validation if per-slot serials were explicitly provided
    if dut_serials:
        return

    requested_phase = config.getoption("--test-phase") or os.environ.get("LITMUS_TEST_PHASE")
    test_phase = resolve_test_phase(requested_phase, mocks_active=_mocks_active(config))

    if test_phase == "development":
        return

    # Non-development phase: require explicit DUT serial
    if dut_serial == "DUT001":
        serial = _prompt_for_serial(test_phase)
        config.option.dut_serial = serial


def pytest_collection_modifyitems(config, items: list[pytest.Item]) -> None:
    """Apply active-profile markers/filters, then capture the item list.

    Two passes:

    1. **Profile application** (only when ``--litmus-profile`` is set):
       inject markers for matching node-ids via ``item.add_marker`` and
       compose profile ``keyword``/``markexpr`` filters with any CLI
       ``-k`` / ``-m`` already present (AND-composed — CLI wins on
       conflict since its expression is appended last).

    2. **Snapshot** every collected item into ``_collected_items`` so the
       step manifest can report not-started steps.

    The snapshot captures markers **after** profile injection, so the
    manifest reflects the effective marker set.
    """
    _apply_cascade_to_items(items)
    _translate_retry_markers(items)

    collected = []
    for item in items:
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
                markers=_join_marker_names(item.iter_markers(), sort=True),
            )
        )
    set_collected_items(collected)

    _warn_unmatched_profile_keys(items)


def _translate_retry_markers(items: list[pytest.Item]) -> None:
    """Pytest adapter — translate ``litmus_retry`` markers into ``pytest.mark.flaky``.

    Most-specific marker wins when multiple stack from different scopes
    (file → class → test → profile). The validation + flaky-kwarg
    mapping is runner-neutral; only the destination marker is pytest's.
    """
    for item in items:
        retry_markers = list(item.iter_markers("litmus_retry"))
        if not retry_markers:
            continue
        marker = retry_markers[0]
        try:
            policy = RetryPolicy.model_validate(dict(marker.kwargs))
        except ValueError as exc:
            raise pytest.UsageError(f"{item.nodeid}: invalid litmus_retry — {exc}") from exc
        item.add_marker(pytest.mark.flaky(**retry_policy_to_flaky_kwargs(policy)))


def _warn_unmatched_profile_keys(items: list[pytest.Item]) -> None:
    """Pytest adapter — collect (cls, func) ids, delegate to runner-neutral matcher."""
    profile = get_active_profile()
    if profile is None:
        return
    test_ids: list[tuple[str | None, str]] = []
    for item in items:
        if not isinstance(item, pytest.Function):
            continue
        cls = getattr(item, "cls", None)
        cls_name = cls.__name__ if cls is not None else None
        test_ids.append((cls_name, item.originalname))
    unmatched = find_unmatched_profile_keys(profile, test_ids)
    if not unmatched:
        return
    warnings.warn(
        "Active profile has keys that match no collected test:\n"
        + "\n".join(unmatched)
        + "\nUse an exact test name, a class name with nested method, or remove the entry.",
        UserWarning,
        stacklevel=1,
    )


def _enforce_no_inline_stacking(item: pytest.Item) -> None:
    """Pytest adapter — count inline ``litmus_X`` markers; delegate to runner-neutral check."""
    try:
        enforce_no_inline_stacking([m.name for m in item.own_markers])
    except StackedMarkersError as exc:
        raise pytest.UsageError(f"{item.nodeid}: {exc}") from exc


def _cascade_for_item(item: pytest.Item) -> TestEntry:
    """Pytest adapter — load sidecar, look up profile, delegate to runner-neutral cascade."""
    if not isinstance(item, pytest.Function):
        return TestEntry()
    module = getattr(item, "module", None)
    module_file = getattr(module, "__file__", None)
    sidecar = _load_sidecar(Path(module_file)) if module_file is not None else None
    cls_name, func_name = _node_cls_func(item)
    return cascade_for(sidecar, get_active_profile(), cls_name, func_name)


def _apply_cascade_to_items(items: list[pytest.Item]) -> None:
    """Apply sidecar + profile cascade as Litmus + ecosystem markers per item."""
    for item in items:
        if not isinstance(item, pytest.Function):
            continue
        _enforce_no_inline_stacking(item)
        apply_entry_markers(item, _cascade_for_item(item))


def pytest_sessionfinish(session, exitstatus):
    """Clean up all session-scoped ContextVars and module-level state."""
    set_active_instruments({})
    set_instrument_records({})
    set_test_node_aliases({})
    set_test_node_configs({})
    set_collected_items([])
    set_channel_store(None)
    set_event_store(None)
    set_active_profile(None)


def pytest_load_initial_conftests(early_config, parser, args):
    """Apply ``profile.pytest.addopts`` via ``PYTEST_ADDOPTS`` before collection."""
    try:
        apply_profile_addopts_env(args)
    except ProfileError as exc:
        raise pytest.UsageError(str(exc)) from exc


def pytest_addoption(parser):
    """Add Litmus command-line options."""
    project = load_project_defaults()
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
        default=None,
        dest="mock_instruments",
        help="Use mock instruments instead of real hardware. "
        "Resolution: this flag > LITMUS_MOCK_INSTRUMENTS env var > "
        "litmus.yaml `mock_instruments:` > false.",
    )
    group.addoption(
        "--no-mock-instruments",
        action="store_false",
        default=None,
        dest="mock_instruments",
        help="Use real hardware (overrides LITMUS_MOCK_INSTRUMENTS env "
        "and litmus.yaml `mock_instruments: true`).",
    )
    group.addoption(
        "--no-test-mocks",
        action="store_true",
        default=False,
        help="Ignore all per-test method mocks (sidecar mocks: blocks). "
        "Driver methods return their real values. Instrument-layer --mock-instruments "
        "is unaffected.",
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
        "--test-phase",
        default=None,
        help="Test phase (development, validation, characterization, production). "
        "If not specified, auto-detects from git status.",
    )
    group.addoption(
        "--strict-traceability",
        action="store_true",
        default=False,
        help="Fail tests whose measurements lack required traceability fields "
        "(run_id, step_name, and spec_ref/dut_pin when a spec is active).",
    )
    group.addoption(
        "--litmus-profile",
        default=os.environ.get("LITMUS_PROFILE"),
        help="Named profile from litmus.yaml `profiles:` "
        "(overrides vectors, limits, markers, and filter for the session).",
    )
    group.addoption(
        "--no-profile",
        action="store_true",
        default=False,
        help="Skip profile resolution. Use when profiles are declared "
        "but you want to run with bare project defaults (ad-hoc runs).",
    )
    # Auto-synthesize one --<facet> flag per declared profile facet key.
    # Declaring `product: power_board` in any profile turns --product into
    # a selector for this project — no generic --facet escape hatch.
    # ``test_phase`` already has its own --test-phase flag above, so the
    # facet reuses it rather than re-registering.
    facet_keys = set(collect_profile_facet_keys(project))
    for key in sorted(facet_keys):
        if key == "test_phase":
            continue
        group.addoption(
            facet_key_to_cli_flag(key),
            default=None,
            help=f"Select profile by facet {key!r} (from litmus.yaml profiles).",
        )
    # Auto-synthesize one --<key> flag per declared required_inputs key.
    # Skip keys that already exist as facet flags (or are built-in like
    # --test-phase) to avoid double-registration.
    builtin_flags = {"--test-phase", "--operator", "--station", "--fixture"}
    for key in sorted(project.required_inputs):
        flag = required_input_key_to_cli_flag(key)
        if flag in builtin_flags or key in facet_keys:
            continue
        prompt_cfg = project.required_inputs[key]
        group.addoption(
            flag,
            default=None,
            help=prompt_cfg.message + " (litmus.yaml: required_inputs)",
        )


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


def _build_run_metadata(request: pytest.FixtureRequest) -> dict[str, Any]:
    """Pytest adapter — read session fixtures + CLI options, delegate to runner-neutral builder."""
    requested_phase = request.config.getoption("--test-phase") or os.environ.get(
        "LITMUS_TEST_PHASE"
    )
    return build_run_metadata(
        dut_serial=request.config.getoption("--dut-serial"),
        dut_part_number=request.config.getoption("--dut-part-number"),
        dut_revision=request.config.getoption("--dut-revision"),
        dut_lot_number=request.config.getoption("--dut-lot"),
        station_id=request.config.getoption("--station"),
        station_config=_safe_get_session_fixture(request, "station_config"),
        fixture_config=_safe_get_session_fixture(request, "fixture_config"),
        spec_context=_safe_get_session_fixture(request, "spec_context"),
        operator_id=request.config.getoption("--operator"),
        project_dir=request.config.rootpath,
        results_dir=request.config.getoption("--results-dir"),
        test_phase=resolve_test_phase(requested_phase, mocks_active=_mocks_active(request.config)),
        profile_name=request.config.getoption("--litmus-profile", default=None),
        profile_facets=dict(get_active_facets()),
        session_inputs=dict(get_session_inputs()),
        instrument_records=_safe_get_session_fixture(request, "instrument_records"),
    )


def _is_multi_slot_worker() -> bool:
    """Return True when this process is one of N>1 workers in a multi-slot run."""
    return (
        os.environ.get("LITMUS_SLOT_ID") is not None
        and int(os.environ.get("LITMUS_SLOT_COUNT", "1")) > 1
    )


def _setup_event_log_and_subscribers(
    logger: TestRunLogger, results_path: Path, session_id: UUID
) -> Any:
    """Wire EventStore + EventLog + default and configured subscribers.

    Returns the EventStore (existing or newly created) so the teardown
    helper can close it after the run.
    """
    from litmus.data.backends.parquet import ParquetSubscriber
    from litmus.data.channels.store import ChannelStore
    from litmus.data.event_store import EventStore
    from litmus.data.subscribers import get_subscriber_class

    event_store = get_event_store()
    if event_store is None:
        event_store = EventStore(_results_dir=results_path)
        set_event_store(event_store)

    event_log = event_store.get_event_log(session_id)
    logger.event_log = event_log

    parquet_on_output = find_format_transport_callback("parquet", results_path)
    event_log.add_subscriber(ParquetSubscriber(results_path, on_output=parquet_on_output))

    channels_on_output = find_format_transport_callback("channels", results_path)
    channel_store = ChannelStore(
        results_path / "channels",
        session_id,
        serve=True,
        on_output=channels_on_output,
    )
    channel_store.open()
    set_channel_store(channel_store)

    # User-configured subscriber formats from litmus.yaml outputs:
    try:
        from litmus.store import load_project_config

        config = load_project_config()
        for output_cfg in config.outputs:
            fmt = output_cfg.format
            if fmt and fmt not in {"parquet", "channels"}:
                cls = get_subscriber_class(fmt)
                if cls is not None:
                    sub = create_subscriber(cls, fmt, output_cfg, results_path, session_id)
                    event_log.add_subscriber(sub)
    except (ValidationError, OSError, KeyError) as exc:
        warnings.warn(
            f"Failed to register configured output subscribers: {exc}",
            stacklevel=2,
        )

    return event_store


def _emit_session_start_events(logger: TestRunLogger) -> None:
    """Emit SessionStarted (orchestrator only) + RunStarted + per-instrument + StepsDiscovered."""
    from litmus.data.events import RunStarted, SessionStarted, StepsDiscovered

    event_log = logger.event_log
    if event_log is None:
        return

    if not _is_multi_slot_worker():
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

    env_slot_id = os.environ.get("LITMUS_SLOT_ID")
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

    _emit_instrument_events(logger, event_log)

    collected = get_collected_items()
    if collected:
        event_log.emit(
            StepsDiscovered(
                session_id=logger._session_id,
                run_id=logger.test_run.id,
                items=[ci.model_dump() for ci in collected],
            )
        )


def _teardown_logger(logger: TestRunLogger, event_store: Any, results_dir: str) -> None:
    """Close subscribers, finalize the run, emit SessionEnded, run configured outputs."""
    from litmus.data.events import SessionEnded

    # ChannelStore closes before the event log so its subscribers see the
    # final flush before the event log shuts subscribers down.
    cs = get_channel_store()
    if cs is not None:
        cs.close()
        set_channel_store(None)

    # finalize() emits RunEnded; it does not close the event log itself.
    test_run = logger.finalize()

    if logger.event_log is not None:
        if not _is_multi_slot_worker():
            logger.event_log.emit(
                SessionEnded(
                    session_id=logger._session_id,
                    outcome=test_run.outcome.value,
                )
            )
        logger.event_log.close()

    if event_store is not None:
        event_store.close()
        set_event_store(None)

    run_configured_outputs(test_run, str(test_run.id), results_dir)


@pytest.fixture(scope="session", autouse=True)
def logger(request) -> Generator[TestRunLogger, None, None]:
    """Provide test run logger for the session.

    Autouse so every test (and the ``verify`` / ``context`` fixtures
    that route through ``set_current_logger``) sees an active logger.
    Snapshots config at run start; streams events to subscribers
    declared by ``litmus.yaml: outputs:`` plus the always-on parquet
    + channels defaults.

    The body delegates to focused helpers — :func:`_setup_event_log_and_subscribers`
    wires the event log, :func:`_emit_session_start_events` fires the
    Session/Run/StepsDiscovered triplet, :func:`_teardown_logger` closes
    everything in the right order at session end.
    """
    from litmus.data.results_dir import resolve_results_dir

    meta = _build_run_metadata(request)
    results_dir = meta["results_dir"]
    if not results_dir:
        results_dir = str(resolve_results_dir())
        meta["results_dir"] = results_dir

    env_session_id = os.environ.get("LITMUS_SESSION_ID")
    session_id = UUID(env_session_id) if env_session_id else uuid4()
    meta["session_id"] = session_id

    env_dut_serial = os.environ.get("LITMUS_DUT_SERIAL")
    if env_dut_serial:
        meta["dut_serial"] = env_dut_serial

    logger = TestRunLogger(**meta)

    event_store: Any = None
    if results_dir:
        event_store = _setup_event_log_and_subscribers(logger, Path(results_dir), session_id)
        _emit_session_start_events(logger)

    set_current_logger(logger)
    try:
        yield logger
    finally:
        # Capture not-started steps onto the run manifest before finalize.
        logger.test_run.collected_items = get_collected_items()
        _teardown_logger(logger, event_store, results_dir)
        set_current_logger(None)


def _emit_instrument_events(logger: TestRunLogger, event_log: Any) -> None:
    """Pytest adapter — read ContextVar records, delegate to runner-neutral emitter."""
    emit_instrument_events(logger, event_log, get_instrument_records())


@pytest.fixture(scope="session")
def run_context(logger) -> RunContext:
    """Provide run context for adding custom metadata.

    This is the run-level context that persists across all tests in the session.
    For step or vector-scoped context, use the `context` fixture instead.

    Usage:
        def test_example(run_context):
            run_context.set("operator_badge", "EMP-12345")
            run_context.set("fixture_serial", "FIX-001")
    """
    return logger.run_context


def _extract_code_identity(item: Any) -> dict[str, str | None]:
    """Extract code identity fields from a pytest.Item node."""
    identity: dict[str, str | None] = {}
    identity["node_id"] = getattr(item, "nodeid", None)
    cls_name, func_name = _node_cls_func(item)
    identity["function"] = func_name
    identity["class_name"] = cls_name
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

    identity["markers"] = _join_marker_names(getattr(item, "own_markers", []))

    return identity


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
    part_number = request.config.getoption("--dut-part-number")

    ctx = None

    if spec_path:
        ctx = SpecContext.from_file(spec_path, guardband_pct=guardband)
    else:
        ctx = _autodiscover_product(request.config, guardband, part_number)

    set_active_spec_context(ctx)
    return ctx


def _autodiscover_product(
    config: pytest.Config,
    guardband: float,
    part_number: str | None,
) -> SpecContext | None:
    """Pick a product YAML from ``products/`` in the project or cwd.

    Selection rules:
    1. If ``--dut-part-number`` is set and exactly one product's
       ``part_number:`` matches (case-insensitive), use it.
    2. If ``--dut-part-number`` is set but no file matches, raise
       ``pytest.UsageError`` — a typo in the selector is worse than a
       silent wrong-product pick.
    3. Otherwise, take the first sorted ``products/*.yaml`` file. If
       the directory holds multiple products and ``--dut-part-number``
       was not provided, raise ``pytest.UsageError`` — Rev-B flows
       need an explicit selector.
    """
    search_roots = [
        config.rootpath,
        Path(config.invocation_params.dir),
    ]

    product_files: list[Path] = []
    for root in search_roots:
        products_dir = root / "products"
        if not products_dir.exists():
            continue
        product_files = [
            p for p in sorted(products_dir.rglob("*.yaml")) if not p.name.startswith("_")
        ]
        if product_files:
            break

    if not product_files:
        return None

    if part_number:
        matches: list[Path] = []
        pn_lower = part_number.lower()
        for yaml_file in product_files:
            try:
                loaded = SpecContext.from_file(yaml_file, guardband_pct=guardband)
            except (ValueError, OSError):
                continue
            if (loaded.product.part_number or "").lower() == pn_lower:
                matches.append(yaml_file)
        if len(matches) == 1:
            return SpecContext.from_file(matches[0], guardband_pct=guardband)
        if not matches:
            raise pytest.UsageError(
                f"--dut-part-number={part_number!r} did not match any product in "
                f"products/. Available: " + ", ".join(sorted(p.stem for p in product_files))
            )
        raise pytest.UsageError(
            f"--dut-part-number={part_number!r} matched multiple products: "
            + ", ".join(sorted(str(m.relative_to(m.parents[1])) for m in matches))
            + ". Use --spec <path> to disambiguate."
        )

    if len(product_files) > 1:
        raise pytest.UsageError(
            f"products/ has {len(product_files)} YAML files "
            f"({', '.join(p.stem for p in product_files)}); "
            "pass --dut-part-number <pn> or --spec <path> to choose one."
        )

    return SpecContext.from_file(product_files[0], guardband_pct=guardband)


def _mocks_active(config: pytest.Config) -> bool:
    """Return whether mock instruments are requested.

    Single source of truth for every consumer (``pytest_sessionstart``,
    ``_build_run_metadata``, the ``mock_instruments`` session fixture,
    ``slot_runner``). Resolution order, highest priority first:

    1. CLI flag — ``--mock-instruments`` (True) or ``--no-mock-instruments``
       (False). Either explicit flag wins.
    2. Env var ``LITMUS_MOCK_INSTRUMENTS=1`` — set by the API runner so
       a server-launched subprocess inherits the operator's choice.
    3. ``litmus.yaml: mock_instruments:`` — project default.
    4. ``False`` if nothing else set.
    """
    cli = config.getoption("mock_instruments", default=None)
    if cli is not None:
        return bool(cli)
    env = os.environ.get("LITMUS_MOCK_INSTRUMENTS")
    if env is not None:
        return env == "1"
    return load_project_defaults().mock_instruments


@pytest.fixture(scope="session")
def mock_instruments(request) -> bool:
    """Return whether to use mock instruments instead of real hardware.

    Mocks do not block any test_phase; ``resolve_test_phase`` demotes
    the run's data stamp to ``"development"`` when mocks are active, so
    profile-driven limits/markers still apply but dashboards ignore
    the row. See ``resolve_test_phase`` for the demotion rule.
    """
    return _mocks_active(request.config)


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
            # Return a flat fixture config with just this slot's connections
            fc = FixtureConfig(
                id=fc.id,
                name=fc.name,
                description=fc.description,
                product_id=fc.product_id,
                product_family=fc.product_family,
                product_revision=fc.product_revision,
                connections=slot.connections,
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
    station_config, mock_instruments, instrument_records, logger
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
    session_id = logger._session_id if logger else None
    run_id = logger.test_run.id if logger else None
    event_log = logger.event_log if logger else None

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
    logger,
) -> Generator[RouteManager | None, None, None]:
    """Session-scoped route manager for switched signal routing.

    Built from fixture points that have routes. Holds locks for the
    session duration. Yields None if no routes are configured.
    """
    if not fixture_config or not instruments:
        yield None
        return

    session_id = logger._session_id if logger else None
    event_log = logger.event_log if logger else None
    station_id = ""
    if logger and hasattr(logger, "_station_id"):
        station_id = getattr(logger, "_station_id", "")

    rm = RouteManager(
        connections=fixture_config.connections,
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
            connection = fixture_manager.get_connection_for_net("VOUT_3V3")
            instrument = fixture_manager.get_instrument_for_connection(connection.name)

    Raises:
        pytest.UsageError: If no fixture config or instruments available.
    """
    _require_fixture_and_instruments(fixture_config, instruments, "fixture_manager")
    return FixtureManager(fixture_config, instruments, route_manager=_route_manager)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Per-test setup: clear aliases/config, capture code identity, reset mocks."""
    set_current_step_aliases({})
    set_current_step_config({})

    set_current_code_identity(_extract_code_identity(item))

    # Reset mock state for clean test isolation
    for inst in get_active_instruments().values():
        if hasattr(inst, "reset_mock_state"):
            inst.reset_mock_state()


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    """Open a step for every pytest-native test; reset dedup sets after.

    Opens a logger step around the test body so every measurement
    inside the method lands in a step scoped to that test. Applies
    equally to class-based sequences and loose module-level
    ``def test_*`` functions.

    Runs as a hookwrapper (rather than an autouse fixture) so it fires
    *after* all setup fixtures — including ones that install the logger
    via ``set_current_logger``. Autouse ordering between unrelated
    autouse fixtures is unspecified, which previously let
    ``log_measurement`` auto-create (and reset) the step before the
    wrapper could open it.

    On a passing test, also runs :func:`_audit_traceability` to report
    (or enforce, under ``--strict-traceability``) that every measurement
    carries the required traceability fields.
    """
    logger_inst = get_current_logger()
    func = getattr(item, "function", None)
    strict = bool(item.config.getoption("--strict-traceability"))

    if logger_inst is not None:
        cls = getattr(item, "cls", None)
        func_name = func.__name__ if func is not None else item.name
        logger_inst.start_step(
            func_name,
            function=func_name,
            module=getattr(func, "__module__", None) if func is not None else None,
            class_name=cls.__name__ if cls is not None else None,
            node_id=item.nodeid,
        )
        try:
            yield
            _audit_traceability(logger_inst, strict=strict)
        finally:
            logger_inst.end_step()
            logger_inst._step_seen_names.clear()
            logger_inst._step_seen_repeatable.clear()
        return

    yield


def _audit_traceability(logger_inst: Any, *, strict: bool) -> None:
    """Pytest adapter — read ``--strict-traceability`` + spec context, delegate."""
    audit_traceability(
        logger_inst,
        strict=strict,
        spec_active=get_active_spec_context() is not None,
    )


# ---------------------------------------------------------------------------
# Multi-slot orchestrator/worker mode
# ---------------------------------------------------------------------------


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session):
    """Take over the test loop when this process is the multi-slot orchestrator.

    In orchestrator mode, delegates to :func:`run_multi_slot_session`, which
    spawns per-slot pytest children and reports aggregate results. In worker
    or single-slot mode, returns ``None`` to fall through to pytest's default
    loop.
    """
    from litmus.execution.slot_runner import is_orchestrator_mode, run_multi_slot_session
    from litmus.store import load_station

    if not is_orchestrator_mode(session.config):
        return None

    station_path = _find_station_file(session.config)
    station_config_obj = load_station(station_path) if station_path else None
    return run_multi_slot_session(session, station_config=station_config_obj)


# ---------------------------------------------------------------------------
# Worker-mode fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sync(logger):
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
    del logger  # dependency-only: forces the session EventStore to exist
    from litmus.execution.slot_runner import is_worker_mode

    if not is_worker_mode():
        yield None
        return

    from litmus.execution.sync import get_sync

    event_store = get_event_store()
    sync_point = get_sync(event_store)
    yield sync_point


# ============================================================================
# Pytest-native sequences — THE Litmus executor.
#
# Every collected test gets Litmus conventions: sidecar-driven parametrize
# (vectors), auto-resolved limits from sidecar or product spec, mock
# installation, a logger step scoped to the test, and (for class-based
# tests) an implicit prereq chain between methods in source order.
# ============================================================================


# Late imports: keep module top-level imports terse and avoid circular risk
# with submodules that import this plugin.
from litmus.execution.vectors import Vector  # noqa: E402

# StashKey for the self-loop vectors matrix. Populated by
# :func:`pytest_generate_tests` whenever the test signature asks for the
# ``vectors`` fixture: the full pre-expanded matrix (native parametrize ×
# sidecar ``vectors:`` × profile overrides) is stashed on the test node
# for the fixture to iterate over, and pytest parametrize expansion is
# suppressed so the test executes as a single case.
_VECTORS_MATRIX_KEY: pytest.StashKey[dict[str, list[Vector]]] = pytest.StashKey()


def _cascade_parametrize_for_metafunc(
    metafunc: pytest.Metafunc,
) -> list[tuple[Any, list[Any], dict[str, Any]]]:
    """Pytest adapter — load sidecar / profile, delegate to runner-neutral parametrize calls."""
    module_file = getattr(metafunc.module, "__file__", None)
    sidecar = _load_sidecar(Path(module_file)) if module_file is not None else None
    cls = metafunc.cls
    cls_name = cls.__name__ if cls is not None else None
    func_name = metafunc.function.__name__
    merged = cascade_for(sidecar, get_active_profile(), cls_name, func_name)
    return parametrize_calls_for_entry(merged)


@pytest.hookimpl(tryfirst=True)
def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Expand parametrize entries from inline + sidecar + profile scopes.

    Sources:
      * Inline ``@pytest.mark.litmus_sweeps(...)`` decorators.
      * Sidecar ``config.sweeps`` and ``runner.markers`` parametrize entries.
      * Profile ``config.sweeps`` and ``runner.markers`` parametrize entries.

    This hook applies each as :meth:`metafunc.parametrize`, stacking
    with inline ``@pytest.mark.parametrize`` decorators (which pytest's
    built-in hook already processes). Different argnames per entry are
    required by pytest — overlap raises a collection-time
    :class:`pytest.UsageError`.

    Self-loop mode (``vectors`` fixture in signature): parametrize
    calls from inline + sidecar + profile are consumed instead of
    expanded, and the cross-product is stashed on the parent node for
    the :func:`vectors` fixture to iterate.
    """
    # Inline @pytest.mark.litmus_sweeps decorators. Reverse iter_markers
    # so the TOP decorator's list registers first → top = outer
    # (slowest-changing). Stacked decorators read top-to-bottom as
    # outer-to-inner, matching nested ``for`` loops and the within-list
    # ordering convention. Pydantic validates each entry (zip-coherence,
    # list-of-list values) when we coerce the raw dicts to SweepEntry.
    iter_top_first = reversed(list(metafunc.definition.iter_markers("litmus_sweeps")))
    inline_sweeps: list[SweepEntry] = []
    for mark in iter_top_first:
        try:
            normalized = normalize_inline_list_payload(
                "litmus_sweeps", mark.args, dict(mark.kwargs)
            )
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from exc
        for raw in normalized:
            inline_sweeps.append(
                raw if isinstance(raw, SweepEntry) else SweepEntry.model_validate(raw)
            )

    parametrize_calls: list[tuple[Any, list[Any], dict[str, Any]]] = []
    for inline_entry in inline_sweeps:
        argnames, argvalues = sweep_to_parametrize_args(inline_entry)
        parametrize_calls.append((argnames, argvalues, {}))
    parametrize_calls.extend(_cascade_parametrize_for_metafunc(metafunc))

    if "vectors" in metafunc.fixturenames:
        # Self-loop mode: consume inline @pytest.mark.parametrize + add
        # cascade rows, cross-product into a single stash matrix.
        inline_rows = _consume_parametrize_markers(metafunc)
        sidecar_rows: list[dict[str, Any]] = [{}]
        for argnames, argvalues, _extra in parametrize_calls:
            rows = parametrize_call_rows(argnames, argvalues)
            sidecar_rows = [{**base, **row} for base in sidecar_rows for row in rows]
        if sidecar_rows == [{}]:
            full_rows = inline_rows
        elif not inline_rows:
            full_rows = sidecar_rows
        else:
            full_rows = [{**i, **s} for i in inline_rows for s in sidecar_rows]
        full_matrix = [Vector(**row, _index=i) for i, row in enumerate(full_rows)]
        parent = metafunc.definition.parent
        if parent is not None:
            matrix_map = parent.stash.setdefault(_VECTORS_MATRIX_KEY, {})
            matrix_map[metafunc.definition.originalname] = full_matrix
        return

    for argnames, argvalues, extra in parametrize_calls:
        normalized = _normalize_parametrize_argvalues(argvalues)
        metafunc.parametrize(argnames, normalized, **extra)


def _normalize_parametrize_argvalues(argvalues: list[Any]) -> list[Any]:
    """Convert ``{value, id, marks}`` dict entries to ``pytest.param`` values."""
    out: list[Any] = []
    for entry in argvalues:
        if isinstance(entry, dict) and "value" in entry:
            value = entry["value"]
            pid = entry.get("id")
            marks_list = entry.get("marks") or []
            marks = [getattr(pytest.mark, name) for name in marks_list]
            out.append(pytest.param(value, id=pid, marks=marks))
        else:
            out.append(entry)
    return out


def _consume_parametrize_markers(metafunc: pytest.Metafunc) -> list[dict[str, Any]]:
    """Extract function-level parametrize markers and remove them from the node.

    Returns a cross-product list of row dicts across every consumed
    marker (so ``@parametrize("vin", [...])`` + ``@parametrize("load",
    [...])`` yields ``{vin, load}`` rows). Mutates
    ``metafunc.definition.own_markers`` in place to drop the consumed
    markers so pytest's built-in parametrize handler does not re-expand
    them into separate test cases.

    Class-level ``@pytest.mark.parametrize`` is rejected: we cannot
    remove per-method markers from a shared class, and class-wide
    parametrize combined with self-loop rarely has a sensible semantic.
    Move those to the method or to the sidecar.
    """
    cls = metafunc.cls
    if cls is not None:
        cls_parametrize = [
            m for m in getattr(cls, "pytestmark", []) if getattr(m, "name", None) == "parametrize"
        ]
        if cls_parametrize:
            raise pytest.UsageError(
                f"{metafunc.definition.nodeid}: ``vectors`` fixture is "
                "incompatible with class-level @pytest.mark.parametrize. "
                "Move the parametrize marker to the method or switch to "
                "a sidecar ``vectors:`` block."
            )

    own = metafunc.definition.own_markers
    consumed: list[pytest.Mark] = []
    remaining: list[pytest.Mark] = []
    for mark in own:
        if mark.name == "parametrize":
            consumed.append(mark)
        else:
            remaining.append(mark)

    if not consumed:
        return []

    own[:] = remaining

    rows: list[dict[str, Any]] = [{}]
    for mark in consumed:
        axis = _parametrize_mark_rows(mark)
        rows = [{**base, **row} for base in rows for row in axis]
    return rows


def _parametrize_mark_rows(mark: pytest.Mark) -> list[dict[str, Any]]:
    """Convert a single @pytest.mark.parametrize marker into row dicts."""
    if len(mark.args) < 2:
        return []
    argnames, argvalues = mark.args[0], mark.args[1]
    names = (
        [n.strip() for n in argnames.split(",")] if isinstance(argnames, str) else list(argnames)
    )
    rows: list[dict[str, Any]] = []
    for raw in argvalues:
        values = getattr(raw, "values", None)
        if values is None:
            values = raw
        if len(names) == 1:
            rows.append({names[0]: values})
        else:
            if not isinstance(values, (tuple, list)):
                raise pytest.UsageError(
                    f"parametrize {argnames!r} expected a tuple per row; got {values!r}"
                )
            rows.append(dict(zip(names, values, strict=True)))
    return rows


@pytest.fixture
def context() -> Context:
    """Context exposed to tests for ``context.get_param("...")`` / ``.changed()``."""
    return Context()


@pytest.fixture
def connections(
    _litmus_resolve_connections: None,
    context: Context,
) -> ConnectionIterator | None:
    """Active fixture connections for the current test.

    Returns the :class:`ConnectionIterator` resolved from
    ``litmus_specs`` / ``litmus_connections`` markers, or ``None`` when
    no markers are declared. Symmetric with ``pins``: tests that take
    fixture connections use this fixture instead of reaching through
    ``context.connections``.

    Iterator semantics are unchanged from ``ctx.connections``::

        def test_foo(connections, dmm):
            for conn in connections:        # drives _active_connection_var
                v = dmm.measure_voltage()

    Mapping access is also available::

        def test_named(connections, dmm):
            with connections["vout"]:        # (future: switch lifecycle)
                v = dmm.measure_voltage()
    """
    return getattr(context, "connections", None)


@pytest.fixture
def spec(request: pytest.FixtureRequest) -> Any:
    """Spec context for ``spec.check(name, value)`` inside tests.

    Short public alias for the session-scoped ``spec_context`` fixture —
    keeps test signatures terse (``def test_foo(self, spec, logger)``).
    Returns ``None`` when no product spec is configured; a test that
    attempts ``spec.check(...)`` in that mode raises ``AttributeError``
    rather than silently skipping the check. Tests that want to tolerate
    a missing spec must guard with ``if spec is not None`` explicitly.
    """
    return request.getfixturevalue("spec_context")


@pytest.fixture
def verify() -> VerifyFn:
    """Callable fixture: ``verify(name, value[, limit=])`` — log + assert.

    Thin pytest wrapper around the runner-neutral
    :func:`litmus.execution.verify.build_verify_callable` — logs a
    measurement, resolves a Limit from the chain, stamps the outcome,
    and raises :class:`LimitFailure` on FAIL.
    """
    return build_verify_callable()


@pytest.fixture
def limits() -> LimitsFn:
    """Read-only ``name → Limit`` mapping for the active test.

    Resolves from the same chain ``verify`` uses. ``limits[name]``
    raises ``KeyError`` when no limit is configured for ``name`` —
    honest for ad-hoc pythonic assertions::

        assert v in limits["vout"]
    """
    from litmus.execution.verify import _LimitsMapping

    return _LimitsMapping(dict(get_active_limits()))


class _VectorIterator:
    """Iterator for self-loop mode: walks the pre-expanded matrix in a single test case.

    Built by the :func:`vectors` fixture when a test takes ``vectors`` in
    its signature. Each ``__next__`` pushes the current row's params into
    ``_active_vector_params_var`` / ``_active_vector_index_var`` so that:

    * ``logger.measure`` stamps ``in_*`` columns + ``meas_vector_index``
      from active state.
    * A fresh :class:`TestVector` is appended to the current step per
      iteration, so parquet rows land on distinct records.
    * ``context.get_param``, ``.changed``, ``.last``, and ``.params``
      reflect the current row; ``_prev`` chains to the prior iteration.

    On cleanup the ContextVars restore; if the matrix is non-empty and
    the test body iterated zero times, the fixture fails the test.
    """

    def __init__(self, matrix: list[Vector], ctx: Context) -> None:
        self._matrix = matrix
        self._ctx = ctx
        self._i = 0
        self._consumed = 0
        self._prev_snapshot: Context | None = None

    def __iter__(self) -> _VectorIterator:
        return self

    def __len__(self) -> int:
        return len(self._matrix)

    def __next__(self) -> Vector:
        if self._i >= len(self._matrix):
            raise StopIteration

        vec = self._matrix[self._i]
        params = vec.params()

        set_active_vector_params(dict(params))
        set_active_vector_index(self._i)

        # Chain prev-context for ``context.changed()`` / ``.last()``.
        if self._prev_snapshot is not None:
            self._ctx._prev = self._prev_snapshot
        self._ctx._params.clear()
        self._ctx._params.update(params)

        snapshot = Context(channel_store=self._ctx._channel_store)
        snapshot._params = dict(params)
        self._prev_snapshot = snapshot

        # Fresh TestVector per iteration so vector_index / params stamp
        # distinctly. Only do this if a step already exists (logger may
        # still auto-create the first one lazily on measure).
        step = get_current_step()
        if step is not None:
            new_vector = TestVector(index=self._i, params=dict(params))
            step.vectors.append(new_vector)
            push_current_vector(new_vector)

        self._i += 1
        self._consumed += 1
        return vec

    @property
    def consumed(self) -> int:
        return self._consumed


@pytest.fixture
def vectors(request: pytest.FixtureRequest) -> Iterator[_VectorIterator]:
    """Pre-expanded vector matrix for self-loop test mode.

    Taking this fixture in a test signature switches collection to
    **self-loop mode**: every source of vectors (native
    ``@pytest.mark.parametrize``, sidecar ``vectors:``, profile
    overrides) is consolidated into one matrix at collection time, and
    the test runs as a single pytest case. The test body iterates the
    matrix itself::

        def test_sweep(vectors, psu, dmm, logger):
            for v in vectors:
                psu.set_voltage(v["vin"])
                logger.measure("vout", dmm.measure_dc_voltage())

    Each ``for`` iteration pushes the row's params and index into the
    active-vector state, so ``logger.measure`` / ``verify`` / ``spec``
    see the same row-scoped context they would in normal mode.

    Fails the test at teardown if the matrix is non-empty but the body
    iterated zero times — silent skips hide bugs.
    """
    parent = request.node.parent
    matrix_map = parent.stash.get(_VECTORS_MATRIX_KEY, {}) if parent is not None else {}
    matrix = matrix_map.get(request.node.originalname, [])
    ctx: Context = request.getfixturevalue("context")
    it = _VectorIterator(matrix=matrix, ctx=ctx)
    try:
        yield it
    finally:
        set_active_vector_params({})
        try:
            _active_vector_index_var.set(0)
        except LookupError:
            pass
        if len(matrix) > 0 and it.consumed == 0:
            pytest.fail(
                f"{request.node.nodeid}: ``vectors`` fixture was not iterated "
                f"({len(matrix)} rows available, 0 consumed). Use "
                "``for v in vectors: ...`` in the test body.",
                pytrace=False,
            )


@pytest.fixture
def prompt(request: pytest.FixtureRequest) -> Callable[..., Any]:
    """Operator prompt fixture.

    Resolves prompts declared via ``litmus_prompts`` markers (file-level,
    class-scoped, per-test, or inline ``@pytest.mark.litmus_prompts``).
    Each marker carries one or more entries keyed by name::

        @pytest.mark.litmus_prompts(
            operator_setup={"message": "Insert DUT", "prompt_type": "confirm"},
            pick_fixture={"message": "Pick fixture", "prompt_type": "choice",
                          "choices": ["bench_01", "bench_02"]},
        )
        def test_setup(prompt):
            prompt("operator_setup")          # confirm  -> True
            chosen = prompt("pick_fixture")   # choice   -> selected string

    ``prompt(key)`` looks the entry up by key. ``prompt()`` (no args)
    works when exactly one entry is in scope. Routing of the prompt
    itself goes through :func:`litmus.prompts.ask` — explicit handler
    (UI runner) → ``LITMUS_PROMPT_MODE=auto-confirm`` → tty fallback.
    """
    # Walk listchain root-to-leaf so more-specific markers win on key
    # conflict via ``update``. Within a node, ``own_markers`` preserves
    # insertion order. Cascade markers carry typed PromptConfig
    # instances; inline decorators carry raw dicts which Pydantic
    # validates here.
    merged: dict[str, PromptConfig] = {}
    for node in request.node.listchain():
        for marker in node.own_markers:
            if marker.name != "litmus_prompts":
                continue
            for key, entry in marker.kwargs.items():
                merged[key] = (
                    entry if isinstance(entry, PromptConfig) else PromptConfig.model_validate(entry)
                )

    def _ask(key: str | None = None) -> Any:
        if key is None:
            if not merged:
                raise pytest.UsageError(
                    f"prompt() called with no key but no litmus_prompts "
                    f"markers are in scope for {request.node.nodeid}"
                )
            if len(merged) > 1:
                raise pytest.UsageError(
                    f"prompt() called with no key but {len(merged)} prompts "
                    f"are in scope for {request.node.nodeid}: "
                    f"{sorted(merged)}. Pass an explicit key."
                )
            entry = next(iter(merged.values()))
        else:
            if key not in merged:
                known = sorted(merged) or "none"
                raise pytest.UsageError(
                    f"prompt({key!r}): no such key in litmus_prompts markers "
                    f"for {request.node.nodeid}; known keys: {known}"
                )
            entry = merged[key]
        return ask_prompt(entry)

    return _ask
