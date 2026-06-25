# Capture an artifact

Attach a file artifact (scope screenshot, camera frame, vendor capture file, IV curve, or a Pydantic record) to a test run so it lands in the operator UI as a clickable, viewable file.

> **Prerequisites.** The `observe` fixture from the bundled pytest plugin, and a value that isn't a scalar or array — an image, a byte capture, or a record. For continuous byte streams, `files.stream` from `litmus.files`.

## Step 1: Single-shot — `observe(name, value)` with a file value

```python
from PIL import Image
from pydantic import BaseModel
from litmus.data.models import XYData


class Report(BaseModel):
    uut_serial: str
    pass_rate: float


def test_thing(observe, verify, ...):
    # PIL image → FileStore PNG
    observe("uut_photo", Image.open("snap.png"))

    # raw bytes → FileStore .bin
    observe("vendor_capture", vendor_driver.fetch_blob())

    # Pydantic model → FileStore JSON
    observe("report", Report(uut_serial="SN001", pass_rate=0.99))

    # XYData (IV curve / S-param sweep / spectrum) → FileStore .npz
    iv = XYData(x=[0.0, 0.5, 1.0], y=[0.0, 2.1, 4.3],
                x_unit="V", y_unit="mA", x_name="Bias", y_name="Current")
    observe("iv_curve", iv)
```

`observe()` records the file in the FileStore and stamps an output named `<name>` carrying a `file://...` URI on the active measurement — click it in `/results/{run_id}` to open the artifact. (Scalars are recorded inline on the measurement row; arrays and waveforms go to the channel store; files go to the FileStore. Why it splits by value shape: see [the three verbs](../../concepts/data/three-verbs.md).)

## Step 2: Continuous byte stream — `files.stream(name, format=...)`

```python
import litmus.files


def test_thing(verify, psu):
    with litmus.files.stream("event_log", format="jsonl") as log:
        log.write({"event": "psu_on", "voltage": 5.0})
        psu.set_voltage(5.0)
        log.write({"event": "psu_off"})

    verify("rail_v", psu.measure_voltage(), Limit(low=4.75, high=5.25, unit="V"))
```

Available formats today: `raw` (binary append), `jsonl` (one JSON value per line), `tdms` (requires `[tdms]` extra), `h5` (requires `[hdf5]` extra). `format=` is the one place the platform makes you be explicit — it can't infer `mp4` vs `wav` vs `tdms` from opaque bytes.

When the `with` block exits, the finished file's `file://...` URI is recorded as an output named `<name>` on the active measurement. (Litmus brackets the capture with `FileStarted` / `FileEnded` events.)

## Step 3: Read it back

Open `/results/{run_id}` in the operator UI. Each measurement row shows its named outputs; `file://...` URIs render as clickable links. Inline view by MIME: PNG renders as `<img>`, JSON is pretty-printed, text shown plain, binary falls back to download.

## Step 4: Custom types

For types the platform doesn't recognize yet:

```python
from litmus.data.files import register_serializer


register_serializer(
    MyVendorFrame,
    extension=".vfm",
    mime="application/x-vendor-frame",
    write=lambda value, dest: dest.write_bytes(value.to_bytes()),
)
```

Register once at module level; subsequent `observe(name, my_vendor_frame_instance)` calls route automatically. Without a registered handler, the platform falls back to pickle and emits a `RuntimeWarning` naming the type so you see what needs registering.

## See also

- [Three verbs concept page](../../concepts/data/three-verbs.md) — discrete vs continuous, ChannelStore vs FileStore routing
- [How-to — Capture a waveform](capture-waveform.md) — the ChannelStore counterpart
- [How-to — Stream a live channel](stream-live-channel.md) — the ChannelStore byte-stream counterpart
- [Reference — Waveform / XYData / Outcome](../../reference/data/models.md) — model definitions
