"""load_ref degrades gracefully on a dangling ref — never crashes (#263).

The federation's integrity contract: a missing reference reads as a clean,
surfaced "unavailable" (the URI comes back unresolved), never a crash or silent
corruption. This is the safety net for manual file surgery and pruned data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from testerkit.data.backends.parquet import load_ref


def test_missing_file_ref_returns_uri(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from testerkit.data.files import _reset_for_tests
    from testerkit.data.files import store as fstore_module

    monkeypatch.setattr(fstore_module, "resolve_data_dir", lambda _=None: tmp_path)
    _reset_for_tests()
    uri = "file://2026-01-01/sess/gone.bin"  # artifact never written
    assert load_ref(uri) == uri  # unavailable → URI back, no exception


def test_dangling_channel_ref_returns_uri() -> None:
    class BrokenStore:
        def query(self, *_a: object, **_k: object) -> object:
            raise RuntimeError("channel daemon unreachable")

    uri = "channel://scope.ch1?session=abc"
    assert load_ref(uri, channel_store=BrokenStore()) == uri  # no crash


def test_channel_ref_without_store_returns_uri() -> None:
    uri = "channel://scope.ch1?session=abc"
    assert load_ref(uri) == uri
