"""C3b — items 7 + 8: stream verb + power-user channels./files. surface.

Item 7: ``stream(name, sample)`` is the third sibling test-author
intent verb (alongside ``observe`` / ``verify``). Always routes to
ChannelStore; never auto-associates with a vector. Both shapes —
``Context.stream(...)`` (method) and bare ``stream(...)`` pytest
fixture — share one implementation.

Item 8: explicit power-user surface for each store.

- ``litmus.channels.write(name, sample)`` — one-shot channel append
- ``with litmus.channels.stream(name) as sink:`` — context-managed
  channel sink with ``.write(sample)`` / ``.close()``
- ``litmus.files.write(name, value)`` — one-shot file write
- ``with litmus.files.stream(name, format=...) as sink:`` —
  signature-only stub; real sink lands in build item 2 (C5).

The verb-symmetry pattern from C3a-followup is followed: ``stream``
shipped both ways from day one.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from litmus import channels, files
from litmus.data.channels.store import ChannelStore
from litmus.data.files import _reset_for_tests
from litmus.execution._state import (
    push_current_context,
    reset_current_context,
    set_channel_store,
)
from litmus.execution.harness import Context, TestHarness


@pytest.fixture(autouse=True)
def _reset_files_singleton():
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
def context_with_store(tmp_path: Path):
    """A Context with a real ChannelStore wired + ContextVar set.

    The ContextVar wiring is what the power-user
    :func:`channels.write` etc. resolve through; production code
    sets it during session start.
    """
    session_id = uuid4()
    store = ChannelStore(tmp_path, session_id, flush_threshold=1000)
    store.open()
    harness = TestHarness(session_id=session_id, channel_store=store)
    ctx = Context(harness=harness, channel_store=store)
    set_channel_store(store)
    ctx_token = push_current_context(ctx)
    try:
        yield ctx, store
    finally:
        reset_current_context(ctx_token)
        set_channel_store(None)
        store.close()


# --------------------------------------------------------------------- #
# Item 7 — Context.stream + bare stream pytest fixture                   #
# --------------------------------------------------------------------- #


class TestContextStreamMethod:
    def test_context_has_stream_method(self) -> None:
        """Per design doc §3, the third sibling verb exists as a Context method."""
        assert hasattr(Context, "stream")
        assert callable(Context.stream)

    def test_context_stream_signature_includes_namespace(self) -> None:
        import inspect

        sig = inspect.signature(Context.stream)
        assert "namespace" in sig.parameters

    def test_context_stream_writes_scalar_to_channel_store(self, context_with_store) -> None:
        ctx, store = context_with_store
        uri = ctx.stream("dmm.voltage", 3.31)
        assert uri.startswith("channel://")
        result = store.query("dmm.voltage")
        assert result.column("value")[0].as_py() == 3.31

    def test_context_stream_writes_array_to_channel_store(self, context_with_store) -> None:
        ctx, store = context_with_store
        ctx.stream("scope.waveform", [1.0, 2.0, 3.0])

        result = store.query("scope.waveform")
        assert result.column("value")[0].as_py() == [1.0, 2.0, 3.0]

    def test_context_stream_respects_namespace(self, context_with_store) -> None:
        ctx, store = context_with_store
        ctx.stream("voltage", 3.31, namespace="psu_under_test")

        result = store.query("psu_under_test.voltage")
        assert result.column("value")[0].as_py() == 3.31

    def test_context_stream_without_channel_store_raises(self) -> None:
        """Bare Context (no harness/store) gives a clear error."""
        ctx = Context()
        with pytest.raises(RuntimeError, match="no ChannelStore wired"):
            ctx.stream("voltage", 3.31)


class TestStreamBareFixture:
    def test_stream_fixture_registered_in_plugin(self) -> None:
        from litmus import pytest_plugin

        assert hasattr(pytest_plugin, "stream")
        assert callable(pytest_plugin.stream)


# --------------------------------------------------------------------- #
# Item 8 — litmus.channels (power-user)                                  #
# --------------------------------------------------------------------- #


class TestChannelsWrite:
    def test_channels_write_appends_via_context_var(self, context_with_store) -> None:
        """The active ChannelStore is resolved via ContextVar."""
        _ctx, store = context_with_store
        uri = channels.write("dmm.voltage", 3.31)
        assert uri.startswith("channel://")
        result = store.query("dmm.voltage")
        assert result.column("value")[0].as_py() == 3.31

    def test_channels_write_respects_namespace(self, context_with_store) -> None:
        _ctx, store = context_with_store
        channels.write("voltage", 3.31, namespace="psu_a")
        result = store.query("psu_a.voltage")
        assert result.column("value")[0].as_py() == 3.31

    def test_channels_write_without_store_raises(self) -> None:
        """No active store → clear runtime error pointing at session setup."""
        with pytest.raises(RuntimeError, match="no active ChannelStore"):
            channels.write("voltage", 3.31)


class TestChannelsStream:
    def test_channels_stream_yields_sink_with_write(self, context_with_store) -> None:
        _ctx, store = context_with_store
        with channels.stream("iv_curve.i") as sink:
            sink.write(0.0)
            sink.write(0.1)
            sink.write(0.2)

        result = store.query("iv_curve.i")
        assert result.column("value").to_pylist() == [0.0, 0.1, 0.2]

    def test_channels_stream_sink_carries_channel_id(self, context_with_store) -> None:
        with channels.stream("scope.ch1", namespace="bench_a") as sink:
            assert sink.channel_id == "bench_a.scope.ch1"

    def test_channels_stream_sink_write_after_close_raises(self, context_with_store) -> None:
        with channels.stream("x") as sink:
            sink.write(1.0)
            sink.close()
            with pytest.raises(RuntimeError, match="is closed"):
                sink.write(2.0)


# --------------------------------------------------------------------- #
# Item 8 — litmus.files (power-user)                                 #
# --------------------------------------------------------------------- #


class TestFilesWrite:
    def test_write_with_explicit_session_id(self, tmp_path: Path) -> None:
        """Power users can pass session_id explicitly (no Context required)."""
        from litmus.data.files import store as store_module

        # Bind FileStore singleton to tmp_path for isolation.
        original = store_module.resolve_data_dir
        store_module.resolve_data_dir = lambda _=None: tmp_path  # type: ignore[assignment]
        _reset_for_tests()
        try:
            uri = files.write("artifact", b"hello", session_id="sid-1234")
            assert uri == "file://sid-1234/artifact.bin"
        finally:
            store_module.resolve_data_dir = original  # type: ignore[assignment]
            _reset_for_tests()

    def test_write_resolves_session_from_active_context(self, context_with_store) -> None:
        """session_id=None falls back to the active Context's session."""
        ctx, _ = context_with_store
        uri = files.write("artifact", b"hello")
        assert uri.startswith(f"file://{ctx._session_id}/")

    def test_write_without_session_raises(self) -> None:
        """No session_id arg + no active Context = clear error."""
        with pytest.raises(RuntimeError, match="no active session_id"):
            files.write("artifact", b"hello")


