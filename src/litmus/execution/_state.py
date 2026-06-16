"""ContextVar-backed mutable state for the execution module.

ALL mutable module state lives here so the pytest plugin and its
collaborators (logger, harness, accessors, fixtures) share one source
of truth without circular imports.

Four ContextVar getter patterns are used:

1. **Create-and-store** (session-scoped dicts): first call creates a
   dict, stores it in the ContextVar, returns it. Callers mutate the
   returned dict in place. Cleanup sets the var to a fresh empty dict.
2. **Return throwaway empty** (per-test dicts): first call returns a
   new empty dict WITHOUT storing it. Stale state cannot leak across
   tests — each test gets its own empty dict that is never persisted.
   The plugin's autouse fixtures set the dict at test start and clear
   on teardown.
3. **Return None** (session singletons): the ContextVar holds a single
   object (or None) installed once per session by a setter. Getter
   returns None if not set.
4. **Stack-like (push/pop with token)**: nested scopes (e.g. a step
   that contains sub-steps) need to restore the prior value on exit,
   not blank it. Setters return a :class:`Token` the caller stashes
   and passes back to the matching ``reset_*`` accessor. ``current_step``
   and ``current_vector`` use this shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any
from uuid import UUID

from litmus.data.models import CollectedItem
from litmus.models.instrument import InstrumentRecord
from litmus.models.project import ProfileConfig
from litmus.models.test_config import FixtureConnection

if TYPE_CHECKING:
    from litmus.execution.logger import RunScope
    from litmus.models.station import StationConfig

# Step/vector — stack-like, push/pop with token. Used by logger.py + harness.py.
_current_step_var: ContextVar[Any] = ContextVar("current_step", default=None)
_current_vector_var: ContextVar[Any] = ContextVar("current_vector", default=None)
# The active ``Context`` (harness.Context, not to be confused with PartContext).
# Pushed by the harness around vector execution; consumed by ``observer.read`` to
# stamp channel URIs onto the active vector's ``out_*`` columns (item 5).
_current_context_var: ContextVar[Any] = ContextVar("current_context", default=None)

# Active RunScope — session singleton, set once per test by the runner.
_current_run_scope_var: ContextVar[RunScope | None] = ContextVar("_current_run_scope", default=None)

_active_instruments_var: ContextVar[dict[str, Any]] = ContextVar("_active_instruments")
_instrument_records_var: ContextVar[dict[str, InstrumentRecord]] = ContextVar("_instrument_records")
_current_step_aliases_var: ContextVar[dict[str, str]] = ContextVar("_current_step_aliases")
_current_step_config_var: ContextVar[dict[str, Any]] = ContextVar("_current_step_config")
_active_part_context_var: ContextVar[Any] = ContextVar("_active_part_context")
_active_station_config_var: ContextVar[Any] = ContextVar("_active_station_config")
_test_node_aliases_var: ContextVar[dict[str, dict[str, str]]] = ContextVar("_test_node_aliases")
_test_node_configs_var: ContextVar[dict[str, dict[str, Any]]] = ContextVar("_test_node_configs")
_channel_store_var: ContextVar[Any] = ContextVar("_channel_store")
_collected_items_var: ContextVar[list[CollectedItem]] = ContextVar("_collected_items")
_current_code_identity_var: ContextVar[dict[str, str | None]] = ContextVar("_current_code_identity")
_event_store_var: ContextVar[Any] = ContextVar("_event_store")
_active_limits_var: ContextVar[dict[str, Any]] = ContextVar("_active_limits")
_active_test_characteristics_var: ContextVar[list[str]] = ContextVar("_active_test_characteristics")
_active_characteristic_var: ContextVar[str | None] = ContextVar("_active_characteristic")
_active_profile_var: ContextVar[ProfileConfig | None] = ContextVar("_active_profile")
_active_profile_name_var: ContextVar[str | None] = ContextVar("_active_profile_name", default=None)
_active_facets_var: ContextVar[dict[str, str]] = ContextVar("_active_facets")
_session_inputs_var: ContextVar[dict[str, str]] = ContextVar("_session_inputs")
_active_vector_params_var: ContextVar[dict[str, Any]] = ContextVar("_active_vector_params")
_active_vector_index_var: ContextVar[int] = ContextVar("_active_vector_index")
_active_connection_var: ContextVar[FixtureConnection | None] = ContextVar("_active_connection")
_slot_id_var: ContextVar[str | None] = ContextVar("_slot_id", default=None)
_active_slot_runner_var: ContextVar[Any] = ContextVar("_active_slot_runner", default=None)


# --- Session-scoped getters (create-and-store on first access) ---


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


def get_active_part_context() -> Any:
    """Return None if not set."""
    try:
        return _active_part_context_var.get()
    except LookupError:
        return None


def get_active_station_config() -> StationConfig | None:
    """Return None if not set (bringup tier or no station YAML loaded)."""
    try:
        return _active_station_config_var.get()
    except LookupError:
        return None


# --- Stack-like (push/pop) accessors ---


def get_current_step() -> Any:
    """Return the active :class:`TestStep`, or ``None`` outside a step."""
    return _current_step_var.get()


def push_current_step(step: Any) -> Token[Any]:
    """Set the active step; returns a token for :func:`reset_current_step`."""
    return _current_step_var.set(step)


def reset_current_step(token: Token[Any]) -> None:
    """Restore the prior active step using a token from :func:`push_current_step`."""
    _current_step_var.reset(token)


def get_current_vector() -> Any:
    """Return the active :class:`TestVector`, or ``None`` outside a vector."""
    return _current_vector_var.get()


def push_current_vector(vector: Any) -> Token[Any]:
    """Set the active vector; returns a token for :func:`reset_current_vector`."""
    return _current_vector_var.set(vector)


def reset_current_vector(token: Token[Any]) -> None:
    """Restore the prior active vector using a token from :func:`push_current_vector`."""
    _current_vector_var.reset(token)


def get_current_context() -> Any:
    """Return the active harness :class:`Context`, or ``None`` outside one."""
    return _current_context_var.get()


def push_current_context(context: Any) -> Token[Any]:
    """Set the active context; returns a token for :func:`reset_current_context`."""
    return _current_context_var.set(context)


def reset_current_context(token: Token[Any]) -> None:
    """Restore the prior active context using a token from :func:`push_current_context`."""
    _current_context_var.reset(token)


def get_current_run_scope() -> RunScope | None:
    """Return the active :class:`RunScope`, or ``None`` if no run is in progress."""
    return _current_run_scope_var.get()


def set_current_run_scope(run_scope: RunScope | None) -> None:
    """Set the active :class:`RunScope`. Returns ``None``."""
    _current_run_scope_var.set(run_scope)


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


def set_active_part_context(value: Any) -> None:
    """Set value. Returns None."""
    _active_part_context_var.set(value)


def set_active_station_config(value: StationConfig | None) -> None:
    """Set value. Returns None."""
    _active_station_config_var.set(value)


def set_test_node_aliases(value: dict[str, dict[str, str]]) -> None:
    """Set value. Returns None."""
    _test_node_aliases_var.set(value)


def set_test_node_configs(value: dict[str, dict[str, Any]]) -> None:
    """Set value. Returns None."""
    _test_node_configs_var.set(value)


def get_channel_store() -> Any:
    """Return None if not set."""
    try:
        return _channel_store_var.get()
    except LookupError:
        return None


def set_channel_store(value: Any) -> None:
    """Set value. Returns None.

    Blanket set; used only by the session-end safety net (pytest_sessionfinish)
    and benchmarks. Producers use :func:`push_channel_store` /
    :func:`reset_channel_store` so a nested session restores the outer binding.
    """
    _channel_store_var.set(value)


def push_channel_store(value: Any) -> Token[Any]:
    """Set the active ChannelStore; returns a token for :func:`reset_channel_store`.

    Token discipline (not blanket ``set``) so a nested session's close restores
    the outer session's store instead of clobbering it to ``None``.
    """
    return _channel_store_var.set(value)


def reset_channel_store(token: Token[Any]) -> None:
    """Restore the prior ChannelStore using a token from :func:`push_channel_store`."""
    _channel_store_var.reset(token)


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


def get_active_test_characteristics() -> list[str]:
    """Return the chars declared in scope for the active test, or ``[]``.

    Set per-test by :func:`set_active_test_characteristics` from the
    ``litmus_characteristics`` marker (or, when the marker is absent, the
    union derived from per-limit ``characteristic:`` values). The list
    drives ``ctx.connections`` resolution and bounds which chars can be
    referenced by per-limit ``characteristic:`` fields.
    """
    try:
        return _active_test_characteristics_var.get()
    except LookupError:
        return []


def set_active_test_characteristics(value: list[str]) -> None:
    """Set the active test's characteristic list. Returns None."""
    _active_test_characteristics_var.set(value)


