"""Pytest lifecycle hooks for the Litmus plugin.

Every ``pytest_*`` hook the plugin contributes lives here, plus the
private helpers each hook uses (cascade application, retry-marker
translation, parametrize expansion, run-time setup/teardown adapters,
…). Pytest discovers hooks by inspecting the plugin module's
namespace; :mod:`litmus.pytest_plugin.__init__` re-imports the names
defined here so the entry-point sees them.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from litmus.data.models import CollectedItem
from litmus.execution._state import (
    get_active_instruments,
    get_active_product_context,
    get_active_profile,
    get_current_logger,
    set_active_instruments,
    set_active_profile,
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
from litmus.execution.audit import audit_traceability
from litmus.execution.cascade import cascade_for, find_unmatched_profile_keys
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
from litmus.execution.vectors import Vector
from litmus.models.test_config import RetryConfig, SweepEntry, TestEntry
from litmus.pytest_plugin.helpers import (
    find_station_file,
    join_marker_names,
    mocks_active,
    node_cls_func,
    prompt_for_serial,
)
from litmus.pytest_plugin.markers import (
    StackedMarkersError,
    apply_entry_markers,
    enforce_no_inline_stacking,
    normalize_inline_list_payload,
)
from litmus.pytest_plugin.retry import retry_config_to_flaky_kwargs
from litmus.pytest_plugin.sweeps import (
    parametrize_call_rows,
    parametrize_calls_for_entry,
    sweep_to_parametrize_args,
)


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
        "litmus_characteristics([<characteristic_id>, ...]): Bind the test to one "
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
    station_path = find_station_file(config)
    if station_path is None:
        return

    try:
        from litmus.store import load_station

        station_model = load_station(station_path)
    except (ValidationError, yaml.YAMLError, OSError, ValueError) as exc:
        # Fail fast on station config errors — same posture as profile
        # load failures. A typo'd station path silently warning would
        # surface as a confusing "instrument not found" later.
        raise pytest.UsageError(f"Failed to load station config {station_path!s}: {exc}") from exc

    if not station_model:
        return

    instruments_map = station_model.instruments or {}

    # Sequences (deleted) used to inject per-test fixture aliases and configs.
    # With sequences gone, both maps are empty for the lifetime of the session.
    set_test_node_aliases({})
    set_test_node_configs({})
    all_alias_names: set[str] = set()

    class _InstrumentFixtures:
        pass

    aliased_role_names = all_alias_names & set(instruments_map.keys())

    def _make_resolved(name: str):
        """Create a function-scoped fixture that resolves aliases."""
        from litmus.execution._state import get_current_step_aliases

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

    if dut_serials:
        return

    requested_phase = config.getoption("--test-phase") or os.environ.get("LITMUS_TEST_PHASE")
    test_phase = resolve_test_phase(requested_phase, mocks_active=mocks_active(config))

    if test_phase == "development":
        return

    if dut_serial == "DUT001":
        serial = prompt_for_serial(test_phase)
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
                markers=join_marker_names(item.iter_markers(), sort=True),
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
            retry_config = RetryConfig.model_validate(dict(marker.kwargs))
        except ValueError as exc:
            raise pytest.UsageError(f"{item.nodeid}: invalid litmus_retry — {exc}") from exc
        item.add_marker(pytest.mark.flaky(**retry_config_to_flaky_kwargs(retry_config)))


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
    cls_name, func_name = node_cls_func(item)
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
    group.addoption(
        "--dut-lot-number",
        default=None,
        help="DUT lot/batch number (mirrors LITMUS_DUT_LOT_NUMBER env var)",
    )
    group.addoption(
        "--station",
        default=None,
        help="Station ID. When unset, the resolver tries hostname "
        "auto-match against stations/*.yaml ``hostname:`` fields, "
        "then falls back to ``ProjectConfig.default_station``.",
    )
    group.addoption("--operator", default=None, help="Operator name")
    group.addoption(
        "--results-dir",
        default=project.results_dir,
        help="Directory for Parquet results (default: platform data dir)",
    )
    group.addoption(
        "--product",
        default=None,
        help="Product ID — looks up ``products/<id>.yaml`` "
        "(matches ``--station``/``--fixture`` resolution shape).",
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
    group.addoption(
        "--fixture",
        default=None,
        help="Fixture ID. When unset, the resolver tries the active "
        "profile's ``fixture:`` field, then "
        "``ProjectConfig.default_fixture``, then the single-file "
        "fallback in ``fixtures/``.",
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
    facet_keys = set(collect_profile_facet_keys(project))
    for key in sorted(facet_keys):
        if key == "test_phase":
            continue
        group.addoption(
            facet_key_to_cli_flag(key),
            default=None,
            help=f"Select profile by facet {key!r} (from litmus.yaml profiles).",
        )
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


def _extract_code_identity(item: Any) -> dict[str, str | None]:
    """Extract code identity fields from a pytest.Item node."""
    identity: dict[str, str | None] = {}
    identity["node_id"] = getattr(item, "nodeid", None)
    cls_name, func_name = node_cls_func(item)
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

    identity["markers"] = join_marker_names(getattr(item, "own_markers", []))

    return identity


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Per-test setup: clear aliases/config, capture code identity, reset mocks."""
    set_current_step_aliases({})
    set_current_step_config({})

    set_current_code_identity(_extract_code_identity(item))

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
    """Pytest adapter — read ``--strict-traceability`` + product context, delegate."""
    audit_traceability(
        logger_inst,
        strict=strict,
        spec_active=get_active_product_context() is not None,
    )


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

    station_path = find_station_file(session.config)
    station_config_obj = load_station(station_path) if station_path else None
    return run_multi_slot_session(session, station_config=station_config_obj)


# StashKey for the self-loop vectors matrix. Populated by
# :func:`pytest_generate_tests` whenever the test signature asks for the
# ``vectors`` fixture: the full pre-expanded matrix (native parametrize ×
# sidecar ``vectors:`` × profile overrides) is stashed on the test node
# for the fixture to iterate over, and pytest parametrize expansion is
# suppressed so the test executes as a single case.
VECTORS_MATRIX_KEY: pytest.StashKey[dict[str, list[Vector]]] = pytest.StashKey()


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
            matrix_map = parent.stash.setdefault(VECTORS_MATRIX_KEY, {})
            matrix_map[metafunc.definition.originalname] = full_matrix
        return

    for argnames, argvalues, extra in parametrize_calls:
        normalized_values = _normalize_parametrize_argvalues(argvalues)
        metafunc.parametrize(argnames, normalized_values, **extra)


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
