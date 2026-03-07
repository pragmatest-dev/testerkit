"""Tests for the subscriber registry."""

from __future__ import annotations

from litmus.data.subscribers._registry import (
    _REGISTRY,
    get_subscriber_class,
    list_subscribers,
    register_subscriber,
)


class TestSubscriberRegistry:
    def test_register_and_get(self):
        class FakeSub:
            format_name = "fake_test"

        register_subscriber(FakeSub)
        assert get_subscriber_class("fake_test") is FakeSub
        # Cleanup
        _REGISTRY.pop("fake_test", None)

    def test_get_nonexistent_returns_none(self):
        assert get_subscriber_class("nonexistent_xyz") is None

    def test_list_includes_lazy(self):
        names = list_subscribers()
        assert "parquet" in names
        assert "telemetry" in names
        assert "sessions" in names

    def test_lazy_load_parquet(self):
        _REGISTRY.pop("parquet", None)
        cls = get_subscriber_class("parquet")
        assert cls is not None
        assert cls.__name__ == "ParquetSubscriber"

    def test_lazy_load_telemetry(self):
        _REGISTRY.pop("telemetry", None)
        cls = get_subscriber_class("telemetry")
        assert cls is not None
        assert cls.__name__ == "TelemetryStore"

    def test_lazy_load_sessions(self):
        _REGISTRY.pop("sessions", None)
        cls = get_subscriber_class("sessions")
        assert cls is not None
        assert cls.__name__ == "SessionSubscriber"
