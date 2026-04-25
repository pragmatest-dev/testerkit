"""pytest plugin for Litmus test framework."""

from __future__ import annotations

import os
import sys
import warnings
from collections.abc import Callable, Generator, Iterator
from itertools import cycle
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from litmus.config.test_config import (
    FixtureConfig,
    FixturePoint,
    MarkerSpec,
)
from litmus.data.models import CollectedItem, TestRun, TestVector
from litmus.execution._state import (
    _active_point_var,
    _active_vector_index_var,
    current_step_var,
    current_vector_var,
    get_active_facets,
    get_active_instruments,
    get_active_limits,
    get_active_point,
    get_active_profile,
    get_active_spec_context,
    get_active_vector_index,
    get_active_vector_params,
    get_channel_store,
    get_collected_items,
    get_current_code_identity,
    get_current_step_aliases,
    get_current_step_config,
    get_event_store,
    get_instrument_records,
    get_sequence_required_fixture,
    get_sequence_test_phase,
    get_test_node_aliases,
    get_test_node_configs,
    set_active_facets,
    set_active_instruments,
    set_active_limits,
    set_active_point,
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
    set_sequence_required_fixture,
    set_sequence_test_phase,
    set_test_node_aliases,
    set_test_node_configs,
)
from litmus.execution.accessors import InstrumentAccessor
from litmus.execution.decorators import get_current_logger, set_current_logger
from litmus.execution.harness import Context
from litmus.execution.logger import RunContext, TestRunLogger
from litmus.execution.profiles import (
    apply_profile_addopts_env,
    collect_profile_facet_keys,
    facet_key_to_cli_flag,
    install_active_profile,
    load_project_defaults,
    resolve_test_phase,
)
from litmus.execution.verify import (  # noqa: F401 — verify re-exported as pytest fixture
    LimitsFn,
    verify,
)
from litmus.fixtures.manager import FixtureManager, PinAccessor
from litmus.instruments.pool import InstrumentPool
from litmus.instruments.route_manager import RouteManager
from litmus.models.instrument import InstrumentRecord
from litmus.models.project import OutputConfig
from litmus.models.station import StationConfig
from litmus.products.context import SpecContext

# State helpers re-exported for back-compat with consumers that import
# from litmus.execution.plugin (logger, harness, accessors, manager, tests).
__all__ = [
    "current_step_var",
    "current_vector_var",
    "get_active_facets",
    "get_active_instruments",
    "get_active_limits",
    "get_active_point",
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
    "get_sequence_required_fixture",
    "get_sequence_test_phase",
    "get_test_node_aliases",
    "get_test_node_configs",
    "set_active_facets",
    "set_active_instruments",
    "set_active_limits",
    "set_active_point",
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
    "set_sequence_required_fixture",
    "set_sequence_test_phase",
    "set_test_node_aliases",
    "set_test_node_configs",
]


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
    for marker in (
        "litmus_limits(**kwargs): Inject limits by measurement name (merges with sidecar limits:)",
        "litmus_spec(characteristic=<id>): Bind the test to a product "
        "characteristic; provides spec-relative limit context and "
        "auto-derives fixture points from the characteristic's pins.",
        "litmus_connections(fixturepoints=[...] | instrument_channels={...}): "
        "Bind the test to explicit fixture points or instrument-channel ranges.",
        "litmus_prompt(**kwargs): Operator prompt hook; kwargs select "
        "timing (before_all | before_each) and message template.",
        "litmus_mock(**kwargs): Install a mock for the duration of a "
        "test; kwargs follow mocker.patch.object(target, ...).",
    ):
        config.addinivalue_line("markers", marker)
    install_active_profile(config)

    # Auto-register instrument role fixtures from station config
    station_path = _find_station_file(config)
    if station_path is None:
        return

    try:
        from litmus.store import load_station

        station_model = load_station(station_path)
    except Exception as exc:
        # ValidationError, bad YAML, missing deps — don't crash pytest,
        # but surface the problem so a typo'd station path isn't silently
        # ignored until the first instrument lookup fails.
        warnings.warn(
            f"Failed to load station config '{station_path}': {exc}. "
            "Instrument fixtures will not be auto-registered.",
            stacklevel=1,
        )
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


