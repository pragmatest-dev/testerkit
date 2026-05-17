# Query Channel Data

Channel data stores time-series instrument readings — waveforms, voltage traces, temperature logs. Query it via MCP tool, HTTP API, or Python.

## MCP Tool: `litmus_channels`

```
# Get channel data
litmus_channels(channel_id="dmm.voltage")

# Filter to a session
litmus_channels(channel_id="scope.ch1_waveform", session_id="abc123")

# Last 100 readings
litmus_channels(channel_id="dmm.voltage", last_n=100)

# Downsample for visualization (LTTB)
litmus_channels(channel_id="scope.ch1_waveform", max_points=500)
```

## HTTP API

```bash
# List all known channels
curl http://localhost:8000/api/channels

# Get channel data
curl http://localhost:8000/api/channels/dmm.voltage

# With filters
curl "http://localhost:8000/api/channels/scope.ch1?session_id=abc123&max_points=500"

# Time range
curl "http://localhost:8000/api/channels/dmm.voltage?start=2026-03-10T14:00:00&end=2026-03-10T15:00:00"
```

## Python: [`ChannelStore`](../concepts/three-stores.md)

```python
from uuid import uuid4
from pathlib import Path
from litmus.data.channels.store import ChannelStore

channels_dir = Path("results/channels")
store = ChannelStore(channels_dir, uuid4())

# Query with all filters
table = store.query(
    "scope.ch1_waveform",
    session_id="abc12345",     # First 8 chars of UUID
    last_n=1000,               # Last N rows
    max_points=500,            # LTTB decimation
)

# Result is a PyArrow Table
print(table.to_pandas())
```

### Cross-Process via ChannelClient

```python
from litmus.data.channels.client import ChannelClient

client = ChannelClient("grpc://localhost:8815")

# Same query API as ChannelStore
table = client.query("dmm.voltage", max_points=500)

# List available channels
channels = client.channels()
```

## LTTB Decimation

When `max_points` is set, Litmus applies Largest Triangle Three Buckets (LTTB) downsampling. This is a visually lossless algorithm that preserves peaks and valleys — much better than naive stride decimation for visualization.

Use `max_points` when displaying data in charts. For analysis, query without decimation.

## Query Parameters

| Parameter | Description | Python | HTTP |
|-----------|-------------|--------|------|
| `channel_id` | Channel name | *required* | URL path |
| `session_id` | Filter to session (8-char prefix match) | `session_id=` | `?session_id=` |
| `start` | Time range start | `start=datetime` | `?start=ISO` |
| `end` | Time range end | `end=datetime` | `?end=ISO` |
| `last_n` | Last N rows only | `last_n=int` | `?last_n=int` |
| `max_points` | LTTB decimation target | `max_points=int` | `?max_points=int` |

Filters apply in order: session → time range → last_n → max_points.

## See Also

- [Three Stores Architecture](../concepts/three-stores.md) — Where channels fit
- [Flight Streaming](../concepts/flight-streaming.md) — Cross-process access model
