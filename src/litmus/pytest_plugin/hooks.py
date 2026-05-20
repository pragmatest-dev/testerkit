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
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from litmus.data._collection_indices import StepKey, assign_indices
from litmus.data.models import CollectedItem, Outcome, escalate_outcome, retry_aware_rollup
from litmus.execution._state import (
    get_active_instruments,
    get_active_product_context,
    get_active_profile,
    get_active_profile_name,
    get_active_slot_runner,
    get_current_logger,
    set_active_instruments,
    set_active_profile,
    set_channel_store,
    set_collected_items,
    set_current_code_identity,
    set_current_slot_id,
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
from litmus.models.test_config import MockEntry, RetryConfig, SweepEntry, TestEntry
from litmus.pytest_plugin.helpers import (
    find_fixture_file,
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

# Step ids (UUIDs as strings) that have shown verdict intent during
# their execution — either pytest's rewritten assert hit and passed,
# or a Litmus measurement with a limit was recorded against them.
# Used by ``_stamp_step_from_call_outcome`` to pick PASSED (intent
# present) vs DONE (no intent) on a clean exit. Cleared per session
# in ``pytest_sessionfinish``.
_STEP_JUDGMENT_INTENT: set[str] = set()


def mark_step_judgment_intent(step_id: str) -> None:
    """Record that a step has shown verdict intent.

    Public so the measurement layer can call us when
    ``logger.measure(..., limit=...)`` records a limited value —
    that's structurally equivalent to a passing assert (the test
    code declared an intent to judge).
    """
    _STEP_JUDGMENT_INTENT.add(step_id)


def _install_termination_handler() -> None:
    """Convert SIGTERM into KeyboardInterrupt so pytest's existing
    keyboard-interrupt path runs full fixture teardown.

    The chain we get for free once SIGTERM is converted:

    1. ``KeyboardInterrupt`` raised in the main thread on the next
       Python eval-loop tick.
    2. Pytest's runner catches it and fires our
       :func:`pytest_keyboard_interrupt` hook → step + run stamped
       ``TERMINATED`` (cleanup-ran semantic).
    3. Fixture teardowns run (instruments → safe state, channel /
       event store close, parquet finalize).
    4. Process exits cleanly.

    Real-world limits worth knowing:

    * **Main thread blocked in C extension** (PyVISA query, scope
      transfer, etc.) — the signal queues until Python re-enters
      its eval loop. Cleanup fires eventually; not instant.
    * **External SIGKILL after timeout** (Docker: ~10s, systemd
      default: 90s) — if cleanup runs longer than the budget, we
      get partial state. The fixture teardown order matters:
      instrument-safe-state runs first (highest priority), parquet
      flush runs last (most likely casualty).
    * **SIGKILL straight up** — no Python runs; nothing to do.

    We install only when no user handler is already registered, so
    operator scripts that wrap pytest don't get stomped on.
    """
    import signal

    existing = signal.getsignal(signal.SIGTERM)
    # SIG_DFL is "default action" (terminate); SIG_IGN is ignore.
    # Anything else is a user-installed handler we shouldn't touch.
    if existing not in (signal.SIG_DFL, signal.SIG_IGN, None):
        return

    def _term_to_interrupt(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _term_to_interrupt)
    except ValueError:
        # ``signal.signal`` only works in the main thread. If
        # pytest is already running on a non-main thread (rare —
        # only via embedding), we silently skip; cleanup falls back
        # to atexit-style flushing.
        pass


def pytest_assertion_pass(item: pytest.Item, lineno: int, orig: str, expl: str) -> None:
    """Pytest fires this whenever a rewritten ``assert`` passes.

    We use it as the runtime signal that the currently-open step
    declared verdict intent. At step-end, if a step exited cleanly
    AND we saw at least one passing assertion (or a limited
    measurement), we stamp ``PASSED``; otherwise ``DONE``.

    This is the runtime version of the AST scan we tried first —
    accurate by construction (per assert hit, per step), no static
    analysis, no module/function scoping puzzles. Cross-module
    helpers come along for free as long as their module is
    registered with ``pytest.register_assert_rewrite()``.
    """
    _ = item, lineno, orig, expl
    from litmus.execution._state import get_current_step

    step = get_current_step()
    if step is not None:
        _STEP_JUDGMENT_INTENT.add(str(step.id))


@contextmanager
def _profile_errors_as_usage() -> Iterator[None]:
    """Context manager: re-raise any ``ProfileError`` as ``pytest.UsageError``.

    Used at each site that calls into the profile / phase-wiring resolver
    and wants the failure to surface as a clean pytest usage error
    rather than a stack trace into Litmus internals.
    """
    try:
        yield
    except ProfileError as exc:
        raise pytest.UsageError(str(exc)) from exc


def pytest_configure(config):
    """Register Litmus markers and auto-register instrument role fixtures."""
    for marker in (
        "litmus_sweeps([{argname: argvalues}, ...]): Declare nested "
        "parametric sweeps — runner-neutral alias for parametrize. The "
        "payload is a list of sweep dicts; each dict is one nesting "
        "level (top = outer, slowest loop). Single-key dict = one axis; "
        "multi-key dict = zipped axes (paired argvalues). Stacking "
        "multiple markers concatenates their lists.",
        "litmus_retry(max_retries=N, delay=S, on=[...]): Declare retry "
        "policy — runner-neutral alias for retry markers. Translates to "
        "pytest-rerunfailures' @pytest.mark.flaky in pytest; OpenHTF / "
        "unittest wrappers map to their own retry primitives. "
        "max_retries is retries beyond the original (0 = no retry); delay "
        "is seconds between retries; on is an optional list of exception "
        "class names to retry on (default: any exception).",
        "litmus_limits(**kwargs): Inject limits by measurement name (merges with sidecar limits:)",
        "litmus_characteristics([<characteristic_id>, ...]): Bind the test to one "
        "or more product characteristics; provides spec-relative limit "
        "context and auto-derives fixture connections from the "
        "characteristic's pins. v1 supports one binding per test (single "
        "iteration scope); multi-binding semantics may relax in future.",
        "litmus_connections([<name>, ...] | **{instrument: channels}): "
        "Bind the test to fixture-connection names (positional list, matches "
        "`litmus_characteristics`) OR to raw instrument channels (kwargs by "
        "instrument name, matches `litmus_limits` shape). The two forms are "
        "alternatives — list shape requires a fixture YAML; dict shape works "
        "pre-fixture-config for early bringup.",
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
    with _profile_errors_as_usage():
        install_active_profile(config)
        install_session_inputs(load_project_defaults(), config)

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

    # Sequences (deleted) used to inject per-test fixture aliases. The
    # alias machinery and per-test config map are gone with them; setters
    # remain to clear any stale state from an earlier session.
    set_test_node_aliases({})
    set_test_node_configs({})

    class _InstrumentFixtures:
        pass

    for role in instruments_map:

        def _make(r=role):
            @pytest.fixture(scope="session")
            def _fix(instruments):
                return instruments.get(r)

            _fix.__name__ = r
            _fix.__qualname__ = r
            return _fix

        setattr(_InstrumentFixtures, role, staticmethod(_make()))

    config.pluginmanager.register(_InstrumentFixtures(), "litmus_instrument_fixtures")


def pytest_report_header(config):
    """Show litmus results location (and active profile's composed addopts) in the header."""
    from litmus.data.data_dir import resolve_data_dir

    data_dir = config.getoption("--data-dir", default=None)
    resolved = resolve_data_dir(data_dir)
    if data_dir:
        lines = [
            f"litmus: results → {resolved}"
            " (local — remove data_dir from litmus.yaml for global storage)"
        ]
    else:
        lines = [f"litmus: results → {resolved}"]

    profile_name = get_active_profile_name()
    if profile_name:
        composed = os.environ.get("PYTEST_ADDOPTS", "").strip()
        if composed:
            lines.append(f"litmus: profile={profile_name} addopts={composed!r}")
        else:
            lines.append(f"litmus: profile={profile_name}")

    return lines


def pytest_sessionstart(session):
    """Wire prompt routing + validate DUT serial at session start."""
    _install_termination_handler()

    # If we're a test subprocess launched by ``litmus serve``, bridge
    # ``litmus.prompts.ask`` to the dialog UI over HTTP. Otherwise the
    # TTY / auto-confirm chain still applies.
    server_url = os.environ.get("LITMUS_SERVER_URL")
    if server_url:
        from litmus.api.dialogs import register_as_prompt_handler

        register_as_prompt_handler(server_url=server_url)

    config = session.config
    _resolve_and_install_slot_id(config)

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


def _resolve_and_install_slot_id(config) -> None:
    """Resolve which slot this process is running against, install on ContextVar.

    Resolution chain:

    1. **Worker child** (``_LITMUS_SLOT_ID`` env var set by orchestrator)
       — env wins. ``--slot`` on the worker's invocation is a usage
       error (operator confusion: orchestrator already chose).
    2. **Operator-set** ``--slot=N`` — validated against the resolved
       fixture's slot list when one exists; an unknown slot is a usage
       error so typos surface immediately.
    3. **Orchestrator parent** (multi-slot fixture, no ``--slot``,
       this process is *about to* dispatch per-slot children) —
       leave the ContextVar at ``None``. The orchestrator parent
       never runs tests itself; each child carries its own slot_id
       via ``_LITMUS_SLOT_ID`` env var.
    4. **No fixture / single-slot fixture** — leave the ContextVar at
       ``None``; the run row's ``slot_id`` column reads as null.
    """
    env_slot_id = os.environ.get("_LITMUS_SLOT_ID")
    cli_slot = config.getoption("--slot")

    if env_slot_id:
        if cli_slot:
            raise pytest.UsageError(
                "--slot is for single-process runs; this process was spawned "
                "by the multi-slot orchestrator (saw _LITMUS_SLOT_ID env var). "
                "Use --dut-serials at the orchestrator level instead."
            )
        set_current_slot_id(env_slot_id)
        return

    fixture_slots = _resolved_fixture_slot_ids(config)

    if cli_slot:
        if fixture_slots and cli_slot not in fixture_slots:
            raise pytest.UsageError(
                f"--slot={cli_slot!r} not in fixture's slot list "
                f"(known: {', '.join(fixture_slots)})."
            )
        set_current_slot_id(cli_slot)
        return
    # Multi-slot fixture without --slot: this is the orchestrator
    # parent. It dispatches per-slot children that each carry their
    # own slot_id via env var; the parent never emits a RunStarted
    # of its own, so leaving the ContextVar at None is correct.


def _resolved_fixture_slot_ids(config) -> list[str]:
    """Return ordered slot ids from the resolved fixture, or ``[]``.

    Returns ``[]`` for single-slot fixtures, missing fixtures, or load
    errors — callers treat any of those as "no slot validation
    available," matching the pre-fixture / bringup-tier flow. Errors
    here must not block test collection; the normal config-loading
    path will surface a real error if one exists.
    """
    from litmus.store import load_fixture

    fixture_path = find_fixture_file(config)
    if fixture_path is None:
        return []
    try:
        fixture_config = load_fixture(fixture_path)
    except (ValidationError, yaml.YAMLError, OSError, ValueError):
        return []
    if not fixture_config.is_multi_slot or not fixture_config.slots:
        return []
    return list(fixture_config.slots.keys())


def pytest_collection_modifyitems(config, items: list[pytest.Item]) -> None:
    """Apply active-profile markers/filters, then capture the item list.

    Three passes:

    1. **Profile application** (only when ``--test-profile`` is set):
       inject markers for matching node-ids via ``item.add_marker`` and
       compose profile ``keyword``/``markexpr`` filters with any CLI
       ``-k`` / ``-m`` already present (AND-composed — CLI wins on
       conflict since its expression is appended last).

    2. **Class-level-sweep reordering**: pytest's natural collection order
       for a class-level ``litmus_sweeps`` marker is method-first (all
       conditions of warmup, then all of efficiency...). The intended
       hardware-test order is condition-first (full sequence per condition).
       Reorder so the class sequence runs once per condition.

    3. **Snapshot** every collected item into ``_collected_items`` with
       sequence-relative ``step_index``, ``vector_index``, and
       ``vector_count_planned`` so the step manifest can report not-started
       steps and detect unrun sweep variants after execution.

    The snapshot captures markers **after** profile injection, so the
    manifest reflects the effective marker set.
    """
    _apply_cascade_to_items(items)
    _translate_retry_markers(items)
    _stash_sweep_dimensions(items)
    _reorder_class_sweep_items(items)

    collected = _build_collected_items(items)
    set_collected_items(collected)

    _warn_unmatched_profile_keys(items)
    _warn_method_mocks_in_non_dev_phase(items, config)


def _extract_sweep_param_names(marker: pytest.Mark) -> set[str]:
    """Return the parameter names declared by one ``litmus_sweeps`` marker.

    The inline form is ``litmus_sweeps([{argname: argvalues, ...}, ...])``
    — args[0] is a list of axis-group dicts, each dict's keys are the
    parametrize argnames. The keyword form is ``litmus_sweeps(argname=values)``
    — kwargs supply the names directly. We accept both shapes.
    """
    names: set[str] = set()
    if marker.args:
        payload = marker.args[0]
        if isinstance(payload, list):
            for entry in payload:
                if isinstance(entry, dict):
                    names.update(k for k in entry.keys() if not k.startswith("_"))
                elif isinstance(entry, SweepEntry):
                    names.update(entry.root.keys())
    names.update(k for k in marker.kwargs.keys() if not k.startswith("_"))
    return names


# Stash key for per-item sweep-dimension resolution. Populated during
# ``pytest_collection_modifyitems`` and read by both ``_reorder_class_sweep_items``
# (sort key) and ``_ensure_class_container`` (boundary detection).
_SWEEP_DIMS_KEY: pytest.StashKey[tuple[frozenset[str], frozenset[str]]] = pytest.StashKey()


def _resolve_sweep_dimensions(
    item: pytest.Item,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(outer_param_names, inner_param_names)`` for an item.

    Outer params come from ``litmus_sweeps`` markers attached to the
    enclosing class node — they define the sequence iterations a class
    container should split into. Inner params are everything else in
    ``callspec.params`` (method-level ``litmus_sweeps``, any
    ``pytest.mark.parametrize`` at either level, ``vectors``-fixture
    expansions).

    ``inner`` is computed by subtraction: ``inner = all_callspec_params - outer``.
    That makes the helper resilient to unknown marker sources — anything
    not explicitly recognized as outer falls through to inner.
    """
    callspec = getattr(item, "callspec", None)
    callspec_params: dict[str, Any] = (callspec.params or {}) if callspec is not None else {}
    all_params = frozenset(callspec_params.keys())

    outer: set[str] = set()
    cls = getattr(item, "cls", None)
    if cls is not None:
        parent = getattr(item, "parent", None)
        while parent is not None:
            if getattr(parent, "obj", None) is cls:
                for m in getattr(parent, "own_markers", []):
                    if m.name == "litmus_sweeps":
                        outer.update(_extract_sweep_param_names(m))
                break
            parent = getattr(parent, "parent", None)

    outer_frozen = frozenset(outer) & all_params
    inner_frozen = all_params - outer_frozen
    return outer_frozen, inner_frozen


def _stash_sweep_dimensions(items: list[pytest.Item]) -> None:
    """Populate ``_SWEEP_DIMS_KEY`` on every item.

    Called once during ``pytest_collection_modifyitems`` so downstream
    readers (sort key, container boundary detection) hit a cached value
    instead of re-walking the parent tree.
    """
    for item in items:
        item.stash[_SWEEP_DIMS_KEY] = _resolve_sweep_dimensions(item)


def _outer_values_for(item: pytest.Item) -> tuple[tuple[str, Any], ...]:
    """Return the sorted ``(name, value)`` tuple of outer-dim params for an item.

    Reads from the stashed ``_SWEEP_DIMS_KEY`` if present; otherwise
    falls back to fresh resolution (e.g., for items synthesized after
    ``pytest_collection_modifyitems``).
    """
    if _SWEEP_DIMS_KEY in item.stash:
        outer_names = item.stash[_SWEEP_DIMS_KEY][0]
    else:
        outer_names = _resolve_sweep_dimensions(item)[0]
    callspec = getattr(item, "callspec", None)
    params: dict[str, Any] = (callspec.params or {}) if callspec is not None else {}
    return tuple(sorted((name, params[name]) for name in outer_names if name in params))


def _has_class_level_sweep(item: pytest.Item) -> bool:
    """True iff the item has any class-level ``litmus_sweeps`` parameter.

    Thin wrapper around :func:`_resolve_sweep_dimensions` — reorder
    applies whenever there is at least one outer-dim parameter, so the
    full class sequence runs once per condition.
    """
    if _SWEEP_DIMS_KEY in item.stash:
        outer = item.stash[_SWEEP_DIMS_KEY][0]
    else:
        outer = _resolve_sweep_dimensions(item)[0]
    return bool(outer)


def _reorder_class_sweep_items(items: list[pytest.Item]) -> None:
    """Reorder class-level-sweep items so the full sequence runs per condition.

    pytest's default for a class-level parametrize / ``litmus_sweeps`` marker:
    ``warmup[0], warmup[1], efficiency[0], efficiency[1]`` (method-first).
    Hardware-test intent: ``warmup[0], efficiency[0], warmup[1], efficiency[1]``
    (condition-first).

    Walks the items list looking for runs of class-level-sweep items sharing
    the same class object. Within each run, re-sorts by
    ``(callspec.indices, method_definition_order)``. Items outside such runs
    (root-level, method-level sweeps, no-sweep) are untouched.
    """
    i = 0
    while i < len(items):
        if not _has_class_level_sweep(items[i]):
            i += 1
            continue
        # Collect a contiguous run from the same class.
        cls = items[i].cls  # type: ignore[attr-defined]
        j = i
        while (
            j < len(items)
            and getattr(items[j], "cls", None) is cls
            and _has_class_level_sweep(items[j])
        ):
            j += 1
        group = items[i:j]
        # Method definition order = first appearance of each originalname in
        # the group (pytest collects methods in definition order, then expands
        # parametrize per method).
        method_order: dict[str, int] = {}
        for item in group:
            name = getattr(item, "originalname", item.name)
            if name not in method_order:
                method_order[name] = len(method_order)

        def _sort_key(item: pytest.Item) -> tuple:
            # Two-level sort: outer-dim VALUES (class-level litmus_sweeps
            # params) establish the iteration buckets; method-definition-order
            # keeps the sequence shape A/B/C within each bucket; inner-dim
            # values unroll method-level sweeps within each method, preserving
            # pytest's natural parametrize order.
            #
            # ``callspec.indices`` cannot be trusted here — when litmus_sweeps
            # lowers via multiple ``metafunc.parametrize`` calls, pytest
            # accumulates indices across axes (every axis sees the same
            # 0..N-1 counter), so two different parameters with the same
            # callspec.indices value can actually have different argvalues.
            # Sort on the actual ``params`` values instead; they are the
            # ground truth and the values that ``inputs`` will carry in
            # the emitted StepStarted events.
            outer_names = item.stash[_SWEEP_DIMS_KEY][0]
            callspec = getattr(item, "callspec", None)
            params: dict[str, Any] = dict((callspec.params or {}) if callspec is not None else {})
            outer_vals = tuple(sorted((n, params[n]) for n in outer_names if n in params))
            inner_vals = tuple(sorted((n, v) for n, v in params.items() if n not in outer_names))
            method_pos = method_order.get(getattr(item, "originalname", item.name), 0)
            return (outer_vals, method_pos, inner_vals)

        items[i:j] = sorted(group, key=_sort_key)
        i = j


def _build_collected_items(items: list[pytest.Item]) -> list[CollectedItem]:
    """Build the manifest with sequence-relative indices and planned counts.

    Identity-tuple extraction is pytest-specific (lives here); the
    sequence-relative ``step_index`` / ``vector_index`` / planned-count
    algorithm is runner-neutral (in :mod:`litmus.data._collection_indices`)
    so the OpenHTF / unittest adapters can reuse it.
    """
    keys: list[StepKey] = []
    func_names: list[str] = []
    for item in items:
        parts = item.nodeid.rsplit("::", 1)
        func_name = parts[-1] if len(parts) > 1 else item.name
        func_names.append(func_name)
        func = getattr(item, "function", None)
        cls = getattr(item, "cls", None)
        mod = getattr(item, "module", None)
        original = getattr(item, "originalname", func_name)
        keys.append(
            (
                mod.__name__ if mod else "",
                cls.__name__ if cls else "",
                func.__name__ if func is not None else original,
            )
        )

    indices = assign_indices(keys)

    collected: list[CollectedItem] = []
    for item, func_name, (step_idx, vec_idx, planned) in zip(
        items, func_names, indices, strict=True
    ):
        cls = getattr(item, "cls", None)
        mod = getattr(item, "module", None)

        # step_path mirrors what ``logger.start_step`` records for the
        # executed StepStarted — ``ClassName/function_name`` for class-
        # nested methods, plain ``function_name`` for module-level.
        # ``func_name`` here is parsed from the nodeid and INCLUDES the
        # parametrize ``[N]`` suffix, which the logger does NOT use
        # (the logger reads ``func.__name__``).  Strip the suffix so the
        # manifest and event streams share one step_path per logical
        # step and the materializer's GROUP BY ``(step_path, vector_index)``
        # folds the parametrize variants onto a single step row.
        func = getattr(item, "function", None)
        logical_name = func.__name__ if func is not None else func_name
        if cls is not None:
            step_path = f"{cls.__name__}/{logical_name}"
            parent_path = cls.__name__
        else:
            step_path = logical_name
            parent_path = ""

        collected.append(
            CollectedItem(
                node_id=item.nodeid,
                file=str(item.path) if hasattr(item, "path") else None,
                module=mod.__name__ if mod else None,
                class_name=cls.__name__ if cls else None,
                function=func_name,
                markers=join_marker_names(item.iter_markers(), sort=True),
                step_path=step_path,
                parent_path=parent_path,
                step_index=step_idx,
                vector_index=vec_idx,
                vector_count_planned=planned,
            )
        )
    return collected


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


def _warn_method_mocks_in_non_dev_phase(items: list[pytest.Item], config: pytest.Config) -> None:
    """Warn when method mocks are active outside ``development`` phase.

    Inline ``mocks:`` blocks (sidecar / marker / profile) override
    specific driver-method return values for a test — legitimate for
    fault-injection (OVP, OCP) where you cannot or will not produce the
    fault on real hardware, suspicious otherwise. ``--mock-instruments``
    already auto-demotes to ``development``; method mocks are split-
    intent and don't, so this warning is the audit signal. To silence
    intentionally-mocked tests in production, declare a profile that
    overrides ``mocks: []`` (cascade strips them) — that's the
    declarative path.
    """
    requested_phase = config.getoption("--test-phase") or os.environ.get("LITMUS_TEST_PHASE")
    test_phase = resolve_test_phase(requested_phase, mocks_active=mocks_active(config))
    if test_phase == "development":
        return

    flagged: list[tuple[str, list[str]]] = []
    for item in items:
        targets: list[str] = []
        seen: set[str] = set()
        for marker in item.iter_markers("litmus_mocks"):
            try:
                normalized = normalize_inline_list_payload(
                    "litmus_mocks", marker.args, dict(marker.kwargs)
                )
            except ValueError:
                continue
            for raw in normalized:
                # Coerce to the typed model so .target is always safe;
                # drop entries that fail validation (we're computing a
                # warning, not enforcing schema here).
                try:
                    entry = raw if isinstance(raw, MockEntry) else MockEntry.model_validate(raw)
                except ValueError:
                    continue
                if entry.target not in seen:
                    seen.add(entry.target)
                    targets.append(entry.target)
        if targets:
            flagged.append((item.nodeid, targets))

    if not flagged:
        return

    lines = [f"  {nodeid} → {', '.join(targets)}" for nodeid, targets in flagged]
    warnings.warn(
        f"Method mocks active in test_phase={test_phase!r}:\n"
        + "\n".join(lines)
        + "\nLegitimate for fault-injection (OVP/OCP); otherwise scrub via a "
        "profile with `mocks: []`.",
        UserWarning,
        stacklevel=1,
    )


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


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Clean up all session-scoped ContextVars and module-level state."""
    # Close any open class container before the logger is torn down so the
    # final container's StepEnded reaches the event log.
    logger_inst = get_current_logger()
    if logger_inst is not None:
        _close_open_class_container(logger_inst)

    set_active_instruments({})
    set_instrument_records({})
    set_test_node_aliases({})
    set_test_node_configs({})
    set_collected_items([])
    set_channel_store(None)
    set_event_store(None)
    set_active_profile(None)
    _STEP_JUDGMENT_INTENT.clear()


def pytest_load_initial_conftests(early_config, parser, args):
    """Apply ``profile.pytest.addopts`` and force-on the assertion-pass hook.

    ``enable_assertion_pass_hook`` is an INI option that pytest's
    assertion rewriter consults to decide whether to fire
    :func:`pytest_assertion_pass` on every passing rewritten assert.
    Litmus uses that hook to detect verdict intent (PASSED-vs-DONE
    on step exit). Without it, every clean-exit step lands as DONE
    and runs cascade to "aborted" via the parquet close-fallback.

    We force it on for every project that loads the Litmus plugin
    rather than asking each one to remember the magic line in
    ``pyproject.toml``. Set on ``early_config._inicache`` *before*
    test modules are imported and rewritten — by the time
    ``pytest_configure`` fires, the rewriter has already cached
    its decision per-module.

    **Cache invalidation**: Pytest's assertion rewriter caches the
    rewritten bytecode in ``__pycache__/`` files. The cache key
    bakes in the rewriter's input but NOT the value of
    ``enable_assertion_pass_hook`` at compile time. So if a
    project ever ran tests with the flag off (or with the Litmus
    plugin not loaded yet — possible during plugin-development
    workflows or during rebuilds), the cached ``.pyc`` files
    contain bytecode WITHOUT the hook calls injected. Subsequent
    runs reuse those stale ``.pyc``s and silently skip the hook,
    landing every test as DONE despite passing asserts. We tag the
    rewriter's bytecode hash so toggling the flag busts the cache.
    """
    _ = parser
    early_config._inicache["enable_assertion_pass_hook"] = True
    _enable_hook_in_rewriter_cache_key()
    with _profile_errors_as_usage():
        apply_profile_addopts_env(args)


_LITMUS_REWRITER_TAG = "litmus-asserthook-v1"


def _enable_hook_in_rewriter_cache_key() -> None:
    """Mix our plugin tag into pytest's assertion-rewriter cache key.

    ``_pytest.assertion.rewrite.PYTEST_TAG`` is the cache-tag string
    pytest writes into ``.pyc`` filenames in ``__pycache__/``. When
    that string changes, pytest treats existing ``.pyc``s as a
    different bytecode flavor and recompiles. We append a
    Litmus-specific suffix so any cache compiled before this plugin
    was loaded (or with the assertion-pass hook off) gets
    recompiled — matching the actually-active hook setting.

    Idempotent: a second append is skipped if the tag is already in
    place.
    """
    try:
        from _pytest.assertion import rewrite as _rewrite

        if _LITMUS_REWRITER_TAG not in _rewrite.PYTEST_TAG:
            _rewrite.PYTEST_TAG = f"{_rewrite.PYTEST_TAG}-{_LITMUS_REWRITER_TAG}"
            _rewrite.PYC_TAIL = "." + _rewrite.PYTEST_TAG + _rewrite.PYC_EXT
    except (ImportError, AttributeError):
        # If pytest internals change, fall back silently — the worst
        # case is the historical staleness bug, which is fixable by
        # clearing __pycache__ manually.
        pass


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
    group.addoption(
        "--slot",
        default=None,
        help="Physical fixture slot for this single-process run "
        "(e.g. ``slot_1``, ``slot_2``). Use this when running a single "
        "DUT against a specific position in a multi-slot fixture so the "
        "run records which slot was exercised. Multi-slot orchestration "
        "uses ``--dut-serials`` instead — supplying both is an error.",
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
        help="Station ID or YAML path. Bare id looks up "
        "``stations/<id>.yaml``; a value with ``/`` or ``.yaml``/"
        "``.yml`` is used as an explicit path. When unset, the "
        "resolver tries hostname auto-match against stations/*.yaml "
        "``hostname:`` fields, then falls back to "
        "``ProjectConfig.default_station``.",
    )
    group.addoption("--operator", default=None, help="Operator name")
    group.addoption(
        "--data-dir",
        default=project.data_dir,
        help="Directory for Parquet results (default: platform data dir)",
    )
    group.addoption(
        "--product",
        default=None,
        help="Product ID or YAML path. Bare id looks up "
        "``products/<id>.yaml``; a value with ``/`` or ``.yaml``/"
        "``.yml`` is used as an explicit path.",
    )
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
        "--fixture",
        default=None,
        help="Fixture ID or YAML path. Bare id looks up "
        "``fixtures/<id>.yaml``; a value with ``/`` or ``.yaml``/"
        "``.yml`` is used as an explicit path. When unset, the "
        "resolver tries the active profile's ``fixture:`` field, then "
        "``ProjectConfig.default_fixture``, then the single-file "
        "fallback in ``fixtures/``.",
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
        "--test-profile",
        default=os.environ.get("LITMUS_TEST_PROFILE"),
        help="Named profile from litmus.yaml `profiles:` "
        "(overrides vectors, limits, markers, and filter for the session).",
    )
    group.addoption(
        "--no-test-profile",
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


def _extract_code_identity(item: pytest.Item) -> dict[str, str | None]:
    """Extract code identity fields from a pytest.Item node.

    ``nodeid``, ``path``, ``config``, ``own_markers`` are guaranteed by
    the pytest.Item API and are accessed directly. ``module`` is a
    :class:`pytest.Function`-specific attribute; non-Function items
    (Doctest, etc.) don't have it, so it stays defensive.
    """
    cls_name, func_name = node_cls_func(item)
    mod = getattr(item, "module", None)

    rootpath = item.config.rootpath
    try:
        file = str(item.path.relative_to(rootpath))
    except ValueError:
        file = str(item.path)

    return {
        "node_id": item.nodeid,
        "function": func_name,
        "class_name": cls_name,
        "module": mod.__name__ if mod else None,
        "file": file,
        "markers": join_marker_names(item.own_markers),
    }


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Per-test setup: clear aliases/config, capture code identity, reset mocks."""
    set_current_step_aliases({})
    set_current_step_config({})

    set_current_code_identity(_extract_code_identity(item))

    for inst in get_active_instruments().values():
        if hasattr(inst, "reset_mock_state"):
            inst.reset_mock_state()


def _step_vector_for_item(
    item: pytest.Item,
) -> tuple[int | None, int | None, dict[str, Any]]:
    """Look up pre-assigned ``(step_index, vector_index, inputs)`` for an item.

    Returns ``(None, None, {})`` if the manifest doesn't know about the item
    (legacy paths, custom items collected outside ``pytest_collection_modifyitems``).
    The logger coerces the inputs dict to JSON-safe values when storing on the
    vector and emitting the events.
    """
    from litmus.execution._state import get_collected_items

    try:
        items = get_collected_items()
    except LookupError:
        return None, None, {}
    for ci in items:
        if ci.node_id == item.nodeid:
            inputs: dict[str, Any] = {}
            callspec = getattr(item, "callspec", None)
            if callspec is not None and callspec.params:
                inputs = dict(callspec.params)
            return ci.step_index, ci.vector_index, inputs
    return None, None, {}


# Attribute name used on the logger instance to track the currently-open
# class container.  Stored on the logger (not in a module-level dict) so
# id-reuse after garbage collection can't shadow a fresh logger's state,
# and so the lifecycle is naturally tied to logger lifetime.
#
# The payload is a dict snapshot of the currently-open iteration:
#   {
#       "cls":               the test class object (used for identity check),
#       "outer_values":      tuple[(name, value), ...] of the outer-dim sweep
#                            params for this iteration. Determines boundary
#                            transitions — when the next item's outer values
#                            differ, close+reopen.
#       "vector_index":      0-based iteration counter for THIS class. A class
#                            run with three voltage values produces vector_index
#                            0/1/2 across its three container events.
#       "first_step_index":  Position in ``logger.test_run.steps`` of THIS
#                            container's TestStep. Children appended after this
#                            index are the iteration's children for rollup.
#   }
_OPEN_CLASS_ATTR = "_litmus_open_class_container"
# Per-class iteration counter, scoped to one logger lifetime.  Stores
# ``{cls_name: next_vector_index}``.  Independent of the open-container
# state because counters must survive close+reopen cycles.
_CLASS_ITERATION_COUNTERS_ATTR = "_litmus_class_iteration_counters"


def _ensure_class_container(logger_inst: Any, item: pytest.Item) -> None:
    """Open a class container step on transition or iteration boundary.

    A pytest class IS a hardware-test sequence — a named, ordered group of
    steps. Logging it as a step gives ``step_path`` the hierarchical form
    ``TestPowerSequence/test_efficiency`` automatically (children push onto
    ``_step_stack``).

    Detects two kinds of boundary:

    * **Class transition** — ``item.cls`` differs from the currently-open
      container's class. Close the old container, open a new one (fresh
      iteration counter).
    * **Iteration boundary** — same class, but the outer-dim sweep
      values (from a class-level ``litmus_sweeps`` marker) differ from
      the open container's values. Close+reopen so iteration N's
      events sit under a fresh container row with the iteration's
      ``vector_index`` and outer-dim ``inputs``.

    Called before ``logger.start_step`` for the test method itself, and from
    ``pytest_runtest_makereport`` so a setup-phase failure also closes the
    container instead of leaving it dangling until session end.
    """
    cls = getattr(item, "cls", None)
    outer_values = _outer_values_for(item) if cls is not None else ()
    open_state: dict[str, Any] | None = getattr(logger_inst, _OPEN_CLASS_ATTR, None)

    if (
        open_state is not None
        and open_state["cls"] is cls
        and open_state["outer_values"] == outer_values
    ):
        return  # same class iteration — still inside; nothing to do

    if open_state is not None:
        # Cascade child step outcomes into the container before we close it
        # so the container row reflects "did anything in this iteration fail".
        _stamp_container_outcome(logger_inst, open_state)
        logger_inst.end_step()
        setattr(logger_inst, _OPEN_CLASS_ATTR, None)

    if cls is not None:
        counters: dict[str, int] | None = getattr(logger_inst, _CLASS_ITERATION_COUNTERS_ATTR, None)
        if counters is None:
            counters = {}
            setattr(logger_inst, _CLASS_ITERATION_COUNTERS_ATTR, counters)
        vi = counters.get(cls.__name__, 0)
        counters[cls.__name__] = vi + 1

        # ``first_step_index`` is the position where the container's TestStep
        # is about to be appended (``len(steps)`` before append).  Children
        # appended afterwards are this iteration's children for rollup.
        first_step_index = len(logger_inst.test_run.steps)
        logger_inst.start_step(
            cls.__name__,
            class_name=cls.__name__,
            module=getattr(cls, "__module__", None),
            inputs=dict(outer_values),
            vector_index=vi,
        )
        setattr(
            logger_inst,
            _OPEN_CLASS_ATTR,
            {
                "cls": cls,
                "outer_values": outer_values,
                "vector_index": vi,
                "first_step_index": first_step_index,
            },
        )


def _close_open_class_container(logger_inst: Any) -> None:
    """Force-close any still-open class container at session end."""
    open_state: dict[str, Any] | None = getattr(logger_inst, _OPEN_CLASS_ATTR, None)
    if open_state is None:
        return
    try:
        _stamp_container_outcome(logger_inst, open_state)
        logger_inst.end_step()
    finally:
        setattr(logger_inst, _OPEN_CLASS_ATTR, None)


def _stamp_container_outcome(logger_inst: Any, open_state: dict[str, Any]) -> None:
    """Cascade THIS iteration's child step outcomes into the container's outcome.

    A container step (a class / sequence) doesn't run measurements itself —
    its outcome is the worst outcome among ITS OWN ITERATION's children.
    Walks ``test_run.steps`` from ``first_step_index + 1`` to the end of the
    list (i.e., everything appended since the container opened), filtering
    by ``parent_path == container.step_path`` to skip nested-deeper
    descendants. Severity ladder via ``escalate_outcome``.

    Critical isolation property: when class TestSeq runs three iterations,
    iteration 1's children at indices [3..5] must NOT leak into iteration
    0's rollup walking [1..2]. The ``first_step_index`` watermark is what
    keeps these disjoint.

    No-op when the container step can't be located (defensive — the rest of
    cleanup proceeds).
    """
    first_idx = open_state["first_step_index"]
    steps = logger_inst.test_run.steps
    if first_idx >= len(steps):
        return
    container = steps[first_idx]
    container_path = container.step_path
    # Retry-aware: a child test that ran twice (litmus_retry +
    # pytest-rerunfailures) contributes only its FINAL attempt's
    # outcome to the container rollup. Industry convention — pytest-
    # rerunfailures, STDF MIR.RTST_COD, Jenkins flaky-test-handler all
    # treat the final attempt as the disposition; the prior attempts
    # stay in the step record as retest metadata.
    eligible = [s for s in steps[first_idx + 1 :] if s.parent_path == container_path]
    container.outcome = retry_aware_rollup(eligible)


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

    Stamps ``step.outcome`` from the call-phase exception (closes the
    bare-assert visibility gap — ``assert v > 0`` failures escalate the
    step to FAILED even when no measurement was recorded). On a passing
    test, also runs :func:`_audit_traceability` to report (or enforce,
    under ``--strict-traceability``) that every measurement carries the
    required traceability fields.
    """
    logger_inst = get_current_logger()
    func = getattr(item, "function", None)
    strict = bool(item.config.getoption("--strict-traceability"))

    if logger_inst is not None:
        cls = getattr(item, "cls", None)
        func_name = func.__name__ if func is not None else item.name
        # Look up pre-assigned (step_index, vector_index, inputs) from the
        # collection-time manifest so all sweep variants of the same logical
        # step share one step_index, distinguished by vector_index.
        step_idx, vec_idx, inputs = _step_vector_for_item(item)
        # Open the class container if we just transitioned to a new class.
        _ensure_class_container(logger_inst, item)
        logger_inst.start_step(
            func_name,
            function=func_name,
            module=getattr(func, "__module__", None) if func is not None else None,
            class_name=cls.__name__ if cls is not None else None,
            node_id=item.nodeid,
            step_index=step_idx,
            vector_index=vec_idx,
            inputs=inputs,
        )
        try:
            outcome_obj = yield
            _stamp_step_from_call_outcome(logger_inst, outcome_obj)
            if outcome_obj.excinfo is None:
                _audit_traceability(logger_inst, strict=strict)
        finally:
            logger_inst.end_step()
            logger_inst._step_seen_names.clear()
            logger_inst._step_seen_repeatable.clear()
        return

    yield


def _escalate_step_and_run(logger_inst: Any, step: Any, new_outcome: Outcome) -> None:
    """Cascade ``new_outcome`` into ``step.outcome`` (when present) or
    the logger's external-run-outcome accumulator.

    Called from runner-side signal hooks (``pytest_runtest_call`` for
    call-phase exceptions, ``pytest_runtest_makereport`` for setup/
    teardown failures, ``pytest_keyboard_interrupt`` for ABORTED).

    Run-level outcome is NOT stamped incrementally here. ``finalize()``
    walks ``test_run.steps`` via :func:`retry_aware_rollup` and folds
    in :attr:`TestRunLogger._external_run_outcome` to compute the final
    run outcome. The retry-aware rollup is what makes a passing retry
    correctly stamp the run as ``PASSED`` instead of the worst earlier
    attempt's outcome.

    When ``step is None`` (setup failure before the step opened, or a
    keyboard interrupt with no test running), the outcome has no step
    to attach to — it goes into ``logger._external_run_outcome`` so
    ``finalize()`` still picks it up.
    """
    if step is not None:
        step.outcome = escalate_outcome(step.outcome, new_outcome)
    else:
        logger_inst._external_run_outcome = escalate_outcome(
            logger_inst._external_run_outcome, new_outcome
        )


def _stamp_step_from_call_outcome(logger_inst: Any, outcome_obj: Any) -> None:
    """Translate the pluggy outcome of pytest's call phase into ``step.outcome``.

    Called from inside the ``pytest_runtest_call`` wrapper while the
    step is still open. The pluggy ``_Result`` carries ``excinfo`` for
    any exception raised during the test body. The mapping:

    * No exception:
        - if the step recorded any verdict intent at runtime — a
          rewritten assert that passed (via
          :func:`pytest_assertion_pass`) or a measurement with
          limits — → ``PASSED``
        - otherwise → ``DONE`` (the step ran code, recorded no
          judgment; "recorded but unjudged" is the right semantic)
    * ``Skipped`` (``pytest.skip``, ``@pytest.mark.skip``, skipif) → SKIPPED
    * ``AssertionError`` (rewritten or bare) → FAILED
    * Anything else → ERRORED

    The PASSED-vs-DONE branch consults the runtime
    ``_STEP_JUDGMENT_INTENT`` set instead of guessing from the AST.
    Accurate by construction; cross-module helpers come along for
    free if their module is registered via
    ``pytest.register_assert_rewrite()``.

    Uses ``escalate_outcome`` so a measurement-level FAILED that already
    cascaded into the step is not weakened by a later runner-side stamp.
    """
    from litmus.execution._state import get_current_step

    step = get_current_step()
    if step is None:
        return
    excinfo = outcome_obj.excinfo
    if excinfo is None:
        new_outcome = Outcome.PASSED if str(step.id) in _STEP_JUDGMENT_INTENT else Outcome.DONE
    else:
        exc_type = excinfo[0]
        if issubclass(exc_type, pytest.skip.Exception):
            new_outcome = Outcome.SKIPPED
        elif issubclass(exc_type, AssertionError):
            new_outcome = Outcome.FAILED
        else:
            new_outcome = Outcome.ERRORED
    _escalate_step_and_run(logger_inst, step, new_outcome)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: Any) -> Iterator[None]:
    """Translate setup/teardown phase failures into ``step.outcome``.

    The call-phase outcome is already handled inside the
    ``pytest_runtest_call`` wrapper. This hook covers the two phases the
    wrapper can't see:

    * ``setup`` failure → the test never ran. The step may not exist;
      the matching collected-item row stays ``planned`` unless we've
      already stamped something. We escalate any open step to ERRORED
      and rely on the manifest reconciliation for tests that never
      reached the call phase.
    * ``teardown`` failure → the step has already been closed by the
      call-phase wrapper. We escalate the *closed* step's outcome and
      the run outcome so a teardown blow-up isn't silently swallowed.
    """
    outcome_obj = yield
    if call.when == "call":
        return
    if call.excinfo is None:
        return
    logger_inst = get_current_logger()
    if logger_inst is None:
        return
    exc_type = call.excinfo.type
    if issubclass(exc_type, pytest.skip.Exception):
        # Setup-phase skip is a normal control-flow case (``skipif``,
        # session-scoped fixture skipping). Find the step opened for
        # this nodeid (if any) and stamp SKIPPED on it; otherwise let
        # the planned-row reconciliation fall through.
        new_outcome = Outcome.SKIPPED
    else:
        new_outcome = Outcome.ERRORED
    _escalate_step_and_run(
        logger_inst,
        _find_step_for_nodeid(logger_inst, item.nodeid),
        new_outcome,
    )
    _ = outcome_obj  # the wrapper returns the original outcome unmodified


def _find_step_for_nodeid(logger_inst: Any, node_id: str) -> Any | None:
    """Return the most recent step matching ``node_id`` on the active run."""
    for step in reversed(logger_inst.test_run.steps):
        if step.node_id == node_id:
            return step
    return None


def pytest_keyboard_interrupt(excinfo: Any) -> None:
    """Stamp TERMINATED on any in-flight step + the active run on Ctrl-C.

    Pytest fires this once when KeyboardInterrupt propagates out of the
    test loop. Any step still open at that point was stopped
    mid-flight; the run as a whole is terminated. We use TERMINATED
    rather than ABORTED because pytest is about to run fixture
    teardowns — instruments get safe-stated, the parquet gets
    finalized via the logger fixture's teardown. TestStand
    semantics: Terminated = stopped with cleanup; Aborted is reserved
    for the no-cleanup case.

    In orchestrator mode (the active :class:`SlotRunner` is exposed
    via ContextVar) the orchestrator forwards SIGTERM to every live
    child *before* its own teardown unwinds. Each child's installed
    SIGTERM-to-KeyboardInterrupt converter then drives the same
    cleanup chain, landing every per-slot run as ``Terminated``
    instead of orphan ``Aborted`` fallbacks.
    """
    _ = excinfo
    logger_inst = get_current_logger()
    if logger_inst is not None:
        from litmus.execution._state import get_current_step

        _escalate_step_and_run(logger_inst, get_current_step(), Outcome.TERMINATED)

    runner = get_active_slot_runner()
    if runner is not None:
        runner._propagate_termination()


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


def _entries_from_marks(marks: list[pytest.Mark]) -> list[SweepEntry]:
    """Translate a list of ``litmus_sweeps`` marks into ``SweepEntry`` objects.

    Caller controls ordering — pass marks in the order they should
    cross-product (top decorator = outer = slowest-changing).
    """
    out: list[SweepEntry] = []
    for mark in marks:
        try:
            normalized = normalize_inline_list_payload(
                "litmus_sweeps", mark.args, dict(mark.kwargs)
            )
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from exc
        for raw in normalized:
            out.append(raw if isinstance(raw, SweepEntry) else SweepEntry.model_validate(raw))
    return out


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

    Self-loop mode (``vectors`` fixture in signature): only INNER
    (function-level) parametrize calls get consumed into the matrix;
    OUTER (class-level, module-level) sweeps still pass through
    :meth:`metafunc.parametrize` so pytest fans out one item per outer
    iteration. That preserves the class-container per-iteration shape
    even when the method body uses ``vectors`` for its inner sweeps.
    """
    # Classify litmus_sweeps markers by owning node level. Function-level
    # markers (``own_markers`` on the test function) are INNER — they
    # parametrize within one logical step. Class-level + higher markers
    # are OUTER — they fan out across separate pytest items so the
    # class container can iterate per-condition.
    own_marks = [
        m for m in getattr(metafunc.definition, "own_markers", []) if m.name == "litmus_sweeps"
    ]
    parent_marks: list[pytest.Mark] = []
    parent = metafunc.definition.parent
    while parent is not None:
        for m in getattr(parent, "own_markers", []):
            if m.name == "litmus_sweeps":
                parent_marks.append(m)
        parent = getattr(parent, "parent", None)

    # Within a level, the TOP decorator is the outermost. ``own_markers``
    # lists decorators bottom-up, so reverse to put top first.
    own_marks.reverse()
    # Across levels, the highest ancestor (module > class) is outermost.
    # The walk above goes class-first then module; reverse so module-level
    # sweeps come first when present.
    parent_marks.reverse()

    outer_sweeps = _entries_from_marks(parent_marks)
    inner_sweeps = _entries_from_marks(own_marks)

    outer_calls: list[tuple[Any, list[Any], dict[str, Any]]] = []
    for entry in outer_sweeps:
        argnames, argvalues = sweep_to_parametrize_args(entry)
        outer_calls.append((argnames, argvalues, {}))

    inner_calls: list[tuple[Any, list[Any], dict[str, Any]]] = []
    for entry in inner_sweeps:
        argnames, argvalues = sweep_to_parametrize_args(entry)
        inner_calls.append((argnames, argvalues, {}))
    # Sidecar/profile parametrize is keyed by class.method, so all entries
    # are method-level → inner.
    inner_calls.extend(_cascade_parametrize_for_metafunc(metafunc))

    if "vectors" in metafunc.fixturenames:
        # Inner sweeps + function-level @parametrize feed the matrix.
        inline_rows = _consume_parametrize_markers(metafunc)
        sidecar_rows: list[dict[str, Any]] = [{}]
        for argnames, argvalues, _extra in inner_calls:
            rows = parametrize_call_rows(argnames, argvalues)
            sidecar_rows = [{**base, **row} for base in sidecar_rows for row in rows]
        if sidecar_rows == [{}]:
            full_rows = inline_rows
        elif not inline_rows:
            full_rows = sidecar_rows
        else:
            full_rows = [{**i, **s} for i in inline_rows for s in sidecar_rows]
        full_matrix = [Vector(**row, _index=i) for i, row in enumerate(full_rows)]
        node_parent = metafunc.definition.parent
        if node_parent is not None:
            matrix_map = node_parent.stash.setdefault(VECTORS_MATRIX_KEY, {})
            matrix_map[metafunc.definition.originalname] = full_matrix
        # Outer sweeps STILL parametrize at the pytest level so the
        # class container fans out one iteration per outer condition.
        for argnames, argvalues, extra in outer_calls:
            normalized_values = _normalize_parametrize_argvalues(argvalues)
            metafunc.parametrize(argnames, normalized_values, **extra)
        return

    for argnames, argvalues, extra in outer_calls + inner_calls:
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
    """Convert a single @pytest.mark.parametrize marker into row dicts.

    Thin wrapper that unpacks the marker's args and delegates to
    :func:`litmus.pytest_plugin.sweeps.parametrize_call_rows` (the
    canonical row-shape converter). Translates the helper's
    ``ValueError`` into ``pytest.UsageError`` for clean reporting.
    """
    if len(mark.args) < 2:
        return []
    try:
        return parametrize_call_rows(mark.args[0], mark.args[1])
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
