"""pytest plugin for Litmus test framework."""

from __future__ import annotations

import functools
import os
import sys
import warnings
from collections.abc import Callable, Generator, Iterator, Mapping
from contextvars import ContextVar
from itertools import cycle
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from litmus.config.test_config import (
    FixtureConfig,
    FixturePoint,
    MeasurementLimitConfig,
    TestConfig,
)
from litmus.data.models import CollectedItem, TestRun, TestVector
from litmus.execution._state import current_step_var, current_vector_var
from litmus.execution.accessors import InstrumentAccessor
from litmus.execution.decorators import get_current_logger, set_current_logger
from litmus.execution.harness import Context
from litmus.execution.logger import RunContext, TestRunLogger
from litmus.execution.verify import (  # noqa: F401 — verify re-exported as pytest fixture
    LimitsFn,
    verify,
)
from litmus.fixtures.manager import FixtureManager, PinAccessor
from litmus.instruments.pool import InstrumentPool
from litmus.instruments.route_manager import RouteManager
from litmus.models.instrument import InstrumentRecord
from litmus.models.project import OutputConfig, ProfileConfig, ProjectConfig
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
_active_limits_var: ContextVar[dict[str, Any]] = ContextVar("_active_limits")
_active_profile_var: ContextVar[ProfileConfig | None] = ContextVar("_active_profile")
_active_facets_var: ContextVar[dict[str, str]] = ContextVar("_active_facets")
_active_vector_params_var: ContextVar[dict[str, Any]] = ContextVar("_active_vector_params")
_active_vector_index_var: ContextVar[int] = ContextVar("_active_vector_index")
_active_point_var: ContextVar[FixturePoint | None] = ContextVar("_active_point")


# --- Session-scoped getters (create-and-store on first access) ---
#
# Three ContextVar getter patterns are used in this module. Each getter
# docstring states which one it follows; the convention is:
#
# 1. **Create-and-store** (session-scoped dicts): First call creates a
#    dict, stores it in the ContextVar, returns it. Callers mutate the
#    returned dict in place. Cleanup sets the var to a fresh empty dict.
#    (Examples: get_step_outcomes, get_active_instruments,
#    get_instrument_records, get_test_node_aliases, get_test_node_configs.)
#
# 2. **Return throwaway empty** (per-test dicts): First call returns a
#    new empty dict WITHOUT storing it. Stale state cannot leak across
#    tests — each test gets its own empty dict that is never persisted.
#    The plugin's autouse fixtures set the dict at test start and clear
#    on teardown. (Examples: get_current_step_aliases,
#    get_current_step_config, get_active_limits.)
#
# 3. **Return None** (session singletons): The ContextVar holds a single
#    object (or None) installed once per session by a setter. Getter
#    returns None if not set. (Examples: get_active_spec_context,
#    get_channel_store, get_event_store.)


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
    """Return the current step config; empty dict when unset.

    Set per-test in ``pytest_runtest_setup``; each new test overwrites
    the value so stale config from a prior test cannot leak through.
    """
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


def get_active_limits() -> dict[str, Any]:
    """Return throwaway empty; never stored. Stale state never leaks.

    Populated by the pytest_native plugin from the sidecar ``limits:``
    block for the duration of one test and cleared on teardown, so the
    'no sidecar' case surfaces as an empty dict rather than a lookup
    error.
    """
    try:
        return _active_limits_var.get()
    except LookupError:
        return {}


def set_active_limits(value: dict[str, Any]) -> None:
    """Set the active limits dict. Returns None."""
    _active_limits_var.set(value)


def get_active_profile() -> ProfileConfig | None:
    """Return the active ``ProfileConfig`` selected via ``--litmus-profile``.

    Returns ``None`` when no profile is active. Session-scoped: installed
    by ``pytest_configure`` and cleared by ``pytest_sessionfinish``.
    """
    try:
        return _active_profile_var.get()
    except LookupError:
        return None


def set_active_profile(value: ProfileConfig | None) -> None:
    """Set the active profile. Returns None."""
    _active_profile_var.set(value)


def get_active_facets() -> dict[str, str]:
    """Return resolved profile facets, or empty dict if none.

    Populated alongside ``_active_profile_var`` at session start from
    the profile's declared facet keys and any facet CLI flags; recorded
    onto each run row as provenance.
    """
    try:
        return _active_facets_var.get()
    except LookupError:
        return {}


def set_active_facets(value: dict[str, str]) -> None:
    """Set the active-facets dict. Returns None."""
    _active_facets_var.set(value)


def get_active_vector_params() -> dict[str, Any]:
    """Return the active test's vector params (parametrize + markers + sidecar).

    Returns throwaway empty; never stored. Populated by
    ``_litmus_push_params`` at test start so ``TestRunLogger.measure``
    can stamp ``TestVector.params`` without the harness wiring.
    """
    try:
        return _active_vector_params_var.get()
    except LookupError:
        return {}


def set_active_vector_params(value: dict[str, Any]) -> None:
    """Set the active vector-params dict. Returns None."""
    _active_vector_params_var.set(value)


def get_active_vector_index() -> int:
    """Return the active iteration index within the ``vectors`` self-loop.

    Returns ``0`` outside self-loop mode (normal parametrized runs carry
    their own ``index`` on ``TestVector``; this ContextVar only matters
    when a test consumes the ``vectors`` fixture and the framework is
    stamping rows from a single pytest case).
    """
    try:
        return _active_vector_index_var.get()
    except LookupError:
        return 0


def set_active_vector_index(value: int) -> None:
    """Set the active vector-index. Returns None."""
    _active_vector_index_var.set(value)


def get_active_point() -> FixturePoint | None:
    """Return the currently active :class:`FixturePoint` or ``None``.

    Pushed/popped by :class:`_PointIterator` as a test body iterates
    ``ctx.points``. Read by :func:`_auto_traceability` to stamp pin /
    channel / terminal / net on each measurement row and by
    :meth:`FixtureManager.route` so driver fixtures route without
    seeing pin names.
    """
    try:
        return _active_point_var.get()
    except LookupError:
        return None


def set_active_point(value: FixturePoint | None) -> None:
    """Set the active :class:`FixturePoint`. Returns None."""
    _active_point_var.set(value)


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
    for marker in (
        "litmus_limits(**kwargs): Inject limits by measurement name (merges with sidecar limits:)",
    ):
        config.addinivalue_line("markers", marker)
    _install_active_profile(config)

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
    test_phase = _resolve_test_phase(requested_phase)

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

    _warn_uncovered_condition_keys(config, items)
    _warn_unmatched_profile_keys(items)
    _check_per_test_condition_coverage(config, items)


