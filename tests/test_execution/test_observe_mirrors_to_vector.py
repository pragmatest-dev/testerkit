"""``Context.observe()`` mirrors writes onto ``get_current_vector().observations``.

Before this bridge landed, ``Context._observations`` held the URI but
``logger.log_measurement`` read ``vector.observations`` (empty) when
building ``out_*`` columns — so every observation made before a
``verify()`` was invisible in the parquet ``record_type='measurement'``
row. Symptom: example 10 wrote 4 artifacts to FileStore but the UI
Measurements tab showed only the verify scalar with no ``out_*`` URIs.

Bridge contract:
- When a vector is active on ``_current_vector_var``, every
  ``Context.observe(name, value)`` writes the resolved value/URI to
  BOTH ``Context._observations[name]`` AND
  ``vector.observations[name]``.
- When no vector is active (interactive ``Context()`` outside a
  test/harness flow), only ``Context._observations[name]`` is set —
  no crash, no warning.

These tests push an active vector IN-BODY (not via a setup-phase fixture):
the testerkit plugin's ``pytest_runtest_call`` hookwrapper runs ``start_step``
for the test itself in the CALL phase, which can auto-close a prior step and
reset the ``_current_vector`` contextvar — clobbering any vector a setup-phase
autouse fixture pushed. Pushing inside the body, after the hookwrapper has
run, and asserting on the pushed vector directly is immune to that ordering.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from testerkit.data.models import TestVector
from testerkit.execution._state import (
    _current_vector_var,
    push_current_vector,
    reset_current_vector,
)
from testerkit.execution.harness import Context


@contextmanager
def _active_vector() -> Iterator[TestVector]:
    """Push a fresh vector for the duration of the body, then reset.

    The grain reshape stopped auto-creating a vector for a non-swept test, so
    these mirror tests establish the active-vector scope explicitly — the
    mirror still fires whenever a vector is active (in-body / swept-variant).
    """
    vec = TestVector()
    token = push_current_vector(vec)
    try:
        yield vec
    finally:
        reset_current_vector(token)


def test_scalar_observe_mirrors_to_vector() -> None:
    with _active_vector() as vec:
        ctx = Context()
        ctx.observe("temperature_c", 23.5)
        assert vec.observations == {"temperature_c": 23.5}


def test_uri_string_observe_mirrors_to_vector() -> None:
    with _active_vector() as vec:
        ctx = Context()
        ctx.observe("scope.trace", "channel://scope.trace")
        assert vec.observations == {"scope.trace": "channel://scope.trace"}


def test_observe_without_active_vector_is_safe() -> None:
    """When no vector is on the contextvar, observe writes only to Context."""
    token = _current_vector_var.set(None)
    try:
        ctx = Context()
        ctx.observe("temperature_c", 23.5)
        assert ctx._observations == {"temperature_c": 23.5}
    finally:
        _current_vector_var.reset(token)


def test_multiple_observes_accumulate_on_vector() -> None:
    with _active_vector() as vec:
        ctx = Context()
        ctx.observe("a", 1.0)
        ctx.observe("b", 2.0)
        ctx.observe("c", "channel://c")
        assert vec.observations == {"a": 1.0, "b": 2.0, "c": "channel://c"}


def test_observe_namespace_prefixes_vector_key() -> None:
    with _active_vector() as vec:
        ctx = Context()
        ctx.observe("voltage", 3.31, namespace="psu_a")
        assert vec.observations == {"psu_a.voltage": 3.31}


def test_observe_none_value_mirrors_to_vector() -> None:
    with _active_vector() as vec:
        ctx = Context()
        ctx.observe("missing", None)
        assert "missing" in vec.observations
        assert vec.observations["missing"] is None


def test_blob_observe_writes_uri_to_vector() -> None:
    """PIL/bytes/Pydantic blobs land as ``file://`` URIs on the vector."""
    from testerkit.data.files import _reset_for_tests

    _reset_for_tests()
    sid = uuid4()
    with _active_vector() as vec:
        ctx = Context(session_id=sid)
        ctx.observe("vendor_blob", b"some bytes payload")
        val = vec.observations.get("vendor_blob")
        assert isinstance(val, str)
        assert val.startswith("file://")
