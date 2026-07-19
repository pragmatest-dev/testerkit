# Channel Storage Schema

Each TesterKit channel produces one or more **Arrow IPC stream files** (`.arrow` extension). These are standard Apache Arrow IPC format files — DuckDB, pandas, Polars, and PyArrow all read them directly without TesterKit.

## File layout

```
<data_dir>/channels/{date}/
├── {channel_id}_{session8}.arrow          # First segment for this channel + session
├── {channel_id}_{session8}_001.arrow      # Rotation: written when segment reaches flush threshold
├── {channel_id}_{session8}_002.arrow      # Further rotations as data accumulates
└── ...
```

`{date}` is the UTC date of the first write to the channel (`YYYY-MM-DD`). `{session8}` is the first eight hex characters of the session UUID. Each channel gets its own file tree — two channels from the same session land in two separate files.

**Segment rotation.** Instead of a single growing file, the store writes a new segment file each time the buffered sample count reaches the flush threshold. Closed segments are immediately readable by any Arrow-capable reader; the in-progress segment becomes readable once it is flushed and closed. This means a long-running session produces several numbered segments per channel; all are needed for a complete picture.

## Arrow IPC format

Each `.arrow` file is an Arrow IPC **stream** (not a random-access file). Open it with `pyarrow.ipc.open_stream`:

```python
import pyarrow.ipc as ipc
import pyarrow as pa

reader = ipc.open_stream(pa.OSFile("data/channels/2026-06-01/psu.voltage_a1b2c3d4.arrow", "rb"))
table = reader.read_all()
print(table.schema)
print(table.to_pandas())
```

Or with DuckDB (reads multiple segments in one query):

```sql
SELECT * FROM read_ipc_stream('data/channels/2026-06-01/psu.voltage_a1b2c3d4*.arrow');
```

## Common columns (all channel shapes)

Every channel file carries these columns regardless of value type:

| Column | Arrow type | Description |
|--------|-----------|-------------|
| `received_at` | `timestamp[us, UTC]` | When the system received the sample (always set; never null) |
| `sampled_at` | `timestamp[us, UTC]` (nullable) | When the instrument captured the value at the source. Null if the driver does not provide a hardware timestamp |
| `source_method` | `utf8` | How the sample was captured — typically the driver method name (e.g. `"measure"`, `"get_waveform"`) |
| `session_id` | `utf8` | Session UUID — groups all channels captured in one test run |
| `sample_offset` | `int64` | Monotonically increasing per-`(channel, session)` position. Use `(session_id, sample_offset)` to deduplicate when stitching live and at-rest data |

## Scalar channels

A scalar channel stores one numeric, boolean, or string value per row. The `value` column type matches the Python type of the first write:

| First-write Python type | `value` Arrow type |
|------------------------|---------------------|
| `float` | `float64` |
| `int` | `int64` |
| `bool` | `bool` |
| `str` | `utf8` |

Full column set for a scalar channel:

| Column | Arrow type | Description |
|--------|-----------|-------------|
| `received_at` | `timestamp[us, UTC]` | System receipt time |
| `sampled_at` | `timestamp[us, UTC]` (nullable) | Hardware sampling time |
| `value` | Inferred from first write | The measured or set value |
| `source_method` | `utf8` | Capture method |
| `session_id` | `utf8` | Session UUID |
| `sample_offset` | `int64` | Monotonic write position |

## Array channels

An array channel stores one waveform (a sequence of values) per row. Scope acquisitions, DAQ block reads, and any Python `list` or `numpy.ndarray` write produce array-shape rows.

| Column | Arrow type | Description |
|--------|-----------|-------------|
| `received_at` | `timestamp[us, UTC]` | System receipt time |
| `sampled_at` | `timestamp[us, UTC]` (nullable) | Hardware sampling time |
| `value` | `list<leaf>` — leaf inferred from values | The waveform payload. Leaf type follows the same rules as scalar: `float64`, `int64`, `bool`, or `utf8` from first write |
| `sample_interval` | `float64` | Time between consecutive samples within one `value` (seconds). Use this with `sampled_at` to reconstruct the sample timeline |
| `source_method` | `utf8` | Capture method |
| `session_id` | `utf8` | Session UUID |
| `sample_offset` | `int64` | Monotonic write position |

## Struct channels

A dict write produces a struct-shape channel. Each top-level key in the dict becomes its own Arrow column, with the type inferred from its first value.

## Schema inference

The Arrow schema for each channel is fixed on the **first write** to that channel in a session. Subsequent writes must match the inferred shape — a type mismatch within a session raises an error. The schema is embedded in the IPC file's stream header, so readers see the correct types without any additional metadata lookup.

## Schema metadata

Each Arrow file carries two keys in the stream-level schema metadata:

| Key | Description |
|-----|-------------|
| `testerkit.channel_descriptor` | JSON-encoded channel descriptor — the full channel identity, including channel ID, value type, unit, instrument role, resource, session ID, hostname, first-seen timestamp, and a user attributes bag. Read this to reconstruct channel identity without scanning rows. |
| `schema_version` | Channel IPC format version (`"0.1"`). Bump when the at-rest column shape changes in a breaking way. |

Read metadata in Python:

```python
import pyarrow.ipc as ipc
import pyarrow as pa

reader = ipc.open_stream(pa.OSFile("psu.voltage_a1b2c3d4.arrow", "rb"))
meta = reader.schema.metadata
print(meta[b"schema_version"])          # b"0.1"
print(meta[b"testerkit.channel_descriptor"])  # JSON blob with channel identity
```

## Querying across sessions

To query all segments for a channel across sessions, glob all matching files and concatenate:

```python
import pyarrow as pa
import pyarrow.ipc as ipc
from pathlib import Path

tables = []
for seg in sorted(Path("data/channels").glob("*/psu.voltage_*.arrow")):
    try:
        tables.append(ipc.open_stream(pa.OSFile(str(seg), "rb")).read_all())
    except pa.ArrowInvalid:
        pass  # Torn segment (in-progress) — skip
if tables:
    result = pa.concat_tables(tables, promote_options="permissive")
```

The same glob works in DuckDB:

```sql
SELECT * FROM read_ipc_stream('data/channels/*/psu.voltage_*.arrow')
ORDER BY received_at;
```

## See also

- [Parquet Storage Schema](parquet-schema.md) — the run / step / vector / measurement at-rest format
- [Query API](query-api.md) — how to query channel data through TesterKit
