"""Oscilloscope driver class plus a PSU-step-response synthesizer.

The ``Scope`` class is the interface tests call against. The
``synthesize_psu_step_response`` function generates a realistic
1000-sample step response (5 V step, ~5 µs rise, ~3 % overshoot,
settling over ~30 µs) with small per-call jitter so successive
captures produce slightly different derived measurements — exactly
the kind of variation a real test would see.

``conftest.py`` wires up the scope fixture as::

    Mock(Scope, capture=synthesize_psu_step_response)

so every call to ``scope.capture()`` returns a fresh waveform.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

from testerkit import Waveform


class Scope:
    """Oscilloscope interface — block-mode capture only."""

    def __init__(self, resource: str = "") -> None:
        self.resource = resource

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    def capture(self) -> Waveform:
        """Acquire one trace from the active channel and return as Waveform."""
        raise NotImplementedError


# Synthesizer parameters — nominal values with tight per-call jitter so the
# generated waveform looks realistic but successive captures vary slightly.
_NOMINAL_V_FINAL = 5.0
_NOMINAL_RISE_US = 5.0
_NOMINAL_OVERSHOOT_FRAC = 0.03  # 3 % of final value

_SAMPLE_INTERVAL_S = 1e-6  # 1 MS/s
_N_SAMPLES = 1_000  # 1 ms record
_STEP_AT_SAMPLE = 100  # pre-trigger zero for 100 µs


def synthesize_psu_step_response() -> Waveform:
    """Generate one PSU step-response trace with realistic shape and jitter.

    Models a second-order underdamped step response:

    * Pre-trigger: 0 V for the first 100 samples (100 µs at 1 MS/s).
    * Rising edge: exponential approach to 5 V with ~5 µs time constant.
    * Overshoot: ~3 % of final value, damped sinusoid with ~30 µs decay.
    * Settling: returns to nominal within ~100 µs of the step.

    Each call jitters the rise time (±10 %) and overshoot (±33 %) so derived
    measurements like rise-time and overshoot vary slightly between captures.
    """
    rise_us = _NOMINAL_RISE_US * random.uniform(0.9, 1.1)
    overshoot_v = _NOMINAL_V_FINAL * _NOMINAL_OVERSHOOT_FRAC * random.uniform(0.66, 1.33)
    damp_us = 30.0
    ringing_period_us = 25.0

    samples: list[float] = []
    for i in range(_N_SAMPLES):
        t_us = (i - _STEP_AT_SAMPLE) * 1.0
        if t_us < 0:
            v = 0.0
        else:
            rise = _NOMINAL_V_FINAL * (1.0 - math.exp(-t_us / rise_us))
            ringing = (
                overshoot_v
                * math.sin(2 * math.pi * t_us / ringing_period_us)
                * math.exp(-t_us / damp_us)
            )
            v = rise + ringing
        samples.append(v)

    # The trigger fires at sample 100 (the step). For a synthesized
    # capture, anchor t0 to "now minus the pre-trigger duration" so
    # the absolute time of the first sample is meaningful for downstream
    # analytics (range queries, cross-channel correlation).
    pre_trigger_s = _STEP_AT_SAMPLE * _SAMPLE_INTERVAL_S
    t0 = datetime.now(UTC) - timedelta(seconds=pre_trigger_s)
    return Waveform(
        t0=t0,
        dt=_SAMPLE_INTERVAL_S,
        Y=samples,
        attributes={"unit": "V", "channel": "ch1", "trigger": "rising"},
    )
