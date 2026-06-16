"""File write-path scaling acceptance benchmark.

Does the FileStore producer scale across 1, 2, 4 concurrent writer PROCESSES for
the three write shapes?

- ``files.write``      — bulk PUT (one 100 KB artifact per op; serialize +
                         sidecar + catalog row).
- ``files.stream_raw`` — one append sink, ``scale`` × 64 KB chunks (the streaming
                         path: frame relay + byte tracking + checkpoints).
- ``files.raw_io``     — raw ``open(...,'wb').write()`` of the same chunks; the
                         file-I/O ceiling the stream is measured against (no sink,
                         no relay, no sidecar).

The file analogue of the channel benchmark (``bench_channel_scaling.py``). Files
have no ``write_many`` — bulk PUT and the stream sink are the two throughput
shapes; ``raw_io`` is the ceiling. Run this whenever the FileStore write path,
the streaming sink, the frame relay, or the stream checkpoint changes.

Read it as same-run ratios, not absolute numbers (WSL2 / machine variance), and
NOT across ops (write moves 100 KB/op, stream/raw 64 KB/op). Aggregate throughput
should hold or INCREASE with writers; the per-writer factor should not collapse
below the single-writer baseline for that op.

NOTE: multiprocessing 'spawn' re-imports this module in each worker, so the run
MUST sit under ``if __name__ == "__main__"`` or workers re-run the benchmark.

Usage::

    uv run python scripts/bench_file_scaling.py
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from litmus.benchmark.concurrency import run_concurrency

# Per-op scale — files are heavier than channel samples, and the shapes differ in
# bytes/op, so each op gets a scale that measures stably without flooding disk.
SCALES = {
    "files.write": 1_000,  # 1k PUTs × 100 KB
    "files.stream_raw": 4_000,  # 4k chunks × 64 KB through one sink
    "files.raw_io": 4_000,  # same chunks, raw I/O ceiling
}
ROUNDS = 3
WORKERS = [1, 2, 4]


def main() -> None:
    # Absolute — the FileStore backend resolves a ``file://`` URI via
    # ``Path.as_uri()``, which rejects a relative path.
    base = Path(".tmp").resolve() / f"bench_file_scaling_{uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        for op, scale in SCALES.items():
            print(f"\n=== {op}  (scale={scale:,}/worker, best-of-{ROUNDS}) ===", flush=True)
            per1 = None
            for w in WORKERS:
                dd = base / op.replace(".", "_")
                dd.mkdir(parents=True, exist_ok=True)
                walls = run_concurrency(dd, op, scale, w, rounds=ROUNDS)
                agg = scale * w / min(walls)
                per = agg / w
                if per1 is None:
                    per1 = per
                factor = per / per1
                print(
                    f"  {w}w: agg {agg:>12,.0f} ops/s   per-writer {per:>11,.0f}/s   "
                    f"factor {factor:4.2f}",
                    flush=True,
                )
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