def get_active_characteristic() -> str | None:
    """Return the currently iterating characteristic, or ``None``.

    Pushed by :class:`ConnectionIterator` as the test body iterates
    ``ctx.connections`` (per-connection char) or
    ``ctx.connections.for_characteristic(...)`` (explicit scope). Read
    by limit resolution and ``logger.measure`` to stamp
    ``characteristic_id`` on the row when no per-limit ``characteristic:``
    is set and the explicit ``verify(characteristic=...)`` override is
    not used.
    """
    try:
        return _active_characteristic_var.get()
    except LookupError:
        return None


def push_active_characteristic(value: str | None) -> Token[str | None]:
    """Push the active characteristic; returns a token for :func:`reset_active_characteristic`."""
    return _active_characteristic_var.set(value)


def reset_active_characteristic(token: Token[str | None]) -> None:
    """Restore the prior active characteristic.

    Pass a token from :func:`push_active_characteristic`.
    """
    _active_characteristic_var.reset(token)


@contextmanager
def pushed_active_characteristic(value: str | None) -> Iterator[None]:
    """Context-managed push/pop of the active characteristic.

    Use when scoping a single block (e.g. inside ``verify``) — pushes
    on enter, pops on exit even if the body raises. Equivalent to:

        token = push_active_characteristic(value)
        try:
            ...
        finally:
            reset_active_characteristic(token)
    """
    token = push_active_characteristic(value)
    try:
        yield
    finally:
        reset_active_characteristic(token)


