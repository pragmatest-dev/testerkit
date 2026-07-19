"""PSU step response — capture the waveform, derive scalars, judge them.

The canonical observe + verify pattern:

1. Trigger the event (``psu.set_voltage(5.0)``)
2. Capture the raw evidence (``scope.capture()`` returns a Waveform)
3. ``observe`` the waveform — routes to ChannelStore; the ``scope_step``
   output on every verify row in this vector carries the ``channel://`` URI
4. Compute the derived scalars (rise time, overshoot)
5. ``verify`` each scalar against its limit

On the ``/results/{run_id}`` page, the two verify rows (``rise_time_us`` and
``overshoot_v``) each show the ``scope_step`` output as a clickable URI that opens
the supporting waveform.
"""

from __future__ import annotations

import math

from testerkit import Limit, Waveform


def compute_rise_time_us(
    wf: Waveform, *, v_final: float, low: float = 0.1, high: float = 0.9
) -> float:
    """Return 10 %–90 % rise time in microseconds.

    Finds the first sample indices crossing ``low * v_final`` and
    ``high * v_final``, then computes the elapsed time from the index
    difference × ``dt``. (Sample-index arithmetic avoids the
    ``datetime`` timeline that ``wf.time_axis()`` would return — for
    a rise-time metric we only need the relative duration.)
    """
    low_v = low * v_final
    high_v = high * v_final

    i_low = next((i for i, y in enumerate(wf.Y) if y >= low_v), None)
    i_high = next((i for i, y in enumerate(wf.Y) if y >= high_v), None)
    if i_low is None or i_high is None:
        return math.nan
    return (i_high - i_low) * wf.dt * 1e6


def compute_overshoot_v(wf: Waveform, *, v_final: float) -> float:
    """Return peak overshoot above ``v_final`` in volts (0.0 if no overshoot)."""
    return max(0.0, max(wf.Y) - v_final)


def test_psu_step_response(observe, verify, psu, scope) -> None:
    psu.set_voltage(5.0)

    wf = scope.capture()
    observe("scope_step", wf)  # routes to ChannelStore; stamps the scope_step output on this vector

    rise_us = compute_rise_time_us(wf, v_final=5.0)
    overshoot_v = compute_overshoot_v(wf, v_final=5.0)

    verify("rise_time_us", rise_us, Limit(low=0, high=20, unit="us"))
    verify("overshoot_v", overshoot_v, Limit(low=0, high=0.5, unit="V"))
