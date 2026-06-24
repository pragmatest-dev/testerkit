# Query Channel Data

Channel data stores time-series readings — waveforms, voltage traces, temperature logs. Query it via MCP tool, HTTP API, or Python.

> **Prerequisites.** Channel data already written under `<data_dir>/channels/` — readings captured automatically when an instrument is driven through Litmus, or written explicitly via `context.observe()`. See [Data stores](../../concepts/data/data-stores.md). Empty stores return empty results, not errors. For the HTTP path, `litmus serve` must be running.

Get a `session_id` from `litmus runs` or the run report; channels share their run's session. Use the first 8 characters (e.g., `"3f9a1c2b"`) to filter to one run.

## MCP Tool: `litmus_channels`

```
# Get channel data
litmus_channels(channel_id="dmm.voltage")

# Filter to a session
litmus_channels(channel_id="scope.ch1_waveform", session_id="3f9a1c2b")

# Last 100 readings
litmus_channels(channel_id="dmm.voltage", last_n=100)

# Point cap for charts
litmus_channels(channel_id="scope.ch1_waveform", max_points=500)
```

## HTTP API

```bash
# List all known channels
curl http://localhost:8000/api/channels

# Get channel data
curl http://localhost:8000/api/channels/dmm.voltage

# With filters
curl "http://localhost:8000/api/channels/scope.ch1?session_id=3f9a1c2b&max_points=500"

# Time range — HTTP uses since/until (Python uses start/end)
curl "http://localhost:8000/api/channels/dmm.voltage?since=2026-03-10T14:00:00&until=2026-03-10T15:00:00"
```

## Python: `channels.query`

The simplest entry point — no file paths to wire up; you just name the channel:

```python
import litmus.channels as channels

table = channels.query("dmm.voltage", last_n=500)  # PyArrow Table
```

`query` is the pull verb: call it once for a report, or poll it in your own loop for a refreshing sparkline. For push (react as data lands) use `channels.latest` or `channels.live` instead — see [Choosing a channel verb](choosing-a-channel-verb.md).

### Plotting the result

The returned table has columns: `received_at`, `sampled_at`, `value`, `source_method`, `session_id`, `sample_interval`. Use `received_at` for the time axis and `value` for the signal:

```python
import litmus.channels as channels
import matplotlib.pyplot as plt

t = channels.query("scope.ch1_waveform", max_points=500).to_pandas()
plt.plot(t["received_at"], t["value"])
plt.show()
```

`max_points` caps how many points come back so charts stay fast and still show every peak and valley — set it when plotting (e.g. `max_points=500`); omit it for analysis when you need every sample. The downsampling algorithm is [LTTB](../../concepts/data/data-stores.md).

### Filtering by session and time

```python
from datetime import datetime, UTC

table = channels.query(
    "dmm.voltage",
    session_id="3f9a1c2b",            # first 8 chars of a session UUID
    start=datetime(2026, 3, 10, 14, tzinfo=UTC),
    end=datetime(2026, 3, 10, 15, tzinfo=UTC),
    last_n=1000,
    max_points=500,
)
```

Note: Python uses `start`/`end`; the HTTP API uses `since`/`until`. Copying a `curl` filter directly to Python requires renaming these parameters.

## Cross-Process via `ChannelClient`

To query channels written by a different machine, connect to that machine's channel daemon:

```python
from litmus.data.channels.client import ChannelClient

client = ChannelClient("grpc://<host>:8815")

table = client.query("dmm.voltage", max_points=500)

# List available channels
descriptors = client.channels()
client.close()
```

## Query Parameters

| Parameter | Description | Python | HTTP |
|-----------|-------------|--------|------|
| `channel_id` | Channel name | *required* | URL path |
| `session_id` | Filter to session (8-char prefix match) | `session_id=` | `?session_id=` |
| Time range start | Filter rows on or after this time | `start=datetime` | `?since=ISO` |
| Time range end | Filter rows on or before this time | `end=datetime` | `?until=ISO` |
| `last_n` | Last N rows only | `last_n=int` | `?last_n=int` |
| `max_points` | Point cap for charts (LTTB) | `max_points=int` | `?max_points=int` |

Filters apply in order: session → time range → last_n → max_points.

## See also
- [Choosing a channel verb](choosing-a-channel-verb.md) — when to use query vs latest/live/window
- [Data stores](../../concepts/data/data-stores.md) — where channels fit and how LTTB downsampling works
- [Flight streaming](../../concepts/data/flight-streaming.md) — cross-process access model
