"""Seed ChannelStore with a few channels so the operator-UI /channels
screenshot renders a populated table instead of an empty state.

07-profiles' test suite records runs and measurements but does not stream
channels, and the channel registry is populated only by live writes — so
the docs screenshot of /channels comes back blank unless something streams
first. This script does exactly that, through the supported store-direct
verbs (``connect`` + ``litmus.channels.stream``) — the same path a bench
script or notebook would use.

Run from the example root before regenerating screenshots::

    cd examples/07-profiles
    uv run python scripts/seed_channels.py

Then run ``scripts/regenerate-ui-screenshots.py`` from the repo root.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

# Make ``drivers/`` (sibling of ``scripts/``) importable so the station
# YAML's ``driver: drivers.PSU`` resolves under ``uv run python scripts/...``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import litmus.channels as channels  # noqa: E402
from litmus import connect  # noqa: E402

SAMPLES = 120

# Synthetic gaussian traces mirroring the station's mock_config nominals —
# the seed needs believable sparkline data, not a live instrument.
TRACES = {
    "psu.voltage": (5.0, 0.02),
    "psu.current": (0.05, 0.004),
    "dmm.dc_voltage": (3.30, 0.015),
}


def main() -> None:
    rng = random.Random(20260622)  # deterministic so reruns are stable
    with connect("bench_01"):
        with (
            channels.stream("psu.voltage") as v,
            channels.stream("psu.current") as c,
            channels.stream("dmm.dc_voltage") as d,
        ):
            for sink, (nominal, sigma) in zip((v, c, d), TRACES.values(), strict=True):
                for _ in range(SAMPLES):
                    sink.write(rng.gauss(nominal, sigma))
    print(f"Seeded {len(TRACES)} channels x {SAMPLES} samples into ChannelStore.")


if __name__ == "__main__":
    main()
