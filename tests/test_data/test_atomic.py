"""Tests for atomic file write helpers."""

from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from testerkit.data._atomic import atomic_write_table, atomic_write_text


def test_atomic_write_table(tmp_path: Path) -> None:
    """Write a table atomically and verify contents."""
    table = pa.table({"x": [1, 2, 3], "y": ["a", "b", "c"]})
    path = tmp_path / "out.parquet"

    atomic_write_table(table, path)

    result = pq.read_table(path)
    assert result.column("x").to_pylist() == [1, 2, 3]
    assert result.column("y").to_pylist() == ["a", "b", "c"]


def test_atomic_write_table_no_partial_on_failure(tmp_path: Path) -> None:
    """On failure, no partial file or temp file should remain."""
    table = pa.table({"x": [1]})
    path = tmp_path / "out.parquet"

    with patch("testerkit.data._atomic.pq.write_table", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            atomic_write_table(table, path)

    assert not path.exists()
    # No temp files left
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_text(tmp_path: Path) -> None:
    """Write text atomically and verify contents."""
    path = tmp_path / "out.json"

    atomic_write_text('{"key": "value"}', path)

    assert path.read_text() == '{"key": "value"}'


def test_atomic_write_text_no_partial_on_failure(tmp_path: Path) -> None:
    """On failure, no partial file or temp file should remain."""
    path = tmp_path / "out.json"

    with patch("testerkit.data._atomic.os.write", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            atomic_write_text("data", path)

    assert not path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_table_creates_parent_dirs(tmp_path: Path) -> None:
    """Parent directories are created if missing."""
    table = pa.table({"x": [1]})
    path = tmp_path / "sub" / "dir" / "out.parquet"

    atomic_write_table(table, path)

    assert path.exists()
