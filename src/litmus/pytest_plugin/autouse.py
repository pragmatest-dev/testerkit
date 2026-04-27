"""Autouse fixtures — internal plumbing the test author never names directly.

Every fixture in this module is ``@pytest.fixture(autouse=True)``: pytest
runs them automatically for every test in scope without the test
function having to ask. They wire per-test state — pushing parametrize
params into the shared :class:`Context`, merging Litmus-marker payloads
into ContextVar state, building :class:`ConnectionIterator` for the
``ctx.connections`` iteration, and installing mocks declared via
``litmus_mocks``.

The underscore prefix on each fixture name reinforces "infrastructure,
not user-facing." Test authors interact through the public surface
(:func:`logger`, :func:`verify`, :func:`context`, …) defined in
:mod:`litmus.pytest_plugin.fixtures`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from litmus.execution._state import (
    get_active_spec_context,
    set_active_limits,
    set_active_test_characteristic,
    set_active_vector_params,
    set_current_logger,
)
from litmus.execution.connections import (
    ConnectionIterator,
    ConnectionResolutionError,
    resolve_test_connections,
)
from litmus.execution.harness import Context
from litmus.execution.logger import TestRunLogger
from litmus.execution.mocks import install_mocks
from litmus.models.test_config import MeasurementLimitConfig, MockEntry
from litmus.pytest_plugin.helpers import safe_get_session_fixture
from litmus.pytest_plugin.markers import (
    extract_specs_characteristic,
    normalize_inline_list_payload,
)

# Stash key for chaining ``Context._prev`` across parametrize cases of
# the same ``(parent_node, method)``. Lives at module level so the
# fixture can read/write it on the parent node's stash without
# re-creating the key per test.
_PREV_STASH_KEY: pytest.StashKey[dict[str, Context]] = pytest.StashKey()


def _extract_specs_characteristic(node: pytest.Item) -> str | None:
    """Pytest adapter — collect ``litmus_specs`` payloads, delegate to runner-neutral helper."""
    marker = next(iter(node.iter_markers("litmus_specs")), None)
    if marker is None:
        return None
    if marker.kwargs:
        raise pytest.UsageError(
            "litmus_specs does not accept keyword arguments; pass "
            "characteristic IDs as positional strings or a single list."
        )
    try:
        return extract_specs_characteristic([marker.args])
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc


@pytest.fixture(autouse=True)
def _reseat_current_logger(logger: TestRunLogger) -> None:
    """Re-install the session logger into the ContextVar for every test.

    Pytester-based tests run an inner pytest session whose own teardown
    clears ``set_current_logger(None)`` — and because ContextVars are
    process-wide, that leaks into the outer session. Re-seating on every
    test keeps ``get_current_logger()`` correct regardless.
    """
    set_current_logger(logger)


@pytest.fixture(autouse=True)
def _route_cleanup(request: pytest.FixtureRequest) -> Iterator[None]:
    """Per-test cleanup for lazy-activated routes (pins[] pattern).

    Ensures all routes activated via RoutedProxy during a test are
    deactivated before the next test runs.
    """
    yield
    rm = safe_get_session_fixture(request, "_route_manager")
    if rm is not None:
        rm.deactivate_all()


@pytest.fixture(autouse=True)
def _litmus_push_params(request: pytest.FixtureRequest) -> Iterator[None]:
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


@pytest.fixture(autouse=True)
def _litmus_push_limits(
    request: pytest.FixtureRequest,
    _litmus_push_params: None,
) -> Iterator[None]:
    """Merge ``litmus_limits`` markers into typed configs and push them into state.

    Markers are attached to the item during collection from four sources,
    in merge order (later wins): sidecar file-level → class-scope →
    per-test → inline ``@pytest.mark.litmus_limits`` decorators →
    profile chain.

    Each marker's kwargs is ``{measurement_name: <limit-spec>}``.
    Sidecar/profile markers carry typed :class:`MeasurementLimitConfig`
    instances directly (Pydantic-validated at YAML load); inline
    decorators carry raw dicts which we coerce here. The merged
    ``dict[str, MeasurementLimitConfig]`` is pushed onto
    ``_active_limits_var`` along with the test's characteristic
    binding (from ``litmus_specs``), and resolution to a concrete
    :class:`Limit` happens at measurement time via
    :func:`sidecar.resolve_limit`.
    """
    set_active_test_characteristic(_extract_specs_characteristic(request.node))

    # Walk listchain root-to-leaf so later (more-specific) markers win
    # via ``update``. Within a node, ``own_markers`` preserves insertion
    # order — file-level sidecar markers are added before per-test ones,
    # so per-test correctly overrides.
    merged: dict[str, MeasurementLimitConfig] = {}
    for node in request.node.listchain():
        for marker in node.own_markers:
            if marker.name != "litmus_limits":
                continue
            for name, spec in marker.kwargs.items():
                if isinstance(spec, MeasurementLimitConfig):
                    merged[name] = spec
                else:
                    # Inline decorator passed a raw dict — Pydantic validates.
                    merged[name] = MeasurementLimitConfig.model_validate(spec)
    set_active_limits(merged)
    try:
        yield
    finally:
        set_active_limits({})
        set_active_test_characteristic(None)


@pytest.fixture(autouse=True)
def _litmus_resolve_connections(request: pytest.FixtureRequest) -> Iterator[None]:
    """Build :class:`ConnectionIterator` on ``ctx.connections`` from spec/connections markers.

    Reads ``litmus_specs`` (characteristic context) and
    ``litmus_connections`` (explicit name list / channel selectors).
    The two compose: connections narrows the spec's pin set, and
    iteration follows the user-listed order. If the test body declares
    connections but never iterates ``ctx.connections``, the test fails —
    silent skips are worse than errors.
    """
    test_char = _extract_specs_characteristic(request.node)
    conn_marker = next(iter(request.node.iter_markers("litmus_connections")), None)
    if test_char is None and conn_marker is None:
        yield
        return

    spec_ctx = get_active_spec_context()
    fixture_cfg = safe_get_session_fixture(request, "fixture_config")
    try:
        connections = resolve_test_connections(test_char, conn_marker, spec_ctx, fixture_cfg)
    except ConnectionResolutionError as exc:
        raise pytest.UsageError(str(exc)) from exc

    ctx: Context = request.getfixturevalue("context")
    iterator = ConnectionIterator(connections)
    ctx.connections = iterator

    try:
        yield
    except BaseException:
        iterator.cleanup()
        raise
    iterator.cleanup()
    if connections and not iterator.started:
        raise AssertionError(
            f"Test {request.node.nodeid} declared connections but did "
            "not iterate ctx.connections. Declared connections must be consumed by "
            "the test body."
        )


@pytest.fixture(autouse=True)
def _litmus_apply_mocks(request: pytest.FixtureRequest) -> Iterator[None]:
    """Install mocks declared via ``litmus_mocks`` markers.

    The marker payload is a list of :class:`MockEntry` (sidecar / profile
    cascade) or raw dicts (inline decorators). Each entry carries
    ``target: <fixture>.<attr>`` plus any kwargs accepted by
    :func:`unittest.mock.patch.object` — ``return_value``,
    ``side_effect``, ``wraps``, ``spec``, ``spec_set``, ``autospec``,
    ``new_callable``, etc. All keys except ``target`` are forwarded
    verbatim, so the surface tracks the stdlib's ``mock`` documentation.
    Pydantic validates target shape and required fields at YAML load
    (sidecar / profile) and at marker decode time (inline). Stacking
    multiple markers concatenates their lists; later entries with the
    same target overwrite earlier ones (file → class → test → profile).
    ``--no-test-mocks`` bypasses all patching.
    """
    if request.config.getoption("--no-test-mocks", default=False):
        yield
        return

    # Walk listchain root-to-leaf so more-specific markers' entries
    # overwrite earlier ones (by target) in ``by_target`` below. Within
    # a node, ``own_markers`` preserves insertion order.
    by_target: dict[str, MockEntry] = {}
    for node in request.node.listchain():
        for marker in node.own_markers:
            if marker.name != "litmus_mocks":
                continue
            try:
                normalized = normalize_inline_list_payload(
                    "litmus_mocks", marker.args, dict(marker.kwargs)
                )
            except ValueError as exc:
                raise pytest.UsageError(str(exc)) from exc
            for raw in normalized:
                entry = raw if isinstance(raw, MockEntry) else MockEntry.model_validate(raw)
                by_target[entry.target] = entry

    if not by_target:
        yield
        return

    install_mocks(
        by_target,
        resolve_fixture=request.getfixturevalue,
        register_cleanup=request.addfinalizer,
        fixture_lookup_error=pytest.FixtureLookupError,
    )

    yield


# Pytest finds autouse fixtures by inspecting the plugin module's
# namespace. ``__init__.py`` imports the names below; this ``__all__``
# tells ruff they're intentional re-exports rather than dead imports.
__all__ = [
    "_litmus_apply_mocks",
    "_litmus_push_limits",
    "_litmus_push_params",
    "_litmus_resolve_connections",
    "_reseat_current_logger",
    "_route_cleanup",
]
