"""Tests for data retention utilities."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from litmus.data.retention import parse_duration, prune_all, prune_date_dirs


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
    @pytest.fixture()
    def project_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Create a project dir with litmus.yaml so prune considers it owned."""
        (tmp_path / "litmus.yaml").write_text(
            f"name: test\nresults_dir: {tmp_path / 'data'}\n"
        )
        monkeypatch.chdir(tmp_path)
        return tmp_path / "data"

    def _make_date_dirs(self, base: Path, dates: list[date]) -> None:
        for d in dates:
            (base / d.isoformat()).mkdir(parents=True)

    def test_deletes_old_keeps_recent(self, project_dir: Path):
        today = date.today()
        old = today - timedelta(days=60)
        recent = today - timedelta(days=5)
        self._make_date_dirs(project_dir, [old, recent])

        cutoff = today - timedelta(days=30)
        removed = prune_date_dirs(project_dir, cutoff)

        assert len(removed) == 1
        assert removed[0].name == old.isoformat()
        assert not (project_dir / old.isoformat()).exists()
        assert (project_dir / recent.isoformat()).exists()

    def test_dry_run(self, project_dir: Path):
        old = date.today() - timedelta(days=60)
        self._make_date_dirs(project_dir, [old])

        cutoff = date.today() - timedelta(days=30)
        removed = prune_date_dirs(project_dir, cutoff, dry_run=True)

        assert len(removed) == 1
        assert (project_dir / old.isoformat()).exists()  # not deleted

    def test_nonexistent_dir(self, project_dir: Path):
        removed = prune_date_dirs(project_dir / "nope", date.today())
        assert removed == []

    def test_ignores_non_date_dirs(self, project_dir: Path):
        (project_dir / "not-a-date").mkdir(parents=True)
        removed = prune_date_dirs(project_dir, date.today())
        assert removed == []

    def test_refuses_unowned_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        no_project = tmp_path / "no_project"
        no_project.mkdir()
        monkeypatch.chdir(no_project)

        with pytest.raises(PermissionError, match="project-owned"):
            prune_date_dirs(tmp_path / "whatever", date.today())


class TestPruneAll:
    @pytest.fixture()
    def project_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Create a project dir with litmus.yaml so prune considers it owned."""
        (tmp_path / "litmus.yaml").write_text(
            f"name: test\nresults_dir: {tmp_path / 'results'}\n"
        )
        monkeypatch.chdir(tmp_path)
        return tmp_path / "results"

    def test_prunes_all_subdirs(self, project_dir: Path):
        old = (date.today() - timedelta(days=60)).isoformat()
        for sub in ("channels", "events"):
            (project_dir / sub / old).mkdir(parents=True)

        result = prune_all(project_dir, "30d")
        for sub in ("channels", "events"):
            assert len(result[sub]) == 1
            assert not (project_dir / sub / old).exists()

    def test_prunes_specific_types(self, project_dir: Path):
        old = (date.today() - timedelta(days=60)).isoformat()
        for sub in ("channels", "events"):
            (project_dir / sub / old).mkdir(parents=True)

        result = prune_all(project_dir, "30d", data_types=("channels",))
        assert len(result) == 1
        assert len(result["channels"]) == 1
        # events untouched
        assert (project_dir / "events" / old).exists()

    def test_refuses_unowned_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Pruning a dir not owned by any project should fail."""
        # chdir to a dir with no litmus.yaml
        no_project = tmp_path / "no_project"
        no_project.mkdir()
        monkeypatch.chdir(no_project)

        target = tmp_path / "some_random_dir"
        target.mkdir()
        with pytest.raises(PermissionError, match="project-owned"):
            prune_all(target, "30d")


