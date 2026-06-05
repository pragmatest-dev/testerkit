# Stage 9 — Continuous instrument streaming + live operator UI

Where streaming actually shines: continuous instrument acquisition that
feeds the live UI panel push-style. No pytest involved — this is the
**interactive** use case (Jupyter notebook, REPL, validation script,
bench debug). Same primitive (`channels.stream`) that test code uses;
this time invoked from a standalone Python script.

## What this example does

A standalone script connects to a station, opens a streaming sink to
a channel called `dmm.voltage`, and pushes one DMM reading every 20 ms
for 60 seconds — about 3,000 samples. The data lands in ChannelStore
session-scoped Arrow IPC files; the operator UI's `/channels/dmm.voltage`
panel renders it as a live-updating chart.

A real bench replaces `drivers/dmm.py` with a PyMeasure or PyVISA
implementation. The rest of the example — connection lifecycle,
streaming sink, live UI — is unchanged.

## Layout

```
examples/09-instrument-streaming/
├── README.md
├── litmus.yaml
├── pyproject.toml
├── drivers/
│   ├── __init__.py
│   └── dmm.py                       # self-simulating DMM
├── stations/
│   └── bench_01.yaml
└── scripts/
    └── live_dmm_monitor.py          # the streaming script
```

`drivers/dmm.py` is a concrete DMM class whose `measure_voltage()`
returns a 30-second sine wave (±50 mV) around 3.3 V with ±5 mV
per-sample noise. Concrete, not mocked — the platform instantiates it
directly because `litmus.yaml` doesn't set `mock_instruments: true`.

## Run it

Two terminals.

**Terminal 1 — operator UI:**

```bash
cd examples/09-instrument-streaming
uv run litmus serve --reload
```

Open `http://localhost:8000/channels/dmm.voltage` in a browser.
(The channel won't exist until the script writes its first sample.)

**Terminal 2 — the streaming script:**

```bash
cd examples/09-instrument-streaming
uv run python scripts/live_dmm_monitor.py
```

Watch the channels panel — samples appear push-style as the script
writes them. The chart updates without a page reload (push, not poll)
via the Flight subscription wired in `litmus serve` startup.

The script runs for 60 seconds. Stop early with Ctrl-C; partial data
stays on disk. Reopen the channel panel after a fresh script run —
all sessions' data accumulates on the same logical channel; per-session
files keep storage segmented for retention.

## Why the imports are this shape

```python
import litmus.channels
from litmus import connect
```

Not test code — no pytest fixtures, no `verify`/`observe` verbs. Outside
the test path, Litmus exposes the data stack via deep imports. The
verbosity is informative: it signals "you're in the store-direct layer,
not the test-author layer." See
[concepts/data/three-verbs.md](../../docs/concepts/data/three-verbs.md)
for the two-layer model.

## What lands on disk

```
data/
├── events/{date}/{session_id}-{pid}.arrow      # SessionStarted, ChannelStarted, etc.
└── channels/{date}/dmm_voltage_{session_short}.arrow   # the samples
```

ChannelStore files are session-scoped. Two concurrent script runs
write to two different files; the operator UI unifies them on the
`dmm.voltage` panel by channel name.

## See also

- [Tutorial 12 — Continuous monitoring](../../docs/tutorial/12-continuous-monitoring.md)
  — step-by-step walkthrough
- [Three verbs concept page](../../docs/concepts/data/three-verbs.md)
  — discrete vs continuous, two-layer surface
- [How-to — Stream a live channel](../../docs/how-to/data/stream-live-channel.md)
  — recipe form