def _warn_uncovered_condition_keys(config, items: list[pytest.Item]) -> None:
    """Emit one warning per SpecBand condition key no test's vectors supply.

    Conservative: collects the union of condition keys across every
    ``ProductCharacteristic.specs[].when`` in the active product, the
    union of vector-param keys across every sidecar in collection,
    and warns for each condition key present in the former and absent
    from the latter.

    False positives possible if the user is running a filtered subset
    on purpose; better than the silent ``ValueError: No spec band
    matches: .`` failure mode at measure time.
    """
    try:
        spec_ctx = _preview_spec_context(config)
    except pytest.UsageError:
        return
    if spec_ctx is None:
        return

    condition_keys: set[str] = set()
    for char in spec_ctx.product.characteristics.values():
        for band in char.specs:
            if band.when:
                condition_keys.update(band.when.keys())
    if not condition_keys:
        return

    vector_keys: set[str] = set()
    seen_sidecars: set[Path] = set()
    for item in items:
        module_file = getattr(item, "path", None)
        if module_file is None or module_file in seen_sidecars:
            continue
        seen_sidecars.add(module_file)
        sidecar = _load_sidecar(module_file)
        if not sidecar:
            continue
        vector_keys.update(_extract_vector_keys(sidecar))

    missing = sorted(condition_keys - vector_keys)
    if not missing:
        return
    warnings.warn(
        "Product characteristics declare condition keys not covered by "
        f"any test's vectors: {', '.join(missing)}. Tests binding those "
        "characteristics will fail at measure time with 'No spec band matches'. "
        "Add the condition key to the test's sidecar `vectors:` block.",
        UserWarning,
        stacklevel=1,
    )


_POLICY_KEYS = frozenset(
    {"tolerance_pct", "tolerance_abs", "guardband_pct", "expr", "lookup", "steps", "callable"}
)


def _any_limit_binds_to_char(
    char_id: str, top_limits: Mapping[str, Any], test_limits: Mapping[str, Any]
) -> bool:
    """Return True if any limit entry will invoke ``build_limit_from_char`` for ``char_id``.

    A limit entry binds to a char when it's a policy-shaped mapping
    (tolerance / guardband / expr / etc.) and either (a) has an explicit
    ``characteristic:`` matching ``char_id``, or (b) omits
    ``characteristic:`` and inherits the test-level char.
    """
    for limits_block in (test_limits, top_limits):
        for entry in limits_block.values():
            if not isinstance(entry, Mapping):
                continue
            if not any(k in entry for k in _POLICY_KEYS):
                continue
            explicit = entry.get("characteristic")
            if explicit is None or explicit == char_id:
                return True
    return False


def _check_per_test_condition_coverage(config, items: list[pytest.Item]) -> None:
    """Raise ``UsageError`` when a test's char cannot match any SpecBand.

    For each test that binds a ``characteristic``, compute the union of
    that test's vector-param keys and compare against every SpecBand's
    ``when:`` keys. If no band has all its ``when:`` keys present in the
    test's vectors, the test will always fail at measure time with
    ``ValueError: No spec band matches``. Surface the missing keys now.
    """
    try:
        spec_ctx = _preview_spec_context(config)
    except pytest.UsageError:
        return
    if spec_ctx is None:
        return

    seen_functions: set[tuple[Any, str]] = set()
    for item in items:
        module_file = getattr(item, "path", None)
        func_name = getattr(item, "originalname", None) or item.name.split("[")[0]
        key = (module_file, func_name)
        if key in seen_functions:
            continue
        seen_functions.add(key)
        if module_file is None:
            continue
        sidecar = _load_sidecar(module_file)
        if not sidecar:
            continue
        test_entry = (sidecar.get("tests") or {}).get(func_name)
        if not isinstance(test_entry, dict):
            continue
        char_id = test_entry.get("characteristic")
        if not char_id:
            continue
        char = spec_ctx.product.characteristics.get(char_id)
        if char is None or not char.specs:
            continue

        # Only validate when a policy-shaped limit entry will actually
        # resolve against this char. Tests that bind a char purely for
        # ``ctx.points`` iteration (no ``limits:`` policy) never call
        # ``build_limit_from_char`` and never hit the when-matching path.
        top_limits = sidecar.get("limits") or {}
        test_limits = test_entry.get("limits") or {}
        if not _any_limit_binds_to_char(char_id, top_limits, test_limits):
            continue

        class_keys = _vector_entry_keys((sidecar.get("vectors") or {}).get("class"))
        method_cfg = (sidecar.get("vectors") or {}).get("methods", {}).get(func_name)
        method_keys = _vector_entry_keys(method_cfg) if method_cfg is not None else set()
        per_test_vectors = test_entry.get("vectors")
        if per_test_vectors is not None:
            method_keys |= _vector_entry_keys(per_test_vectors)
        test_keys = class_keys | method_keys

        missing_per_band: list[set[str]] = []
        reachable = False
        for band in char.specs:
            band_keys = set(band.when.keys()) if band.when else set()
            missing = band_keys - test_keys
            if not missing:
                reachable = True
                break
            missing_per_band.append(missing)
        if reachable:
            continue

        shared_missing = sorted(set.intersection(*missing_per_band)) if missing_per_band else []
        raise pytest.UsageError(
            f"{item.nodeid}: test binds characteristic {char_id!r} but its "
            f"vectors don't cover condition keys required by any spec band. "
            f"Add {', '.join(shared_missing) or '<condition keys>'} to "
            f"`tests.{func_name}.vectors:` or `vectors.methods.{func_name}:`."
        )


def _warn_unmatched_profile_keys(items: list[pytest.Item]) -> None:
    """Warn when a profile's ``vectors``/``limits``/``markers`` key matches no collected test.

    Profile keys are matched against pytest node IDs via exact match or
    ``fnmatch`` glob (see :func:`_profile_match`). A silent no-op on a
    typo is worse than a skipped mock — it's a production screen that
    stopped running. Warn once per orphan key.
    """
    profile = get_active_profile()
    if profile is None:
        return
    import fnmatch

    nodeids = {item.nodeid for item in items}

    def _orphans(patterns: Mapping[str, Any] | None, label: str) -> list[str]:
        if not patterns:
            return []
        out = []
        for pattern in patterns:
            if pattern in nodeids:
                continue
            if any(fnmatch.fnmatchcase(nid, pattern) for nid in nodeids):
                continue
            out.append(f"  profile.{label}[{pattern!r}]")
        return out

    unmatched: list[str] = []
    unmatched.extend(_orphans(profile.vectors, "vectors"))
    unmatched.extend(_orphans(profile.limits, "limits"))
    unmatched.extend(_orphans(profile.markers, "markers"))
    if not unmatched:
        return
    warnings.warn(
        "Active profile has keys that match no collected test:\n"
        + "\n".join(unmatched)
        + "\nUse an exact node-id, a glob like '*::test_name', or remove the entry.",
        UserWarning,
        stacklevel=1,
    )


def _preview_spec_context(config) -> SpecContext | None:
    """Load the product a session will use, without going through the fixture.

    Runs the same rules as the ``spec_context`` fixture — explicit
    ``--spec`` wins, else ``_autodiscover_product`` applies. Does not
    set the active ContextVar; the fixture will do that on first use.
    """
    spec_path = config.getoption("--spec")
    guardband = float(config.getoption("--guardband"))
    if spec_path:
        return SpecContext.from_file(spec_path, guardband_pct=guardband)

    part_number = config.getoption("--dut-part-number")
    return _autodiscover_product(config, guardband, part_number)


