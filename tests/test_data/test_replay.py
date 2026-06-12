"""Tests for replay_to_subscriber."""

from __future__ import annotations

from typing import Any

import pytest

from litmus.data.event_log import EventSubscriber
from litmus.data.models import TestRun
from litmus.data.subscribers.replay import replay_to_subscriber
from tests.test_data.conftest import _replay_events


class TestReplayToSubscriber:
    def test_roundtrip_events(self, realistic_test_run: TestRun):
        """Events from TestRun serialize to dicts and replay correctly."""
        from litmus.data.events import (
            MeasurementRecorded,
            RunEnded,
            RunStarted,
            StepEnded,
            StepStarted,
        )

        # Collect events as dicts via a recording subscriber
        event_dicts: list[dict[str, Any]] = []

        class DictCollector:
            format_name = "collector"
            event_types = {RunStarted, StepStarted, MeasurementRecorded, StepEnded, RunEnded}

            def open(self):
                pass

            def on_event(self, event: Any) -> None:
                event_dicts.append(event.model_dump(mode="json"))

            def close(self):
                pass

        collector = DictCollector()
        collector.open()
        _replay_events(realistic_test_run, collector)
        collector.close()

        assert len(event_dicts) > 0

        # Now replay those dicts through another subscriber
        received: list[Any] = []

        class Receiver(EventSubscriber):
            format_name = "receiver-roundtrip"
            event_types = {RunStarted, MeasurementRecorded, RunEnded}

            def open(self):
                pass

            def on_event(self, event: Any) -> None:
                received.append(event)

            def close(self):
                pass

        replay_to_subscriber(Receiver(), event_dicts)

        # Should have: 1 RunStarted + measurements + 1 RunEnded
        run_starts = [e for e in received if isinstance(e, RunStarted)]
        run_ends = [e for e in received if isinstance(e, RunEnded)]
        measurements = [e for e in received if isinstance(e, MeasurementRecorded)]

        assert len(run_starts) == 1
        assert len(run_ends) == 1
        assert len(measurements) > 0
        assert run_starts[0].uut_serial == "UUT-001"

    def test_filters_by_event_types(self, realistic_test_run: TestRun):
        """Subscriber only receives events in its event_types set."""
        from litmus.data.events import (
            MeasurementRecorded,
            RunEnded,
            RunStarted,
            StepEnded,
            StepStarted,
        )

        # Build ALL event dicts
        all_dicts: list[dict[str, Any]] = []

        class AllCollector:
            format_name = "all"
            event_types = {RunStarted, StepStarted, MeasurementRecorded, StepEnded, RunEnded}

            def open(self):
                pass

            def on_event(self, event: Any) -> None:
                all_dicts.append(event.model_dump(mode="json"))

            def close(self):
                pass

        all_collector = AllCollector()
        all_collector.open()
        _replay_events(realistic_test_run, all_collector)
        all_collector.close()

        # Replay but receiver only wants RunStarted
        received: list[Any] = []

        class RunOnlyReceiver(EventSubscriber):
            format_name = "run_only"
            event_types = {RunStarted}

            def open(self):
                pass

            def on_event(self, event: Any) -> None:
                received.append(event)

            def close(self):
                pass

        replay_to_subscriber(RunOnlyReceiver(), all_dicts)
        assert len(received) == 1
        assert isinstance(received[0], RunStarted)

    def test_invalid_event_skipped(self):
        """Invalid event dicts are skipped without crashing."""
        from litmus.data.events import RunStarted

        received: list[Any] = []

        class Receiver(EventSubscriber):
            format_name = "receiver-skip-invalid"
            event_types = {RunStarted}

            def open(self):
                pass

            def on_event(self, event: Any) -> None:
                received.append(event)

            def close(self):
                pass

        bad_events = [
            {"event_type": "not.real", "garbage": True},
            {"completely": "wrong"},
        ]

        # Should not raise; emits one UserWarning per invalid event.
        with pytest.warns(UserWarning, match="Skipping invalid event"):
            replay_to_subscriber(Receiver(), bad_events)
        assert len(received) == 0
