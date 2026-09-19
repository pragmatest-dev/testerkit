# FileStore Schema

TesterKit persists non-numeric artifacts (images, waveforms, data logs, vendor files) as opaque blobs in the FileStore. Each blob is paired with a JSON sidecar that describes the artifact. This page covers the on-disk layout, the URI format, and every field of the sidecar.

## On-disk layout

```
<data_dir>/files/
└── {date}/                        # UTC date of the write (YYYY-MM-DD)
    └── {session_id}/
        ├── {filename}             # Artifact blob
        └── {filename}.meta.json   # Sidecar — MIME, size, attributes, provenance
```

`{date}` uses the UTC calendar date at write time, matching the date-partitioning convention of runs, events, and channels. `{session_id}` is the session UUID of the test run that produced the artifact.

### Filename convention

Each artifact name maps to a filename using these rules:

| Context | Filename pattern |
|---------|-----------------|
| Written with a `vector_id` | `{vector_id[:8]}_{name}{ext}` |
| Written without a `vector_id` | `{name}{ext}` |
| Collision on second write | `{stem}_2{ext}`, `{stem}_3{ext}`, … |

The collision suffix preserves claim-check immutability: a repeated write never overwrites an existing blob. Each write produces its own distinct file. The `out_<name>` field on a run record follows last-write-wins independently of the files on disk.

## URI format

Every artifact is identified by a `file://` URI:

```
file://{date}/{session_id}/{filename}
```

Example:

```
file://2026-06-28/a3f1b2c4d5e6/abc12345_scope.ch1.capture.npz
```

The URI encodes the full backend-relative key. Resolution to bytes requires no catalog lookup — the store strips the `file://` prefix and reads the key directly from the configured backend. This means a local-to-remote backend swap is transparent to code that holds URIs.

## Sidecar format

Each artifact's sidecar is a JSON file named `{filename}.meta.json`, written atomically alongside the artifact. The sidecar validates as a `FileArtifactMetadata` Pydantic model.

### Sidecar fields

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | Format version of this sidecar. Current: `"0.1"`. |
| `mime` | string | MIME type of the artifact (see [MIME convention](#mime-convention)). |
| `extension` | string | File extension on disk, including the dot (e.g. `.npz`, `.bin`, `.json`). |
| `size_bytes` | integer | Byte size of the artifact after writing. Read back from the backend after the atomic publish so it reflects the actual stored size, not a pre-write estimate. |
| `attributes` | object | User-supplied metadata bag. Keys and values are arbitrary. Empty object `{}` when no attributes were passed. |
| `instrument_role` | string | Station-config instrument role that produced the artifact (e.g. `"scope"`, `"psu"`). Empty string when not applicable. Populated on the FileStore fallback path when a waveform or channel-shaped value is routed here because no ChannelStore is wired. |
| `resource` | string | VISA or network resource string for the instrument paired with `instrument_role`. Empty string when not applicable. |
| `run_id` | string or null | UUID of the run that produced this artifact. `null` for writes outside a run. Persisted so the catalog can filter by run and a daemon restart can rebuild its index from sidecars alone. |

Example sidecar:

```json
{
  "schema_version": "0.1",
  "mime": "application/x-numpy-npz",
  "extension": ".npz",
  "size_bytes": 4096,
  "attributes": {
    "channel": "ch1",
    "scale_v_div": 0.5
  },
  "instrument_role": "scope",
  "resource": "USB0::0x0957::0x1799::MY12345678::INSTR",
  "run_id": "3f6b1a2c-0d4e-4f8a-b2c7-1e3d5a7f9b0e"
}
```

### `schema_version`

`schema_version` is the first version stamp on the FileStore sidecar. Sidecars written before it was introduced do not carry the field; when `FileArtifactMetadata` validates such a sidecar, it fills in `"0.1"` as the default, so reads are backward-tolerant. Use `schema_version` to detect format changes in downstream pipelines — a value other than `"0.1"` signals a format revision; consult the release notes for that version.

## MIME convention

The MIME type in the sidecar follows the TesterKit serialization table. Each value type maps to a fixed MIME:

| Python type | Extension | MIME |
|-------------|-----------|------|
| `bytes` | `.bin` | `application/octet-stream` |
| `Path` (any suffix) | source suffix | `application/octet-stream` |
| `Waveform` | `.npz` | `application/x-numpy-npz` |
| `XYData` | `.npz` | `application/x-numpy-npz` |
| `numpy.ndarray` | `.npy` | `application/x-numpy-npy` |
| Pydantic `BaseModel` | `.json` | `application/json` |
| `PIL.Image.Image` | `.png` | `image/png` |
| `pandas.DataFrame` | `.parquet` | `application/vnd.apache.parquet` |
| `pyarrow.Table` | `.arrow` | `application/vnd.apache.arrow.stream` |
| Fallback (pickle) | `.pkl` | `application/x-python-pickle` |

For `Path` values, the extension on disk follows the source file's suffix, not the serializer default. The MIME stays `application/octet-stream` because the store cannot inspect the content; pass the real MIME via `attributes` if a downstream reader needs it.

## Reading sidecars in Python

```python
from testerkit.data.files import FileStore, FileArtifactMetadata

store = FileStore()

# Read via the store (resolves URI to backend bytes automatically)
meta: FileArtifactMetadata | None = store.read_attributes(uri)
if meta is not None:
    print(meta.schema_version)  # "0.1"
    print(meta.mime)
    print(meta.size_bytes)
    print(meta.attributes)
```

To read a sidecar directly from disk without going through the store:

```python
import json
from pathlib import Path
from testerkit.data.files import FileArtifactMetadata

sidecar_path = Path("data/files/2026-06-28/my-session/artifact.bin.meta.json")
meta = FileArtifactMetadata.model_validate_json(sidecar_path.read_bytes())
print(meta.schema_version)
```

## Backward compatibility

The FileStore sidecar is a published consumer surface. Fields will not be removed or renamed within a `schema_version`. New optional fields may be added in a future `schema_version`; the `schema_version` field itself will change when the format breaks backward compatibility.

Sidecars written before `schema_version` was introduced validate successfully — `FileArtifactMetadata` fills in the `"0.1"` default for the missing field.

## See also

- [Parquet schema](parquet-schema.md) — run, step, vector, and measurement rows in the Parquet files
- [Query API](query-api.md) — how to query runs and measurements in analysis
- [Models](models.md) — Pydantic model index
