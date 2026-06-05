# Step 11: Waveforms and Evidence

**Goal:** Capture a raw waveform alongside a scalar measurement so every pass/fail row links back to the trace that produced it.

## Prerequisites

- [Step 2: Running Without Hardware](02-mock-instruments.md) — mock instruments
- [Step 4: Add Limits](04-limits.md) — `verify` + `Limit`
- [Step 7: Real Instruments](07-real-instruments.md) — writing driver fixtures

## The scenario

A PSU steps from 0 V to 5 V. A scope captures the transient. You want two judgments: rise time under 20 µs and overshoot under 0.5 V. Both numbers come from the same captured trace.

The problem: `verify` accepts a scalar. `scope.capture()` returns a `Waveform` — 1 000 samples, a `t0` timestamp, and a `dt` sample interval. Passing the waveform directly to `verify` raises `TypeError`.

The solution is two verbs in sequence: `observe` the raw waveform first, then `verify` each derived scalar. `observe` routes the waveform to ChannelStore and stamps its URI on the active test vector — so both verify rows carry `out_scope_step`, a `channel://` link to the waveform they were computed from.

See [The Three Test-Author Verbs](../concepts/data/three-verbs.md) for the model behind this pattern.

## The observe + verify pattern

```python
# tests/test_psu_step_response.py

from litmus import Limit
from litmus import Waveform


def compute_rise_time_us(wf: Waveform, *, v_final: float, low: float = 0.1, high: float = 0.9) -> float:
    """Return 10 %–90 % rise time in microseconds."""
    low_v = low * v_final
    high_v = high * v_final
    i_low = next((i for i, y in enumerate(wf.Y) if y >= low_v), None)
    i_high = next((i for i, y in enumerate(wf.Y) if y >= high_v), None)
    if i_low is None or i_high is None:
        return math.nan
    return (i_high - i_low) * wf.dt * 1e6


def compute_overshoot_v(wf: Waveform, *, v_final: float) -> float:
    """Return peak overshoot above v_final in volts (0.0 if no overshoot)."""
    return max(0.0, max(wf.Y) - v_final)


def test_psu_step_response(observe, verify, psu, scope) -> None:
    psu.set_voltage(5.0)

    wf = scope.capture()
    observe("scope_step", wf)  # routes to ChannelStore; stamps out_scope_step on this vector

    rise_us = compute_rise_time_us(wf, v_final=5.0)
    overshoot_v = compute_overshoot_v(wf, v_final=5.0)

    verify("rise_time_us", rise_us, Limit(low=0, high=20, units="us"))
    verify("overshoot_v", overshoot_v, Limit(low=0, high=0.5, units="V"))
```

What each line does:

1. `psu.set_voltage(5.0)` — triggers the step. The PSU fixture is mocked; its `set_voltage` is a no-op.
2. `scope.capture()` — acquires one trace. With the mock wired to `synthesize_psu_step_response`, this returns a fresh `Waveform` with realistic shape and small per-call jitter.
3. `observe("scope_step", wf)` — writes the waveform to ChannelStore and stamps `out_scope_step = channel://scope_step?session=…` on the active test vector. Every measurement row emitted from this point forward in this test carries that URI.
4. `compute_rise_time_us` / `compute_overshoot_v` — pure functions that work on `wf.Y` (sample values) and `wf.dt` (sample interval in seconds).
5. `verify(...)` — records a parquet measurement row with value, limit, and `out_scope_step`. Both rows carry the same URI.

`observe` and `verify` are pytest fixtures provided by Litmus's bundled plugin — they appear as parameters in the test signature with no import needed.

## The mock scope

`drivers/scope.py` defines the `Scope` class and `synthesize_psu_step_response`:

```python
# drivers/scope.py

class Scope:
    """Oscilloscope interface — block-mode capture only."""

    def __init__(self, resource: str = "") -> None:
        self.resource = resource

    def capture(self) -> Waveform:
        """Acquire one trace from the active channel and return as Waveform."""
        raise NotImplementedError
```

```python
def synthesize_psu_step_response() -> Waveform:
    """Generate one PSU step-response trace with realistic shape and jitter.

    * Pre-trigger: 0 V for the first 100 samples (100 µs at 1 MS/s).
    * Rising edge: exponential approach to 5 V with ~5 µs time constant.
    * Overshoot: ~3 % of final value, damped sinusoid with ~30 µs decay.

    Each call jitters rise time (±10 %) and overshoot (±33 %).
    """
    ...
    return Waveform(
        t0=t0,
        dt=_SAMPLE_INTERVAL_S,
        Y=samples,
        attributes={"units": "V", "channel": "ch1", "trigger": "rising"},
    )
```

`conftest.py` wires the fixture:

```python
# conftest.py

from litmus import Mock

@pytest.fixture(scope="session")
def scope(mock_instruments) -> Scope:
    if mock_instruments:
        return Mock(Scope, capture=synthesize_psu_step_response)
    return Scope(resource="TCPIP::192.168.1.103::INSTR")
```

`Mock(Scope, capture=synthesize_psu_step_response)` passes a callable as the value for `capture`. `Mock` wraps that callable so every call to `scope.capture()` invokes `synthesize_psu_step_response()` and returns a fresh waveform. The same pattern works with a lambda or any zero-argument callable.

## Running it

```cli
cd examples/08-waveform-evidence
uv run pytest -v
```

pytest runs one test — `test_psu_step_response` — with both `rise_time_us` and `overshoot_v` passing. Each run produces a slightly different rise time and overshoot because the synthesizer jitters each capture.

Then start the operator UI:

```cli
uv run litmus serve --reload
```

## What landed on disk

The example keeps its data local (set via `data_dir: data` in `litmus.yaml`):

```
data/
  channels/
    2026-06-03/
      scope_step_<session_short>.arrow   ← the captured waveform (ChannelStore)
  events/
    2026-06-03/
      <run_id>-<timestamp>.arrow         ← run events (EventStore)
```

The `scope_step_<session_short>.arrow` file is one Arrow row per `observe` call. Its session-scoped filename means two concurrent test sessions writing `observe("scope_step", wf)` never collide. See [Three Stores Architecture](../concepts/data/three-stores.md) for the full on-disk layout.

## Where to see it in the UI

Open `http://localhost:8000/results`. Click the run row to open the detail view at `/results/<run_id>`.

On the **Measurements** tab, the two rows (`rise_time_us` and `overshoot_v`) each carry `out_scope_step` — a `channel://scope_step?session=…` URI. Click it to jump to `/channels/scope_step`, which plots the waveform. From a failing measurement you are one click from the trace that caused it.

For the full reference on what the Channels page shows, see [Operator UI → Channels](../reference/operator-ui/channels/list.md). For the Measurements tab layout, see [Operator UI → Results — detail](../reference/operator-ui/results/detail.md).

## What's next

Step 12 covers continuous monitoring — streaming samples into ChannelStore from a live sensor feed using `stream`, with the operator UI updating in real time.

For the model behind discrete vs continuous capture and when to reach for each verb, see [The Three Test-Author Verbs](../concepts/data/three-verbs.md).

← [Step 10: Live Monitoring](10-live-monitoring.md)  |  [Tutorial index](index.md)