def get_active_profile() -> ProfileConfig | None:
    """Return the active ``ProfileConfig`` selected via ``--test-profile``.

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


def get_active_profile_name() -> str | None:
    """Return the source-name of the active profile (the dict key in
    ``project.profiles``), or ``None`` if no profile is active.

    Separate from ``get_active_profile()`` because ``ProfileConfig``
    itself doesn't carry the name — profile names live on the
    ``project.profiles`` dict key, lost when we hand the value off
    to ``set_active_profile``. The pytest banner + diagnostics need
    the human-facing name, not the merged-config object.
    """
    try:
        return _active_profile_name_var.get()
    except LookupError:
        return None


def set_active_profile_name(value: str | None) -> None:
    """Set the active profile name. Returns None."""
    _active_profile_name_var.set(value)


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


def get_session_inputs() -> dict[str, str]:
    """Return resolved ``required_inputs`` for the active session.

    Populated at session start from CLI flags / env vars / operator
    prompts per the project's ``required_inputs`` declaration.
    Stamped onto each run record for reproducibility.
    """
    try:
        return _session_inputs_var.get()
    except LookupError:
        return {}


def set_session_inputs(value: dict[str, str]) -> None:
    """Set the session-inputs dict. Returns None."""
    _session_inputs_var.set(value)


def get_active_vector_params() -> dict[str, Any]:
    """Return the active test's vector params (parametrize + markers + sidecar).

    Returns throwaway empty; never stored. Populated by
    ``_litmus_push_params`` at test start so ``RunScope.measure``
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


def get_active_connection() -> FixtureConnection | None:
    """Return the currently active :class:`FixtureConnection` or ``None``.

    Pushed/popped by :class:`ConnectionIterator` as a test body iterates
    ``ctx.connections``. Read by :func:`_auto_traceability` to stamp pin /
    channel / terminal / net on each measurement row and by
    :meth:`FixtureManager.route` so driver fixtures route without
    seeing pin names.
    """
    try:
        return _active_connection_var.get()
    except LookupError:
        return None


def push_active_connection(
    value: FixtureConnection | None,
) -> Token[FixtureConnection | None]:
    """Set the active connection; returns a token for :func:`reset_active_connection`."""
    return _active_connection_var.set(value)


def reset_active_connection(token: Token[FixtureConnection | None]) -> None:
    """Restore the prior active connection using a token from :func:`push_active_connection`."""
    _active_connection_var.reset(token)


def get_event_store() -> Any:
    """Return the session EventStore, or None if not set."""
    try:
        return _event_store_var.get()
    except LookupError:
        return None


def set_event_store(value: Any) -> None:
    """Set the session EventStore. Returns None.

    Blanket set; used only by the session-end safety net (pytest_sessionfinish).
    The session primitive uses :func:`push_event_store` / :func:`reset_event_store`
    so a nested session restores the outer binding instead of clobbering it.
    """
    _event_store_var.set(value)


def push_event_store(value: Any) -> Token[Any]:
    """Set the active EventStore; returns a token for :func:`reset_event_store`.

    Token discipline (not blanket ``set``) so a nested session's close restores
    the outer session's EventStore instead of clobbering it to ``None`` — the
    fix for the cross-session contextvar clobber.
    """
    return _event_store_var.set(value)


