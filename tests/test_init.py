"""Tests for litmus init project scaffolding."""

from pathlib import Path
from unittest.mock import patch

import yaml

from litmus.init import _resolve_project_name, _sanitize_name, init_project


class TestSanitizeName:
    def test_hyphens(self):
        assert _sanitize_name("my-project") == "my_project"

    def test_spaces(self):
        assert _sanitize_name("my project") == "my_project"

    def test_clean(self):
        assert _sanitize_name("already_clean") == "already_clean"

    def test_mixed(self):
        assert _sanitize_name("my-cool project") == "my_cool_project"


class TestResolveProjectName:
    def test_git_remote_wins(self, tmp_path):
        with (
            patch(
                "litmus.execution._git.get_git_remote",
                return_value="https://github.com/org/board_a.git",
            ),
            patch("litmus.execution._git._git_repo_root", return_value=tmp_path),
        ):
            assert _resolve_project_name(tmp_path) == "board_a"

    def test_git_root_when_no_remote(self, tmp_path):
        root = tmp_path / "my_repo"
        root.mkdir()
        with (
            patch("litmus.execution._git.get_git_remote", return_value=None),
            patch("litmus.execution._git._git_repo_root", return_value=root),
        ):
            assert _resolve_project_name(tmp_path) == "my_repo"

    def test_folder_name_when_no_git(self, tmp_path):
        project_dir = tmp_path / "fallback_project"
        project_dir.mkdir()
        with (
            patch("litmus.execution._git.get_git_remote", return_value=None),
            patch("litmus.execution._git._git_repo_root", return_value=None),
        ):
            assert _resolve_project_name(project_dir) == "fallback_project"

    def test_sanitizes_hyphens(self, tmp_path):
        with (
            patch(
                "litmus.execution._git.get_git_remote",
                return_value="git@github.com:org/my-board.git",
            ),
            patch("litmus.execution._git._git_repo_root", return_value=None),
        ):
            assert _resolve_project_name(tmp_path) == "my_board"

    def test_ssh_remote(self, tmp_path):
        with (
            patch(
                "litmus.execution._git.get_git_remote",
                return_value="git@github.com:org/widget.git",
            ),
            patch("litmus.execution._git._git_repo_root", return_value=None),
        ):
            assert _resolve_project_name(tmp_path) == "widget"


class TestInitProject:
    """Test init_project with all name resolution permutations."""

    def _read_litmus_yaml(self, path: Path) -> dict:
        return yaml.safe_load((path / "litmus.yaml").read_text())

    def test_explicit_name_override(self, tmp_path):
        """litmus init my_project --name custom_name"""
        project = tmp_path / "my_project"
        project.mkdir()
        init_project(project, git=False, name="custom_name")
        cfg = self._read_litmus_yaml(project)
        assert cfg["name"] == "custom_name"

    def test_name_sanitized(self, tmp_path):
        """litmus init --name 'my-cool board'"""
        project = tmp_path / "whatever"
        project.mkdir()
        init_project(project, git=False, name="my-cool board")
        cfg = self._read_litmus_yaml(project)
        assert cfg["name"] == "my_cool_board"

    def test_no_name_resolves_from_git_remote(self, tmp_path):
        """litmus init (CWD mode, inside a repo with remote)"""
        project = tmp_path / "local_folder"
        project.mkdir()
        with (
            patch(
                "litmus.execution._git.get_git_remote",
                return_value="https://github.com/org/board_x.git",
            ),
            patch("litmus.execution._git._git_repo_root", return_value=tmp_path),
        ):
            init_project(project, git=False)
        cfg = self._read_litmus_yaml(project)
        assert cfg["name"] == "board_x"

    def test_no_name_resolves_from_git_root(self, tmp_path):
        """litmus init (CWD mode, repo but no remote)"""
        project = tmp_path / "local_folder"
        project.mkdir()
        repo_root = tmp_path / "my_repo"
        repo_root.mkdir()
        with (
            patch("litmus.execution._git.get_git_remote", return_value=None),
            patch("litmus.execution._git._git_repo_root", return_value=repo_root),
        ):
            init_project(project, git=False)
        cfg = self._read_litmus_yaml(project)
        assert cfg["name"] == "my_repo"

    def test_no_name_falls_back_to_folder(self, tmp_path):
        """litmus init (no git at all)"""
        project = tmp_path / "my_project"
        project.mkdir()
        with (
            patch("litmus.execution._git.get_git_remote", return_value=None),
            patch("litmus.execution._git._git_repo_root", return_value=None),
        ):
            init_project(project, git=False)
        cfg = self._read_litmus_yaml(project)
        assert cfg["name"] == "my_project"

    def test_creates_expected_dirs(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        init_project(project, git=False)
        for d in ["parts", "stations", "fixtures", "instruments", "tests"]:
            assert (project / d).is_dir(), f"Missing directory: {d}"

    def test_creates_litmus_yaml(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        init_project(project, git=False, name="test_proj")
        assert (project / "litmus.yaml").exists()
        cfg = self._read_litmus_yaml(project)
        assert cfg["name"] == "test_proj"

    def test_starter_creates_example_files(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        init_project(project, git=False, starter=True, name="proj")
        assert (project / "stations" / "starter_station.yaml").exists()
        assert (project / "tests" / "test_example.py").exists()

    def test_skip_if_exists(self, tmp_path):
        """Running init twice doesn't overwrite existing files."""
        project = tmp_path / "proj"
        project.mkdir()
        init_project(project, git=False, name="first")
        (project / "litmus.yaml").write_text("name: custom\n")
        init_project(project, git=False, name="second")
        cfg = self._read_litmus_yaml(project)
        assert cfg["name"] == "custom"

    def test_starter_sets_defaults(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        init_project(project, git=False, starter=True, name="proj")
        cfg = self._read_litmus_yaml(project)
        assert cfg["default_station"] == "starter_station"
        assert cfg["mock_instruments"] is True

    def test_tier_bringup_minimal_scaffold(self, tmp_path):
        """--tier bringup writes conftest mocks + smoke test + sidecar, no station/part YAML."""
        project = tmp_path / "proj"
        project.mkdir()
        init_project(project, git=False, tier="bringup", name="proj")
        assert (project / "tests" / "conftest.py").exists()
        assert (project / "tests" / "test_smoke.py").exists()
        assert (project / "tests" / "test_smoke.yaml").exists()
        assert "MagicMock" in (project / "tests" / "conftest.py").read_text()
        # Bringup skips the Tier 2+ folders
        assert not (project / "stations").exists()
        assert not (project / "parts").exists()
        assert not (project / "fixtures").exists()
        # pyproject.toml has no --station addopts
        pyproject = (project / "pyproject.toml").read_text()
        assert "--station=" not in pyproject

    def test_tier_bench_equivalent_to_starter(self, tmp_path):
        """--tier bench produces the same scaffold as --starter."""
        project = tmp_path / "proj"
        project.mkdir()
        init_project(project, git=False, tier="bench", name="proj")
        assert (project / "stations" / "starter_station.yaml").exists()
        assert (project / "tests" / "test_example.py").exists()
