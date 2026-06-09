"""v0.2.0 event-subscription proof of concept.

End-to-end PoC that a live consumer can subscribe to *every* event
class introduced in v0.2.0 and see it land in real time.

The architectural promise (per Position 2 + the lifecycle-only
streaming-event decision):

- ``EventStore`` is the **discovery** spine — significant moments
  emit events; consumers walk the timeline to find / open / close
  things.
- **Sample data** lives on a separate transport per store:
  - ChannelStore: Arrow Flight ``do_get`` for live samples
  - FileStore: read the file (range-read while writing)

This PoC exercises both halves:

1. **Event subscription** — an ``EventLog`` is created with an
   ``on_emit`` callback that captures every event in order. The test
   triggers each v0.2.0 event class via the verb layer that
   production paths actually use (``Context.observe``,
   ``Context.stream``, ``litmus.files.stream``), then asserts each
   class was received in the captured stream.

2. **Live channel subscription** — a real
   :class:`ChannelFlightServer` is started in-process; a
   :class:`ChannelClient` subscribes to ``"*"``; samples written via
   ``ChannelStore`` (the production path) are received over Flight
   by the subscriber.

Together these prove: consumers can subscribe to the v0.2.0 event
spine and reach the underlying sample data through the right
transport, without polling and without bespoke wiring.

**Coverage of v0.2.0 event classes**

| Event             | Triggered by                                    | Asserted |
|-------------------|-------------------------------------------------|----------|
| ``ChannelStarted``| First channel write per (channel, session)      | ✅       |
| ``Observation``   | ``Context.observe(name, value)``                | ✅       |
| ``StreamStarted`` | ``litmus.files.stream(...)`` open               | ✅       |
| ``StreamEnded``   | ``litmus.files.stream(...)`` close              | ✅       |
| ``ChannelClosed`` | (defined; SessionEnded-tied emission deferred — | ⏸       |
|                   | see ``ChannelClosed`` docstring in events.py)   |          |

Per CLAUDE.md test conventions: in-process Flight server bound to
``tmp_path``; no daemon spawn.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from litmus.data.channels.client import ChannelClient
from litmus.data.channels.models import ChannelSample
from litmus.data.channels.server import start_server_background
from litmus.data.channels.store import ChannelStore
from litmus.data.event_log import EventLog
from litmus.data.events import (
    ChannelStarted,
    Observation,
    StreamEnded,
    StreamStarted,
)
from litmus.data.files import FileStore
from litmus.data.files import _reset_for_tests as _reset_filestore
from litmus.execution._state import (
    get_current_logger,
    push_current_context,
    reset_current_context,
    set_channel_store,
    set_current_logger,
)
from litmus.execution.harness import Context, TestHarness
from litmus.execution.logger import TestRunLogger
from litmus.instruments.observer import EventEmitter


@pytest.fixture
def session(tmp_path: Path):
    """Single fixture spinning up the full v0.2.0 wiring.

    Yields a namespace with:

    - ``events`` — list[EventBase] populated by ``EventLog.on_emit``
    - ``channel_store`` — open ``ChannelStore`` (in-process Flight server)
    - ``file_store`` — ``FileStore`` bound to ``tmp_path``
    - ``location`` — Flight URL for the channel server
    - ``ctx`` — a :class:`Context` pushed via ContextVar
    - ``logger`` — a real :class:`TestRunLogger` with our EventLog attached
    - ``session_id`` / ``run_id``
    """
    from litmus.data.files import store as fstore_module

    session_id = uuid4()
    run_id = uuid4()
    captured: list = []

    # 1) EventLog with on_emit callback for our PoC subscriber
    log = EventLog(
        log_dir=tmp_path / "events",
        session_id=session_id,
        on_emit=captured.append,
    )

    # 2) ChannelStore with in-process Flight server (no daemon spawn).
    #    Wire event_log so ChannelStarted/Closed lifecycle events flow
    #    into the captured stream (item 4b consolidation — store owns
    #    the per-(channel, session) tracker now).
    cstore = ChannelStore(tmp_path, session_id, flush_threshold=100, event_log=log)
    cstore.open()
    server, location = start_server_background(cstore)

    # 3) FileStore bound to tmp_path
    _orig_resolve = fstore_module.resolve_data_dir
    fstore_module.resolve_data_dir = lambda _=None: tmp_path  # type: ignore[assignment]
    _reset_filestore()
    fstore = FileStore(data_dir=tmp_path)

    # 4) Real TestRunLogger with our EventLog attached. The pytest plugin's
    #    own hooks read TestRun fields off the active logger, so a stub
    #    won't do — use the real one and swap in our event log.
    logger = TestRunLogger(
        dut_serial="POC-DUT-001",
        station_id="poc-station",
        run_id=run_id,
        session_id=session_id,
        data_dir=tmp_path,
    )
    logger.event_log = log

    # 5) Harness + Context wired with everything
    harness = TestHarness(session_id=session_id, channel_store=cstore, logger=logger)
    ctx = Context(harness=harness, channel_store=cstore, session_id=session_id)
    set_channel_store(cstore)
    prior_logger = get_current_logger()
    set_current_logger(logger)
    token = push_current_context(ctx)

    class _Session:
        pass

    sess = _Session()
    sess.events = captured  # type: ignore[attr-defined]
    sess.channel_store = cstore  # type: ignore[attr-defined]
    sess.file_store = fstore  # type: ignore[attr-defined]
    sess.location = location  # type: ignore[attr-defined]
    sess.ctx = ctx  # type: ignore[attr-defined]
    sess.logger = logger  # type: ignore[attr-defined]
    sess.session_id = session_id  # type: ignore[attr-defined]
    sess.run_id = run_id  # type: ignore[attr-defined]
    sess.event_log = log  # type: ignore[attr-defined]

    try:
        yield sess
    finally:
        reset_current_context(token)
        set_current_logger(prior_logger)
        set_channel_store(None)
        server.shutdown()
        cstore.close()
        log.close()
        fstore_module.resolve_data_dir = _orig_resolve  # type: ignore[assignment]
        _reset_filestore()


# --------------------------------------------------------------------- #
# Event-subscription PoC                                                 #
# --------------------------------------------------------------------- #


def _emitter(session: Any) -> EventEmitter:
    """Build an EventEmitter wired to the session's EventLog + ChannelStore.

    Production instrument observers go through this; using it directly
    in the PoC exercises the same code path that fires ``ChannelStarted``.
    """
    return EventEmitter(
        event_log=session.event_log,
        session_id=session.session_id,
        role="dmm",
        run_id=session.run_id,
        resource="POC::INSTR",
        channel_store=session.channel_store,
    )


class TestEventSubscriptionPoC:
    """A subscriber attached to EventLog receives every v0.2.0 event."""

    def test_channel_started_lands_in_subscriber_on_first_write(self, session: Any) -> None:
        """Position 2 lifecycle: ChannelStarted fires once on first write."""
        emit = _emitter(session)
        emit.read("dmm.voltage", 3.31, method="measure_dc_voltage")
        emit.read("dmm.voltage", 3.32, method="measure_dc_voltage")
        emit.read("dmm.voltage", 3.33, method="measure_dc_voltage")

        started = [e for e in session.events if isinstance(e, ChannelStarted)]
        assert len(started) == 1
        assert started[0].channel_id == "dmm.voltage"
        assert started[0].instrument_role == "dmm"
        assert started[0].method == "measure_dc_voltage"
        assert str(started[0].session_id) == str(session.session_id)

    def test_observation_lands_in_subscriber(self, session: Any) -> None:
        """observe() emits Observation per call."""
        session.ctx.observe("dut_temp_c", 23.5)
        session.ctx.observe("operator", "ALICE")

        observations = [e for e in session.events if isinstance(e, Observation)]
        assert len(observations) == 2
        assert {o.name for o in observations} == {"dut_temp_c", "operator"}
        # scalar value preserved inline
        by_name = {o.name: o for o in observations}
        assert by_name["dut_temp_c"].value == 23.5
        assert by_name["operator"].value == "ALICE"

    def test_observation_blob_lands_with_file_uri(self, session: Any) -> None:
        """observe(blob) auto-routes to FileStore; Observation carries the URI."""
        session.ctx.observe("capture", b"\x89PNG\r\n\x1a\n")

        observations = [e for e in session.events if isinstance(e, Observation)]
        assert len(observations) == 1
        assert observations[0].name == "capture"
        uri = observations[0].value
        assert isinstance(uri, str)
        assert f"/{session.session_id}/" in uri and uri.startswith("file://")

    def test_stream_lifecycle_lands_in_subscriber(self, session: Any) -> None:
        """litmus.files.stream() emits StreamStarted + StreamEnded only."""
        import litmus.files

        with litmus.files.stream(
            "daq_capture", format="raw", session_id=str(session.session_id)
        ) as sink:
            sink.write(b"chunk-1")
            sink.write(b"chunk-2")
            sink.write(b"chunk-3")

        stream_events = [e for e in session.events if isinstance(e, StreamStarted | StreamEnded)]
        assert len(stream_events) == 2
        assert isinstance(stream_events[0], StreamStarted)
        assert stream_events[0].name == "daq_capture"
        assert stream_events[0].format == "raw"
        assert stream_events[0].run_id == session.run_id

        assert isinstance(stream_events[1], StreamEnded)
        assert stream_events[1].stream_id == stream_events[0].stream_id
        assert stream_events[1].uri is not None and stream_events[1].uri.endswith(
            f"/{session.session_id}/daq_capture.bin"
        )
        assert stream_events[1].size_bytes == len(b"chunk-1chunk-2chunk-3")

    def test_full_session_drives_every_v020_event(self, session: Any) -> None:
        """One coherent session emits every v0.2.0 event class we ship."""
        import litmus.files

        # 1) Instrument read → ChannelStarted (first time per (ch, session))
        emit = _emitter(session)
        emit.read("psu.voltage", 12.0, method="measure_dc_voltage")
        emit.read("psu.voltage", 11.9, method="measure_dc_voltage")

        # 2) observe scalar → Observation
        session.ctx.observe("vbus", 12.05)

        # 3) observe blob → Observation + FileStore write
        session.ctx.observe("scope_capture", b"\x89PNG\r\n\x1a\nbytes")

        # 4) files.stream → StreamStarted/Ended
        with litmus.files.stream(
            "audio_capture",
            format="raw",
            session_id=str(session.session_id),
        ) as sink:
            sink.write(b"raw-audio-bytes")

        # Subscriber sees them all — assert by class presence + count
        types = [type(e).__name__ for e in session.events]
        assert types.count("ChannelStarted") == 1
        assert types.count("Observation") == 2
        assert types.count("StreamStarted") == 1
        assert types.count("StreamEnded") == 1


# --------------------------------------------------------------------- #
# Live channel data via Flight do_get                                    #
# --------------------------------------------------------------------- #


class TestChannelSubscriptionPoC:
    """Live samples flow over Flight to a subscriber.

    EventStore is for discovery; sample data rides Flight.
    """

    def test_flight_subscriber_receives_samples_as_written(self, session: Any) -> None:
        """A ChannelClient subscriber receives every sample written by the
        producer, in order, over Arrow Flight."""
        received: list[ChannelSample] = []
        ready = threading.Event()

        def _on_sample(sample: ChannelSample) -> None:
            received.append(sample)
            ready.set()

        client = ChannelClient(session.location)
        # Subscribe to all channels with "*"
        unsub = client.on_channel("*", _on_sample)
        try:
            # Give the reader thread a moment to connect before producing
            time.sleep(0.1)

            # Produce three samples via the instrument-observer path (the
            # production source of channel writes)
            emit = _emitter(session)
            emit.read("psu.voltage", 12.0, method="measure_dc_voltage")
            emit.read("psu.voltage", 11.95, method="measure_dc_voltage")
            emit.read("psu.voltage", 11.9, method="measure_dc_voltage")

            # Wait for the first sample to land (bounded)
            assert ready.wait(timeout=2.0), "subscriber received no samples"

            # Drain — give time for the trailing samples
            deadline = time.time() + 2.0
            while len(received) < 3 and time.time() < deadline:
                time.sleep(0.05)
        finally:
            unsub()
            client.close()

        assert len(received) >= 3, (
            f"expected ≥3 samples via Flight; got {len(received)}: {received!r}"
        )
        assert all(s.channel_id == "psu.voltage" for s in received[:3])
        # Values round-trip through Flight's utf8/JSON encoding
        values = [s.value for s in received[:3]]
        assert values == [12.0, 11.95, 11.9]

    def test_event_log_and_flight_complement_each_other(self, session: Any) -> None:
        """The two transports cover the two jobs: discovery + sample data.

        The event spine fires ONE ChannelStarted for the (channel, session)
        pair; Flight delivers ALL samples. Together they tell a complete
        story without either transport carrying the other's load.
        """
        received: list[ChannelSample] = []

        def _on_sample(sample: ChannelSample) -> None:
            received.append(sample)

        client = ChannelClient(session.location)
        unsub = client.on_channel("*", _on_sample)
        try:
            time.sleep(0.1)
            emit = _emitter(session)
            for v in [1.0, 1.5, 2.0, 2.5, 3.0]:
                emit.read("ramp", v, method="measure_dc_voltage")
            deadline = time.time() + 2.0
            while len(received) < 5 and time.time() < deadline:
                time.sleep(0.05)
        finally:
            unsub()
            client.close()

        # Event spine: lifecycle-only
        ramp_started = [
            e for e in session.events if isinstance(e, ChannelStarted) and e.channel_id == "ramp"
        ]
        assert len(ramp_started) == 1, (
            f"ChannelStarted should fire once per (channel, session); got {len(ramp_started)}"
        )

        # Flight transport: all 5 samples
        assert len(received) >= 5, f"Flight subscriber should see every sample; got {len(received)}"
        assert [s.value for s in received[:5]] == [1.0, 1.5, 2.0, 2.5, 3.0]
