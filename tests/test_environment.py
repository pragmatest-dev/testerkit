"""Tests for litmus.environment — environment snapshot capture."""

from litmus.environment import (
    EnvironmentSnapshot,
    PackageInfo,
    _package_sort_key,
    capture_environment,
)


class TestPackageSortKey:
    def test_lowercases(self):
        pkg = PackageInfo(name="PyTest", version="1.0")
        assert _package_sort_key(pkg) == "pytest"


class TestEnvironmentSnapshot:
    def test_fingerprint_deterministic(self):
        """Same packages produce same fingerprint regardless of order."""
        pkgs_a = [
            PackageInfo(name="alpha", version="1.0"),
            PackageInfo(name="beta", version="2.0"),
        ]
        pkgs_b = [
            PackageInfo(name="beta", version="2.0"),
            PackageInfo(name="alpha", version="1.0"),
        ]
        snap_a = EnvironmentSnapshot(
            python_version="3.12.0", os_name="Linux", os_version="6.0",
            platform_machine="x86_64", litmus_version="0.1.0", packages=pkgs_a,
        )
        snap_b = EnvironmentSnapshot(
            python_version="3.12.0", os_name="Linux", os_version="6.0",
            platform_machine="x86_64", litmus_version="0.1.0", packages=pkgs_b,
        )
        assert snap_a.fingerprint == snap_b.fingerprint

    def test_fingerprint_changes_with_version(self):
        base = dict(
            python_version="3.12.0", os_name="Linux", os_version="6.0",
            platform_machine="x86_64", litmus_version="0.1.0",
        )
        snap_a = EnvironmentSnapshot(
            **base, packages=[PackageInfo(name="pkg", version="1.0")],
        )
        snap_b = EnvironmentSnapshot(
            **base, packages=[PackageInfo(name="pkg", version="2.0")],
        )
        assert snap_a.fingerprint != snap_b.fingerprint

    def test_fingerprint_length(self):
        snap = EnvironmentSnapshot(
            python_version="3.12.0", os_name="Linux", os_version="6.0",
            platform_machine="x86_64", litmus_version="0.1.0", packages=[],
        )
        assert len(snap.fingerprint) == 16

    def test_roundtrip_json(self):
        snap = EnvironmentSnapshot(
            python_version="3.12.0", os_name="Linux", os_version="6.0",
            platform_machine="x86_64", litmus_version="0.1.0",
            packages=[PackageInfo(name="foo", version="1.0")],
            lockfile_hash="abc123",
        )
        json_str = snap.model_dump_json()
        restored = EnvironmentSnapshot.model_validate_json(json_str)
        assert restored.python_version == snap.python_version
        assert restored.packages[0].name == "foo"
        assert restored.lockfile_hash == "abc123"

    def test_roundtrip_json_bytes(self):
        """model_validate_json accepts bytes (as used by parquet metadata)."""
        snap = EnvironmentSnapshot(
            python_version="3.12.0", os_name="Linux", os_version="6.0",
            platform_machine="x86_64", litmus_version="0.1.0", packages=[],
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
        assert len(snap.packages) > 0

    def test_no_duplicate_packages(self):
        snap = capture_environment()
        names = [p.name.lower() for p in snap.packages]
        assert len(names) == len(set(names))

    def test_lockfile_hash_or_none(self):
        snap = capture_environment()
        # Either a 16-char hex string or None
        if snap.lockfile_hash is not None:
            assert len(snap.lockfile_hash) == 16
