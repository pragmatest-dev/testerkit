"""Tests for the subscriber registry."""

from __future__ import annotations

from litmus.data.event_log import EventSubscriber
from litmus.data.subscribers._base import get_subscriber_class, list_subscribers


class TestSubscriberRegistry:
    def test_register_via_init_subclass(self):
        class FakeSub(EventSubscriber):
            format_name = "fake_test"
            event_types: set[type] = set()

        assert get_subscriber_class("fake_test") is FakeSub
        # Cleanup
        EventSubscriber._registry.pop("fake_test", None)

    def test_get_nonexistent_returns_none(self):
        assert get_subscriber_class("nonexistent_xyz") is None

    def test_list_includes_builtins(self):
        names = list_subscribers()
        assert "parquet" in names

    def test_parquet_registered(self):
        cls = get_subscriber_class("parquet")
        assert cls is not None
        assert cls.__name__ == "ParquetSubscriber"

    def test_channels_not_in_registry(self):
        """ChannelStore is no longer a subscriber — it's created directly."""
        cls = get_subscriber_class("channels")
        assert cls is None
