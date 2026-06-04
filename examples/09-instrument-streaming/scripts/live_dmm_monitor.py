"""Continuous DMM streaming + live operator UI panel.

Run this script in one terminal, then open
``http://localhost:8000/channels/dmm.voltage`` in your browser (after
starting ``litmus serve``). The chart updates push-style as samples
arrive — same primitive (``channels.stream``) that feeds the live UI
on the real test bench.

Usage::

    # terminal 1
    cd examples/09-instrument-streaming
    uv run litmus serve --reload

    # terminal 2
    cd examples/09-instrument-streaming
    uv run python scripts/live_dmm_monitor.py

The script runs for 60 seconds at 50 samples/second (3000 samples).
Stop early with Ctrl-C. Reopen the channel panel — your samples
stay on the timeline (ChannelStore is persisted, not just live).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Make ``drivers/`` (sibling of ``scripts/``) importable so the station
# YAML's ``driver: drivers.DMM`` resolves. ``uv run python scripts/foo.py``
# puts ``scripts/`` on sys.path, not the example root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import litmus.channels  # noqa: E402
from litmus.connect import connect  # noqa: E402

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