def reset_event_store(token: Token[Any]) -> None:
    """Restore the prior EventStore using a token from :func:`push_event_store`."""
    _event_store_var.reset(token)


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


def get_current_slot_id() -> str | None:
    """Return the active slot id (e.g. ``"slot_1"``) or ``None``.

    In multi-slot worker children, set from the ``_LITMUS_SLOT_ID`` env
    var at session start. In single-process operator-targeted runs, set
    from the ``--slot`` CLI flag. ``None`` for non-fixtured single-UUT
    runs. Read by the plugin to stamp ``slot_id`` on run rows.
    """
    return _slot_id_var.get()


def set_current_slot_id(value: str | None) -> None:
    """Set the active slot id. Returns None."""
    _slot_id_var.set(value)


def get_active_slot_runner() -> Any:
    """Return the orchestrator's :class:`SlotRunner`, or ``None``.

    Set by ``run_multi_slot_session`` for the lifetime of one
    orchestrator session so ``pytest_keyboard_interrupt`` can forward
    SIGTERM to live children before its own teardown unwinds. ``None``
    in single-process / worker-mode invocations.
    """
    return _active_slot_runner_var.get()


def set_active_slot_runner(value: Any) -> None:
    """Set the active slot runner. Returns None."""
    _active_slot_runner_var.set(value)


# -----------------------------------------------------------------------------
# Error message factory — consistent "no active X" RuntimeError text
# -----------------------------------------------------------------------------


def resolve_session_id(
    explicit: UUID | str | None,
    *,
    context: Any = None,
    harness: Any = None,
    parent: Any = None,
    fallback_to_active: bool = False,
) -> UUID | str | None:
    """Resolve a session_id from the active sources, in precedence order.

    Single resolution rule used by every code path that needs to find
    "the current session" without forcing the caller to thread it
    explicitly. Order is fixed and documented here so additions stay
    consistent:

    1. ``explicit`` argument (if non-None — caller knows best)
    2. ``context._session_id`` (a Context object already in scope)
    3. ``harness._session_id`` (a TestHarness was passed in)
    4. ``parent._session_id`` (a parent Context inherited from)
    5. *(only when ``fallback_to_active=True``)*
       ``get_current_context()._session_id`` (the active ContextVar
       set by pytest's ``context`` fixture or ``connect()``)
    6. ``None`` (no session available — caller decides whether that's
       an error)

    ``fallback_to_active`` is OFF by default so call sites that
    construct a fresh Context with no parent do NOT silently inherit
    the surrounding test's session_id. Callers that DO want the
    inheritance (the ``litmus.files.write`` user-facing surface, for
    example) opt in explicitly.

    Callers that need an error on miss should use
    :func:`no_active_resource_error` after this returns ``None``.
    """
    if explicit is not None:
        return explicit
    for source in (context, harness, parent):
        if source is not None:
            sid = getattr(source, "_session_id", None)
            if sid is not None:
                return sid
    if fallback_to_active:
        ctx = get_current_context()
        if ctx is not None:
            return getattr(ctx, "_session_id", None)
    return None


def no_active_resource_error(resource: str, *, explicit_arg: str = "") -> RuntimeError:
    """Build a consistent ``RuntimeError`` for "no active X" code paths.

    Used by the four sites that resolve a session-scoped resource
    from a ContextVar and fail when nothing is wired:

    - ``Context.observe(blob)`` when ``session_id is None``
    - ``Context.stream()`` when no ``ChannelStore`` wired
    - ``channels.write/stream`` when ``get_channel_store()`` returns None
    - ``files.write/stream`` when no active session_id resolvable

    Standardizing the message means operators recognize the pattern
    (always "no active X. Wire one by ..." with the same three
    remedies) and find the right fix faster.

    Args:
        resource: Human label for the missing resource — e.g.
            ``"ChannelStore"``, ``"session_id"``, ``"Litmus context"``.
        explicit_arg: Optional name of an explicit kwarg the caller
            could pass to bypass the lookup (e.g. ``"session_id"``,
            ``"channel_store"``). Empty string omits the third bullet.

    Returns:
        ``RuntimeError`` with a uniformly-formatted message ready to
        ``raise`` from the call site.
    """
    bullets = [
        "  - Run inside a pytest session (the ``context`` fixture wires it).",
        "  - Open a connection: ``with connect(<station>) as station: ...``.",
    ]
    if explicit_arg:
        bullets.append(f"  - Pass an explicit ``{explicit_arg}=`` argument.")
    return RuntimeError(f"No active {resource}.\n" + "\n".join(bullets))
