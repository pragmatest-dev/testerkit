"""Channel write-path scaling acceptance benchmark.

Does `channels.write` / `write_many` / `stream` scale across 1, 2, 4 concurrent
writer PROCESSES (``serve=True`` — every write pushes to the shared daemon)?

This guards the channel producer's painfully-tuned throughput across all three
write modes. The bundled ``litmus benchmark`` concurrent sweep only exercises
``channels.write``; the high-throughput batched paths (``write_many``, the
``stream`` sink) are NOT in it, so a regression there is invisible to the CLI.
Run this whenever the producer push path, the daemon do_put / ingest path, or
the liveness probe changes.

Read it as same-run ratios, not absolute numbers (WSL2 / machine variance):
aggregate throughput should hold or INCREASE with writers; the per-writer factor
should not collapse below the single-writer baseline for that op. Reference
(Intel Ultra 9 275HX, 24c, WSL2): write_many / stream ~150–200k samp/s at 1w.

NOTE: multiprocessing 'spawn' re-imports this module in each worker, so the run
MUST sit under ``if __name__ == "__main__"`` or workers re-run the benchmark.

Usage::

    uv run python scripts/bench_channel_scaling.py
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from litmus.benchmark.concurrency import run_concurrency

SCALE = 20_000
ROUNDS = 3
WORKERS = [1, 2, 4]
OPS = ["channels.write", "channels.write_many", "channels.stream"]


def main() -> None:
    base = Path(".tmp") / f"bench_channel_scaling_{uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        for op in OPS:
            print(f"\n=== {op}  (scale={SCALE:,}/worker, best-of-{ROUNDS}) ===", flush=True)
            per1 = None
            for w in WORKERS:
                dd = base / op.replace(".", "_")
                dd.mkdir(parents=True, exist_ok=True)
                walls = run_concurrency(dd, op, SCALE, w, rounds=ROUNDS)
                agg = SCALE * w / min(walls)
                per = agg / w
                if per1 is None:
                    per1 = per
                factor = per / per1
                print(
                    f"  {w}w: agg {agg:>12,.0f} samp/s   per-writer {per:>11,.0f}/s   "
                    f"factor {factor:4.2f}",
                    flush=True,
                )
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