def _join_marker_names(markers: Any, sort: bool = False) -> str | None:
    """Return a comma-joined marker-name string, or ``None`` when empty.

    Accepts anything iterable that yields objects with a ``.name``
    attribute — ``item.iter_markers()`` or ``item.own_markers``.
    ``sort=True`` produces deterministic output for the collection
    manifest; leaving it unsorted preserves source order for code
    identity (which is what the audit cares about).
    """
    if not markers:
        return None
    names = [m.name for m in markers]
    if sort:
        names.sort()
    return ",".join(names) or None


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
    _apply_sidecar_to_items(items)
    _apply_profile_to_items(config, items)

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


def _warn_unmatched_profile_keys(items: list[pytest.Item]) -> None:
    """Warn when a profile's ``tests.<name>`` / ``classes.<Cls>`` key matches no collected test.

    Keys in ``profile.tests`` may be either a bare method name
    (``test_foo``) or a qualified form (``TestCls.test_foo``); keys in
    ``profile.classes`` are class names. A silent no-op on a typo is
    worse than a skipped mock — it's a production screen that stopped
    running. Warn once per orphan key.
    """
    profile = get_active_profile()
    if profile is None:
        return

    originalnames: set[str] = set()
    qualified: set[str] = set()
    class_names: set[str] = set()
    for item in items:
        if not isinstance(item, pytest.Function):
            continue
        originalnames.add(item.originalname)
        cls = getattr(item, "cls", None)
        if cls is not None:
            class_names.add(cls.__name__)
            qualified.add(f"{cls.__name__}.{item.originalname}")

    unmatched: list[str] = []
    for key in profile.tests:
        if key in originalnames or key in qualified:
            continue
        unmatched.append(f"  profile.tests[{key!r}]")
    for cls_name in profile.classes:
        if cls_name in class_names:
            continue
        unmatched.append(f"  profile.classes[{cls_name!r}]")
    if not unmatched:
        return
    warnings.warn(
        "Active profile has keys that match no collected test:\n"
        + "\n".join(unmatched)
        + "\nUse an exact test name, a qualified 'Class.method', or remove the entry.",
        UserWarning,
        stacklevel=1,
    )


def _apply_profile_to_items(config, items: list[pytest.Item]) -> None:
    """Inject profile markers onto items from the three profile scopes.

    Filter composition (``keyword``, ``markexpr``) happens in
    ``install_active_profile`` during ``pytest_configure``; this step
    only handles per-item marker injection, which must happen at
    collection time. Accumulates file-level + class + per-test markers
    via :func:`_profile_markers_for_item`.
    """
    if get_active_profile() is None:
        return
    for item in items:
        for spec in _profile_markers_for_item(item):
            marker = getattr(pytest.mark, spec.name)
            item.add_marker(marker(*spec.args, **spec.kwargs))


def _apply_sidecar_to_items(items: list[pytest.Item]) -> None:
    """Attach sidecar file + class + per-test markers to each item.

    Skips ``parametrize`` markers — those are consumed by
    :func:`pytest_generate_tests` and must not be re-applied here, or
    pytest would see two parametrize markers per axis.
    """
    for item in items:
        if not isinstance(item, pytest.Function):
            continue
        module = getattr(item, "module", None)
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            continue
        sidecar = _load_sidecar(Path(module_file))
        if sidecar is None:
            continue
        cls_name, func_name = _node_cls_func(item)
        for spec in _sidecar_markers_for(sidecar, cls_name, func_name):
            if spec.name == "parametrize":
                continue
            marker = getattr(pytest.mark, spec.name)
            item.add_marker(marker(*spec.args, **spec.kwargs))


def pytest_sessionfinish(session, exitstatus):
    """Clean up all session-scoped ContextVars and module-level state."""
    set_active_instruments({})
    set_instrument_records({})
    set_test_node_aliases({})
    set_test_node_configs({})
    set_sequence_test_phase(None)
    set_sequence_required_fixture(None)
    set_collected_items([])
    set_channel_store(None)
    set_event_store(None)
    set_active_profile(None)


