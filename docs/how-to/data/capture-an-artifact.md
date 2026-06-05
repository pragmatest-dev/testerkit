# Capture an artifact

Attach a non-numeric artifact (image, vendor blob, Pydantic model, byte stream) to a test run so it lands in the operator UI as a clickable, viewable file.

> **Prerequisites.** The `observe` fixture from the bundled pytest plugin, and a value that's not a scalar/array (i.e. doesn't fit ChannelStore's typed-row schema). For byte streams, `files.stream` from `litmus.files`.

## Step 1: Single-shot — `observe(name, value)` with a blob-shaped value

```python
from PIL import Image
from pydantic import BaseModel


class Report(BaseModel):
    dut_serial: str
    pass_rate: float


def test_thing(observe, verify, ...):
    # PIL image → FileStore PNG
    observe("dut_photo", Image.open("snap.png"))

    # raw bytes → FileStore .bin
    observe("vendor_capture", vendor_driver.fetch_blob())

    # Pydantic model → FileStore JSON
    observe("report", Report(dut_serial="SN001", pass_rate=0.99))
```

The platform routes by value shape: scalars/arrays → ChannelStore, blobs → FileStore. Each call stamps `out_<name>` on the active vector with the resulting `file://...` URI. The verify row reached from `/results/{run_id}` shows all four `out_*` columns; clicking any opens the artifact.

## Step 2: Continuous byte stream — `files.stream(name, format=...)`

```python
import litmus.files


def test_thing(verify, psu):
    with litmus.files.stream("event_log", format="jsonl") as log:
        log.write({"event": "psu_on", "voltage": 5.0})
        psu.set_voltage(5.0)
        log.write({"event": "psu_off"})

    verify("rail_v", psu.measure_voltage(), Limit(low=4.75, high=5.25, units="V"))
```

Available formats today: `raw` (binary append), `jsonl` (one JSON value per line), `tdms` (requires `[tdms]` extra), `h5` (requires `[hdf5]` extra). `format=` is the one place the platform makes you be explicit — it can't infer `mp4` vs `wav` vs `tdms` from opaque bytes.

`StreamStarted` and `StreamEnded` lifecycle events bracket the sink session. The final `file://...` URI lands on the active vector's `out_<name>` column when the `with` block exits.

## Step 3: Read it back

In the operator UI:

- `/results/{run_id}` — verify rows show every `out_*` column with `file://...` URI as clickable link
- Inline view by MIME: PNG renders as `<img>`, JSON is pretty-printed, text shown plain, binary falls back to download

In code:

```python
from litmus.data.backends.parquet import load_file

artifact = load_file(parquet_path=None, ref="file://abc.../dut_photo.png")
# → PIL.Image (PNG decoded via the serializer registry's PIL handler)
```

## Step 4: Custom types

For types the registry doesn't know:

```python
from litmus.data.files import register_serializer


register_serializer(
    MyVendorFrame,
    extension=".vfm",
    mime="application/x-vendor-frame",
    write=lambda value, dest: dest.write_bytes(value.to_bytes()),
)
```

Register once at module level; subsequent `observe(name, my_vendor_frame_instance)` calls route automatically. Without a registered serializer, the platform falls back to pickle and emits a `RuntimeWarning` naming the type so you see what needs registering.

## See also

- [Three verbs concept page](../../concepts/data/three-verbs.md) — discrete vs continuous, ChannelStore vs FileStore routing
- [How-to — Capture a waveform](capture-waveform.md) — the ChannelStore counterpart
- [How-to — Stream a live channel](stream-live-channel.md) — the ChannelStore byte-stream counterpart
- [Reference — Waveform / XYData / Outcome](../../reference/data/models.md) — model definitions
