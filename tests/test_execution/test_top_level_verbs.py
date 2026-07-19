"""Top-level ``observe`` / ``verify`` / ``stream`` resolve via the active context.

The same three verbs are accessible three ways: top-level imports,
pytest fixtures, and ``Context`` methods. This file pins the
top-level-import shape against the active pytest context (set by the
plugin's ``context`` fixture) plus the "no active context" error
path for callers outside any test/connection.

See :mod:`testerkit.verbs` for the resolution contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from testerkit import observe, stream, verify
from testerkit.data.models import TestVector
from testerkit.execution._state import (
    _current_context_var,
    get_current_context,
    push_current_vector,
    reset_current_vector,
)


@contextmanager
def _active_vector() -> Iterator[TestVector]:
    """Push a fresh vector for the duration of the body, then reset.

    Pushed IN-BODY (not via a setup-phase fixture): the testerkit plugin's
    call-phase ``start_step`` can reset the ``_current_vector`` contextvar in
    the full suite, clobbering a fixture-pushed vector. Asserting on the
    pushed vector directly is immune to that ordering.
    """
    vec = TestVector()
    token = push_current_vector(vec)
    try:
        yield vec
    finally:
        reset_current_vector(token)


def test_top_level_observe_writes_to_active_context() -> None:
    """``observe(...)`` mirrors to the active vector (same bridge the fixture uses)."""
    with _active_vector() as vec:
        observe("temp", 23.5)
        assert vec.observations == {"temp": 23.5}


def test_top_level_observe_namespace_prefixes_key() -> None:
    with _active_vector() as vec:
        observe("voltage", 3.31, namespace="psu_a")
        assert vec.observations == {"psu_a.voltage": 3.31}


def test_top_level_verb_no_context_raises_with_useful_message() -> None:
    """Outside any test/connection, the verb tells the caller how to wire it."""
    token = _current_context_var.set(None)
    try:
        with pytest.raises(RuntimeError, match="No active TesterKit context"):
            observe("temp", 1.0)
        with pytest.raises(RuntimeError, match="No active TesterKit context"):
            verify("v", 1.0, limit=None)
        with pytest.raises(RuntimeError, match="No active TesterKit context"):
            stream("ch", 1.0)
    finally:
        _current_context_var.reset(token)


def test_top_level_verb_same_resolution_as_fixture(observe) -> None:  # type: ignore[no-redef]
    """The fixture form and the top-level import resolve to the same Context."""
    from testerkit import observe as observe_module

    # Both paths should write to the same vector — proven by writing
    # via each shape and asserting both keys land on the active vector.
    with _active_vector() as vec:
        observe("via_fixture", 1.0)
        observe_module("via_import", 2.0)
        assert vec.observations == {"via_fixture": 1.0, "via_import": 2.0}


def test_top_level_verb_resolves_same_context_object() -> None:
    """``_active_context()`` returns the same instance as ``get_current_context``."""
    from testerkit.verbs import _active_context

    assert _active_context() is get_current_context()
