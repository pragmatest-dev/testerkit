"""Atomic file write helpers.

Prevent data corruption from crashes during Parquet/JSON writes by
writing to a temporary file in the same directory, then atomically
renaming to the final path via ``os.replace()``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def atomic_write_table(table: pa.Table, path: Path) -> None:
    """Write a PyArrow table to Parquet atomically.

    Writes to a temp file in the same directory, then ``os.replace()``
    to the final path. On failure, the temp file is cleaned up and no
    partial file is left at ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    try:
        pq.write_table(table, tmp_path)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_text(text: str, path: Path) -> None:
    """Write text to a file atomically.

    Same pattern as ``atomic_write_table``: temp file + ``os.replace()``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    closed = False
    try:
        os.write(fd, text.encode("utf-8"))
        os.close(fd)
        closed = True
        os.replace(tmp_path, path)
    except BaseException:
        if not closed:
            os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
