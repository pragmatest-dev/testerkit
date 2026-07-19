"""Tests for signal/atexit cleanup registry."""

from testerkit.signals import _callbacks, _run_all, deregister_cleanup, register_cleanup


class TestSignalRegistry:
    def setup_method(self):
        _callbacks.clear()

    def test_register_and_run(self):
        called = []
        register_cleanup("test1", lambda: called.append("a"))
        register_cleanup("test2", lambda: called.append("b"))

        _run_all()
        assert sorted(called) == ["a", "b"]
        assert len(_callbacks) == 0

    def test_deregister(self):
        called = []
        register_cleanup("test1", lambda: called.append("a"))
        deregister_cleanup("test1")

        _run_all()
        assert called == []

    def test_deregister_nonexistent_noop(self):
        deregister_cleanup("does-not-exist")  # Should not raise

    def test_callback_error_continues(self):
        called = []

        def _raise() -> None:
            raise ZeroDivisionError

        register_cleanup("bad", _raise)
        register_cleanup("good", lambda: called.append("ok"))

        _run_all()
        assert called == ["ok"]
