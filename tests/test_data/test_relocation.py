"""`litmus data import` merges a data_dir; daemons rebuild from the files.

Tests the file-merge half (union, skip collisions). The daemon-restart half is
covered by the daemon lifecycle tests — here we just verify the merge is a safe,
idempotent union of the source's store subdirs.
"""

from __future__ import annotations

from pathlib import Path

from litmus.cli import _merge_data_dir


def test_merge_unions_stores_and_skips_collisions(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"

    (src / "runs" / "runs" / "2026-01-01").mkdir(parents=True)
    (src / "runs" / "runs" / "2026-01-01" / "r.parquet").write_bytes(b"new-run")
    (src / "channels" / "2026-01-01").mkdir(parents=True)
    (src / "channels" / "2026-01-01" / "c_abcdef12.arrow").write_bytes(b"seg")
    (src / "files" / "2026-01-01" / "sess").mkdir(parents=True)
    (src / "files" / "2026-01-01" / "sess" / "blob.bin").write_bytes(b"blob")

    # dst already holds a colliding run (must NOT be overwritten).
    (dst / "runs" / "runs" / "2026-01-01").mkdir(parents=True)
    (dst / "runs" / "runs" / "2026-01-01" / "r.parquet").write_bytes(b"EXISTING")

    copied = _merge_data_dir(src, dst)

    assert copied == 2  # channel segment + file blob; the colliding run was skipped
    assert (dst / "channels" / "2026-01-01" / "c_abcdef12.arrow").read_bytes() == b"seg"
    assert (dst / "files" / "2026-01-01" / "sess" / "blob.bin").read_bytes() == b"blob"
    # collision left untouched (idempotent / non-destructive)
    assert (dst / "runs" / "runs" / "2026-01-01" / "r.parquet").read_bytes() == b"EXISTING"


def test_merge_empty_source(tmp_path: Path) -> None:
    assert _merge_data_dir(tmp_path / "nope", tmp_path / "dst") == 0
