"""Tests for litmus.environment — environment snapshot capture."""

from litmus.environment import (
    EnvironmentSnapshot,
    capture_environment,
)


class TestEnvironmentSnapshot:
    def test_roundtrip_json(self):
        snap = EnvironmentSnapshot(
            python_version="3.12.0",
            os_name="Linux",
            os_version="6.0",
            platform_machine="x86_64",
            litmus_version="0.1.0",
            dependencies=["litmus>=0.1.0", "pytest>=8.0"],
            lockfile_hash="abc123",
        )
        json_str = snap.model_dump_json()
        restored = EnvironmentSnapshot.model_validate_json(json_str)
        assert restored.python_version == snap.python_version
        assert restored.dependencies == ["litmus>=0.1.0", "pytest>=8.0"]
        assert restored.lockfile_hash == "abc123"

    def test_roundtrip_json_bytes(self):
        """model_validate_json accepts bytes (as used by parquet metadata)."""
        snap = EnvironmentSnapshot(
            python_version="3.12.0",
            os_name="Linux",
            os_version="6.0",
            platform_machine="x86_64",
            litmus_version="0.1.0",
            dependencies=[],
        )
        json_bytes = snap.model_dump_json().encode("utf-8")
        restored = EnvironmentSnapshot.model_validate_json(json_bytes)
        assert restored.python_version == "3.12.0"


class TestCaptureEnvironment:
    def test_returns_snapshot(self):
        snap = capture_environment()
        assert isinstance(snap, EnvironmentSnapshot)
        assert snap.python_version
        assert snap.os_name
        assert snap.litmus_version

    def test_dependencies_from_pyproject(self):
        snap = capture_environment()
        # We're running in the litmus repo, so pyproject.toml exists
        assert len(snap.dependencies) > 0
        # Should be dependency specifiers, not package names
        assert any(">" in d or "=" in d for d in snap.dependencies)

    def test_lockfile_hash_or_none(self):
        snap = capture_environment()
        # Either a 16-char hex string or None
        if snap.lockfile_hash is not None:
            assert len(snap.lockfile_hash) == 16