def _extract_vector_keys(sidecar: dict[str, Any]) -> set[str]:
    """Return the union of vector-param keys across a sidecar's vectors block.

    Covers both shapes: top-level ``vectors.methods.<name>`` (list or
    product/zip forms) and per-test ``tests.<name>.vectors``.
    """
    keys: set[str] = set()
    vectors_block = sidecar.get("vectors")
    if isinstance(vectors_block, dict):
        methods = vectors_block.get("methods", {})
        if isinstance(methods, dict):
            for entry in methods.values():
                keys.update(_vector_entry_keys(entry))
        cls_entry = vectors_block.get("class")
        if cls_entry is not None:
            keys.update(_vector_entry_keys(cls_entry))
    tests_block = sidecar.get("tests")
    if isinstance(tests_block, dict):
        for entry in tests_block.values():
            if isinstance(entry, dict):
                keys.update(_vector_entry_keys(entry.get("vectors")))
    return keys


def _vector_entry_keys(entry: Any) -> set[str]:
    """Pull vector-param keys out of one vectors entry."""
    if not entry:
        return set()
    if isinstance(entry, list):
        out: set[str] = set()
        for row in entry:
            if isinstance(row, dict):
                out.update(row.keys())
        return out
    if isinstance(entry, dict):
        for mode in ("list", "product", "zip"):
            if mode in entry:
                value = entry[mode]
                if isinstance(value, dict):
                    return set(value.keys())
                if isinstance(value, list):
                    out = set()
                    for row in value:
                        if isinstance(row, dict):
                            out.update(row.keys())
                    return out
        # Flat form: keys other than `expand` are vector keys.
        return {k for k in entry.keys() if k != "expand"}
    return set()


def _apply_profile_to_items(config, items: list[pytest.Item]) -> None:
    """Inject profile markers onto items matching their node-id patterns.

    Filter composition (``keyword``, ``markexpr``) happens in
    ``_install_active_profile`` during ``pytest_configure``; this step
    only handles per-node-id marker injection, which must happen at
    collection time.
    """
    if get_active_profile() is None:
        return
    for item in items:
        for spec in _profile_markers_for_node(item.nodeid):
            name, kwargs, args = _parse_profile_marker_spec(spec)
            marker = getattr(pytest.mark, name)
            item.add_marker(marker(*args, **kwargs))


def _compose_filter_expr(profile_expr: str, cli_expr: str) -> str:
    """AND-compose a profile filter with any CLI-provided filter."""
    profile_expr = (profile_expr or "").strip()
    cli_expr = (cli_expr or "").strip()
    if not profile_expr:
        return cli_expr
    if not cli_expr:
        return profile_expr
    return f"({profile_expr}) and ({cli_expr})"


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


def _load_project_defaults() -> ProjectConfig:
    """Load ProjectConfig from litmus.yaml, falling back to defaults."""
    try:
        from litmus.store import load_project_config

        return load_project_config()
    except Exception:  # noqa: BLE001 — any load failure falls back to defaults
        # Bad or missing litmus.yaml — don't crash pytest over config
        return ProjectConfig(name="litmus")


def _collect_profile_facet_keys(project: ProjectConfig) -> list[str]:
    """Return the union of facet keys declared across all profiles.

    Used to auto-synthesize one ``--<facet>`` CLI flag per declared key,
    so operators can select profiles by facet query instead of by name.
    """
    keys: set[str] = set()
    for profile in project.profiles.values():
        keys.update(profile.facets)
    return sorted(keys)


def _facet_key_to_cli_flag(key: str) -> str:
    """Map a facet key (``product``, ``instrument_set``) to its CLI flag form."""
    return f"--{key.replace('_', '-')}"


def _resolve_profile_name(profile_name: str | None) -> ProfileConfig | None:
    """Look up ``profile_name`` in litmus.yaml ``profiles:``; raise on unknown.

    Returns ``None`` when ``profile_name`` is falsy. Returns the matching
    ``ProfileConfig`` when found. Raises ``pytest.UsageError`` for a
    missing profile so the mistake surfaces at session start rather than
    silently running with no overrides applied.
    """
    if not profile_name:
        return None
    project = _load_project_defaults()
    profile = project.profiles.get(profile_name)
    if profile is None:
        known = ", ".join(sorted(project.profiles)) or "(none defined)"
        raise pytest.UsageError(
            f"Unknown --litmus-profile={profile_name!r}; known profiles: {known}"
        )
    return profile


def _resolve_active_profile(
    profile_name: str | None,
    facet_flags: dict[str, str],
    project: ProjectConfig,
) -> tuple[str | None, ProfileConfig | None, dict[str, str]]:
    """Select a profile by name, by facet query, or by cross-checked both.

    Resolution rules (see ``docs/guides/profiles.md``):

    * **Name + facets** — name wins, but every flag must match the
      profile's declared facet value. Mismatches raise ``UsageError``.
    * **Name only** — direct lookup in ``profiles:``.
    * **Facets only** — filter profiles matching **all** provided flags.
      A profile that does not declare a facet key the query uses does
      **not** match (strict "unspecified" semantics). Zero matches and
      >1 matches both raise ``UsageError``.
    * **Neither** — returns ``(None, None, {})``.

    Returns ``(profile_name, profile, facets_dict)``. ``facets_dict`` is
    the profile's declared facets (so a name-only selection still gets
    provenance facets populated) plus any explicitly provided flags.
    """
    if not profile_name and not facet_flags:
        return None, None, {}

    if profile_name:
        profile = project.profiles.get(profile_name)
        if profile is None:
            known = ", ".join(sorted(project.profiles)) or "(none defined)"
            raise pytest.UsageError(
                f"Unknown --litmus-profile={profile_name!r}; known profiles: {known}"
            )
        if facet_flags:
            mismatches = [
                f"--{k.replace('_', '-')}={v!r} (profile declares {k}={profile.facets.get(k)!r})"
                for k, v in facet_flags.items()
                if profile.facets.get(k) != v
            ]
            if mismatches:
                raise pytest.UsageError(
                    f"Profile {profile_name!r} does not match facet flags: " + ", ".join(mismatches)
                )
        facets = {**profile.facets, **facet_flags}
        return profile_name, profile, facets

    # Facet-only query.
    matches = [
        (name, profile)
        for name, profile in project.profiles.items()
        if all(profile.facets.get(k) == v for k, v in facet_flags.items())
    ]
    if len(matches) == 0:
        known = sorted(
            " ".join(f"{k}={v}" for k, v in p.facets.items()) or "(no facets)"
            for p in project.profiles.values()
        )
        raise pytest.UsageError(
            "No profile matches the facet query "
            f"({', '.join(f'{k}={v}' for k, v in facet_flags.items())}); "
            f"available facet combinations: {'; '.join(known) or '(none defined)'}"
        )
    if len(matches) > 1:
        overlap = ", ".join(name for name, _ in matches)
        raise pytest.UsageError(
            "Facet query is ambiguous — matches multiple profiles: "
            f"{overlap}. Disambiguate with --litmus-profile=<name>."
        )
    name, profile = matches[0]
    return name, profile, {**profile.facets, **facet_flags}


