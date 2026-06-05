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

These tests use the vector that pytest's ``pytest_runtest_call``
hookwrapper pushes via ``logger.start_step`` — that's the one
``log_measurement`` actually projects into the parquet row, so it's
the contract that matters. The vector is read fresh at test-body
time (not snapshotted in a fixture, because the hookwrapper pushes
AFTER autouse fixtures run).
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest

from litmus.execution._state import _current_vector_var, get_current_vector
from litmus.execution.harness import Context


@pytest.fixture(autouse=True)
def _clear_vector_observations() -> Iterator[None]:
    """Reset the active vector's observations between tests."""
    yield
    vec = get_current_vector()
    if vec is not None:
        vec.observations.clear()


def _active_observations() -> dict:
    vec = get_current_vector()
    assert vec is not None, "pytest_runtest_call did not push a vector"
    return vec.observations


def test_scalar_observe_mirrors_to_vector() -> None:
    ctx = Context()
    ctx.observe("temperature_c", 23.5)
    assert _active_observations() == {"temperature_c": 23.5}


def test_uri_string_observe_mirrors_to_vector() -> None:
    ctx = Context()
    ctx.observe("scope.trace", "channel://scope.trace")
    assert _active_observations() == {"scope.trace": "channel://scope.trace"}


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
    ctx = Context()
    ctx.observe("a", 1.0)
    ctx.observe("b", 2.0)
    ctx.observe("c", "channel://c")
    assert _active_observations() == {"a": 1.0, "b": 2.0, "c": "channel://c"}


def test_observe_namespace_prefixes_vector_key() -> None:
    ctx = Context()
    ctx.observe("voltage", 3.31, namespace="psu_a")
    assert _active_observations() == {"psu_a.voltage": 3.31}


def test_observe_none_value_mirrors_to_vector() -> None:
    ctx = Context()
    ctx.observe("missing", None)
    obs = _active_observations()
    assert "missing" in obs
    assert obs["missing"] is None


def test_blob_observe_writes_uri_to_vector() -> None:
    """PIL/bytes/Pydantic blobs land as ``file://`` URIs on the vector."""
    from litmus.data.files import _reset_for_tests

    _reset_for_tests()
    sid = uuid4()
    ctx = Context(session_id=sid)
    ctx.observe("vendor_blob", b"some bytes payload")
    val = _active_observations().get("vendor_blob")
    assert isinstance(val, str)
    assert val.startswith("file://")
