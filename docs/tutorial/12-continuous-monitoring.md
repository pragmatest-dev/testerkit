# Step 12: Continuous Monitoring

**Goal:** Stream live DMM readings from an interactive script into the operator UI and watch the channel panel update in real time.

## Prerequisites

- [Step 10: Live Monitoring](10-live-monitoring.md) — `litmus serve`, sessions, events
- [Step 11: Waveforms and Evidence](11-waveforms-and-evidence.md) — `observe`, ChannelStore

## The scenario

A DMM is free-running at 50 Hz during bench characterization. No test to run — just record the rail voltage for 60 seconds and watch it live. At 50 samples/second × 60 seconds that is 3 000 samples on one channel. You want the data on disk, queryable, and visible in the operator panel while the script runs.

This is the "outside the test loop" use case. There is no pytest invocation, no `verify`, no pass/fail judgment.

## The two-layer split

Before looking at the script, the relevant piece from [The Three Test-Author Verbs](../concepts/data/three-verbs.md):

| Layer | Where you are | Continuous write |
|---|---|---|
| Test-author verbs | Inside a pytest test body | `stream` fixture |
| Store-direct | Outside a test — notebook, script, REPL | `litmus.channels.stream` |

The store-direct surface is the same ChannelStore underneath. It skips the per-test bookkeeping that `observe` does inside a test — here there's no test step to attach the readings to.

## The streaming script

```python
# scripts/live_dmm_monitor.py

import time

import litmus.channels
from litmus import connect

RATE_HZ = 50.0
DURATION_S = 60.0


def main() -> None:
    interval_s = 1.0 / RATE_HZ
    print(f"Streaming dmm.voltage at {RATE_HZ:.0f} Hz for {DURATION_S:.0f} s")
    print("Open http://localhost:8000/channels/dmm.voltage to watch live.")

    with connect("bench_01") as station:
        dmm = station.instrument("dmm")

        n = int(RATE_HZ * DURATION_S)
        with litmus.channels.stream("dmm.voltage") as sink:
            for i in range(n):
                sink.write(dmm.measure_voltage())
                if i % 50 == 0:
                    print(f"  {i + 1}/{n} samples")
                time.sleep(interval_s)

    print(f"Done — {n} samples on dmm.voltage. Reload the panel to see the full history.")


if __name__ == "__main__":
    main()
```

Two calls do all the work:

- `connect("bench_01")` opens a Litmus session — EventStore, ChannelStore, and the instrument pool. It reads `bench_01` from `stations/bench_01.yaml` (or the `default_station` in `litmus.yaml`). The `with` block records `SessionStarted` on entry and `SessionEnded` on exit, with proper cleanup on Ctrl-C.
- `litmus.channels.stream("dmm.voltage")` opens a context-managed sink named `dmm.voltage`. Every `sink.write(value)` appends one sample to ChannelStore. The sink closes cleanly at the end of the `with` block; Ctrl-C mid-stream leaves partial data on disk.

`station.instrument("dmm")` connects the driver declared in `stations/bench_01.yaml` under role `dmm` and returns the connected instrument.

## The interactive entry point

`from litmus import connect` is the non-pytest on-ramp. The pytest plugin opens and closes a session for you around each run; here you open and close the session yourself.

```python
with connect("bench_01") as station:
    ...
```

Inside a pytest run, the session opens and closes for you; outside pytest, you write it explicitly.

## The self-simulating DMM

`drivers/dmm.py` is a concrete `DMM` class whose `measure_voltage()` returns a 30-second sine wave (±50 mV) around 3.3 V with ±5 mV per-sample noise. `litmus.yaml` does not set `mock_instruments: true`, so the script runs the real `DMM` class — no mocking involved.

```python
# drivers/dmm.py (excerpt)
class DMM:
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def measure_voltage(self) -> float:
        t = time.monotonic() - _T0
        drift = _AMPLITUDE_V * math.sin(2 * math.pi * t / _PERIOD_S)
        noise = random.gauss(0, _NOISE_V)
        return _NOMINAL_V + drift + noise
```

To run against a real bench, replace this class with a PyMeasure or PyVISA implementation. The script, station YAML, and streaming primitives are unchanged.

## Running it

Two terminals.

**Terminal 1 — operator UI:**

```cli
cd examples/09-instrument-streaming
uv run litmus serve --reload
```

Open `http://localhost:8000/channels/dmm.voltage`. The channel does not exist until the script writes its first sample.

**Terminal 2 — the streaming script:**

```cli
cd examples/09-instrument-streaming
uv run python scripts/live_dmm_monitor.py
```

The channel panel updates as each sample lands — no page reload. Stop early with Ctrl-C; partial data stays on disk.

For reference on what the Channels page shows, see [Operator UI → Channels](../reference/operator-ui/channels/list.md).

## What lands on disk

The example sets `data_dir: data` in `litmus.yaml`, so everything lands under `examples/09-instrument-streaming/data/`:

```
data/
├── events/{date}/{session_id}-{pid}.arrow      ← SessionStarted, ChannelStarted, etc.
└── channels/{date}/dmm.voltage_{session_short}.arrow   ← the recorded samples
```

ChannelStore files are session-scoped — two concurrent script runs write to two separate files. The operator UI shows both on the same `dmm.voltage` panel, matched by channel name. Run the script a second time and both sessions' data appear on the same timeline; add `session_id=…` to a query to scope to one run.

See [Data stores](../concepts/data/data-stores.md) for the full on-disk layout and retention model.

## What's next

Step 13 covers byte-stream artifacts — video, audio, and vendor capture formats — using `litmus.files.stream`.

For the full model behind discrete vs continuous capture, and when to reach for `observe` vs `stream`, see [The Three Test-Author Verbs](../concepts/data/three-verbs.md).

← [Step 11: Waveforms and Evidence](11-waveforms-and-evidence.md)  |  [Tutorial index](index.md)
