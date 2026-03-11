# Real-Time Event Monitoring

Subscribe to events as they happen — for dashboards, alerting, or custom integrations.

## In-Process: `EventStore.on_event()`

The `on_event()` method provides a catch-up subscription: it replays matching historical events, then pushes new ones as they arrive.

```python
from litmus.data.event_store import EventStore

store = EventStore()

def handle_event(event_dict: dict):
    print(f"{event_dict['event_type']}: {event_dict.get('step_name', '')}")

# Subscribe to all events
unsub = store.on_event(handle_event)

# Subscribe with filters
unsub = store.on_event(
    handle_event,
    event_type="test.measurement",     # Only measurements
    role="dmm",                         # Only DMM-related
    session_id=some_uuid,               # Only this session
    since=datetime(2026, 3, 10, 14, 0), # Only after this time
)

# Stop receiving
unsub()
```

### How It Works

1. **Replay**: Existing events matching your filters are delivered immediately
2. **In-process**: New events from `emit()` in the same process are dispatched instantly
3. **Cross-process**: A background watcher polls DuckDB via Flight (500ms interval) to detect events from other processes, deduplicating against in-process deliveries

## Channel Subscriptions: `ChannelStore.on_channel()`

For live time-series data:

```python
from litmus.data.channels.store import ChannelStore

def handle_sample(sample):
    print(f"{sample.channel_id}: {sample.value} {sample.units}")

# Subscribe to a specific channel
unsub = store.on_channel("dmm.voltage", handle_sample)

# Subscribe to all channels
unsub = store.on_channel(None, handle_sample)
```

Cross-process channel subscriptions via `ChannelClient`:

```python
from litmus.data.channels.client import ChannelClient

client = ChannelClient("grpc://localhost:8815")
unsub = client.on_channel("dmm.voltage", handle_sample)
```

## NiceGUI Live View

The operator UI (`litmus serve`) subscribes to events internally to show real-time test progress. The subscription pattern is the same — `EventStore.on_event()` with session filtering.

## Grafana Integration

For production monitoring, see [Grafana Dashboards](grafana-dashboards.md). Litmus events can be exposed to Grafana via the HTTP API or direct DuckDB/Flight connection.

## EventLog Subscribers (Plugin Pattern)

For deeper integration, implement the `EventSubscriber` protocol on the `EventLog` level:

```python
from litmus.data.event_log import EventSubscriber
from litmus.data.events import EventBase, MeasurementRecorded

class MySubscriber:
    format_name = "my_format"
    event_types = {MeasurementRecorded}

    def open(self) -> None:
        pass  # Initialize resources

    def on_event(self, event: EventBase) -> None:
        # Called synchronously on emit()
        assert isinstance(event, MeasurementRecorded)
        print(f"Measured {event.measurement_name}: {event.value}")

    def close(self) -> None:
        pass  # Cleanup

# Register on an EventLog
event_log.add_subscriber(MySubscriber())
```

This is how `ParquetSubscriber` and `SessionSubscriber` work internally. Use this pattern when you need synchronous processing of typed events within the same process.

## See Also

- [Querying Events](querying-events.md) — Historical queries
- [Event Log Architecture](../concepts/event-log.md) — How the push model works
