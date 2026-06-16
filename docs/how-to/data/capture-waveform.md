# Capture a Waveform and Judge Derived Scalars

Capture a scope trace in a test, store it in ChannelStore, and verify rise time and overshoot against limits — so every failing measurement row links directly to the supporting waveform.

> **Prerequisites.** A scope driver class with a `capture() -> Waveform` method (mock or real); the `observe` and `verify` fixtures from the bundled pytest plugin (taken by name in the test signature, no import); `Limit` imported from `litmus`; `Waveform` imported from `litmus.data.models`.

## Step 1: Define the scope driver

Your driver class must return `Waveform` from its acquisition method. `Waveform` carries `Y` (sample values), `dt` (sample interval in seconds), and optional `t0` and `attributes`.

```python
# drivers/scope.py
from litmus import Waveform

class Scope:
    """Oscilloscope interface — block-mode capture only."""

    def __init__(self, resource: str = "") -> None:
        self.resource = resource

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def capture(self) -> Waveform:
        """Acquire one trace from the active channel and return as Waveform."""
        raise NotImplementedError
```

For a mock, pass a zero-argument callable to `Mock` — the callable runs on every `scope.capture()` call:

```python
# conftest.py
import pytest
from litmus import Mock
from drivers import Scope, synthesize_psu_step_response

@pytest.fixture(scope="session")
def scope(mock_instruments) -> Scope:
    if mock_instruments:
        return Mock(Scope, capture=synthesize_psu_step_response)
    return Scope(resource="TCPIP::192.168.1.103::INSTR")
```

Real drivers return `Waveform` from whatever block-mode acquisition call they expose. The fixture shape is the same.

## Step 2: Capture and observe in the test body

```python
wf = scope.capture()
observe("scope_step", wf)  # routes to ChannelStore; stamps out_scope_step on this vector
```

`observe` routes the `Waveform` to ChannelStore and stamps `out_scope_step = channel://scope_step?session=…` on the active test vector. Every `verify` call that follows in this test carries that URI in its parquet row. Call `observe` before the `verify` calls that depend on the same waveform.

## Step 3: Derive scalars and verify each

Write pure functions that work on `wf.Y` and `wf.dt`, then pass each result to `verify` with a `Limit`:

```python
import math
from litmus import Limit
from litmus import Waveform


def compute_rise_time_us(wf: Waveform, *, v_final: float, low: float = 0.1, high: float = 0.9) -> float:
    """Return 10%–90% rise time in microseconds."""
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

Both `verify` calls share the same `out_scope_step` URI because `observe` stamps it on the vector before either call. `verify` raises `AssertionError` on a failing value — if you want to record failures without stopping the test, use `measure` instead (see [`measure` fixture](../../reference/pytest/fixtures.md#measure--function)).

## Step 4: Read it back

Open `http://localhost:8000/results`. Click the run row to reach `/results/<run_id>`, then open the **Measurements** tab. The `rise_time_us` and `overshoot_v` rows each show `out_scope_step` as a clickable URI. Click it to jump to `/channels/scope_step`, which plots the waveform.

From any failing measurement you are one click from the trace that produced it.

LTTB downsampling applies automatically above 500 points when the channels page renders — the peaks and valleys that matter for rise time and overshoot are preserved.

## See also

- [The Three Test-Author Verbs](../../concepts/data/three-verbs.md) — when to use `observe` vs `stream`; why `verify` rejects `Waveform` directly
- [Tutorial 11 — Waveforms and evidence](../../tutorial/11-waveforms-and-evidence.md) — build this pattern from scratch with a full walkthrough
- [Models reference — `Limit`](../../reference/data/models.md#model-limit) — field reference for inline limits
- [Models reference — `Waveform`](../../reference/data/models.md#model-waveform) — `Y`, `dt`, `t0`, `attributes` fields
- [Fixtures reference — `observe` and `verify`](../../reference/pytest/fixtures.md) — full signatures and behavior
