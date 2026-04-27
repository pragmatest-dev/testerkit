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

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

from litmus.data.models import CollectedItem
from litmus.models.instrument import InstrumentRecord
from litmus.models.project import ProfileConfig
from litmus.models.test_config import FixtureConnection

if TYPE_CHECKING:
    from litmus.execution.logger import TestRunLogger

# Step/vector — stack-like, push/pop with token. Used by logger.py + harness.py.
_current_step_var: ContextVar[Any] = ContextVar("current_step", default=None)
_current_vector_var: ContextVar[Any] = ContextVar("current_vector", default=None)

# Active TestRunLogger — session singleton, set once per test by the runner.
_current_logger_var: ContextVar[TestRunLogger | None] = ContextVar("_current_logger", default=None)

_active_instruments_var: ContextVar[dict[str, Any]] = ContextVar("_active_instruments")
_instrument_records_var: ContextVar[dict[str, InstrumentRecord]] = ContextVar("_instrument_records")
_current_step_aliases_var: ContextVar[dict[str, str]] = ContextVar("_current_step_aliases")
_current_step_config_var: ContextVar[dict[str, Any]] = ContextVar("_current_step_config")
_active_spec_context_var: ContextVar[Any] = ContextVar("_active_spec_context")
_test_node_aliases_var: ContextVar[dict[str, dict[str, str]]] = ContextVar("_test_node_aliases")
_test_node_configs_var: ContextVar[dict[str, dict[str, Any]]] = ContextVar("_test_node_configs")
_channel_store_var: ContextVar[Any] = ContextVar("_channel_store")
_collected_items_var: ContextVar[list[CollectedItem]] = ContextVar("_collected_items")
_current_code_identity_var: ContextVar[dict[str, str | None]] = ContextVar("_current_code_identity")
_event_store_var: ContextVar[Any] = ContextVar("_event_store")
_active_limits_var: ContextVar[dict[str, Any]] = ContextVar("_active_limits")
_active_test_characteristic_var: ContextVar[str | None] = ContextVar("_active_test_characteristic")
_active_profile_var: ContextVar[ProfileConfig | None] = ContextVar("_active_profile")
_active_facets_var: ContextVar[dict[str, str]] = ContextVar("_active_facets")
_session_inputs_var: ContextVar[dict[str, str]] = ContextVar("_session_inputs")
_active_vector_params_var: ContextVar[dict[str, Any]] = ContextVar("_active_vector_params")
_active_vector_index_var: ContextVar[int] = ContextVar("_active_vector_index")
_active_connection_var: ContextVar[FixtureConnection | None] = ContextVar("_active_connection")


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


def get_active_spec_context() -> Any:
    """Return None if not set."""
    try:
        return _active_spec_context_var.get()
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


def get_current_logger() -> TestRunLogger | None:
    """Return the active :class:`TestRunLogger`, or ``None`` if no run is in progress."""
    return _current_logger_var.get()


def set_current_logger(logger: TestRunLogger | None) -> None:
    """Set the active :class:`TestRunLogger`. Returns ``None``."""
    _current_logger_var.set(logger)


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


def get_active_test_characteristic() -> str | None:
    """Return the active test's characteristic binding (from ``litmus_specs``), or ``None``.

    Set per-test alongside :func:`set_active_limits` when the test
    declares ``specs: [<char_id>]`` (sidecar / profile / inline marker).
    Read at measurement time so per-label limits with a ``characteristic:``
    field can fall back to the test-level binding when their own field
    is omitted.
    """
    try:
        return _active_test_characteristic_var.get()
    except LookupError:
        return None


def set_active_test_characteristic(value: str | None) -> None:
    """Set the active test characteristic. Returns None."""
    _active_test_characteristic_var.set(value)


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
