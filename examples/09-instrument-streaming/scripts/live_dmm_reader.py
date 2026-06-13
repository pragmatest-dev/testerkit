"""Live CONSUMER companion — read dmm.voltage as it streams, via the verbs.

The sibling ``live_dmm_monitor.py`` *produces* the channel. This script
*consumes* it with the ``litmus.channels`` consumer verbs — no UI, just the
SDK a script or agent would use:

- ``channels.latest(name, cb)`` — newest sample, conflated (a gauge).
- ``channels.live(name, cb, max_hz=)`` — every sample, coalesced into batches
  (a chart edge), throttled to ``max_hz`` deliveries/second.

Run the producer in one terminal and this in another (both from the example
root so they share the same ``data/`` and daemon)::

    # terminal 1 — produce
    uv run python scripts/live_dmm_monitor.py

    # terminal 2 — consume
    uv run python scripts/live_dmm_reader.py

Set ``LITMUS_READ_SECONDS`` to run a shorter slice.
"""

from __future__ import annotations

import json
import os
import time

import litmus.channels as channels

DURATION_S = float(os.environ.get("LITMUS_READ_SECONDS", "30"))


def main() -> None:
    total = {"n": 0}

    def on_batch(batch) -> None:  # noqa: ANN001 — pyarrow.RecordBatch
        total["n"] += batch.num_rows
        newest = json.loads(batch.column("value")[-1].as_py())
        print(f"  live:   +{batch.num_rows:>3} samples (total {total['n']}), newest={newest}")

    def on_latest(sample) -> None:  # noqa: ANN001 — ChannelSample
        print(f"  latest: {sample.value}   (the gauge value)")

    print(f"Reading dmm.voltage live for {DURATION_S:.0f}s (run live_dmm_monitor.py to produce).")
    unsub_live = channels.live("dmm.voltage", on_batch, max_hz=10)
    unsub_latest = channels.latest("dmm.voltage", on_latest)
    try:
        time.sleep(DURATION_S)
    finally:
        unsub_live()
        unsub_latest()

    print(f"Done — received {total['n']} samples on the live feed.")


if __name__ == "__main__":
    main()