def pytest_load_initial_conftests(early_config, parser, args):
    """Apply ``profile.pytest.addopts`` via ``PYTEST_ADDOPTS`` before collection."""
    apply_profile_addopts_env(args)


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
        default=project.mock_instruments,
        help="Use mock instruments instead of real hardware",
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
    # Auto-synthesize one --<facet> flag per declared profile facet key.
    # Declaring `product: power_board` in any profile turns --product into
    # a selector for this project — no generic --facet escape hatch.
    # ``test_phase`` already has its own --test-phase flag above, so the
    # facet reuses it rather than re-registering.
    for key in collect_profile_facet_keys(project):
        if key == "test_phase":
            continue
        group.addoption(
            facet_key_to_cli_flag(key),
            default=None,
            help=f"Select profile by facet {key!r} (from litmus.yaml profiles).",
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
    except Exception:  # noqa: BLE001 — missing/invalid config means no transport
        # No litmus.yaml, YAML parse error, or schema mismatch — transport
        # is an opt-in feature so missing config means "skip transport".
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
    test_phase = resolve_test_phase(requested_phase, mocks_active=_mocks_active(request.config))

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
    profile_name = request.config.getoption("--litmus-profile", default=None)
    profile_facets = dict(get_active_facets())

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
        "profile": profile_name,
        "profile_facets": profile_facets,
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
def logger(request) -> Generator[TestRunLogger, None, None]:
    """Provide test run logger for the session.

    This fixture is autouse=True so it's always active, enabling
    pytest-native tests (and the ``verify`` / ``context`` fixtures) to
    log measurements.

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


@pytest.fixture(autouse=True)
def _reseat_current_logger(logger: TestRunLogger) -> None:
    """Re-install the session logger into the ContextVar for every test.

    Pytester-based tests run an inner pytest session whose own teardown
    clears ``set_current_logger(None)`` — and because ContextVars are
    process-wide, that leaks into the outer session. Re-seating on every
    test keeps ``get_current_logger()`` correct regardless.
    """
    set_current_logger(logger)


def _emit_instrument_events(logger: TestRunLogger, event_log: Any) -> None:
    """Emit InstrumentConnected events from instrument records."""
    from litmus.data.events import InstrumentConnected
    from litmus.execution.logger import instrument_cal_fields, instrument_info_fields

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
            **instrument_info_fields(rec),
            **instrument_cal_fields(rec),
            mocked=rec.mocked,
        )
        event_log.emit(event)


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

    Shared by ``pytest_sessionstart``, ``_build_run_metadata``, and the
    ``mock_instruments`` session fixture — single source of truth for
    the combined ``--mock-instruments`` flag + ``LITMUS_MOCK_INSTRUMENTS``
    env-var check.
    """
    return bool(
        config.getoption("--mock-instruments") or os.environ.get("LITMUS_MOCK_INSTRUMENTS") == "1"
    )


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
    request, station_config, mock_instruments, instrument_records, logger
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


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Reset mock state and set per-step aliases/config."""
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
    """Check the current step's measurements for traceability completeness.

    Walks measurements recorded during the just-completed test and tags
    each one with ``trace_completeness`` describing which required
    fields were missing. In ``strict`` mode, raises ``AssertionError``
    if any measurement is incomplete so the test fails.

    Required fields:
        * ``step_path`` — populated by the plugin's step wrapper.
        * ``spec_ref`` OR ``dut_pin`` — only required when a product spec
          is active for the session. Tests that don't declare a
          ``--spec`` exercise the graceful-degradation path and are not
          penalized for lacking pin/spec references.
    """
    steps = getattr(getattr(logger_inst, "test_run", None), "steps", None)
    if not steps:
        return
    step = steps[-1]
    spec_active = get_active_spec_context() is not None

    incomplete: list[str] = []
    for vec in step.vectors:
        for m in vec.measurements:
            missing: list[str] = []
            if not m.step_path:
                missing.append("step_path")
            if spec_active and not m.spec_ref and not m.dut_pin:
                missing.append("spec_ref/dut_pin")
            if missing:
                incomplete.append(f"{m.name}: missing {', '.join(missing)}")

    if incomplete and strict:
        raise AssertionError(
            "--strict-traceability: measurements missing required fields:\n  "
            + "\n  ".join(incomplete)
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
    from litmus.execution.slot_runner import is_worker_mode

    if not is_worker_mode():
        yield None
        return

    from litmus.execution.sync import get_sync

    # Reuse the session EventStore from logger — never create a second one
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


def _profile_markers_for_item(item: pytest.Item) -> list[MarkerSpec]:
    """Return accumulated profile markers for ``item``: file-level + class + per-test.

    Walks three scopes in order so downstream marker application can apply
    them least- to most-specific (same cascade as sidecars):

    1. ``profile.markers`` — every test sees these
    2. ``profile.classes[<ClassName>].markers`` — if the item is a method
    3. ``profile.tests[<bare or qualified name>].markers`` — per-test

    Per-test keys accept either bare method name (``test_foo``) or the
    disambiguated form (``TestFoo.test_foo``); the qualified form wins
    when both are present.
    """
    profile = get_active_profile()
    if profile is None:
        return []
    if not isinstance(item, pytest.Function):
        return list(profile.markers)

    out: list[MarkerSpec] = list(profile.markers)
    cls = getattr(item, "cls", None)
    if cls is not None:
        class_block = profile.classes.get(cls.__name__)
        if class_block is not None:
            out.extend(class_block.markers)

    if cls is not None:
        qualified = f"{cls.__name__}.{item.originalname}"
        qualified_block = profile.tests.get(qualified)
        if qualified_block is not None:
            out.extend(qualified_block.markers)
            return out

    bare_block = profile.tests.get(item.originalname)
    if bare_block is not None:
        out.extend(bare_block.markers)
    return out


# StashKey for the self-loop vectors matrix. Populated by
# :func:`pytest_generate_tests` whenever the test signature asks for the
# ``vectors`` fixture: the full pre-expanded matrix (native parametrize ×
# sidecar ``vectors:`` × profile overrides) is stashed on the test node
# for the fixture to iterate over, and pytest parametrize expansion is
# suppressed so the test executes as a single case.
_VECTORS_MATRIX_KEY: pytest.StashKey[dict[str, list[Vector]]] = pytest.StashKey()


def _sidecar_parametrize_markers_for_metafunc(
    metafunc: pytest.Metafunc,
) -> list[MarkerSpec]:
    """Collect sidecar + profile parametrize markers for a metafunc.

    Returns parametrize-named markers from, in merge order:

        sidecar.markers (file-level)
        sidecar.classes[<Cls>].markers
        sidecar.tests[<qualified|bare>].markers
        profile chain .markers / .classes / .tests (qualified|bare)

    Inline ``@pytest.mark.parametrize`` decorators on the function are
    intentionally excluded — pytest's built-in hook already applies
    those. This list is applied on top via :meth:`metafunc.parametrize`
    calls, stacking with different argnames per entry.
    """
    module_file = getattr(metafunc.module, "__file__", None)
    sidecar = _load_sidecar(Path(module_file)) if module_file is not None else None
    cls = metafunc.cls
    cls_name = cls.__name__ if cls is not None else None
    func_name = metafunc.function.__name__

    merged: list[MarkerSpec] = list(_sidecar_markers_for(sidecar, cls_name, func_name))

    profile = get_active_profile()
    if profile is not None:
        merged.extend(profile.markers)
        if cls_name is not None:
            class_block = profile.classes.get(cls_name)
            if class_block is not None:
                merged.extend(class_block.markers)
        qualified = f"{cls_name}.{func_name}" if cls_name is not None else None
        tests_block = profile.tests.get(qualified) if qualified is not None else None
        if tests_block is None:
            tests_block = profile.tests.get(func_name)
        if tests_block is not None:
            merged.extend(tests_block.markers)

    return [m for m in merged if m.name == "parametrize"]


@pytest.hookimpl(tryfirst=True)
def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Expand parametrize markers from sidecar + profile scopes.

    The sidecar and profile ``markers:`` lists may contain ``parametrize``
    entries. This hook applies each one via :meth:`metafunc.parametrize`,
    stacking with inline ``@pytest.mark.parametrize`` decorators (which
    pytest's built-in hook already processes). Different argnames per
    entry are required by pytest — overlap raises a collection-time
    :class:`pytest.UsageError`.

    Self-loop mode (``vectors`` fixture in signature): parametrize
    markers from inline + sidecar + profile are consumed instead of
    expanded, and the cross-product is stashed on the parent node for
    the :func:`vectors` fixture to iterate.
    """
    sidecar_parametrize = _sidecar_parametrize_markers_for_metafunc(metafunc)

    if "vectors" in metafunc.fixturenames:
        # Self-loop mode: consume inline parametrize markers + add
        # sidecar/profile rows, cross-product into a single stash matrix.
        inline_rows = _consume_parametrize_markers(metafunc)
        sidecar_rows: list[dict[str, Any]] = [{}]
        for marker in sidecar_parametrize:
            axis = _marker_spec_parametrize_rows(marker)
            sidecar_rows = [{**base, **row} for base in sidecar_rows for row in axis]
        # When sidecar rows is empty dict, inline alone; otherwise cross-product.
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

    # Normal mode: apply each sidecar/profile parametrize marker via
    # metafunc.parametrize. pytest's built-in hook will also expand
    # inline @pytest.mark.parametrize decorators separately.
    for marker in sidecar_parametrize:
        argnames, argvalues, kwargs = _marker_spec_to_parametrize_args(marker)
        metafunc.parametrize(argnames, argvalues, **kwargs)


def _marker_spec_to_parametrize_args(
    marker: MarkerSpec,
) -> tuple[Any, list[Any], dict[str, Any]]:
    """Convert a parametrize MarkerSpec into metafunc.parametrize arguments."""
    if marker.args and len(marker.args) >= 2:
        argnames = marker.args[0]
        argvalues = marker.args[1]
        extra_kwargs = dict(marker.kwargs)
    elif "argnames" in marker.kwargs and "argvalues" in marker.kwargs:
        extra_kwargs = dict(marker.kwargs)
        argnames = extra_kwargs.pop("argnames")
        argvalues = extra_kwargs.pop("argvalues")
    else:
        raise pytest.UsageError(
            f"parametrize marker requires argnames and argvalues; got "
            f"args={marker.args!r}, kwargs={marker.kwargs!r}"
        )
    normalized: list[Any] = []
    for entry in argvalues:
        if isinstance(entry, dict) and "value" in entry:
            value = entry["value"]
            pid = entry.get("id")
            marks_list = entry.get("marks") or []
            marks = [getattr(pytest.mark, name) for name in marks_list]
            normalized.append(pytest.param(value, id=pid, marks=marks))
        else:
            normalized.append(entry)
    return argnames, normalized, extra_kwargs


def _marker_spec_parametrize_rows(marker: MarkerSpec) -> list[dict[str, Any]]:
    """Convert a parametrize MarkerSpec into a list of row dicts.

    Used by self-loop mode to cross-product parametrize markers into
    a single vector matrix. Skips per-case ``id`` / ``marks``.
    """
    argnames, argvalues, _ = _marker_spec_to_parametrize_args(marker)
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


from litmus.execution.sidecar import (  # noqa: E402, I001  (late import; keep grouped)
    load_sidecar as _load_sidecar,
    parse_limits_block as _parse_limits_block,
    resolve_limits as _resolve_limits,
    sidecar_markers_for as _sidecar_markers_for,
)


@pytest.fixture
def context() -> Context:
    """Context exposed to tests for ``context.get_param("...")`` / ``.changed()``."""
    return Context()


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
        step = current_step_var.get()
        if step is not None:
            new_vector = TestVector(index=self._i, params=dict(params))
            step.vectors.append(new_vector)
            current_vector_var.set(new_vector)

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


# StashKey for the previous-Context map, stored on each test's parent node
# (class node for class-based tests, module node for loose functions).
# Pytest auto-discards the stash when it finishes with that parent, so we
# don't manage teardown ourselves. Keyed by ``originalname`` — stable
# across parametrize cases of the same method so ``context.changed(...)``
# compares across cases rather than within a single case's retries.
_PREV_STASH_KEY: pytest.StashKey[dict[str, Context]] = pytest.StashKey()


@pytest.fixture(autouse=True)
def _litmus_push_params(
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Merge parametrize params into ``context`` for this test.

    Source-agnostic: reads ``request.node.callspec.params`` so inline
    ``@pytest.mark.parametrize``, sidecar/profile parametrize markers,
    and ``@pytest.fixture(params=...)`` all populate ``context`` the
    same way. Applies to every collected test.

    Also chains ``Context._prev`` to the previous parametrize case of
    the same ``(parent_node, method)`` so ``context.changed("vin")``
    picks up transitions across adjacent cases. The lookup is keyed by
    ``originalname`` on the parent node's stash, so two classes (or
    modules) that share a method name do not cross-contaminate.
    """
    ctx: Context = request.getfixturevalue("context")

    callspec = getattr(request.node, "callspec", None)
    if callspec is not None:
        extras = {k: v for k, v in callspec.params.items() if not k.startswith("_")}
        if extras:
            ctx.set_params(extras)

    # Publish to the session-scoped ContextVar so logger.measure can
    # stamp TestVector.params without going through the harness.
    set_active_vector_params(dict(ctx.params))

    parent = request.node.parent
    if parent is not None:
        prev_map = parent.stash.setdefault(_PREV_STASH_KEY, {})
        key = request.node.originalname
        prev = prev_map.get(key)
        if prev is not None:
            ctx._prev = prev
        try:
            yield
        finally:
            # Store the most recent run's context so the next parametrize
            # case — or a @flaky retry of this same case — reads the
            # latest state via ``context.changed(...)``. On retry the
            # second attempt overwrites the first, which matches the
            # "compare against whatever just ran" semantics callers expect.
            prev_map[key] = ctx
            set_active_vector_params({})
    else:
        try:
            yield
        finally:
            set_active_vector_params({})


def _node_cls_func(node: pytest.Item) -> tuple[str | None, str | None]:
    """Extract (class_name, original_func_name) for a pytest node."""
    cls = getattr(node, "cls", None)
    cls_name = cls.__name__ if cls is not None else None
    func_name = getattr(node, "originalname", None) or node.name.split("[")[0]
    return cls_name, func_name


@pytest.fixture(autouse=True)
def _litmus_push_limits(
    request: pytest.FixtureRequest,
    _litmus_push_params: None,
) -> Iterator[None]:
    """Merge all ``litmus_limits`` markers and push into ``_active_limits_var``.

    Markers are attached to the item during collection from four sources,
    in merge order (later wins):

        sidecar file-level ``markers:``
        sidecar class-scoped ``classes.<Cls>.markers:``
        sidecar per-test ``tests.<name>.markers:``
        inline ``@pytest.mark.litmus_limits`` decorators
        profile chain markers (parent → child)

    Each marker kwargs is ``{measurement_name: band_spec}`` where
    ``band_spec`` is a raw ``Limit`` dict, ``{"ref": ...}`` pointer, or
    :class:`MeasurementLimitConfig` policy shape. Policy entries resolve
    at push time against ``product.characteristics[char].get_spec_at(
    active_vector_params)``. Depends on :func:`_litmus_push_params` so
    active vector params are populated before resolution.
    """
    spec_marker = next(iter(request.node.iter_markers("litmus_spec")), None)
    test_char = spec_marker.kwargs.get("characteristic") if spec_marker is not None else None

    merged_raw: dict[str, Any] = {}
    # Walk listchain root-to-leaf so later (more-specific) markers win
    # via ``update``. Within a node, ``own_markers`` preserves insertion
    # order — file-level sidecar markers are added before per-test ones,
    # so per-test correctly overrides.
    for node in request.node.listchain():
        for marker in node.own_markers:
            if marker.name == "litmus_limits":
                merged_raw.update(marker.kwargs)

    raw = _parse_limits_block(merged_raw, test_char=test_char)
    resolved = _resolve_limits(raw)
    set_active_limits(resolved)
    try:
        yield
    finally:
        set_active_limits({})


class _PointIterator:
    """Iterator that pushes each :class:`FixturePoint` into ``_active_point_var``.

    Built by :func:`_litmus_bind_points` from a test's sidecar binding
    (``characteristic`` / ``fixturepoints`` / ``instrument_channels``).
    ``__next__`` resets the prior token, sets the new point, and returns it.
    The test body sees opaque handles; the framework reads the ContextVar
    for driver routing and measurement traceability.
    """

    def __init__(self, points: list[FixturePoint]) -> None:
        self._points = points
        self._idx = 0
        self._token: Any = None
        self.started = False

    def __iter__(self) -> _PointIterator:
        return self

    def __next__(self) -> FixturePoint:
        if self._token is not None:
            _active_point_var.reset(self._token)
            self._token = None
        if self._idx >= len(self._points):
            raise StopIteration
        point = self._points[self._idx]
        self._idx += 1
        self.started = True
        self._token = _active_point_var.set(point)
        return point

    def __len__(self) -> int:
        return len(self._points)

    def cleanup(self) -> None:
        """Pop any lingering active-point token on teardown or mid-iter exit."""
        if self._token is not None:
            _active_point_var.reset(self._token)
            self._token = None


def _resolve_spec_to_points(
    characteristic: str,
    spec_ctx: Any,
    fixture_cfg: FixtureConfig | None,
) -> list[FixturePoint]:
    """Expand a ``litmus_spec(characteristic=...)`` payload into fixture points.

    Walks the characteristic's ``resolved_pins`` and looks up matching
    points in the fixture config (by ``dut_pin`` or ``net``). Returns
    ``[]`` when no fixture is loaded.
    """
    if spec_ctx is None:
        raise pytest.UsageError(
            f"litmus_spec(characteristic={characteristic!r}) "
            "requires a product spec (load via --spec or products/ auto-discovery)."
        )
    char = spec_ctx.product.characteristics.get(characteristic)
    if char is None:
        raise pytest.UsageError(
            f"Characteristic {characteristic!r} not found in product {spec_ctx.product.id!r}."
        )
    if fixture_cfg is None:
        return []
    points_map = fixture_cfg.points
    points: list[FixturePoint] = []
    for pin_id in char.resolved_pins:
        pin = spec_ctx.product.pins.get(pin_id)
        net = pin.net if pin else None
        for pt in points_map.values():
            if pt.dut_pin == pin_id or (net is not None and pt.net == net):
                points.append(pt)
                break
    return points


def _resolve_connections_to_points(
    kwargs: dict[str, Any],
    fixture_cfg: FixtureConfig | None,
) -> list[FixturePoint]:
    """Expand a ``litmus_connections`` payload into fixture points.

    Exactly one of ``fixturepoints`` / ``instrument_channels`` must be
    set. Returns ``[]`` when no fixture is loaded.
    """
    fixturepoints = kwargs.get("fixturepoints")
    instrument_channels = kwargs.get("instrument_channels")

    if fixturepoints is not None and instrument_channels is not None:
        raise pytest.UsageError(
            "litmus_connections must set exactly one of fixturepoints or instrument_channels."
        )
    if fixturepoints is None and instrument_channels is None:
        raise pytest.UsageError(
            "litmus_connections requires either fixturepoints=[...] or instrument_channels={...}."
        )

    if fixture_cfg is None:
        return []
    points_map = fixture_cfg.points

    if fixturepoints:
        resolved: list[FixturePoint] = []
        for name in fixturepoints:
            pt = points_map.get(name)
            if pt is None:
                raise pytest.UsageError(f"Fixture point {name!r} not found in fixture config.")
            resolved.append(pt)
        return resolved

    out: list[FixturePoint] = []
    assert instrument_channels is not None
    for inst_name, channels in instrument_channels.items():
        if channels == "all":
            out.extend(pt for pt in points_map.values() if pt.instrument == inst_name)
        else:
            wanted = {str(c) for c in channels}
            out.extend(
                pt
                for pt in points_map.values()
                if pt.instrument == inst_name and pt.instrument_channel in wanted
            )
    return out


@pytest.fixture(autouse=True)
def _litmus_bind_points(
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Build :class:`_PointIterator` on ``ctx.points`` from binding markers.

    Reads ``litmus_spec`` (characteristic-driven points) and
    ``litmus_connections`` (explicit fixturepoints / instrument_channels).
    Both markers cannot be set on the same test. If the test body
    declares a binding but never iterates ``ctx.points``, the test
    fails — silent skips are worse than errors.
    """
    spec_marker = next(iter(request.node.iter_markers("litmus_spec")), None)
    conn_marker = next(iter(request.node.iter_markers("litmus_connections")), None)

    if spec_marker is not None and conn_marker is not None:
        raise pytest.UsageError(
            "Cannot combine litmus_spec and litmus_connections on the same test. "
            "Use one or the other."
        )
    if spec_marker is None and conn_marker is None:
        yield
        return

    spec_ctx = get_active_spec_context()
    fixture_cfg = _safe_get_session_fixture(request, "fixture_config")
    if spec_marker is not None:
        characteristic = spec_marker.kwargs.get("characteristic")
        if not characteristic:
            raise pytest.UsageError("litmus_spec requires characteristic=<id>.")
        points = _resolve_spec_to_points(characteristic, spec_ctx, fixture_cfg)
    else:
        assert conn_marker is not None
        points = _resolve_connections_to_points(dict(conn_marker.kwargs), fixture_cfg)

    ctx: Context = request.getfixturevalue("context")
    iterator = _PointIterator(points)
    ctx.points = iterator

    try:
        yield
    except BaseException:
        iterator.cleanup()
        raise
    iterator.cleanup()
    if points and not iterator.started:
        raise AssertionError(
            f"Test {request.node.nodeid} declared a points binding but did "
            "not iterate ctx.points. Declared bindings must be consumed by "
            "the test body."
        )


@pytest.fixture(autouse=True)
def _litmus_apply_mocks(
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Install mocks declared via ``litmus_mock`` markers.

    Each marker kwargs is ``{target: <fixture>.<attr>, return_value: ...}``.
    The handler routes through ``mocker.patch.object`` so pytest-mock
    owns teardown. The attribute is replaced with a
    ``Mock(return_value=...)`` for the duration of the test. Multiple
    markers stack; ``--no-test-mocks`` bypasses all patching.
    """
    if request.config.getoption("--no-test-mocks", default=False):
        yield
        return

    # Walk listchain root-to-leaf so more-specific markers with the same
    # target overwrite earlier ones in ``by_target`` below. Within a node,
    # ``own_markers`` preserves insertion order.
    mock_markers: list[pytest.Mark] = []
    for node in request.node.listchain():
        for marker in node.own_markers:
            if marker.name == "litmus_mock":
                mock_markers.append(marker)
    if not mock_markers:
        yield
        return

    mocker = request.getfixturevalue("mocker")

    # Deduplicate by target dotted path — later marker wins.
    by_target: dict[str, dict[str, Any]] = {}
    for marker in mock_markers:
        kwargs = dict(marker.kwargs)
        target = kwargs.get("target")
        if not target or not isinstance(target, str):
            raise ValueError(
                f"litmus_mock marker must supply `target:` as a "
                f"'<fixture>.<attr>' string; got {kwargs!r}"
            )
        by_target[target] = kwargs

    for target, kwargs in by_target.items():
        fixture_name, _, attr = target.partition(".")
        if not attr:
            raise ValueError(f"litmus_mock target {target!r} must be '<fixture>.<attr>' form")
        try:
            fixture_value = request.getfixturevalue(fixture_name)
        except pytest.FixtureLookupError:
            warnings.warn(
                f"litmus_mock target {target!r}: fixture {fixture_name!r} not "
                "found on this test — mock skipped. Check the marker `target:` "
                "matches a fixture in the test's signature.",
                stacklevel=1,
            )
            continue
        return_value = kwargs.get("return_value")
        if isinstance(return_value, list):
            values = cycle(return_value)
            mocker.patch.object(fixture_value, attr, side_effect=lambda *_a, **_kw: next(values))
        else:
            mocker.patch.object(fixture_value, attr, return_value=return_value)

    yield