def _collect_facet_flags_from_config(config, project: ProjectConfig) -> dict[str, str]:
    """Read user-provided facet flag values off ``config.option``."""
    values: dict[str, str] = {}
    for key in _collect_profile_facet_keys(project):
        raw = config.getoption(_facet_key_to_cli_flag(key), default=None)
        if raw:
            values[key] = str(raw)
    return values


def _install_active_profile(config) -> None:
    """Resolve profile (name and/or facets) and install it; compose filter options.

    ``keyword`` and ``markexpr`` are set on ``config.option`` **here**
    (not in ``pytest_collection_modifyitems``) so pytest's own ``-k``/
    ``-m`` filter — which runs via its own modifyitems hook — sees them
    during deselection. Marker injection per node-id remains in
    ``pytest_collection_modifyitems`` because it depends on the item
    list that only exists at collection time.
    """
    project = _load_project_defaults()
    profile_name = config.getoption("--litmus-profile", default=None)
    facet_flags = _collect_facet_flags_from_config(config, project)
    _, profile, facets = _resolve_active_profile(profile_name, facet_flags, project)
    set_active_profile(profile)
    set_active_facets(facets)
    if profile is None:
        return
    if profile.pytest.keyword:
        existing = getattr(config.option, "keyword", None) or ""
        config.option.keyword = _compose_filter_expr(profile.pytest.keyword, existing)
    if profile.pytest.markexpr:
        existing = getattr(config.option, "markexpr", None) or ""
        config.option.markexpr = _compose_filter_expr(profile.pytest.markexpr, existing)


def _parse_flag_from_args(args, flag: str) -> str | None:
    """Scan ``args`` for ``--flag value`` or ``--flag=value`` and return the value."""
    for i, tok in enumerate(args):
        if tok == flag and i + 1 < len(args):
            return args[i + 1]
        if tok.startswith(f"{flag}="):
            return tok.split("=", 1)[1]
    return None


