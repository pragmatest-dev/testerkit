# Stage 10 — FileStore artifacts + byte streams

The FileStore showcase. When a value can't fit in ChannelStore's
typed-row schema, the platform routes it to FileStore. A single
burn-in test demonstrates the four shapes:

- **PIL.Image** — a synthesized photo of the UUT, captured at start
  of test. Lands as PNG; the operator UI renders it inline.
- **bytes** — a vendor capture file (synthesized TDMS-like binary).
  Lands as `.bin`.
- **Pydantic model** — a structured burn-in report. Lands as JSON
  (pretty-printable in the artifact viewer).
- **JSONL byte stream** — a streaming event log opened with
  `files.stream(name, format="jsonl")`. One JSON line per event.
  `StreamStarted` + `StreamEnded` lifecycle events bracket the
  capture; the final `file://...` URI lands on the verify row's
  `out_burn_log` column.

Plus a regular `verify` on the mean rail voltage. All four artifacts
ride on the same vector — `out_uut_photo` / `out_vendor_capture` /
`out_burn_log` / `out_burn_report` are all reachable from the verify
row, so the analyst can navigate from a failing measurement to any
piece of supporting evidence in one click.

## Layout

```
examples/10-artifacts-and-byte-streams/
├── README.md
├── litmus.yaml
├── pyproject.toml
├── pytest.ini
├── conftest.py
├── drivers/
│   ├── __init__.py
│   ├── psu.py            # self-simulating PSU
│   └── scene.py          # PIL-based UUT-photo synthesizer
├── stations/
│   └── bench_01.yaml
└── tests/
    └── test_burn_in.py   # one test, four artifact shapes + verify
```

## Run it

```bash
cd examples/10-artifacts-and-byte-streams
uv run pytest -v
```

Then start the operator UI:

```bash
uv run litmus serve --reload
```

Navigate:
- `http://localhost:8000/results/{run_id}` — the verify row. The
  Artifacts section shows the four `out_*` columns, each a clickable
  `file://...` URI.
- Click `out_uut_photo` → the photo renders inline.
- Click `out_burn_report` → the JSON is shown.
- Click `out_burn_log` → the JSONL log (text view).
- Click `out_vendor_capture` → download (binary, no inline viewer).

## What lands on disk

```
data/
├── events/{date}/{session_id}-{pid}.arrow   # SessionStarted, ObserveEvent x4, StreamStarted/Ended, MeasurementRecorded
├── files/{date}/{session_id}/                # the artifacts:
│   ├── uut_photo.png + .meta.json
│   ├── vendor_capture.bin + .meta.json
│   ├── burn_log.jsonl + .meta.json
│   └── burn_report.json + .meta.json
└── runs/{date}/{ts}_{uut_serial}.parquet     # the verify row + every out_* URI
```

Per-artifact `.meta.json` sidecars carry MIME, extension, size, and
any caller-supplied attributes (build item 1c).

## Why no mock infrastructure

`drivers/psu.py` is a concrete self-simulating class — no
`Mock(DriverClass, ...)` wrapping needed. The platform instantiates
it directly because `litmus.yaml` doesn't set `mock_instruments:
true`. Swap in a real PyMeasure / PyVISA implementation when a
bench is attached; the test is unchanged.

## See also

- [How-to — Capture an artifact](../../docs/how-to/data/capture-an-artifact.md)
  — recipe form
- [Three verbs concept page](../../docs/concepts/data/three-verbs.md)
  — when `observe` routes to ChannelStore vs FileStore (shape-based)
- [Tutorial 11 — Waveforms and evidence](../../docs/tutorial/11-waveforms-and-evidence.md)
  — companion (ChannelStore side)
- [Tutorial 12 — Continuous monitoring](../../docs/tutorial/12-continuous-monitoring.md)
  — companion (channels.stream side)
