"""C3a-followup — verb symmetry: bare ``observe`` fixture + ``Context.verify``.

Per §3 of the design doc, ``observe`` / ``verify`` / ``stream`` are
three sibling test-author intent verbs. Pre-this-PR the surfaces
drifted:

| Verb | Method on Context | Bare pytest fixture |
|---|---|---|
| ``observe`` | ✓ | — (missing) |
| ``verify`` | — (missing) | ✓ |
| ``stream`` (item 7) | — (future) | — (future) |

This PR adds the two missing surfaces so observe + verify are
symmetric: each exists as a method on ``Context`` AND as a bare
pytest fixture. The two shapes share one implementation
(``Context.observe`` body for both observe surfaces;
``_perform_verify`` for both verify surfaces) so the verb behaves
identically regardless of which surface the test author reaches for.

Item 7 (the ``stream`` verb) will follow the same pattern when it
lands — both shapes from day one.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from litmus.data.channels.store import ChannelStore
from litmus.execution.harness import Context, TestHarness


@pytest.fixture
def context_with_store(tmp_path: Path):
    """A Context with a real ChannelStore wired (matches the production shape)."""
    session_id = uuid4()
    store = ChannelStore(tmp_path, session_id, flush_threshold=1000)
    store.open()
    harness = TestHarness(session_id=session_id, channel_store=store)
    ctx = Context(harness=harness, channel_store=store)
    yield ctx, store
    store.close()


# --------------------------------------------------------------------- #
# Context.verify — method form (mirrors Context.observe)                 #
# --------------------------------------------------------------------- #


class TestContextVerifyMethodExists:
    def test_context_has_verify_method(self) -> None:
        """The verb is reachable as ``context.verify(...)`` (post C3a-followup)."""
        assert hasattr(Context, "verify")
        assert callable(Context.verify)

    def test_context_verify_signature_includes_namespace(self) -> None:
        """The method signature includes the C3a namespace= kwarg."""
        import inspect

        sig = inspect.signature(Context.verify)
        assert "namespace" in sig.parameters

    def test_context_verify_signature_mirrors_observe_shape(self) -> None:
        """``Context.observe`` and ``Context.verify`` accept the same shape.

        Both: (name, value, *, namespace=None, ...). Symmetric.
        """
        import inspect

        observe_sig = inspect.signature(Context.observe)
        verify_sig = inspect.signature(Context.verify)

        # Both accept name + value + namespace
        for sig in (observe_sig, verify_sig):
            assert "name" in sig.parameters or "key" in sig.parameters
            assert "value" in sig.parameters
            assert "namespace" in sig.parameters


# --------------------------------------------------------------------- #
# build_verify_callable delegates to _perform_verify                     #
# --------------------------------------------------------------------- #


class TestVerifyCallableDelegates:
    def test_build_verify_callable_returns_perform_verify(self) -> None:
        """The bare-callable form IS the same function used by Context.verify.

        Pin the indirection so a future refactor doesn't accidentally
        diverge the two surfaces.
        """
        from litmus.execution.verify import _perform_verify, build_verify_callable

        verify_fn = build_verify_callable()
        assert verify_fn is _perform_verify


# --------------------------------------------------------------------- #
# Bare observe pytest fixture                                            #
# --------------------------------------------------------------------- #


class TestObserveBareFixture:
    """The bare ``observe`` fixture in the pytest plugin.

    Smoke-testing via the actual fixture system requires running
    through pytester (which today's stage has broken daemon-side); we
    pin the fixture's existence + signature instead, plus exercise
    the ``build_observe_callable`` equivalent directly when an
    explicit Context is in hand.
    """

    def test_observe_fixture_is_defined_in_plugin(self) -> None:
        """``observe`` is registered as a pytest fixture in the plugin module."""
        from litmus import pytest_plugin

        assert hasattr(pytest_plugin, "observe")
        # Pytest's @fixture decorator wraps the function; the marker
        # is on the wrapper.
        assert callable(pytest_plugin.observe)

    def test_observe_via_context_method_routes_identically(self, context_with_store) -> None:
        """The bare fixture's behavior is identical to ``context.observe(...)``.

        Both shapes pass through the same ``Context.observe`` body.
        Test the method side (the canonical impl); pytester-level
        coverage of the fixture would require a working runs daemon.
        """
        ctx, store = context_with_store
        ctx.observe("temperature", 23.5, namespace="ambient")

        assert "ambient.temperature" in ctx._observations
        assert ctx._observations["ambient.temperature"] == 23.5

    def test_observe_via_context_method_routes_array_to_channel_store(
        self, context_with_store
    ) -> None:
        ctx, store = context_with_store
        ctx.observe("samples", [1.0, 2.0, 3.0])

        uri = ctx._observations["samples"]
        assert uri.startswith("channel://")
        result = store.query("samples")
        assert result.column("value")[0].as_py() == [1.0, 2.0, 3.0]


# --------------------------------------------------------------------- #
# Two surfaces, one implementation                                       #
# --------------------------------------------------------------------- #


class TestVerifySymmetricSurfaces:
    """Both Context.verify and build_verify_callable use _perform_verify.

    Pinning the one-implementation invariant by introspection — full
    behavior tests live in test_verify_cascade.py (which exercises
    the bare-callable shape via pytester) and the integration tests
    that wire a logger.
    """

    def test_context_verify_method_calls_perform_verify(self) -> None:
        """Context.verify body delegates to _perform_verify."""
        import inspect

        source = inspect.getsource(Context.verify)
        assert "_perform_verify" in source

    def test_build_verify_callable_returns_perform_verify_directly(self) -> None:
        from litmus.execution.verify import _perform_verify, build_verify_callable

        assert build_verify_callable() is _perform_verify