def pytest_load_initial_conftests(early_config, parser, args):
    """Apply ``profile.pytest.addopts`` via ``PYTEST_ADDOPTS`` before collection.

    Setting ``PYTEST_ADDOPTS`` at this stage is the pytest-blessed path
    for injecting CLI tokens — equivalent to exporting the variable in
    the shell. Downstream plugins (pytest-rerunfailures, pytest-xdist,
    pytest-timeout) see the tokens during their own configure phase.
    Mutating ``config.option.*`` later is too fragile when plugins
    register their own option handlers.
    """
    # Scan args directly — our options aren't registered on early_config yet.
    profile_name = _parse_flag_from_args(args, "--litmus-profile") or os.environ.get(
        "LITMUS_PROFILE"
    )

    project = _load_project_defaults()
    facet_flags: dict[str, str] = {}
    for key in _collect_profile_facet_keys(project):
        value = _parse_flag_from_args(args, _facet_key_to_cli_flag(key))
        if value:
            facet_flags[key] = value

    if not profile_name and not facet_flags:
        return
    try:
        _, profile, _ = _resolve_active_profile(profile_name, facet_flags, project)
    except pytest.UsageError:
        # Let pytest_configure surface the error with a clean stacktrace.
        return
    if profile is None or not profile.pytest.addopts:
        return
    existing = os.environ.get("PYTEST_ADDOPTS", "").strip()
    merged = f"{existing} {profile.pytest.addopts}".strip()
    os.environ["PYTEST_ADDOPTS"] = merged


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
    for key in _collect_profile_facet_keys(project):
        group.addoption(
            _facet_key_to_cli_flag(key),
            default=None,
            help=f"Select profile by facet {key!r} (from litmus.yaml profiles).",
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
    profile_name = request.config.getoption("--litmus-profile", default=None)
    facets = dict(get_active_facets())

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
        "facets": facets,
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
    except Exception:  # noqa: BLE001 — fall back to single-slot on any load error
        # Missing or invalid fixture file — fall back to single-slot mode
        # and let the normal config-loading path surface the real error.
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
    # logger session setup (session-scoped fixture), so get_event_store()
    # will return the store logger already created. logger owns
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
    if not _is_worker_mode():
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
import yaml  # noqa: E402

from litmus.config.test_config import Limit  # noqa: E402
from litmus.execution.expand import expand as _expand_sidecar_block  # noqa: E402
from litmus.execution.vectors import Vector  # noqa: E402


def _profile_match(nodeid: str, patterns: Mapping[str, Any]) -> Any | None:
    """Return the first ``patterns`` value whose key fnmatches ``nodeid``.

    Exact matches take precedence over glob matches so a user can pin one
    test with its full node-id and still have a class-wide ``TestFoo::*``
    sibling rule cover the rest.
    """
    if nodeid in patterns:
        return patterns[nodeid]
    import fnmatch

    for pattern, value in patterns.items():
        if fnmatch.fnmatchcase(nodeid, pattern):
            return value
    return None


def _profile_vectors_for_node(nodeid: str) -> dict[str, list[Any]] | None:
    """Return profile vectors for ``nodeid`` (by exact or fnmatch), or None."""
    profile = get_active_profile()
    if profile is None or not profile.vectors:
        return None
    return _profile_match(nodeid, profile.vectors)


def _profile_limits_for_node(nodeid: str) -> dict[str, Any] | None:
    """Return profile limits for ``nodeid`` (by exact or fnmatch), or None."""
    profile = get_active_profile()
    if profile is None or not profile.limits:
        return None
    return _profile_match(nodeid, profile.limits)


def _profile_markers_for_node(nodeid: str) -> list[dict[str, Any] | str]:
    """Return every profile marker spec list whose pattern matches ``nodeid``.

    Unlike vectors/limits (single match wins), markers **accumulate** so
    multiple overlapping patterns (e.g. one for the class, one for the
    method) can each contribute markers.
    """
    profile = get_active_profile()
    if profile is None or not profile.markers:
        return []
    import fnmatch

    out: list[dict[str, Any] | str] = []
    for pattern, specs in profile.markers.items():
        if nodeid == pattern or fnmatch.fnmatchcase(nodeid, pattern):
            out.extend(specs)
    return out


def _parse_profile_marker_spec(spec: Any) -> tuple[str, dict[str, Any], list[Any]]:
    """Return ``(name, kwargs, args)`` from a profile marker YAML spec.

    Supported shapes::

        - flaky                               # bare name
        - skip: "reason"                      # single positional arg
        - flaky: {reruns: 2, reruns_delay: 1} # kwargs
    """
    if isinstance(spec, str):
        return spec, {}, []
    if isinstance(spec, dict):
        if len(spec) != 1:
            raise ValueError(
                f"Profile marker spec must have a single top-level key; got {list(spec)}"
            )
        ((name, payload),) = spec.items()
        if isinstance(payload, dict):
            return name, dict(payload), []
        return name, {}, [payload]
    raise TypeError(f"Unsupported profile marker spec: {spec!r}")


# StashKey for the self-loop vectors matrix. Populated by
# :func:`pytest_generate_tests` whenever the test signature asks for the
# ``vectors`` fixture: the full pre-expanded matrix (native parametrize ×
# sidecar ``vectors:`` × profile overrides) is stashed on the test node
# for the fixture to iterate over, and pytest parametrize expansion is
# suppressed so the test executes as a single case.
_VECTORS_MATRIX_KEY: pytest.StashKey[dict[str, list[Vector]]] = pytest.StashKey()


@pytest.hookimpl(tryfirst=True)
def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Expand vectors from every source for a test.

    Sources, cross-producted in this order:

    1. Native ``@pytest.mark.parametrize`` markers on the function
    2. Sidecar ``vectors:`` block (method- or ``tests.<name>.vectors``)
    3. Profile overrides by node-id

    Two modes, switched on test signature:

    * **Normal mode** — pytest expands cases via its own parametrize
      handling; we additionally parametrize sidecar/profile rows.
    * **Self-loop mode** (``vectors`` fixture in signature) — all
      sources are consolidated into a single matrix stashed on the
      node. Native parametrize markers are **consumed** (removed from
      ``own_markers`` before pytest's built-in hook runs), so pytest
      produces **one** test case. The ``vectors`` fixture yields each
      row in turn and pushes active params per iteration.
    """
    module_file = getattr(metafunc.module, "__file__", None)
    sidecar = _load_sidecar(Path(module_file)) if module_file is not None else None
    vectors_block = (sidecar.get("vectors") if sidecar else None) or {}

    class_block = vectors_block.get("class")
    if class_block:
        class_vectors = _expand_sidecar_block(class_block)
        if class_vectors:
            metafunc.parametrize(
                "_litmus_class_vec",
                class_vectors,
                ids=[_vec_id(v) for v in class_vectors],
                indirect=True,
                scope="class",
            )

    methods_block = vectors_block.get("methods") or {}
    method_cfg = methods_block.get(metafunc.function.__name__)
    if method_cfg is None and sidecar is not None:
        tests_block = sidecar.get("tests")
        if isinstance(tests_block, dict):
            tests_entry = tests_block.get(metafunc.function.__name__)
            if isinstance(tests_entry, dict):
                method_cfg = tests_entry.get("vectors")

    profile_vectors = _profile_vectors_for_node(metafunc.definition.nodeid)
    if profile_vectors is not None:
        method_cfg = {"product": profile_vectors}

    method_vectors = _expand_sidecar_block(method_cfg) if method_cfg else []

    if "vectors" in metafunc.fixturenames:
        # Self-loop mode: consume native parametrize markers, cross with
        # sidecar rows, stash the full matrix on the parent node (module
        # or class) keyed by originalname — the Function item that later
        # runs the test is a distinct pytest node from
        # ``metafunc.definition``, so its own stash is empty. Parent
        # stash + originalname key is the same pattern used by
        # :data:`_PREV_STASH_KEY`.
        parametrize_rows = _consume_parametrize_markers(metafunc)
        full_matrix = _cross_product_vectors(parametrize_rows, method_vectors)
        parent = metafunc.definition.parent
        if parent is not None:
            matrix_map = parent.stash.setdefault(_VECTORS_MATRIX_KEY, {})
            matrix_map[metafunc.definition.originalname] = full_matrix
        return

    if not method_vectors:
        return

    direct_keys = _direct_exposable_keys(metafunc, method_vectors)
    argnames = ["_litmus_method_vec", *direct_keys]
    argvalues = [[vec, *[vec.get(k) for k in direct_keys]] for vec in method_vectors]
    metafunc.parametrize(
        argnames,
        argvalues,
        ids=[_vec_id(v) for v in method_vectors],
        indirect=["_litmus_method_vec"],
    )


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


def _cross_product_vectors(
    parametrize_rows: list[dict[str, Any]],
    sidecar_vectors: list[Vector],
) -> list[Vector]:
    """Cross-product native parametrize rows with sidecar vectors.

    Sidecar keys are the base; parametrize keys overlay on top (so an
    overlapping key takes the parametrize value). Returns a flat list
    of :class:`Vector` with ``_index`` stamped 0..N-1.
    """
    if not parametrize_rows and not sidecar_vectors:
        return []
    if not parametrize_rows:
        return [Vector(**v.params(), _index=i) for i, v in enumerate(sidecar_vectors)]
    if not sidecar_vectors:
        return [Vector(**row, _index=i) for i, row in enumerate(parametrize_rows)]
    out: list[Vector] = []
    for p_row in parametrize_rows:
        for s_vec in sidecar_vectors:
            merged = {**s_vec.params(), **p_row}
            out.append(Vector(**merged, _index=len(out)))
    return out


def _markers_named(target: Any, marker_name: str) -> list[pytest.Mark]:
    """Return every marker named ``marker_name`` attached to ``target``.

    Accepts either a class (reads ``pytestmark``) or a test node (reads
    ``own_markers``). Returns an empty list when ``target`` is ``None``.
    Callers decide merge order: class-level markers are typically merged
    in reverse (closest to the class definition wins), while method-level
    own_markers are already ordered with the outermost decorator first.
    """
    if target is None:
        return []
    source = (
        getattr(target, "pytestmark", []) or []
        if isinstance(target, type)
        else getattr(target, "own_markers", [])
    )
    return [m for m in source if getattr(m, "name", None) == marker_name]


def _direct_exposable_keys(metafunc: pytest.Metafunc, vectors: list[Vector]) -> list[str]:
    """Vector keys the test signature accepts as direct parameters.

    Sidecar values are always available via ``context.get_param(key)`` and
    additionally exposed as direct arguments when the test function
    declares them in its signature.
    """
    all_keys: set[str] = set()
    for vec in vectors:
        all_keys.update(vec.params().keys())
    return sorted(k for k in all_keys if k in metafunc.fixturenames)


@functools.cache
def _load_sidecar(module_file: Path) -> dict[str, Any] | None:
    """Return parsed ``<module>.yaml`` next to ``module_file`` or ``None``.

    Cached on ``module_file`` so a module with many parametrize cases
    parses its YAML once per session instead of once per test. The
    returned dict must be treated as read-only by callers.

    Sidecar shape::

        vectors:
          class: {list: [...]}
          methods:
            test_foo: {list: [...]}
        limits:
          <name>: {low: ..., high: ..., units: ...}
        mocks:
          <fixture.attr>: <value>
        tests:
          test_foo:            # per-method TestConfig
            characteristic: rail_3v3_output     # ctx.points binding
            limits:
              <name>: {tolerance_pct: 2}
    """
    yaml_path = module_file.with_suffix(".yaml")
    if not yaml_path.exists():
        return None
    with yaml_path.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{yaml_path} must contain a mapping at the top level; got {type(data).__name__}"
        )
    return data


def _vec_id(vec: Vector) -> str:
    """Build a readable parametrize id from a vector's params."""
    params = vec.params()
    if not params:
        return "-"
    return "-".join(f"{k}={v}" for k, v in params.items())


class _LimitRef:
    """Placeholder for ``limits.<name>.ref: <product_char_id>``.

    Resolved at push time by looking up the product spec via the active
    :class:`SpecContext`; swallows a missing spec / missing
    characteristic silently (the measurement just records unchecked).
    """

    __slots__ = ("target",)

    def __init__(self, target: str) -> None:
        self.target = target


# Keys that signal an entry is a :class:`MeasurementLimitConfig` policy —
# direct Limit entries use ``low`` / ``high`` / ``nominal`` / ``units`` only.
_POLICY_LIMIT_FIELDS = frozenset({"characteristic", "tolerance_pct", "tolerance_abs"})


class _PolicyLimit:
    """Policy-limit entry deferred until push time.

    Carries the raw :class:`MeasurementLimitConfig` plus the test-level
    characteristic (from ``sidecar.tests.<method>.characteristic``) so
    the resolver can derive a concrete :class:`Limit` from the product
    spec + active vector params.
    """

    __slots__ = ("config", "test_char")

    def __init__(self, config: MeasurementLimitConfig, test_char: str | None) -> None:
        self.config = config
        self.test_char = test_char


class _BandSet:
    """Condition-indexed list of limit bands deferred until measurement time.

    Carries a list of ``(when, entry)`` pairs where each ``entry`` is
    itself a parsed band (``Limit`` / :class:`_LimitRef` / :class:`_PolicyLimit`).
    At measurement time the logger picks the first band whose ``when``
    matches the active vector params (same logic as
    ``SpecBand.when`` via :func:`band_matches`). No match →
    ``pytest.UsageError``.
    """

    __slots__ = ("bands",)

    def __init__(
        self,
        bands: list[tuple[dict[str, Any], Limit | _LimitRef | _PolicyLimit]],
    ) -> None:
        self.bands = bands


def _limit_entry_to_raw(value: Any) -> Any:
    """Normalize a :class:`TestConfig.limits` entry back to raw YAML shape.

    ``MeasurementLimitConfig`` / ``Limit`` → dict; a list of
    ``MeasurementLimitConfig`` (condition-indexed bands) → list of dicts;
    anything else passes through (already a mapping).
    """
    if isinstance(value, list):
        return [v.model_dump(exclude_none=True) if hasattr(v, "model_dump") else v for v in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return value


def _parse_limit_entry(
    name: str,
    spec: Mapping[str, Any],
    *,
    test_char: str | None,
) -> Limit | _LimitRef | _PolicyLimit:
    """Parse a single limit mapping into its deferred-or-resolved form."""
    from litmus.execution.logger import _limit_from_dict

    if "ref" in spec:
        return _LimitRef(spec["ref"])
    if _POLICY_LIMIT_FIELDS & spec.keys():
        return _PolicyLimit(MeasurementLimitConfig.model_validate(dict(spec)), test_char)
    return _limit_from_dict(spec)


def _parse_limits_block(
    raw: Mapping[str, Any] | None,
    *,
    test_char: str | None = None,
) -> dict[str, Limit | _LimitRef | _PolicyLimit | _BandSet]:
    """Convert a sidecar ``limits:`` mapping into Limit / reference / policy / bandset objects.

    Entries with ``ref:`` become :class:`_LimitRef`. Entries with any of
    :data:`_POLICY_LIMIT_FIELDS` become :class:`_PolicyLimit` wrapping a
    :class:`MeasurementLimitConfig` (resolution deferred to push time so
    the active vector params + spec context are in scope). A list-valued
    entry is parsed as :class:`_BandSet` — condition-indexed bands matched
    at measurement time via the entry's ``when:`` keys. Everything else
    is treated as a direct :class:`Limit`.
    """
    if not raw:
        return {}
    out: dict[str, Limit | _LimitRef | _PolicyLimit | _BandSet] = {}
    for name, spec in raw.items():
        if isinstance(spec, list):
            bands: list[tuple[dict[str, Any], Limit | _LimitRef | _PolicyLimit]] = []
            for band_spec in spec:
                if not isinstance(band_spec, Mapping):
                    raise ValueError(
                        f"limits.{name!r} bands must be mappings; got {type(band_spec).__name__}"
                    )
                when = dict(band_spec.get("when") or {})
                body = {k: v for k, v in band_spec.items() if k != "when"}
                bands.append((when, _parse_limit_entry(name, body, test_char=test_char)))
            out[name] = _BandSet(bands)
            continue
        if not isinstance(spec, Mapping):
            raise ValueError(
                f"limits.{name!r} must be a mapping or list; got {type(spec).__name__}"
            )
        out[name] = _parse_limit_entry(name, spec, test_char=test_char)
    return out


def _resolve_entry(
    value: Limit | _LimitRef | _PolicyLimit,
    *,
    spec: Any,
    params: dict[str, Any],
    guardband_pct: float,
) -> Limit | None:
    """Resolve a single parsed limit entry to a concrete :class:`Limit`.

    Shared by :func:`_resolve_limits` (push-time) and the logger's
    band-set matcher (measurement-time). Returns ``None`` if the entry
    can't be resolved (missing spec / characteristic).
    """
    from litmus.execution.limits import _apply_guardband
    from litmus.models.config import Comparator
    from litmus.models.config import Limit as LimitModel

    if isinstance(value, _LimitRef):
        if spec is None:
            return None
        try:
            return spec.get_limit(value.target, guardband_pct=guardband_pct, **params)
        except (KeyError, ValueError):
            return None

    if isinstance(value, _PolicyLimit):
        cfg = value.config
        char_id = cfg.characteristic or value.test_char
        if char_id is None or spec is None:
            return None
        char = spec.product.characteristics.get(char_id)
        if char is None:
            return None
        band = char.get_spec_at(dict(params))
        if band is None or not isinstance(band.value, (int, float)):
            return None
        nominal = float(band.value)
        if cfg.tolerance_pct is not None:
            delta = abs(nominal) * cfg.tolerance_pct / 100.0
        elif cfg.tolerance_abs is not None:
            delta = float(cfg.tolerance_abs)
        else:
            return None
        low, high = nominal - delta, nominal + delta
        low, high = _apply_guardband(low, high, guardband_pct, Comparator.GELE.value)
        return LimitModel(
            low=low,
            high=high,
            nominal=nominal,
            units=cfg.units or char.units or "",
            spec_id=char_id,
            spec_ref=char_id,
            comparator=Comparator.GELE,
        )

    return value


def _resolve_limits(
    raw_map: Mapping[str, Limit | _LimitRef | _PolicyLimit | _BandSet],
) -> dict[str, Limit | _BandSet]:
    """Resolve deferred entries against the active spec + vector params.

    Literal :class:`Limit` entries pass through unchanged. :class:`_LimitRef`
    entries look up the named characteristic on the active spec.
    :class:`_PolicyLimit` entries derive a :class:`Limit` from
    ``MeasurementLimitConfig`` policy fields (``tolerance_pct`` /
    ``tolerance_abs``) against ``product.characteristics[char]
    .get_spec_at(active_vector_params).value``, layered with
    ``profile.guardband_pct``. :class:`_BandSet` entries pass through
    as-is — band matching happens at measurement time against the
    current ``_active_vector_params_var`` (needed so self-loop tests
    resolve a distinct band per iteration). Entries that can't be
    resolved (no spec, missing characteristic) are dropped so the
    measurement records unchecked.
    """
    resolved: dict[str, Limit | _BandSet] = {}
    spec = get_active_spec_context()
    profile = get_active_profile()
    guardband_pct = float(getattr(profile, "guardband_pct", 0.0) or 0.0) if profile else 0.0
    params = get_active_vector_params()

    for name, value in raw_map.items():
        if isinstance(value, _BandSet):
            resolved[name] = value
            continue
        result = _resolve_entry(value, spec=spec, params=params, guardband_pct=guardband_pct)
        if result is not None:
            resolved[name] = result
    return resolved


def _match_band(
    bandset: _BandSet,
    active_params: Mapping[str, Any],
) -> Limit:
    """Pick the matching band and resolve it to a concrete :class:`Limit`.

    Iterates ``bandset.bands`` in order; picks the first whose ``when:``
    matches ``active_params`` using :func:`band_matches` (the same logic
    that ``ProductCharacteristic.get_spec_at`` uses). Raises
    ``pytest.UsageError`` when no band matches — silent skips of a
    declared limit would hide bugs.
    """
    from litmus.config.capability import SpecBand, band_matches

    spec_ctx = get_active_spec_context()
    profile = get_active_profile()
    guardband_pct = float(getattr(profile, "guardband_pct", 0.0) or 0.0) if profile else 0.0
    params = dict(active_params)

    for when, entry in bandset.bands:
        # Reuse SpecBand.when semantics by constructing a synthetic band.
        probe = SpecBand.model_validate({"when": when}) if when else SpecBand(when={})
        if band_matches(probe, params):
            resolved = _resolve_entry(
                entry, spec=spec_ctx, params=params, guardband_pct=guardband_pct
            )
            if resolved is None:
                raise pytest.UsageError(
                    f"Limit band matched (when={when!r}) but resolution yielded no Limit "
                    "(missing spec context or characteristic)."
                )
            return resolved

    raise pytest.UsageError(
        f"No limit band matched active params {params!r}. "
        f"Declared bands: {[dict(w) for w, _ in bandset.bands]!r}"
    )


@pytest.fixture(scope="class")
def _litmus_class_vec(request: pytest.FixtureRequest) -> Vector:
    """Indirect-parametrize target for class-level vectors.

    Not for direct use. Populated by
    :func:`pytest_generate_tests` when a sidecar declares class vectors —
    the fixture is parametrized with ``indirect=True`` so pytest routes
    the Vector here instead of to the test function. Class scope means
    all method-level parametrize cases for one class vector run before
    pytest moves to the next class vector.
    """
    param = getattr(request, "param", None)
    return param if isinstance(param, Vector) else Vector()


@pytest.fixture
def _litmus_method_vec(request: pytest.FixtureRequest) -> Vector:
    """Indirect-parametrize target for method-level vectors.

    Not for direct use. Populated by :func:`pytest_generate_tests`
    when a sidecar declares method vectors (or when a test method uses
    ``@pytest.mark.parametrize`` directly). Function-scoped so each
    parametrize case gets its own Vector instance.
    """
    param = getattr(request, "param", None)
    return param if isinstance(param, Vector) else Vector()


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
    _litmus_class_vec: Vector,
    _litmus_method_vec: Vector,
) -> Iterator[None]:
    """Merge parametrize params into ``context`` for this test.

    Source-agnostic: reads ``request.node.callspec.params`` so that
    sidecar vectors, native ``@pytest.mark.parametrize``, and
    ``@pytest.fixture(params=...)`` all populate ``context`` the same
    way. Applies to every collected test.

    Also chains ``Context._prev`` to the previous parametrize case of
    the same ``(parent_node, method)`` so ``context.changed("vin")``
    picks up transitions across adjacent cases. The lookup is keyed by
    ``originalname`` on the parent node's stash, so two classes (or
    modules) that share a method name do not cross-contaminate.
    """
    ctx: Context = request.getfixturevalue("context")

    ctx.set_params(_litmus_class_vec.params())
    ctx.set_params(_litmus_method_vec.params())

    callspec = getattr(request.node, "callspec", None)
    if callspec is not None:
        extras = {
            k: v
            for k, v in callspec.params.items()
            if k not in ("_litmus_class_vec", "_litmus_method_vec") and not k.startswith("_")
        }
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


def _load_sidecar_for_node(node: pytest.Item) -> dict[str, Any] | None:
    """Load the sidecar next to the test item's module file."""
    module = getattr(node, "module", None)
    module_file = getattr(module, "__file__", None)
    if module_file is None:
        return None
    return _load_sidecar(Path(module_file))


@pytest.fixture
def _litmus_sidecar(request: pytest.FixtureRequest) -> dict[str, Any] | None:
    """Function-scoped cache of the sidecar YAML for the current test.

    Both ``_litmus_push_limits`` and ``_litmus_apply_mocks`` depend on
    this fixture so the sidecar is parsed once per test, not twice.
    Returns ``None`` when no sidecar file exists.
    """
    return _load_sidecar_for_node(request.node)


@pytest.fixture(autouse=True)
def _litmus_push_limits(
    request: pytest.FixtureRequest,
    _litmus_sidecar: dict[str, Any] | None,
    _litmus_class_vec: Vector,
    _litmus_method_vec: Vector,
    _litmus_push_params: None,
) -> Iterator[None]:
    """Merge sidecar ``limits:``, sidecar ``tests.<method>.limits``,
    ``@pytest.mark.litmus_limits`` markers, and active-profile limits,
    then push into ``_active_limits_var``.

    Merge order (later wins):

        sidecar.limits                        # file-level shorthand
        sidecar.tests.<method>.limits         # per-method override
        @pytest.mark.litmus_limits (class)    # class marker
        @pytest.mark.litmus_limits (method)   # method marker
        profile.limits[<node-id>]             # session-level override

    Values at each layer may be raw ``Limit`` dicts, ``{"ref": ...}``
    pointers, or :class:`MeasurementLimitConfig` policy shapes
    (``characteristic`` / ``tolerance_pct`` / ``tolerance_abs``). Policy
    entries derive from ``product.characteristics[char]
    .get_spec_at(active_vector_params)`` at push time, layered with
    ``profile.guardband_pct``. Depends on :func:`_litmus_push_params` so
    the active vector params are populated before resolution.
    """
    binding = _load_test_binding(request.node, _litmus_sidecar)
    test_char = binding.characteristic if binding is not None else None

    sidecar_raw = _litmus_sidecar.get("limits") if _litmus_sidecar else None
    per_test_raw = binding.limits if binding is not None and binding.limits else None
    per_test_raw_dict = (
        {k: _limit_entry_to_raw(v) for k, v in per_test_raw.items()} if per_test_raw else None
    )
    class_raw = _limits_marker_kwargs(getattr(request.node, "cls", None), scope="class")
    method_raw = _limits_marker_kwargs(request.node, scope="method")
    profile_raw = _profile_limits_for_node(request.node.nodeid)

    merged_raw: dict[str, Any] = {}
    for layer in (sidecar_raw, per_test_raw_dict, class_raw, method_raw, profile_raw):
        if layer:
            merged_raw.update(layer)

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


def _load_test_binding(
    node: pytest.Item,
    sidecar: dict[str, Any] | None,
) -> TestConfig | None:
    """Parse ``sidecar.tests.<method>`` into a :class:`TestConfig` or ``None``.

    Keyed by ``node.originalname`` so parametrize cases of one method
    share the same entry. For tests inside a class, the qualified form
    ``tests.TestClass.test_method`` disambiguates across classes that
    share a method name in one file; the qualified key wins over the
    bare-method shorthand when both are present. Missing ``tests:``
    block returns ``None``; missing entry returns ``None``.
    """
    if not sidecar:
        return None
    tests = sidecar.get("tests")
    if not isinstance(tests, dict):
        return None
    method = getattr(node, "originalname", None) or node.name
    cls = getattr(node, "cls", None)
    entry: Any = None
    key_used = method
    if cls is not None:
        qualified_key = f"{cls.__name__}.{method}"
        entry = tests.get(qualified_key)
        if entry is not None:
            key_used = qualified_key
    if entry is None:
        entry = tests.get(method)
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ValueError(f"tests.{key_used!r} must be a mapping; got {type(entry).__name__}")
    return TestConfig.model_validate(entry)


def _resolve_binding_to_points(
    binding: TestConfig,
    spec_ctx: Any,
    fixture_cfg: FixtureConfig | None,
) -> list[FixturePoint]:
    """Expand a test-level binding into an ordered list of :class:`FixturePoint`.

    - ``characteristic``: product char → pins → fixture points routing those pins.
    - ``fixturepoints``: direct point-name lookup.
    - ``instrument_channels``: filter fixture points by instrument (and
      optionally channel).

    Returns ``[]`` when no fixture is loaded (simple path — the test runs
    with ``ctx.points`` as an empty iterator so the body is a no-op).
    """
    if fixture_cfg is None:
        return []

    points_map = fixture_cfg.points

    if binding.characteristic:
        if spec_ctx is None:
            raise pytest.UsageError(
                f"Test binding 'characteristic: {binding.characteristic}' "
                "requires a product spec (load via --spec or products/ auto-discovery)."
            )
        char = spec_ctx.product.characteristics.get(binding.characteristic)
        if char is None:
            raise pytest.UsageError(
                f"Characteristic {binding.characteristic!r} not found in "
                f"product {spec_ctx.product.id!r}."
            )
        points: list[FixturePoint] = []
        for pin_id in char.resolved_pins:
            pin = spec_ctx.product.pins.get(pin_id)
            net = pin.net if pin else None
            for pt in points_map.values():
                if pt.dut_pin == pin_id or (net is not None and pt.net == net):
                    points.append(pt)
                    break
        return points

    if binding.fixturepoints:
        resolved: list[FixturePoint] = []
        for name in binding.fixturepoints:
            pt = points_map.get(name)
            if pt is None:
                raise pytest.UsageError(f"Fixture point {name!r} not found in fixture config.")
            resolved.append(pt)
        return resolved

    if binding.instrument_channels:
        out: list[FixturePoint] = []
        for inst_name, channels in binding.instrument_channels.items():
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

    return []


@pytest.fixture(autouse=True)
def _litmus_bind_points(
    request: pytest.FixtureRequest,
    _litmus_sidecar: dict[str, Any] | None,
) -> Iterator[None]:
    """Build :class:`_PointIterator` on ``ctx.points`` from the test's binding.

    Sidecar ``tests.<method>`` is parsed as :class:`TestConfig`. If exactly
    one of ``characteristic`` / ``fixturepoints`` / ``instrument_channels``
    is set, the framework resolves it against the active product spec +
    fixture and attaches an iterator. If the test body declares a binding
    but never iterates ``ctx.points``, the test fails (silent skips are
    worse than errors). Tests without a binding leave ``ctx.points = None``.
    """
    binding = _load_test_binding(request.node, _litmus_sidecar)
    if binding is None or not (
        binding.characteristic or binding.fixturepoints or binding.instrument_channels
    ):
        yield
        return

    spec_ctx = get_active_spec_context()
    fixture_cfg = _safe_get_session_fixture(request, "fixture_config")
    points = _resolve_binding_to_points(binding, spec_ctx, fixture_cfg)

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


def _limits_marker_kwargs(
    target: Any,
    *,
    scope: str,
) -> dict[str, Any]:
    """Read ``@pytest.mark.litmus_limits(**kwargs)`` at the given scope."""
    markers = _markers_named(target, "litmus_limits")
    if scope == "class":
        merged: dict[str, Any] = {}
        for marker in reversed(markers):
            merged.update(marker.kwargs)
        return merged
    if scope == "method":
        return dict(markers[0].kwargs) if markers else {}
    raise ValueError(f"scope must be 'class' or 'method'; got {scope!r}")


@pytest.fixture(autouse=True)
def _litmus_apply_mocks(
    request: pytest.FixtureRequest,
    _litmus_sidecar: dict[str, Any] | None,
) -> Iterator[None]:
    """Install mocks declared via sidecar ``mocks:`` blocks.

    Entries use dotted paths of the form ``<fixture>.<attr>``. The fixture
    value must already exist (resolved via ``request.getfixturevalue``);
    the attribute is replaced with a ``Mock(return_value=...)`` for the
    duration of the test. Layers (later wins):

        sidecar.mocks                        # file-level shorthand
        sidecar.tests.<method>.mocks         # per-method override
    """
    if request.config.getoption("--no-test-mocks", default=False):
        yield
        return

    sidecar_mocks = _litmus_sidecar.get("mocks") if _litmus_sidecar else None
    binding = _load_test_binding(request.node, _litmus_sidecar)
    per_test_mocks = binding.mocks if binding is not None and binding.mocks else None

    mocks: dict[str, Any] = {}
    if sidecar_mocks:
        mocks.update(sidecar_mocks)
    if per_test_mocks:
        mocks.update(per_test_mocks)

    if not mocks:
        yield
        return

    patchers: list[Any] = []
    try:
        for dotted, return_value in mocks.items():
            fixture_name, _, attr = dotted.partition(".")
            if not attr:
                raise ValueError(f"mocks entry {dotted!r} must be <fixture>.<attr> form")
            try:
                target = request.getfixturevalue(fixture_name)
            except pytest.FixtureLookupError:
                # Most commonly a typo; warn so the user sees the
                # misspelling instead of a silently-skipped mock that then
                # fails the measurement on real hardware.
                warnings.warn(
                    f"mocks entry {dotted!r}: fixture {fixture_name!r} not "
                    "found on this test — mock skipped. Check the sidecar / "
                    "marker key matches a fixture in the test's signature.",
                    stacklevel=1,
                )
                continue
            if isinstance(return_value, list):
                # List-valued mock — rotate values across calls (each
                # ``ctx.points`` iteration or any repeated fixture call
                # gets the next value, cycling when the list is exhausted).
                values = cycle(return_value)
                p = patch.object(target, attr, side_effect=lambda *_a, **_kw: next(values))
            else:
                p = patch.object(target, attr, return_value=return_value)
            p.start()
            patchers.append(p)
        yield
    finally:
        for p in patchers:
            p.stop()
