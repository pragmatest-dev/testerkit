"""Tests for data retention utilities."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from litmus.data.retention import parse_duration, prune_all, prune_date_dirs, prune_from_config


class TestParseDuration:
    def test_valid(self):
        assert parse_duration("30d") == timedelta(days=30)
        assert parse_duration("90d") == timedelta(days=90)
        assert parse_duration(" 7d ") == timedelta(days=7)

    def test_invalid(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_duration("30h")
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_duration("abc")


class TestPruneDateDirs:
    def _make_date_dirs(self, base: Path, dates: list[date]) -> None:
        for d in dates:
            (base / d.isoformat()).mkdir(parents=True)

    def test_deletes_old_keeps_recent(self, tmp_path: Path):
        today = date.today()
        old = today - timedelta(days=60)
        recent = today - timedelta(days=5)
        self._make_date_dirs(tmp_path, [old, recent])

        cutoff = today - timedelta(days=30)
        removed = prune_date_dirs(tmp_path, cutoff)

        assert len(removed) == 1
        assert removed[0].name == old.isoformat()
        assert not (tmp_path / old.isoformat()).exists()
        assert (tmp_path / recent.isoformat()).exists()

    def test_dry_run(self, tmp_path: Path):
        old = date.today() - timedelta(days=60)
        self._make_date_dirs(tmp_path, [old])

        cutoff = date.today() - timedelta(days=30)
        removed = prune_date_dirs(tmp_path, cutoff, dry_run=True)

        assert len(removed) == 1
        assert (tmp_path / old.isoformat()).exists()  # not deleted

    def test_nonexistent_dir(self, tmp_path: Path):
        removed = prune_date_dirs(tmp_path / "nope", date.today())
        assert removed == []

    def test_ignores_non_date_dirs(self, tmp_path: Path):
        (tmp_path / "not-a-date").mkdir()
        removed = prune_date_dirs(tmp_path, date.today())
        assert removed == []


class TestPruneAll:
    def test_prunes_all_subdirs(self, tmp_path: Path):
        old = (date.today() - timedelta(days=60)).isoformat()
        for sub in ("telemetry", "sessions", "events"):
            (tmp_path / sub / old).mkdir(parents=True)

        result = prune_all(tmp_path, "30d")
        for sub in ("telemetry", "sessions", "events"):
            assert len(result[sub]) == 1
            assert not (tmp_path / sub / old).exists()

    def test_prunes_specific_types(self, tmp_path: Path):
        old = (date.today() - timedelta(days=60)).isoformat()
        for sub in ("telemetry", "sessions", "events"):
            (tmp_path / sub / old).mkdir(parents=True)

        result = prune_all(tmp_path, "30d", data_types=("telemetry",))
        assert len(result) == 1
        assert len(result["telemetry"]) == 1
        # sessions and events untouched
        assert (tmp_path / "sessions" / old).exists()
        assert (tmp_path / "events" / old).exists()


class TestPruneFromConfig:
    def test_prunes_outputs_with_retention(self, tmp_path: Path):
        from litmus.schemas import OutputConfig

        old = (date.today() - timedelta(days=60)).isoformat()
        (tmp_path / "results" / "telemetry" / old).mkdir(parents=True)
        (tmp_path / "results" / "sessions" / old).mkdir(parents=True)

        outputs = [
            OutputConfig(format="telemetry", retention="30d"),
            OutputConfig(format="sessions"),  # no retention — should be skipped
        ]
        result = prune_from_config(tmp_path, outputs)
        assert len(result) == 1
        assert "telemetry" in result
        assert len(result["telemetry"]) == 1
        # sessions untouched
        assert (tmp_path / "results" / "sessions" / old).exists()
