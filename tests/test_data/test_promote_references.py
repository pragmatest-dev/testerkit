"""`litmus data promote` carries a run's referenced data — no dangling refs.

Promotion is the *fine* portability grain: a run + the channel slices and files
it references (reachability via the run's own ``out_*`` columns), not the whole
``data_dir``. Unreferenced data is left behind.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from litmus.cli import _copy_run_references, _extract_parquet_refs

_SID = "abcdef1234567890"  # session_id; short = abcdef12
_DATE = "2026-01-01"


def _build_src(tmp_path: Path) -> tuple[Path, Path]:
    """A src data_dir: a run parquet referencing one channel + one file, both present."""
    src = tmp_path / "src"
    runs = src / "runs" / "runs" / _DATE
    runs.mkdir(parents=True)
    parquet = runs / "120000_DUT1.parquet"
    file_key = f"{_DATE}/{_SID}/capture.bin"
    pq.write_table(
        pa.table(
            {
                "measurement_name": ["m1", "m2"],
                "out_scope.ch1": [f"channel://scope.ch1?session={_SID}", None],
                "out_capture": [None, f"file://{file_key}"],
            }
        ),
        parquet,
    )

    chdir = src / "channels" / _DATE
    chdir.mkdir(parents=True)
    (chdir / f"scope.ch1_{_SID[:8]}.arrow").write_bytes(b"seg")  # referenced
    (chdir / "scope.ch9_99999999.arrow").write_bytes(b"other")  # NOT referenced

    fdir = src / "files" / _DATE / _SID
    fdir.mkdir(parents=True)
    (fdir / "capture.bin").write_bytes(b"blob")
    (fdir / "capture.bin.meta.json").write_text("{}")
    return src, parquet


def test_extract_parquet_refs_finds_both_schemes(tmp_path: Path) -> None:
    _, parquet = _build_src(tmp_path)
    channels, files = _extract_parquet_refs(parquet)
    assert channels == {("scope.ch1", _SID)}
    assert files == {f"{_DATE}/{_SID}/capture.bin"}


def test_copy_run_references_carries_referenced_data(tmp_path: Path) -> None:
    src, parquet = _build_src(tmp_path)
    dst = tmp_path / "dst"

    n_chan, n_files = _copy_run_references(parquet, src, dst, with_events=False)
    assert (n_chan, n_files) == (1, 1)

    # referenced channel segment + file + sidecar carried
    assert (dst / "channels" / _DATE / f"scope.ch1_{_SID[:8]}.arrow").read_bytes() == b"seg"
    assert (dst / "files" / _DATE / _SID / "capture.bin").read_bytes() == b"blob"
    assert (dst / "files" / _DATE / _SID / "capture.bin.meta.json").exists()
    # the UNreferenced channel segment is left behind
    assert not (dst / "channels" / _DATE / "scope.ch9_99999999.arrow").exists()


def test_with_events_is_opt_in(tmp_path: Path) -> None:
    src, parquet = _build_src(tmp_path)
    ev = src / "events" / _DATE
    ev.mkdir(parents=True)
    (ev / f"{_SID}-12345.arrow").write_bytes(b"events")

    _copy_run_references(parquet, src, tmp_path / "with", with_events=True)
    assert (tmp_path / "with" / "events" / _DATE / f"{_SID}-12345.arrow").exists()

    _copy_run_references(parquet, src, tmp_path / "without", with_events=False)
    assert not (tmp_path / "without" / "events").exists()
