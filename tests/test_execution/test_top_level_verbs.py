"""Top-level ``observe`` / ``verify`` / ``stream`` resolve via the active context.

The same three verbs are accessible three ways: top-level imports,
pytest fixtures, and ``Context`` methods. This file pins the
top-level-import shape against the active pytest context (set by the
plugin's ``context`` fixture) plus the "no active context" error
path for callers outside any test/connection.

See :mod:`litmus.verbs` for the resolution contract.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from litmus import observe, stream, verify
from litmus.execution._state import (
    _current_context_var,
    get_current_context,
    get_current_vector,
)


@pytest.fixture(autouse=True)
def _clear_vector_observations() -> Iterator[None]:
    """Reset the active vector's observations between tests."""
    yield
    vec = get_current_vector()
    if vec is not None:
        vec.observations.clear()


def test_top_level_observe_writes_to_active_context() -> None:
    """``observe(...)`` mirrors to the active vector (same bridge the fixture uses)."""
    observe("temp", 23.5)
    vec = get_current_vector()
    assert vec is not None
    assert vec.observations == {"temp": 23.5}


def test_top_level_observe_namespace_prefixes_key() -> None:
    observe("voltage", 3.31, namespace="psu_a")
    vec = get_current_vector()
    assert vec is not None
    assert vec.observations == {"psu_a.voltage": 3.31}


def test_top_level_verb_no_context_raises_with_useful_message() -> None:
    """Outside any test/connection, the verb tells the caller how to wire it."""
    token = _current_context_var.set(None)
    try:
        with pytest.raises(RuntimeError, match="No active Litmus context"):
            observe("temp", 1.0)
        with pytest.raises(RuntimeError, match="No active Litmus context"):
            verify("v", 1.0, limit=None)
        with pytest.raises(RuntimeError, match="No active Litmus context"):
            stream("ch", 1.0)
    finally:
        _current_context_var.reset(token)


def test_top_level_verb_same_resolution_as_fixture(observe) -> None:  # type: ignore[no-redef]
    """The fixture form and the top-level import resolve to the same Context."""
    from litmus import observe as observe_module

    # Both paths should write to the same vector — proven by writing
    # via each shape and asserting both keys land on the active vector.
    observe("via_fixture", 1.0)
    observe_module("via_import", 2.0)
    vec = get_current_vector()
    assert vec is not None
    assert vec.observations == {"via_fixture": 1.0, "via_import": 2.0}


def test_top_level_verb_resolves_same_context_object() -> None:
    """``_active_context()`` returns the same instance as ``get_current_context``."""
    from litmus.verbs import _active_context

    assert _active_context() is get_current_context()