class TestFilesStreamSignature:
    """C5 (item 2) landed the sink; verify the public signature stays stable.

    Format-specific behavior tested in ``test_filestore_streaming.py``
    (FileStore.open_stream class method) and ``test_files_stream_verb.py``
    (litmus.files.stream verb integration).
    """

    def test_stream_signature_includes_format_and_session_id(self) -> None:
        import inspect

        sig = inspect.signature(files.stream)
        assert "name" in sig.parameters
        assert "format" in sig.parameters
        assert "session_id" in sig.parameters

    def test_stream_unknown_format_raises_with_helpful_message(self) -> None:
        """Unknown format dispatches the registry's clear error."""
        from uuid import uuid4 as _uuid4

        with pytest.raises(ValueError, match="unknown format 'mp4'"):
            with files.stream("video.mp4", format="mp4", session_id=str(_uuid4())):
                pass


# --------------------------------------------------------------------- #
# Sibling verb symmetry — all three verbs land both ways                 #
# --------------------------------------------------------------------- #


class TestThreeVerbsSymmetric:
    def test_all_three_verbs_on_context(self) -> None:
        """``observe`` / ``verify`` / ``stream`` all present as Context methods."""
        for verb in ("observe", "verify", "stream"):
            assert hasattr(Context, verb), f"Context missing {verb!r}"
            assert callable(getattr(Context, verb))

    def test_all_three_verbs_in_pytest_plugin(self) -> None:
        """All three are exposed as pytest fixtures."""
        from litmus import pytest_plugin

        for verb in ("observe", "verify", "stream"):
            assert hasattr(pytest_plugin, verb), f"pytest_plugin missing {verb!r}"
            assert callable(getattr(pytest_plugin, verb))
